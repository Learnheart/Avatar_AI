# Planning — MAGIC MIRROR AI AVATAR

> **Source:** `docs/02-system-architecture.md` Section 6A + Section 7
> **Mục đích:** Hướng dẫn code agents biết chính xác cần tạo file gì, ở đâu, theo thứ tự nào.

---

## 1. Code Structure

```
avatar_AI/
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.py                  ← AppConfig dataclass, load env vars
│   ├── main.py                    ← FastAPI app, WebSocket endpoint, startup/shutdown
│   ├── orchestrator.py            ← State machine (IDLE→TRACKING→LISTENING→SPEAKING)
│   ├── models.py                  ← Dataclasses: HeadRotation, VadEvent, SpeechOutput, SessionState
│   ├── modules/
│   │   ├── __init__.py
│   │   ├── vision.py              ← MediaPipe Face Mesh, head rotation, JPEG snapshot
│   │   ├── vad.py                 ← Silero VAD wrapper
│   │   ├── gemini_client.py       ← Gemini Live API session management
│   │   └── tts.py                 ← TTS Abstraction Layer (gemini / azure / viettel)
│   └── utils/
│       └── logging.py             ← Structured JSON logger setup
│
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html                 ← Single page, canvas + script import
│   ├── public/
│   │   └── models/
│   │       └── avatar.vrm         ← VRM model file (download từ VRoid Hub)
│   └── src/
│       ├── main.js                ← Entry point: init scene, WS, audio
│       ├── avatar-renderer.js     ← VRM loader, head tracking, idle animation
│       ├── lip-sync.js            ← Web Audio AnalyserNode → blendshape mapping
│       ├── ws-client.js           ← WebSocket connect, reconnect, message dispatch
│       └── audio-manager.js       ← AudioWorklet capture, playback queue, interrupt
│
├── docker-compose.yml
├── .env.example                   ← Placeholder keys (gitignored .env chứa real keys)
│
└── docs/
    ├── 01_prd.md
    ├── 02-system-architecture.md
    └── 03_planning.md             ← File này
```

### File Responsibility Map

| File | Responsibility | Giao tiếp với |
|---|---|---|
| **backend/config.py** | Load env vars → `AppConfig` dataclass. Single source of truth cho mọi config. | Mọi module backend import |
| **backend/models.py** | Định nghĩa `HeadRotation`, `VadEvent`, `SpeechOutput`, `SessionState`, `AvatarState` enum. | Mọi module backend import |
| **backend/main.py** | FastAPI app. Một WebSocket endpoint `/ws`. Một REST endpoint `/health`. Startup: init modules → start orchestrator. Shutdown: cleanup. | orchestrator.py |
| **backend/orchestrator.py** | Nơi DUY NHẤT quản lý `AvatarState`. Nhận events từ modules → quyết định state transition → gửi messages qua WS. | vision.py, vad.py, gemini_client.py, tts.py |
| **backend/modules/vision.py** | Camera capture (thread riêng) → MediaPipe Face Mesh → `HeadRotation`. JPEG snapshot định kỳ. | orchestrator.py (qua asyncio.Queue) |
| **backend/modules/vad.py** | `process_audio(chunk) → VadEvent`. Silero VAD, load 1 lần khi startup. | orchestrator.py |
| **backend/modules/gemini_client.py** | Quản lý Gemini Live session. `start_session()`, `send_audio()`, `send_image()`, `on_response()`, `close_session()`. | orchestrator.py |
| **backend/modules/tts.py** | `process_output(response) → SpeechOutput`. Gemini pass-through hoặc Azure/Viettel fallback. | orchestrator.py |
| **backend/utils/logging.py** | Structured JSON logger. Các module gọi `get_logger(module_name)`. | Mọi module backend |
| **frontend/src/main.js** | Entry point. Init Three.js scene → load VRM → init WS → init audio. Render loop. | Tất cả frontend modules |
| **frontend/src/ws-client.js** | WebSocket connection + auto-reconnect (exp backoff). `on(type, handler)` pattern. | main.js |
| **frontend/src/avatar-renderer.js** | VRM loader. Head tracking (Quaternion + Lerp). Blink + breathing idle animation. | main.js, ws-client.js |
| **frontend/src/audio-manager.js** | AudioWorklet mic capture (echo cancellation). Playback queue. Interrupt handling. | main.js, ws-client.js |
| **frontend/src/lip-sync.js** | AnalyserNode → RMS → blendshape `aa`. Lerp mouth close khi audio hết. | audio-manager.js, avatar-renderer.js |

