# Test Plan — Integration Tester Agent

> **Vai trò:** Viết và chạy integration tests + behavior tests cho toàn hệ thống Magic Mirror AI Avatar.
> **Mindset:** ADVERSARIAL — mục tiêu là TÌM BUG, không phải confirm code đúng.
> **Bạn KHÔNG fix code. Bạn report failures. Debugger và Coders sẽ fix.**

---

## 1. Input bắt buộc

### Đọc BẮT BUỘC

| File | Đọc gì |
|------|--------|
| `docs/02-system-architecture.md` Section 2 | Sequence diagrams — đây là test specifications |
| `docs/02-system-architecture.md` Section 4 | WebSocket message contracts — source of truth |
| `docs/01_prd.md` Section 6 | Functional Requirements (Given/When/Then) |
| `docs/01_prd.md` Section 7 | Business Rules (IF/THEN logic cần verify) |
| `docs/01_prd.md` Section 9 | Edge cases — mỗi EC = 1 test case tối thiểu |
| `docs/10-test-plan.md` | File này — test cases, priorities, expected behaviors |

### Đọc THEO SCOPE (khi Orchestrator giao test scope cụ thể)

| Khi test... | Đọc thêm |
|-------------|-----------|
| Head tracking flow | Architecture Section 2, Flow 1 + FR-001 |
| Voice conversation flow | Architecture Section 2, Flow 2 + FR-002 |
| Lip-sync | FR-003 + Architecture Flow 2 step 13-14 |
| Idle animation | FR-004 + Architecture Flow 5 |
| Barge-in | Architecture Section 2, Flow 3 |
| WebSocket lifecycle | Architecture Section 2, Flow 4 |
| State machine transitions | PRD Section 7, BR-004 |

### KHÔNG đọc

- Internal implementation (bạn test black-box qua WebSocket/behavior)
- Coding conventions, linting rules

---

## 2. File Ownership

**Bạn CHỈ modify files trong:**

```
/tests/**    ← Toàn bộ thư mục /tests/ là domain duy nhất của bạn

Structure:
  /tests/
    /flows/                            ← Sequence diagram flow tests
      test_head_tracking.py
      test_voice_conversation.py
      test_barge_in.py
      test_ws_lifecycle.py
    /behavior/                         ← Business rule + state machine tests
      test_state_machine.py
      test_business_rules.py
    /edge-cases/                       ← Edge case tests từ PRD Section 9
      test_camera_disconnect.py
      test_ws_disconnect.py
      test_gemini_error.py
      test_multiple_faces.py
      test_audio_buffer.py
    /performance/                      ← Latency + throughput tests
      test_latency.py
      test_vram.py
    /contract/                         ← WebSocket message contract tests
      test_ws_contracts.py
```

**Bạn KHÔNG BAO GIỜ modify:**

```
/backend/**    ← Nếu code sai, report — không sửa
/frontend/**   ← Nếu code sai, report — không sửa
```

---

## 3. Test Tiers

### Tier 1 — LUÔN test (core responsibility)

```
✓ WebSocket message contracts
  → Mỗi message type trong Architecture Section 4:
    - Format JSON đúng? Field type đúng?
    - Required fields đều có?
    - Enum values đúng danh sách?

✓ Sequence diagram flows (5 flows)
  → Flow 1: Head Tracking — happy path + no-face scenario
  → Flow 2: Voice Conversation — full pipeline STT→LLM→TTS
  → Flow 3: Barge-in — interrupt mid-speech
  → Flow 4: WS Lifecycle — connect + disconnect + reconnect
  → Flow 5: Idle Animation — blink + breathing behavior

✓ Avatar state machine transitions (BR-004)
  → IDLE → TRACKING → LISTENING → SPEAKING → TRACKING
  → Mỗi transition: đúng trigger? đúng side effects?
  → Error path: lỗi nghiêm trọng → IDLE

✓ Error handling (PRD Section 9, edge cases)
  → EC-001: Camera disconnect
  → EC-002: WS disconnect + auto-reconnect
  → EC-003: Gemini API error / timeout
  → EC-004: Multiple faces → track largest
  → EC-005: Barge-in
  → EC-006: Noise trigger VAD (false positive)
  → EC-007: Audio buffer overflow
  → EC-008: Missing VRM blendshape
```

