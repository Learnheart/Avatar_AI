# PRD — MAGIC MIRROR AI AVATAR

> **Product:** Magic Mirror AI Avatar Kiosk
> **Version:** POC (v0.1)
> **Author:** Product Strategist Agent
> **Source:** `first_POC.md`, `init.md`, `init_architecture.md`
> **Status:** Draft — chờ Human Review

---

# PHASE 1 — PRODUCT OWNER MINDSET

---

## Section 1: Problem Statement

### 1.1. Vấn đề

Trải nghiệm khách hàng VIP tại quầy giao dịch tài chính hiện tại thiếu yếu tố **cá nhân hóa** và **tức thì**:

- Khách VIP phải **chờ đợi** nhân viên rảnh để được tư vấn, dù chỉ cần hỏi thông tin cơ bản (lãi suất, tỷ giá, trạng thái tài khoản).
- Không có cơ chế **nhận diện khách VIP** chủ động khi họ bước vào — mọi khách hàng đều bị đối xử giống nhau tại bước tiếp đón ban đầu.
- Nhân viên không thể phục vụ 24/7, đặc biệt ngoài giờ hành chính hoặc tại các chi nhánh nhỏ.

### 1.2. Impact nếu không giải quyết

- Giảm mức độ hài lòng của khách VIP — nhóm đóng góp ~20% doanh thu nhưng chỉ chiếm ~5% số lượng khách.
- Mất cơ hội cross-sell/up-sell tại điểm tiếp xúc đầu tiên.
- Thua kém đối thủ đã triển khai kiosk tương tác (dù chưa có AI avatar).

### 1.3. Tại sao giải quyết BÂY GIỜ

- Gemini Live API vừa ra mắt khả năng **multimodal real-time** (audio + vision), cho phép build pipeline đơn giản hơn so với ghép nhiều service riêng lẻ.
- Chi phí phần cứng GPU tầm trung (RTX 3070) đủ sức chạy vision tracking local với VRAM headroom lớn (~7 GB dư).
- Chuẩn VRM (Virtual Reality Model) cho web avatar đã mature — có thư viện production-ready (`@pixiv/three-vrm`).
- Đây là thời điểm có lợi thế first-mover trong ngành tài chính Việt Nam.

---

## Section 2: Target Users & Personas

### Persona 1: Khách hàng VIP (PRIMARY)

| Thuộc tính | Mô tả |
|---|---|
| **Role** | Khách hàng VIP tại chi nhánh ngân hàng / tổ chức tài chính |
| **Goals** | Được nhận diện, chào đón cá nhân hóa; nhận tư vấn tài chính nhanh chóng mà không cần chờ đợi |
| **Pain points** | Phải chờ nhân viên; không có ai chào đón khi vừa đến; phải lặp lại thông tin cá nhân nhiều lần |
| **Current workaround** | Chờ lấy số thứ tự → ngồi đợi → gặp nhân viên |

### Persona 2: Ban Lãnh đạo / Stakeholder

| Thuộc tính | Mô tả |
|---|---|
| **Role** | Giám đốc chi nhánh, trưởng phòng dịch vụ khách hàng |
| **Goals** | Nâng cao trải nghiệm khách VIP; tối ưu nhân sự quầy tiếp đón; tạo ấn tượng công nghệ hiện đại |
| **Pain points** | Khó đánh giá ROI của các giải pháp CX; lo ngại rủi ro công nghệ mới |
| **Current workaround** | Thuê thêm nhân viên tiếp đón hoặc bố trí nhân viên xoay ca |

### Persona ưu tiên khi có xung đột

> **Khách hàng VIP** là persona PRIMARY. Mọi quyết định UX/feature khi có xung đột phải ưu tiên trải nghiệm của khách.

### User Journey Overview (High-level)

```
Khách bước vào → Camera nhận diện khuôn mặt → Avatar quay đầu theo khách
→ Khách dừng trước Kiosk → Avatar chào hỏi bằng giọng nói
→ Khách hỏi câu hỏi → Avatar nghe, suy luận, trả lời bằng giọng (miệng mấp máy)
→ Hội thoại tiếp tục cho đến khi khách rời đi
```

---

## Section 3: Success Metrics

### Primary Metrics (North Star)