---

## 2. Thứ Tự Triển Khai

### Dependency Graph

```
TASK-01 (Scaffolding)
  ├──→ TASK-02 (WS Server)
  │       └──→ TASK-05 (Orchestrator) ─────────→ TASK-08 (Backend Integration)
  ├──→ TASK-03 (Vision) ──→ TASK-05                    │
  ├──→ TASK-04 (VAD) ─────→ TASK-05                    │
  ├──→ TASK-06 (Gemini Client) ──→ TASK-07 (TTS) ──→ TASK-08
  │                                                     │
  ├──→ TASK-09 (Three.js + VRM)                         │
  │       ├──→ TASK-11 (Head Tracking Anim) ────────────┼──→ TASK-15 (E2E Test)
  │       ├──→ TASK-12 (Idle Animation) ────────────────┤
  │       └──→ TASK-14 (Lip-sync) ──────────────────────┤
  └──→ TASK-10 (WS Client)                              │
          ├──→ TASK-11                                   │
          ├──→ TASK-13 (Audio Manager) ──→ TASK-14 ─────┘
          └──→ TASK-13                          │
                                                └──→ TASK-15 ──→ TASK-16 (TTS Eval)
```

### Phase 1: Foundation — Tạo khung xương

> Mục tiêu: Hệ thống chạy được, WS kết nối, camera capture, VAD detect.

| Thứ tự | Task | File tạo/sửa | Estimate | Depends on |
|---|---|---|---|---|
| 1 | **TASK-01:** Project Scaffolding | Tất cả folders, `requirements.txt`, `package.json`, `Dockerfile` x2, `docker-compose.yml`, `.env.example`, `config.py`, `models.py` | 2h | — |
| 2a | **TASK-02:** WebSocket Server | `backend/main.py` | 2h | TASK-01 |
| 2b | **TASK-03:** Vision Module | `backend/modules/vision.py` | 3h | TASK-01 |
| 2c | **TASK-04:** VAD Module | `backend/modules/vad.py` | 2h | TASK-01 |
| 3 | **TASK-05:** Orchestrator | `backend/orchestrator.py` | 3h | TASK-02, 03, 04 |

> TASK-02, 03, 04 có thể phát triển **song song** sau TASK-01.

**Milestone 1 checklist:**
- [ ] `docker compose up` → backend khởi động
- [ ] WS client kết nối → nhận `connected`
- [ ] Camera capture 30 FPS + MediaPipe detect face
- [ ] VAD detect speech start/end
- [ ] State machine chuyển đổi IDLE ↔ TRACKING ↔ LISTENING
- [ ] `tracking_update` gửi qua WS ~30 Hz

---

### Phase 2: AI Integration — Kết nối Gemini, pipeline voice hoàn chỉnh

> Mục tiêu: Nói vào mic → nhận audio response từ Gemini.

| Thứ tự | Task | File tạo/sửa | Estimate | Depends on |
|---|---|---|---|---|
| 4 | **TASK-06:** Gemini Live Client | `backend/modules/gemini_client.py` | 4h | TASK-01 |
| 5 | **TASK-07:** TTS Abstraction | `backend/modules/tts.py` | 2h | TASK-06 |
| 6 | **TASK-08:** Pipeline Integration | `backend/orchestrator.py`, `backend/main.py` | 3h | TASK-05, 06, 07 |

> TASK-06 có thể bắt đầu **song song** với Phase 1 (chỉ cần TASK-01 xong).