### Tier 2 — Test nếu feasible

```
✓ VRAM monitoring
  → Sau load: VRAM < 2 GB (PRD Section 3)
  → Sau 30 phút session: VRAM không tăng (no leak)

✓ Latency thresholds (PRD Section 10, NFR-001)
  → Head tracking end-to-end: < 200ms
  → Voice response latency: < 5 giây
  → VAD detection: < 50ms
  → Tracking FPS: ≥ 30 FPS
```

### Tier 3 — KHÔNG test (ngoài phạm vi)

```
✗ Visual quality của avatar (render đẹp hay không)
✗ Chất lượng giọng TTS (đây là TASK-16 TTS Eval, do human evaluate)
✗ Noise suppression hiệu quả bao nhiêu %
✗ Cross-browser compatibility
✗ Load testing (nhiều kiosk)
```

---

## 4. WebSocket Contract Tests

> **Source:** `docs/02-system-architecture.md` Section 4

### TC-WS-001: `connected` message on connect

```
Given   WebSocket client kết nối tới ws://localhost:8000/ws
When    Kết nối thành công
Then    Server gửi message với type = "connected"
And     Message có field session_id (string, non-empty)
And     Message format: {"type": "connected", "session_id": "<uuid>"}
```

**Expected payload:**
```json
{
  "type": "connected",
  "session_id": "a1b2c3d4-e5f6-..."
}
```

### TC-WS-002: `tracking_update` format

```
Given   Hệ thống đang tracking và camera detect khuôn mặt
When    Server gửi tracking_update
Then    Message có đúng format:
          - type = "tracking_update"
          - timestamp: Float (Unix epoch, > 0, monotonically increasing)
          - avatar_control.head_rotation.pitch: Float, range [-0.52, 0.52]
          - avatar_control.head_rotation.yaw: Float, range [-0.78, 0.78]
          - avatar_control.head_rotation.roll: Float, range [-0.35, 0.35]
          - avatar_control.is_speaking: Boolean
And     Tần suất nhận ≥ 30 messages/giây
```

**Expected payload:**
```json
{
  "type": "tracking_update",
  "timestamp": 1709400000.123,
  "avatar_control": {
    "head_rotation": {"pitch": 0.12, "yaw": -0.05, "roll": 0.01},
    "is_speaking": false
  }
}
```

### TC-WS-003: `tracking_update` value constraints

```
Given   Có khuôn mặt ở góc cực đoan trước camera
When    Vision module tính toán và gửi tracking
Then    pitch luôn nằm trong [-0.52, 0.52] (clamp đã áp dụng)
And     yaw luôn nằm trong [-0.78, 0.78]
And     roll luôn nằm trong [-0.35, 0.35]
```

> **Docs ref:** PRD Section 8c — Data Constraints; Architecture Flow 1 Step 4

### TC-WS-004: `speech_output` format

```
Given   Khách vừa nói xong và Gemini đã response
When    Server gửi speech_output
Then    Message có đúng format:
          - type = "speech_output"
          - text_content: String, UTF-8, tiếng Việt
          - audio_source: Enum ["gemini", "azure", "viettel"]
          - audio_data: String (Base64 encoded, non-empty)
```

**Expected payload:**
```json
{
  "type": "speech_output",
  "text_content": "Chào mừng anh quay trở lại.",
  "audio_source": "gemini",
  "audio_data": "SUQzBAAAAAAAI1RTU0UAAAAP..."
}
```

### TC-WS-005: `interrupt` message format

```
Given   Avatar đang nói (state = SPEAKING)
When    VAD detect giọng khách (barge-in)
Then    Server gửi message {"type": "interrupt"}
And     KHÔNG có payload thêm
```