| Metric | Baseline | Target | Timeframe | Cách đo |
|---|---|---|---|---|
| **End-to-end interaction thành công** | 0% (chưa có hệ thống) | ≥ 80% sessions hoàn tất không lỗi | POC demo (30 phút liên tục) | Log số session thành công / tổng session |
| **First response latency** | N/A | < 5 giây từ lúc khách nói xong đến khi Avatar bắt đầu trả lời | POC demo | Timestamp diff giữa VAD speech_end và audio playback start |

### Secondary Metrics

| Metric | Baseline | Target | Timeframe | Cách đo |
|---|---|---|---|---|
| Head tracking latency | N/A | < 200ms | POC demo | Timestamp diff giữa camera frame và avatar rotation update |
| VRAM usage ổn định | N/A | < 2 GB suốt phiên | POC demo | `nvidia-smi` monitoring liên tục |
| Uptime liên tục | N/A | ≥ 30 phút không crash | POC demo | System uptime log |

### Guardrail Metrics

| Metric | Ngưỡng | Cách đo |
|---|---|---|
| VRAM không vượt ngưỡng OOM | < 6 GB (hard limit trên RTX 3070 8GB) | `nvidia-smi` alert |
| Avatar không bị "gãy cổ" (góc xoay vượt giới hạn) | Pitch/Yaw/Roll trong phạm vi clamp | Visual QA + log giá trị rotation |
| Giọng TTS tiếng Việt đạt chuẩn | Thanh điệu đúng ≥ 90%, nghe tự nhiên | Human evaluation (xem tiêu chí TTS ở Section 7) |

---

## Section 4: Scope & Priorities

### MVP (P0) — Ship đợt 1

| ID | Feature | Justification |
|---|---|---|
| MVP-01 | **Head tracking real-time** (MediaPipe → Avatar quay đầu) | Core value: Avatar phải "sống" — nhìn theo khách. Không có thì chỉ là hình tĩnh. |
| MVP-02 | **Hội thoại voice** (Gemini Live: nghe → suy luận → nói) | Core value: khả năng tương tác bằng giọng nói là lý do tồn tại của sản phẩm. |
| MVP-03 | **Lip-sync amplitude-based** | Core value: miệng mấp máy khi nói tạo cảm giác tự nhiên tối thiểu. Không có thì avatar nói mà miệng không động → uncanny. |
| MVP-04 | **Blink & breathing idle animation** | Core value: Avatar phải có biểu hiện sống khi không nói — chớp mắt, thở. Không có thì trông như mannequin. |

### V2 (P1) — Ship đợt 2

| ID | Feature | Ghi chú |
|---|---|---|
| V2-01 | Gemini nhận diện ngữ cảnh thị giác (biểu cảm, trang phục) | Nâng cao tính cá nhân hóa, Gemini Live hỗ trợ sẵn multimodal |
| V2-02 | VIP recognition (InsightFace local) | Nhận diện khách VIP theo khuôn mặt, có thể hardcode danh sách ban đầu |

### Out of Scope (POC)

| ID | Feature | Lý do loại |
|---|---|---|
| OOS-01 | Noise suppression nâng cao | Môi trường demo kiểm soát được, không cần cho POC |
| OOS-02 | One Euro Filter (smoothing tracking) | Lerp nội suy đủ cho POC, optimize sau |
| OOS-03 | Offline fallback | POC chạy môi trường có mạng ổn định |
| OOS-04 | Error handling nâng cao | Chấp nhận restart nếu lỗi trong POC |
| OOS-05 | Viseme mapping chi tiết (5 nguyên âm A/I/U/E/O) | Amplitude-based đủ cho POC, viseme mapping là V2+ |
| OOS-06 | Multi-user tracking | POC chỉ cần track 1 người |
| OOS-07 | Dashboard monitoring / analytics | Không cần cho demo |

---

## Section 5: Risks & Dependencies

### Risks