**Milestone 2 checklist:**
- [ ] Gemini Live session mở/đóng thành công
- [ ] Gửi audio → nhận audio + text response
- [ ] JPEG snapshot gửi định kỳ → Gemini nhận visual context
- [ ] TTS pass-through hoạt động
- [ ] Full backend pipeline: mic → VAD → Gemini → TTS → WS output
- [ ] Barge-in: interrupt gửi đúng
- [ ] VRAM < 2 GB

---

### Phase 3: Frontend — Avatar sống động

> Mục tiêu: Avatar render, quay đầu, chớp mắt, mấp máy miệng.

| Thứ tự | Task | File tạo/sửa | Estimate | Depends on |
|---|---|---|---|---|
| 7a | **TASK-09:** Three.js + VRM Loader | `frontend/src/main.js`, `frontend/src/avatar-renderer.js`, `frontend/index.html` | 3h | TASK-01 |
| 7b | **TASK-10:** WebSocket Client | `frontend/src/ws-client.js` | 2h | TASK-02 |
| 8a | **TASK-11:** Head Tracking Animation | `frontend/src/avatar-renderer.js` | 3h | TASK-09, 10 |
| 8b | **TASK-12:** Idle Animation | `frontend/src/avatar-renderer.js` | 1h | TASK-09 |
| 8c | **TASK-13:** Audio Manager | `frontend/src/audio-manager.js` | 3h | TASK-10 |
| 9 | **TASK-14:** Lip-sync | `frontend/src/lip-sync.js` | 2h | TASK-09, 13 |

> TASK-09 và TASK-10 có thể bắt đầu **song song** ngay sau TASK-01.
> TASK-11, 12, 13 có thể phát triển **song song** sau khi 09 + 10 xong.

**Milestone 3 checklist:**
- [ ] Avatar VRM hiển thị fullscreen, 60 FPS
- [ ] WebSocket kết nối + auto-reconnect
- [ ] Avatar quay đầu theo tracking data (< 200ms latency)
- [ ] Chớp mắt ngẫu nhiên + nhịp thở
- [ ] Mic capture với echo cancellation
- [ ] Audio playback + queue hoạt động
- [ ] Lip-sync: miệng mấp máy theo audio
- [ ] Neck bone fallback hoạt động

---

### Phase 4: Integration & Polish — Kết nối toàn bộ, kiểm thử

> Mục tiêu: Full system chạy ổn định 30 phút, đạt tiêu chí POC.

| Thứ tự | Task | File tạo/sửa | Estimate | Depends on |
|---|---|---|---|---|
| 10 | **TASK-15:** End-to-End Integration | Toàn hệ thống, `docker-compose.yml` | 3h | TASK-08, 11, 12, 13, 14 |
| 11 | **TASK-16:** TTS Quality Eval | `backend/modules/tts.py`, `backend/config.py` | 2h | TASK-15 |

**Milestone 4 (FINAL) checklist:**
- [ ] Avatar quay đầu real-time (< 200ms)
- [ ] Khách nói → Avatar trả lời + lip-sync (< 5s)
- [ ] Chạy ổn định ≥ 30 phút không crash
- [ ] VRAM < 2 GB suốt phiên
- [ ] (Bonus) Gemini nhận visual context và đề cập trong hội thoại

---

## 3. Execution Plan theo Developer

### Nếu 1 developer

```
Tuần 1 (20h):
  TASK-01 (2h) → TASK-02 (2h) → TASK-03 (3h) → TASK-04 (2h) → TASK-05 (3h)
  Song song: TASK-06 (4h) sau TASK-01
  Cuối tuần: TASK-07 (2h) → TASK-08 (3h)
  ✓ Milestone 1 + 2

Tuần 2 (20h):
  TASK-09 (3h) → TASK-10 (2h) → TASK-11 (3h) → TASK-12 (1h) → TASK-13 (3h) → TASK-14 (2h)
  Cuối tuần: TASK-15 (3h) → TASK-16 (2h)
  ✓ Milestone 3 + 4

Tổng: ~40h (2 tuần)
```