### TC-WS-006: `audio_chunk` accepted từ client

```
Given   Client gửi audio_chunk {"type": "audio_chunk", "audio_data": "<base64 PCM>"}
When    Server nhận
Then    Server KHÔNG trả về error
And     VAD được trigger
```

### TC-WS-007: `error` message format

```
Given   Xảy ra lỗi (camera disconnect / Gemini timeout)
When    Server gửi error message
Then    Message có đúng format:
          - type = "error"
          - code: Enum ["CAMERA_DISCONNECTED", "GEMINI_SESSION_ERROR",
                        "GEMINI_TIMEOUT", "TTS_FALLBACK_FAILED"]
          - message: String non-empty
          - recoverable: Boolean
```

---

## 5. Flow Tests — Sequence Diagrams

### FLOW 1: Head Tracking

> **Source:** Architecture Section 2, Flow 1

#### TC-HT-001: Happy path — face detected, head rotation received

```
Given   Camera kết nối và MediaPipe loaded
And     Khuôn mặt đứng trước camera
When    Client kết nối WS
Then    Trong vòng 500ms: nhận tracking_update đầu tiên
And     Liên tục nhận tracking_update với tần suất ≥ 30/giây
And     pitch/yaw/roll thay đổi theo chuyển động khuôn mặt
And     Timestamp tăng đơn điệu (monotonically increasing)
```

#### TC-HT-002: No face — giữ giá trị cuối

```
Given   Đang nhận tracking_update khi có khuôn mặt
When    Khuôn mặt rời khỏi frame (không còn detect được)
Then    Tiếp tục nhận tracking_update
And     Giá trị pitch/yaw/roll giữ nguyên giá trị cuối (không reset về 0 ngay)
And     is_speaking vẫn = false
```

> **Docs ref:** FR-001-B — No face detected → giữ rotation cuối

#### TC-HT-003: No face 3 giây — Lerp về trung tâm

```
Given   Không có khuôn mặt trong frame
When    Đã qua 3 giây
Then    Các tracking_update tiếp theo có pitch/yaw/roll dần về 0.0
And     Sau một thời gian ngắn: pitch ≈ yaw ≈ roll ≈ 0.0
```

> **Docs ref:** FR-001-B; BR-001

#### TC-HT-004: Multiple faces — track largest

```
Given   Có 2+ khuôn mặt trong frame
When    Vision module xử lý
Then    tracking_update phản ánh khuôn mặt có bounding box lớn nhất
And     Không có error message gửi xuống frontend
```

> **Docs ref:** PRD Section 9, EC-004

#### TC-HT-005: Head rotation clamped

```
Given   Người nghiêng đầu cực độ (vượt ngưỡng cho phép)
When    Vision module tính toán
Then    pitch ∈ [-0.52, 0.52] (không bao giờ vượt ngoài range)
And     yaw ∈ [-0.78, 0.78]
And     roll ∈ [-0.35, 0.35]
And     KHÔNG có error message
```

> **Docs ref:** BR-002

---

### FLOW 2: Voice Conversation

> **Source:** Architecture Section 2, Flow 2

#### TC-VC-001: Happy path — full pipeline

```
Given   Mic hoạt động, Gemini API key valid, session chưa mở
When    Client gửi audio_chunk chứa giọng nói (speech)
Then    VAD phát hiện SpeechStart
And     Server mở Gemini Live session
And     Audio chunks được stream tới Gemini
And     Sau khi khách dừng nói (SpeechEnd):
          - Server nhận audio response từ Gemini
          - Server gửi speech_output message
          - speech_output.text_content != ""
          - speech_output.audio_data != ""
          - speech_output.audio_source = "gemini"
And     Latency từ SpeechEnd đến nhận speech_output: < 5 giây
```

> **Docs ref:** FR-002-B; NFR-001 Voice response latency

#### TC-VC-002: Multi-turn — context preserved