| ID | Rủi ro | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| RSK-01 | Giọng Việt của Gemini TTS kém (sai thanh điệu, nghe máy) | **Cao** — demo thất bại nếu giọng không tự nhiên | Trung bình | Đánh giá theo tiêu chí TTS cụ thể (Section 7). Nếu fail → swap sang Azure / Viettel AI TTS. Backend có TTS Abstraction Layer, frontend không cần sửa. |
| RSK-02 | VRAM OOM trên RTX 3070 | **Cao** — crash toàn hệ thống | Thấp | Chỉ dùng ~1 GB VRAM (MediaPipe + Silero). Monitor bằng `nvidia-smi`. Headroom ~7 GB. |
| RSK-03 | Mất kết nối mạng → Avatar câm điếc | **Cao** — mất hoàn toàn khả năng hội thoại | Thấp (POC) | POC chạy trong môi trường mạng kiểm soát. Note cho production: cần offline fallback. |
| RSK-04 | Chi phí API Gemini Live cao | **Trung bình** — vượt budget nếu session mở liên tục | Trung bình | Silero VAD kiểm soát mở/đóng session Gemini — chỉ kết nối khi có người nói. |
| RSK-05 | Frontend audio streaming phức tạp (latency, buffer, sync) | **Trung bình** — lip-sync bị lệch hoặc audio bị giật | Trung bình | Sử dụng AudioWorklet + queue pattern đã proven trên Web Audio API. |
| RSK-06 | Gemini Live API thay đổi hoặc không ổn định | **Cao** — pipeline chính bị gãy | Thấp | Pin version API. Thiết kế module có thể swap không cần refactor. |

### Dependencies

| ID | Dependency | Loại | Status |
|---|---|---|---|
| DEP-01 | Gemini Live API (`gemini-2.0-flash-live`) | 3rd-party API | Available |
| DEP-02 | RTX 3070 8GB + Ubuntu server | Hardware | Có sẵn |
| DEP-03 | Camera USB | Hardware | Cần mua/chuẩn bị |
| DEP-04 | Màn hình Kiosk + Loa | Hardware | Cần mua/chuẩn bị |
| DEP-05 | Mô hình VRM (.vrm file) | Asset 3D | Download từ VRoid Hub |
| DEP-06 | Mạng Internet ổn định | Infrastructure | Cần đảm bảo tại vị trí demo |

### Assumptions

| ID | Assumption | Nếu sai → |
|---|---|---|
| ASM-01 | Gemini Live API hỗ trợ tiếng Việt đủ tốt cho STT + TTS | Cần swap STT sang Faster-Whisper local + TTS sang Azure/Viettel |
| ASM-02 | Một camera USB đủ chất lượng cho MediaPipe Face Mesh | Cần thử nghiệm camera, có thể cần camera tốt hơn (IR, wide-angle) |
| ASM-03 | Môi trường demo có ánh sáng đủ cho face tracking | Cần bổ sung đèn hoặc dùng IR camera |
| ASM-04 | Khách hàng sẵn sàng nói chuyện với Avatar AI | Cần UX onboarding — avatar chào trước để khách biết có thể tương tác |
| ASM-05 | WebSocket đủ nhanh cho real-time tracking 30 FPS trên mạng LAN | Nếu không → cần giảm FPS hoặc tối ưu payload |

---

# PHASE 2 — BUSINESS ANALYST MINDSET

> Phase này viết CHI TIẾT cho Architecture Agent. Không mơ hồ. Mỗi ambiguity = 1 bug trong code.

---

## Section 6: Functional Requirements

### FR-001: Head Tracking Real-time

**Feature:** MVP-01 — Head tracking (MediaPipe → Avatar quay đầu)
**Actors:** Khách hàng (passive — chỉ cần đứng trước camera), System
**Priority:** P0

**Preconditions:**
- Camera USB đã kết nối và hoạt động (`cv2.VideoCapture(0)` trả về frame)
- Backend MediaPipe Face Mesh đã load thành công
- Frontend đã kết nối WebSocket và load mô hình VRM

**Postconditions:**
- Avatar trên màn hình xoay đầu theo hướng khuôn mặt khách trong thời gian thực

**Given/When/Then:**

```
FR-001-A: Tracking cơ bản
  Given   Camera đang capture frame và MediaPipe đã sẵn sàng
  When    Hệ thống phát hiện khuôn mặt trong frame
  Then    Tính toán Pitch, Yaw, Roll từ 468 face landmarks
  And     Gửi tracking_update JSON qua WebSocket ở tần suất ≥ 30 FPS
  And     Frontend nhận JSON, parse Pitch/Yaw/Roll
  And     Áp dụng THREE.Quaternion xoay node xương head + neck
  And     Sử dụng MathUtils.clamp() giới hạn góc quay
  And     Sử dụng Lerp nội suy làm mượt giữa các frame

FR-001-B: Không phát hiện khuôn mặt
  Given   Camera đang capture frame
  When    MediaPipe không phát hiện khuôn mặt nào trong frame
  Then    Giữ nguyên vị trí head rotation cuối cùng (không reset về 0)
  And     Sau 3 giây không phát hiện → Avatar từ từ quay về vị trí trung tâm (Lerp về 0,0,0)

FR-001-C: Chụp frame ngữ cảnh cho Gemini
  Given   Hệ thống đang tracking bình thường
  When    Mỗi 2-3 giây (configurable interval)
  Then    Chụp 1 frame JPEG từ camera
  And     Gửi kèm vào Gemini Live session để cung cấp ngữ cảnh thị giác
```