### Nếu 2 developers (Backend + Frontend)

```
           Backend Dev                          Frontend Dev
           ──────────                           ────────────
Ngày 1:    TASK-01 (2h, pair)                   TASK-01 (2h, pair)
Ngày 1-2:  TASK-02 (2h)                         TASK-09 (3h)
           TASK-03 (3h)                          TASK-10 (2h)
           TASK-04 (2h)
Ngày 2-3:  TASK-06 (4h)                         TASK-11 (3h)
           TASK-05 (3h)                          TASK-12 (1h)
                                                 TASK-13 (3h)
Ngày 3-4:  TASK-07 (2h)                         TASK-14 (2h)
           TASK-08 (3h)
Ngày 5:    TASK-15 (3h, pair)                   TASK-15 (3h, pair)
           TASK-16 (2h)

Tổng: ~25h (~5 ngày làm việc)
```

---

## 4. Critical Path

```
TASK-01 → TASK-02 → TASK-05 → TASK-08 → TASK-15
                                  ↑
                    TASK-06 ──────┘
```

Bất kỳ delay nào trên critical path đều delay toàn bộ dự án. Các task khác (TASK-03, 04, 07, 09-14) chạy song song và có buffer.

**Task rủi ro cao nhất:** TASK-06 (Gemini Live API Client) — phụ thuộc API external, có thể gặp vấn đề SDK/documentation. Nên bắt đầu sớm, song song với Phase 1.

---

## 5. Tracking Template

> Sử dụng bảng dưới đây để theo dõi tiến độ. Cập nhật mỗi khi hoàn thành task.

| Task | Module | File chính | Est. | Status | % | Ghi chú |
|---|---|---|---|---|---|---|
| TASK-01 | Scaffolding | Tất cả folders + configs | 2h | NOT STARTED | 0 | |
| TASK-02 | Backend | `main.py` | 2h | NOT STARTED | 0 | |
| TASK-03 | Backend | `modules/vision.py` | 3h | NOT STARTED | 0 | |
| TASK-04 | Backend | `modules/vad.py` | 2h | NOT STARTED | 0 | |
| TASK-05 | Backend | `orchestrator.py` | 3h | NOT STARTED | 0 | |
| TASK-06 | Backend | `modules/gemini_client.py` | 4h | NOT STARTED | 0 | Rủi ro cao — bắt đầu sớm |
| TASK-07 | Backend | `modules/tts.py` | 2h | NOT STARTED | 0 | |
| TASK-08 | Backend | `orchestrator.py`, `main.py` | 3h | NOT STARTED | 0 | |
| TASK-09 | Frontend | `main.js`, `avatar-renderer.js` | 3h | NOT STARTED | 0 | |
| TASK-10 | Frontend | `ws-client.js` | 2h | NOT STARTED | 0 | |
| TASK-11 | Frontend | `avatar-renderer.js` | 3h | NOT STARTED | 0 | |
| TASK-12 | Frontend | `avatar-renderer.js` | 1h | NOT STARTED | 0 | |
| TASK-13 | Frontend | `audio-manager.js` | 3h | NOT STARTED | 0 | |
| TASK-14 | Frontend | `lip-sync.js` | 2h | NOT STARTED | 0 | |
| TASK-15 | Full System | Toàn bộ | 3h | NOT STARTED | 0 | |
| TASK-16 | Backend | `tts.py`, `config.py` | 2h | NOT STARTED | 0 | Chỉ cần nếu Gemini TTS kém |

**Tổng:** 16 tasks, ~40h estimate

| Phase | Tasks | Est. tổng | Milestone |
|---|---|---|---|
| Phase 1: Foundation | TASK-01 → 05 | 12h | Backend core chạy |
| Phase 2: AI Integration | TASK-06 → 08 | 9h | Voice pipeline hoàn chỉnh |
| Phase 3: Frontend | TASK-09 → 14 | 14h | Avatar sống động |
| Phase 4: Integration | TASK-15 → 16 | 5h | POC hoàn thiện |