```
Given   Khách đã nói 1 câu và Avatar đã trả lời
When    Khách hỏi câu tiếp theo liên quan đến câu trước (ví dụ "thế còn...")
Then    speech_output chứa response có tham chiếu ngữ cảnh hội thoại
And     Session Gemini vẫn là session cũ (không mở lại)
```

> **Docs ref:** FR-002-C

#### TC-VC-003: Session auto-close sau 30 giây idle

```
Given   Gemini session đang mở
When    Không có speech activity trong 30 giây
Then    Server đóng Gemini session
And     KHÔNG gửi error message (đây là behavior bình thường)
When    Khách nói lại sau đó
Then    Server mở session mới thành công
```

> **Docs ref:** FR-002-D; BR-005

#### TC-VC-004: is_speaking flag — state changes

```
Given   Avatar ở trạng thái TRACKING (is_speaking = false)
When    Server nhận speech_output (chuyển sang SPEAKING)
Then    tracking_update tiếp theo có is_speaking = true
When    Audio phát xong (SPEAKING → TRACKING)
Then    tracking_update tiếp theo có is_speaking = false
```

> **Docs ref:** BR-007, BR-008

#### TC-VC-005: Silent audio — không trigger Gemini

```
Given   Client gửi audio_chunk toàn silence (< VAD threshold)
When    VAD phân tích
Then    KHÔNG có SpeechStart event
And     KHÔNG có Gemini session mở
And     KHÔNG có speech_output gửi xuống
```

> **Docs ref:** PRD Section 9, EC-006; BR-004 TRACKING state

---

### FLOW 3: Barge-in

> **Source:** Architecture Section 2, Flow 3

#### TC-BI-001: Interrupt khi Avatar đang nói

```
Given   Avatar đang ở state SPEAKING (đang phát audio)
When    Client gửi audio_chunk chứa giọng khách
Then    VAD phát hiện SpeechStart
And     Server gửi message {"type": "interrupt"} NGAY LẬP TỨC
And     State chuyển SPEAKING → LISTENING
And     Tiếp tục flow Voice Conversation bình thường
```

> **Docs ref:** PRD Section 9, EC-005; Architecture Flow 3

#### TC-BI-002: Interrupt signal có trước speech_output mới

```
Given   Barge-in đã xảy ra
When    Server gửi message
Then    "interrupt" gửi TRƯỚC bất kỳ speech_output mới nào
And     KHÔNG có speech_output "cũ" (của câu nói bị interrupt) gửi thêm sau interrupt
```

---

### FLOW 4: WebSocket Lifecycle

> **Source:** Architecture Section 2, Flow 4

#### TC-WL-001: Connect → nhận connected message

```
Given   Backend đang chạy
When    Client kết nối ws://localhost:8000/ws
Then    Trong vòng 1 giây: nhận {"type": "connected", "session_id": "..."}
And     session_id là UUID non-empty
```

#### TC-WL-002: Disconnect → cleanup

```
Given   Client đang kết nối và tracking hoạt động
When    Client đóng kết nối (simulate disconnect)
Then    Backend cleanup: đóng Gemini session (nếu đang mở)
And     State reset về IDLE
And     Backend KHÔNG crash
```

#### TC-WL-003: Reconnect → session mới hoạt động

```
Given   Client đã disconnect
When    Client kết nối lại
Then    Nhận connected message mới (session_id khác session cũ)
And     Tracking hoạt động bình thường ngay sau reconnect
```

---

### FLOW 5: Idle Animation

> **Source:** Architecture Section 2, Flow 5; FR-004

#### TC-IA-001: Avatar state IDLE — blink xảy ra

```
Given   Avatar đang ở state IDLE hoặc TRACKING
When    Theo dõi tracking_update trong 10 giây
Then    Quan sát thấy ít nhất 2 blink events (blendshape blink signal)
Note    Blink frequency: mỗi 3-5 giây (random)
```

> **Lưu ý:** Idle animation chạy trên frontend, không có WebSocket signal. Test này verify avatar-renderer.js behavior.