---

### FR-002: Hội thoại Voice (Gemini Live)

**Feature:** MVP-02 — Hội thoại voice
**Actors:** Khách hàng (nói), System (nghe + suy luận + trả lời)
**Priority:** P0

**Preconditions:**
- Microphone Kiosk đã kết nối và hoạt động
- Silero VAD đã load thành công
- Gemini Live API key đã cấu hình và có kết nối mạng
- System prompt đã thiết lập (vai Chuyên viên tài chính VIP)

**Postconditions:**
- Khách nhận được câu trả lời bằng giọng nói từ Avatar, nội dung phù hợp ngữ cảnh tài chính

**Given/When/Then:**

```
FR-002-A: Phát hiện giọng nói
  Given   Microphone đang thu âm và Silero VAD đang phân tích
  When    VAD phát hiện speech start (có người bắt đầu nói)
  Then    Mở session Gemini Live API (nếu chưa mở)
  And     Bắt đầu stream audio data tới Gemini

FR-002-B: Kết thúc câu nói và nhận phản hồi
  Given   Khách đang nói và audio đang stream tới Gemini
  When    VAD phát hiện speech end (khách dừng nói, khoảng lặng > ngưỡng)
  Then    Gemini xử lý audio input (STT + LLM suy luận)
  And     Gemini trả về audio stream (TTS)
  And     Backend gửi speech_output JSON xuống frontend
  And     Frontend phát audio qua loa Kiosk

FR-002-C: Hội thoại nhiều lượt
  Given   Avatar vừa trả lời xong một câu
  When    Khách tiếp tục nói câu tiếp theo
  Then    Session Gemini Live vẫn duy trì context hội thoại
  And     Phản hồi tiếp theo có tham chiếu ngữ cảnh trước đó

FR-002-D: Khách không nói trong thời gian dài
  Given   Session Gemini Live đang mở
  When    VAD không phát hiện giọng nói trong 30 giây (configurable)
  Then    Đóng session Gemini Live để tiết kiệm chi phí API
  And     Khi khách nói lại → mở session mới
```

---

### FR-003: Lip-sync Amplitude-based

**Feature:** MVP-03 — Lip-sync
**Actors:** System (tự động dựa trên audio output)
**Priority:** P0

**Preconditions:**
- Mô hình VRM đã load và có blendshape miệng (ít nhất blendshape `aa`)
- Web Audio API (AudioContext) đã khởi tạo

**Postconditions:**
- Miệng Avatar mấp máy đồng bộ với âm thanh đang phát

**Given/When/Then:**

```
FR-003-A: Lip-sync khi Avatar nói
  Given   Frontend nhận audio data từ WebSocket (speech_output)
  When    Audio đang được phát qua AudioContext
  Then    AnalyserNode trích xuất biên độ RMS mỗi requestAnimationFrame
  And     Map biên độ vào blendshape há miệng (aa) của mô hình VRM
  And     Giá trị blendshape tỷ lệ thuận với biên độ âm thanh (0.0 → 1.0)

FR-003-B: Dừng lip-sync khi hết audio
  Given   Audio đang phát và lip-sync đang hoạt động
  When    Audio phát xong (buffer hết)
  Then    Blendshape miệng Lerp về 0 (đóng miệng) trong ~100ms
  And     Avatar trở về trạng thái idle
```

---

### FR-004: Blink & Breathing Idle Animation

**Feature:** MVP-04 — Idle animation
**Actors:** System (tự động)
**Priority:** P0

**Preconditions:**
- Mô hình VRM đã load với blendshape mắt (blink) và xương chest

**Postconditions:**
- Avatar luôn có biểu hiện "sống" ngay cả khi không nói

**Given/When/Then:**

```
FR-004-A: Chớp mắt tự động
  Given   Avatar đang hiển thị trên màn hình
  When    Mỗi 3-5 giây (random interval)
  Then    Blendshape blink tăng từ 0 → 1 trong ~100ms (nhắm mắt)
  And     Giữ 50ms
  And     Giảm từ 1 → 0 trong ~100ms (mở mắt)

FR-004-B: Nhịp thở
  Given   Avatar đang hiển thị trên màn hình
  When    Liên tục (mỗi render frame)
  Then    Áp dụng Math.sin(time * speed) lên xương chest
  And     Biên độ nhỏ (scale ~0.002-0.005) tạo chuyển động thở nhẹ
  And     Tốc độ ~ 12-15 chu kỳ/phút (nhịp thở bình thường)

FR-004-C: Idle animation không xung đột với lip-sync
  Given   Avatar đang chớp mắt hoặc thở
  When    Đồng thời có lip-sync đang hoạt động
  Then    Cả hai animation chạy song song, không override lẫn nhau
  And     Blendshape mắt và miệng là độc lập
```

---

## Section 7: Business Rules & Logic

### Validation Rules

```
BR-001: IF camera frame không có khuôn mặt nào
        THEN giữ head rotation cuối cùng
        AND sau 3 giây → Lerp về vị trí trung tâm (0, 0, 0)

BR-002: IF head rotation values ngoài phạm vi an toàn
        (|pitch| > 30°, |yaw| > 45°, |roll| > 20°)
        THEN clamp về giá trị max cho phép
        AND log warning

BR-003: IF audio data trong speech_output bị corrupt hoặc decode thất bại
        THEN bỏ qua audio chunk đó
        AND log error
        AND tiếp tục nhận chunk tiếp theo
```

### Workflow Rules (State Machine)

```
BR-004: Avatar State Machine

  IDLE → TRACKING
    IF   camera phát hiện khuôn mặt
    THEN chuyển sang TRACKING, bắt đầu gửi tracking_update

  TRACKING → LISTENING
    IF   VAD phát hiện speech start
    THEN mở session Gemini Live (nếu chưa mở)
    AND  bắt đầu stream audio

  LISTENING → SPEAKING
    IF   VAD phát hiện speech end VÀ Gemini trả về audio response
    THEN chuyển sang SPEAKING
    AND  phát audio + kích hoạt lip-sync
    AND  set avatar_control.is_speaking = true

  SPEAKING → TRACKING
    IF   audio phát xong (buffer hết)
    THEN set avatar_control.is_speaking = false
    AND  quay về TRACKING (tiếp tục theo dõi khuôn mặt)

  TRACKING → IDLE
    IF   camera không phát hiện khuôn mặt > 3 giây
    THEN Avatar Lerp về trung tâm
    AND  chuyển về IDLE

  BẤT KỲ STATE → IDLE
    IF   lỗi nghiêm trọng (WebSocket disconnect, VRAM OOM)
    THEN reset về IDLE
    AND  log error
```

### Authorization Rules

```
BR-005: IF Gemini Live session đã mở > 30 giây mà không có speech activity
        THEN đóng session tự động
        AND giải phóng connection

BR-006: IF VRAM usage > 6 GB (kiểm tra qua nvidia-smi)
        THEN không load thêm model mới
        AND log alert
```

### Notification Rules

```
BR-007: IF Avatar state chuyển sang SPEAKING
        THEN gửi tracking_update với is_speaking = true
        AND frontend bắt đầu lip-sync

BR-008: IF Avatar state chuyển từ SPEAKING về TRACKING
        THEN gửi tracking_update với is_speaking = false
        AND frontend dừng lip-sync, Lerp miệng về 0
```

### TTS Quality Rules

```
BR-009: IF giọng Gemini TTS tiếng Việt fail bất kỳ 1 trong 2 tiêu chí:
          - Thanh điệu 6 thanh: phát âm đúng < 90%
          - Độ tự nhiên: nghe rõ ràng là máy / ngoại quốc
        THEN swap sang TTS chuyên dụng (Azure / Viettel AI)
        AND chuyển Gemini sang response_modalities: ["TEXT"]
        AND đẩy text output qua TTS provider
        AND frontend không cần thay đổi (audio format giữ nguyên)

BR-010: IF audio_source = "gemini"
        THEN phát audio trực tiếp từ Gemini response
        ELSE IF audio_source = "azure" OR "viettel"
        THEN phát audio từ TTS provider tương ứng
```