#### TC-IA-002: Idle animation không xung đột với lip-sync

```
Given   Avatar đang ở state SPEAKING (lip-sync active)
When    Quan sát behavior
Then    Blink animation vẫn xảy ra (không bị disable)
And     Breathing animation vẫn chạy
And     Lip-sync không bị override bởi blink/breathing
```

> **Docs ref:** FR-004-C

---

## 6. Business Rule Tests

> **Source:** PRD Section 7

### TC-BR-001: Validation — camera no-face giữ rotation

```
Given   Tracking đang active
When    Không có khuôn mặt trong frame (< 3 giây)
Then    pitch/yaw/roll không đột ngột về 0
And     Không có error message
Verify: BR-001
```

### TC-BR-002: Validation — rotation clamped

```
Given   Khuôn mặt nghiêng góc lớn
When    Vision module xử lý
Then    Giá trị KHÔNG vượt quá giới hạn BR-002
And     Backend log warning (kiểm tra trong log)
Verify: BR-002
```

### TC-BR-003: Authorization — session idle timeout

```
Given   Gemini session đang mở
When    Không có speech trong 30 giây
Then    Session tự động đóng (BR-005)
And     VRAM không tăng sau khi session đóng
Verify: BR-005
```

### TC-BR-004: Authorization — VRAM guard

```
Given   System đang load
When    Kiểm tra VRAM usage sau khi tất cả modules load
Then    VRAM usage < 2 GB (target POC)
And     VRAM < 6 GB hard limit (BR-006)
Verify: BR-006
```

### TC-BR-005: Notification — is_speaking flag timing

```
Given   State machine chuyển LISTENING → SPEAKING
When    Backend gửi tracking_update tiếp theo
Then    is_speaking = true NGAY (không có delay tracking_update nào với is_speaking = false ở giữa)
And     Khi state SPEAKING → TRACKING: is_speaking = false ngay lập tức
Verify: BR-007, BR-008
```

---

## 7. Edge Case Tests

> **Source:** PRD Section 9

### TC-EC-001: Camera disconnect giữa chừng

```
Given   Tracking đang hoạt động bình thường
When    Camera USB bị ngắt kết nối (simulate: stop cv2 capture)
Then    Backend log error (CAMERA_DISCONNECTED)
And     Server gửi error message: {"type": "error", "code": "CAMERA_DISCONNECTED", "recoverable": true}
And     Backend retry kết nối camera (mỗi 2 giây, tối đa 5 lần)
And     Trong thời gian retry: gửi tracking_update với giá trị cuối cùng
And     Voice conversation vẫn hoạt động (Gemini session không bị ảnh hưởng)
```

> **Docs ref:** EC-001; Architecture Flow 1 Error Handling

### TC-EC-002: WebSocket disconnect + auto-reconnect

```
Given   Frontend đang kết nối và tracking
When    Simulate network disconnect (đóng WS từ client)
Then    Frontend auto-reconnect sau 3 giây (exponential backoff)
And     Sau reconnect: nhận connected message mới
And     Tracking và voice hoạt động lại bình thường
```

> **Docs ref:** EC-002

### TC-EC-003: Gemini API error

```
Given   Khách vừa nói xong (SpeechEnd detected)
When    Gemini API trả về lỗi hoặc timeout (simulate: mock Gemini lỗi)
Then    Backend log error GEMINI_SESSION_ERROR hoặc GEMINI_TIMEOUT
And     Server gửi error message với code tương ứng
And     Avatar KHÔNG crash
And     Khi khách nói lại: mở session Gemini mới thành công
And     KHÔNG có speech_output gửi xuống cho lượt nói bị lỗi
```

> **Docs ref:** EC-003; Architecture Flow 2 Error Handling step 5

### TC-EC-004: Gemini timeout 15 giây