---

## Section 8: Data Requirements

### 8a. Core Entities & Relationships

```
TrackingFrame (N) ──→ (1) CameraSession
  - Mỗi camera session tạo nhiều tracking frame liên tục

AudioChunk (N) ──→ (1) ConversationSession
  - Mỗi cuộc hội thoại gồm nhiều audio chunk từ khách

SpeechOutput (N) ──→ (1) ConversationSession
  - Mỗi cuộc hội thoại tạo nhiều speech output từ Avatar

ConversationSession (1) ──→ (1) GeminiLiveSession
  - Mỗi cuộc hội thoại map 1:1 với 1 Gemini Live session
```

### 8b. Data Lifecycle

| Entity | Tạo khi | Update khi | Xoá khi |
|---|---|---|---|
| TrackingFrame | Mỗi camera frame (~30Hz) | Không update, chỉ tạo mới | Không lưu — stream trực tiếp qua WebSocket, bỏ sau khi gửi |
| AudioChunk | VAD phát hiện speech | Không update | Không lưu — stream trực tiếp tới Gemini |
| ConversationSession | VAD phát hiện speech start lần đầu | Mỗi lượt hội thoại | Khi VAD timeout 30s hoặc khách rời đi |
| SpeechOutput | Gemini trả về response | Không update | Không lưu — stream trực tiếp tới frontend |

> **Lưu ý POC:** Tất cả data là ephemeral (streaming, không persist). Không có database trong POC. Logging ra file/console cho debug.

### 8c. Data Constraints

| Field | Constraint |
|---|---|
| `head_rotation.pitch` | Float, range: [-0.52, 0.52] rad (~±30°) |
| `head_rotation.yaw` | Float, range: [-0.78, 0.78] rad (~±45°) |
| `head_rotation.roll` | Float, range: [-0.35, 0.35] rad (~±20°) |
| `timestamp` | Float (Unix epoch), required, monotonically increasing |
| `audio_data` | String (Base64 encoded), max size phụ thuộc chunk duration |
| `text_content` | String (UTF-8), tiếng Việt, max ~500 ký tự/response |
| `audio_source` | Enum: `"gemini"` | `"azure"` | `"viettel"` |
| `type` (WebSocket message) | Enum: `"tracking_update"` | `"speech_output"` | `"audio_chunk"` |

### 8d. External Data

| Source | Data | Protocol | Format |
|---|---|---|---|
| Gemini Live API | Audio response + Text | WebSocket / gRPC | Audio stream (PCM/MP3) + Text |
| Camera USB | Video frames | USB → cv2 | BGR numpy array |
| Microphone Kiosk | Audio input | WebSocket từ frontend | PCM 16kHz Base64 |

### 8e. Data Sensitivity

| Data | Sensitivity | Xử lý |
|---|---|---|
| Video frame khuôn mặt khách | **PII** — biometric data | Không lưu, chỉ xử lý in-memory. Gửi Gemini qua HTTPS. |
| Audio giọng nói khách | **PII** — voice biometric | Không lưu, stream trực tiếp. Gửi Gemini qua HTTPS. |
| Nội dung hội thoại | **Sensitive** — thông tin tài chính cá nhân | Không lưu trong POC. Production cần encryption + retention policy. |

---

## Section 9: Edge Cases & Exception Handling

### EC-001: Camera bị ngắt kết nối giữa chừng

- **Trigger:** USB camera bị rút hoặc driver crash
- **Expected behavior:** Backend catch exception từ `cv2.VideoCapture.read()`, retry kết nối mỗi 2 giây tối đa 5 lần
- **User sees:** Avatar đứng yên ở vị trí trung tâm (idle), vẫn có thể hội thoại voice bình thường

### EC-002: WebSocket bị disconnect

- **Trigger:** Mất mạng LAN giữa backend và frontend
- **Expected behavior:** Frontend tự động reconnect mỗi 3 giây với exponential backoff (max 30 giây)
- **User sees:** Avatar freeze tại vị trí cuối cùng. Khi reconnect → Avatar hoạt động lại bình thường.

### EC-003: Gemini Live API trả về lỗi hoặc timeout

- **Trigger:** API rate limit, network issue, hoặc server error
- **Expected behavior:** Backend log error, đóng session, thử mở session mới ở lượt nói tiếp theo
- **User sees:** Avatar không trả lời lượt nói đó. Khách nói lại → hệ thống thử kết nối lại.

### EC-004: Nhiều khuôn mặt trong frame

- **Trigger:** Có > 1 người đứng trước camera
- **Expected behavior:** Track khuôn mặt **lớn nhất** (gần camera nhất) — giả định đó là người đang tương tác
- **User sees:** Avatar nhìn theo người gần nhất

### EC-005: Khách nói khi Avatar đang nói (barge-in)

- **Trigger:** VAD phát hiện speech trong khi Avatar đang phát audio
- **Expected behavior:** Dừng audio đang phát, ngắt lip-sync, chuyển sang LISTENING ngay lập tức
- **User sees:** Avatar "im lặng" và lắng nghe khách

### EC-006: Âm thanh môi trường nhiễu

- **Trigger:** Tiếng ồn nền kích hoạt VAD (nhạc, tiếng người khác)
- **Expected behavior:** Silero VAD có ngưỡng confidence threshold (configurable, mặc định 0.5). Chỉ trigger khi confidence > threshold.
- **User sees:** Avatar không phản ứng với tiếng ồn nền (best effort — POC chấp nhận false positive thỉnh thoảng)

### EC-007: Audio buffer overflow trên frontend

- **Trigger:** Backend gửi audio chunks nhanh hơn frontend có thể phát
- **Expected behavior:** Frontend duy trì queue buffer. Nếu queue > 5 chunks → drop chunks cũ nhất, giữ chunks mới nhất.
- **User sees:** Có thể bị mất vài từ đầu câu trong trường hợp cực đoan, nhưng câu trả lời vẫn liền mạch.

### EC-008: Mô hình VRM không có blendshape cần thiết

- **Trigger:** File .vrm tải về thiếu blendshape `aa` hoặc blink
- **Expected behavior:** Frontend kiểm tra blendshape availability khi load model. Nếu thiếu → disable feature tương ứng (lip-sync hoặc blink) và log warning.
- **User sees:** Avatar vẫn hoạt động nhưng không có lip-sync hoặc không chớp mắt (graceful degradation).

---

## Section 10: Non-Functional Requirements

### NFR-001: Performance

| Metric | Target |
|---|---|
| Head tracking end-to-end latency (camera → avatar rotation) | < 200ms |
| Voice response latency (khách nói xong → avatar bắt đầu trả lời) | < 5 giây |
| Tracking frame rate (backend → frontend) | ≥ 30 FPS |
| Lip-sync frame rate (frontend render) | ≥ 30 FPS (synced with requestAnimationFrame) |
| VAD detection latency | < 50ms |
| WebSocket message size (tracking_update) | < 1 KB |

### NFR-002: Scalability

| Metric | Hiện tại (POC) | Dự kiến (Production) |
|---|---|---|
| Số Kiosk đồng thời / server | 1 | 1 (1 server per kiosk) |
| Số user đồng thời / kiosk | 1 | 1 |
| Thời gian session tối đa | 30 phút | 8 giờ (giờ hành chính) |

> POC không cần scale. Mỗi kiosk là 1 deployment độc lập.

### NFR-003: Availability

| Metric | Target (POC) |
|---|---|
| Uptime liên tục | ≥ 30 phút không crash |
| Recovery từ crash | Manual restart chấp nhận được |
| Auto-reconnect WebSocket | Có (exponential backoff) |

### NFR-004: Security

| Aspect | Yêu cầu (POC) |
|---|---|
| Authentication | Không (LAN nội bộ, 1 kiosk duy nhất) |
| Data encryption in transit | WebSocket qua LAN (không cần TLS cho POC). API calls tới Gemini qua HTTPS. |
| API key storage | Environment variable, không hardcode trong source |
| PII handling | Không lưu video/audio/text. Chỉ xử lý in-memory. |

### NFR-005: Compatibility

| Component | Yêu cầu |
|---|---|
| Browser | Chromium ≥ 90 hoặc Edge ≥ 90 (Kiosk mode) |
| GPU (server) | NVIDIA RTX 3070 8GB, CUDA ≥ 11.8 |
| OS (server) | Ubuntu 22.04+ |
| Python | 3.10+ |
| Camera | USB camera, hỗ trợ ≥ 720p @ 30 FPS |