```
Given   Audio đã gửi tới Gemini (SpeechEnd xảy ra)
When    Gemini không response trong 15 giây
Then    Backend đóng session
And     Gửi error: {"type": "error", "code": "GEMINI_TIMEOUT", "recoverable": true}
And     Lượt nói tiếp theo: mở session mới, hoạt động bình thường
```

> **Docs ref:** Architecture Flow 2 Error Handling step 9

### TC-EC-005: Multiple faces — largest wins

```
Given   Có 2 khuôn mặt trong frame: 1 nhỏ (xa) và 1 lớn (gần)
When    Vision module xử lý
Then    tracking_update phản ánh khuôn mặt LỚN HƠN
And     Không có error, không có warning về multiple faces
```

> **Docs ref:** EC-004

### TC-EC-006: Barge-in flow đầy đủ

```
Given   Avatar ở state SPEAKING
When    VAD detect giọng khách
Then    1. Server gửi interrupt NGAY
        2. State → LISTENING
        3. Avatar nhận audio chunks từ khách
        4. SpeechEnd → Gemini process
        5. speech_output gửi xuống
        6. State → SPEAKING lại
```

> **Docs ref:** EC-005

### TC-EC-007: Audio buffer overflow

```
Given   Backend gửi speech_output chunks nhanh hơn frontend có thể phát
When    Frontend audio queue vượt 5 chunks
Then    Frontend drop chunks CŨ NHẤT, giữ chunks MỚI NHẤT
And     KHÔNG crash
And     Playback tiếp tục (có thể miss một số từ đầu — acceptable)
```

> **Docs ref:** EC-007

### TC-EC-008: VRM thiếu blendshape

```
Given   VRM model không có blendshape "aa" (lip-sync) hoặc blink
When    Frontend load model
Then    Frontend log warning (không error, không crash)
And     Feature tương ứng disabled gracefully:
          - Thiếu "aa": lip-sync không hoạt động, nhưng audio vẫn phát bình thường
          - Thiếu blink: blink animation không chạy
And     Các feature khác vẫn hoạt động
```

> **Docs ref:** EC-008

---

## 8. Performance Tests

> **Source:** PRD Section 10, NFR-001

### TC-PERF-001: Head tracking latency

```
Given   Camera đang capture khuôn mặt
When    Đo thời gian từ camera frame → tracking_update nhận ở client
Then    P95 latency < 200ms
And     Tracking rate ≥ 30 tracking_update/giây
Measurement: Timestamp diff between camera frame capture và WebSocket message receipt
```

### TC-PERF-002: Voice response latency

```
Given   Khách nói 1 câu ngắn (~3 giây)
When    Đo từ lúc VAD detect SpeechEnd → client nhận speech_output đầu tiên
Then    P95 latency < 5 giây
Note    Latency phụ thuộc vào Gemini API — document lại actual P95 để baseline
```

### TC-PERF-003: VAD detection latency

```
Given   Silence, sau đó bắt đầu nói
When    Đo từ lúc audio chunk chứa speech đến VAD fire SpeechStart
Then    Latency < 50ms
```

### TC-PERF-004: VRAM usage

```
Given   Hệ thống vừa khởi động và load xong tất cả modules
When    Đọc nvidia-smi
Then    Total VRAM usage < 2 GB
And     Sau 30 phút session liên tục: VRAM không tăng (no memory leak)
```

### TC-PERF-005: Uptime 30 phút

```
Given   Full system đang chạy
When    Chạy liên tục 30 phút với interaction mô phỏng (tracking + vài voice turns)
Then    Không có crash
And     Không có OOM error
And     WS connection ổn định (không tự disconnect)
And     VRAM < 6 GB hard limit (BR-006) suốt phiên
```

---

## 9. Test Report Format