### NFR-006: Localization

| Aspect | Yêu cầu |
|---|---|
| Ngôn ngữ giao tiếp | Tiếng Việt (duy nhất cho POC) |
| System prompt | Tiếng Việt, phong cách chuyên viên tài chính VIP |
| Timezone | UTC+7 (Vietnam) |

---

## OPEN QUESTIONS

| ID | Câu hỏi | Ảnh hưởng | Status |
|---|---|---|---|
| OQ-01 | Gemini Live API có hỗ trợ tiếng Việt tốt cho cả STT lẫn TTS không? Cần test thực tế. Nếu STT kém → cần fallback Faster-Whisper local (tốn thêm ~2 GB VRAM). | Ảnh hưởng kiến trúc pipeline voice | **Chưa trả lời** |
| OQ-02 | Camera USB cụ thể nào sẽ dùng? Góc rộng bao nhiêu? Có IR cho điều kiện ánh sáng yếu không? | Ảnh hưởng chất lượng face tracking | **Chưa trả lời** |
| OQ-03 | System prompt chi tiết cho vai Chuyên viên tài chính VIP — cần domain knowledge cụ thể gì? (lãi suất, sản phẩm, quy trình...) | Ảnh hưởng chất lượng câu trả lời AI | **Chưa trả lời** |
| OQ-04 | Kiosk đặt ở đâu (sảnh chính, phòng VIP, quầy giao dịch)? Khoảng cách khách đứng cách camera bao xa? | Ảnh hưởng camera FOV, microphone sensitivity | **Chưa trả lời** |
| OQ-05 | Avatar 3D cần thiết kế riêng hay dùng mô hình có sẵn từ VRoid Hub? Có yêu cầu branding (đồng phục, logo)? | Ảnh hưởng timeline chuẩn bị asset | **Chưa trả lời** |

---

## GATE CHECKLIST — G1: PRD Approved

### Auto checks — Phase 1 (PO)

- [x] Problem statement có impact đo lường được (Section 1.2)
- [x] Ít nhất 1 primary persona defined (Khách VIP — Section 2)
- [x] Có primary metric + ít nhất 1 guardrail metric (Section 3)
- [x] Mọi metric có: baseline, target, timeframe, cách đo (Section 3)
- [x] MVP scope có ít nhất 3 items, mỗi item có justification (Section 4 — 4 items)
- [x] Ít nhất 3 risks identified với mitigation (Section 5 — 6 risks)

### Auto checks — Phase 2 (BA)

- [x] Mọi MVP feature có functional requirement ở format Given/When/Then (Section 6 — FR-001 → FR-004)
- [x] Mọi FR có: actors, preconditions, postconditions (Section 6)
- [x] Có ít nhất 5 business rules ở format IF/THEN/ELSE (Section 7 — 10 rules)
- [x] Business rules cover: validation (BR-001→003), authorization (BR-005→006), workflow (BR-004) (Section 7)
- [x] Core entities listed với relationships (Section 8a)
- [x] Data constraints defined (unique, required, range, format) (Section 8c)
- [x] Data sensitivity annotated (PII fields identified) (Section 8e)
- [x] Ít nhất 5 edge cases với expected behavior (Section 9 — 8 edge cases)
- [x] Edge cases cover: empty state (EC-008), concurrent access (EC-004), network failure (EC-002, EC-003) (Section 9)
- [x] NFRs có performance targets + scalability estimates (Section 10)

### Auto checks — Cross-check

- [x] Mọi item trong MVP scope (Section 4) có ít nhất 1 FR (Section 6): MVP-01→FR-001, MVP-02→FR-002, MVP-03→FR-003, MVP-04→FR-004
- [x] Mọi FR reference entity tồn tại trong Data Requirements (Section 8)
- [x] Mọi business rule reference entity tồn tại trong Data Requirements
- [ ] Không có OPEN QUESTIONS chưa resolved → **5 câu hỏi chưa trả lời (OQ-01 → OQ-05)**

### Human Review: BẮT BUỘC

- [ ] Phase 1: Stakeholder approve scope, priorities, metrics
- [ ] Phase 2: Xác nhận business rules đúng, edge cases đủ
- [ ] Open questions: Trả lời trước khi Architecture Agent bắt đầu