```markdown
## Integration Test Report

**Scope:** [Phase / Task group — ví dụ: Phase 1 Milestone, TASK-08 Backend Pipeline]
**Branch:** [branch name]
**Timestamp:** [datetime]
**Tester:** Integration Tester Agent

### Summary
- Total: XX tests
- Passed: XX
- Failed: XX
- Skipped: XX (lý do)

### Passed Tests
- ✅ TC-WS-001 — connected message format đúng
- ✅ TC-HT-001 — Head tracking happy path, 30 FPS confirmed
- ...

### Failed Tests
- ❌ **TC-VC-001 — Voice conversation happy path**
  - **Flow:** Architecture Section 2, Flow 2
  - **Expected:** speech_output nhận trong < 5 giây sau SpeechEnd
  - **Actual:** speech_output nhận sau 8.2 giây (P50 đo được)
  - **Error:** Không phải crash — latency vượt threshold
  - **Request sent:** audio_chunk x15, SpeechEnd triggered
  - **Response received:** speech_output sau 8.2s
  - **Docs ref:** NFR-001 "Voice response latency < 5 giây"
  - **Verdict to Debugger:** Cần investigate Gemini API latency hoặc backend processing

### Test Coverage vs Sequence Diagrams

| Flow | Happy Path | Error Paths | Edge Cases |
|------|-----------|-------------|------------|
| Flow 1: Head Tracking | ✅ | EC-001 ✅ | EC-004 ✅ |
| Flow 2: Voice Conversation | ✅ | EC-003 ✅, EC-004 skipped | — |
| Flow 3: Barge-in | ✅ | — | — |
| Flow 4: WS Lifecycle | ✅ | EC-002 ✅ | — |
| Flow 5: Idle Animation | ✅ | EC-008 ✅ | — |

### Recommendations
- [Pattern nào trong failures cần chú ý]
- [Gaps trong test coverage]
```

---

## 10. Test Priorities theo Phase

### Phase 1 (Foundation — TASK-01→05)

Priority order:
1. TC-WL-001, TC-WL-002, TC-WL-003 (WS lifecycle)
2. TC-WS-001, TC-WS-002, TC-WS-003 (contract: connected + tracking_update)
3. TC-HT-001, TC-HT-002, TC-HT-003 (head tracking flows)
4. TC-EC-001 (camera disconnect)

### Phase 2 (AI Integration — TASK-06→08)

Priority order:
1. TC-VC-001, TC-VC-004 (voice happy path + is_speaking flag)
2. TC-WS-004, TC-WS-005, TC-WS-006 (contract: speech_output + interrupt)
3. TC-VC-003 (session timeout)
4. TC-EC-003, TC-EC-004 (Gemini errors)
5. TC-BI-001, TC-BI-002 (barge-in)

### Phase 3 (Frontend — TASK-09→14)

Priority order:
1. TC-WL-003 (reconnect)
2. TC-IA-001, TC-IA-002 (idle animation)
3. TC-EC-007, TC-EC-008 (audio buffer, missing blendshape)
4. TC-PERF-001, TC-PERF-002 (latency)

### Phase 4 (Integration — TASK-15)

Priority order (ALL tests):
1. Full regression: tất cả TC-HT, TC-VC, TC-BI, TC-WL, TC-IA
2. TC-PERF-005 (uptime 30 phút)
3. TC-PERF-004 (VRAM)
4. TC-BR (business rules)
5. TC-EC (edge cases còn lại)

---

## 11. Anti-patterns — KHÔNG LÀM

| Anti-pattern | Tại sao |
|-------------|---------|
| Sửa code trong /backend/, /frontend/ | Bạn test, không fix. Report cho Orchestrator. |
| Viết test không trace được về docs section | Test phải validate docs compliance |
| Skip error paths | Error handling = 50% behavior quan trọng |
| Test implementation detail (mock internal calls) | Test observable behavior qua WebSocket |
| Hardcode expected values không từ docs | Expected value phải từ PRD/Architecture |
| Viết test depend on test order | Mỗi test độc lập, setup/cleanup riêng |
| Mark test "pass" khi chỉ chạy không crash | Pass = behavior đúng theo spec, không phải không crash |
| Skip PERF tests vì "không đo được" | Latency tests quan trọng — dùng timestamp diff |
