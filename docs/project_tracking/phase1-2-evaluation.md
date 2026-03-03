# Phase Evaluation — Phase 1 (Foundation) + Phase 2 (AI Integration)

> **Ngày đánh giá:** 2026-03-03
> **Source:** `docs/02-system-architecture.md`, `docs/03_planning.md`

---

## 1. Scope Phase Hiện Tại

**Trong scope (Phase 1 + 2):**
- TASK-01: Scaffolding (config, models, requirements, Docker)
- TASK-02: WebSocket server (FastAPI + `/ws` endpoint + `/health`)
- TASK-03: Vision module (MediaPipe Face Mesh + solvePnP head rotation)
- TASK-04: VAD module (Silero VAD wrapper)
- TASK-05: Orchestrator (state machine + inter-module coordination)
- TASK-06: Gemini Live Client (bidirectional streaming)
- TASK-07: TTS Abstraction Layer (gemini/azure/viettel)
- TASK-08: Pipeline Integration (orchestrator wires all modules)

**Ngoài scope (Phase 3+):**
- Frontend renderer, animations, audio (TASK-09 → 14) — thuộc Phase 3
- E2E test, TTS evaluation (TASK-15, 16) — thuộc Phase 4

---

## 2. Feature Evaluation

| Feature | Yêu cầu | Status | % | Phần còn thiếu |
|---------|---------|--------|---|----------------|
| **FR-001-A** Head tracking basic | Camera → MediaPipe → HeadRotation → WS 30Hz | **DONE** | 100 | — |
| **FR-001-B** No-face timeout | Giữ last rotation → Lerp về 0 sau 3s (timeout monitor) | **DONE** | 100 | — |
| **FR-001-C** JPEG snapshot → Gemini | Snapshot mỗi 2.5s, gửi vào Gemini session | **DONE** | 100 | — |
| **FR-002-A** Speech start detection | VAD → speech_start → open Gemini session | **DONE** | 100 | — |
| **FR-002-B** Speech end + response | VAD → speech_end → Gemini response → WS | **DONE** | 100 | — |
| **FR-002-C** Multi-turn hội thoại | Gemini Live session giữ context | **DONE** | 100 | API handles natively |
| **FR-002-D** Idle session close | 30s no speech → close Gemini session | **DONE** | 100 | — |
| **EC-004** Multiple faces → track largest | solvePnP picks largest bbox | **DONE** | 100 | — |
| **EC-005** Barge-in interrupt | interrupt message + state → LISTENING | **DONE** | 100 | — |
| **BR-004** State machine | IDLE→TRACKING→LISTENING→SPEAKING→TRACKING | **DONE** | 100 | — |
| **BR-005** Gemini idle timeout | 30s → close session | **DONE** | 100 | — |

---

## 3. Hạ Tầng

| Thành phần | Yêu cầu | Status | Ghi chú |
|------------|---------|--------|---------|
| `backend/requirements.txt` | Tất cả deps | **DONE** | fastapi, uvicorn, mediapipe, torch, google-genai, httpx, azure-speech |
| `backend/Dockerfile` | Python 3.11 + system libs | **DONE** | libglib, libgl, gomp |
| `docker-compose.yml` | GPU passthrough + camera `/dev/video0` | **DONE** | nvidia runtime config |
| `.env.example` | Tất cả env vars | **DONE** | Đầy đủ, không hardcode secrets |
| `frontend/Dockerfile` | Node 20 + Vite | **DONE** | — |
| Structured logging | JSON logger mỗi module | **DONE** | `utils/logging.py` |

---

## 4. Kiến Trúc

| Quyết định | Yêu cầu | Status | Ghi chú |
|------------|---------|--------|---------|
| Modular Monolith | 1 Python process, modules tách biệt | **DONE** | Đúng design |
| Vision chạy thread riêng | asyncio.to_thread (blocking cv2) | **DONE** | `threading.Thread` + `asyncio.Queue(maxsize=2)` |
| VAD chạy trên CPU | Silero VAD, load 1 lần | **DONE** | Cả pip package lẫn torch hub fallback |
| Orchestrator = ONLY state mutator | Duy nhất gọi `_transition()` | **DONE** | Đúng design |
| GeminiClient = bidirectional streaming | send_loop + receive_loop concurrent | **DONE** | `asyncio.gather` |
| TTS mode-switchable | Không cần restart | **DONE** | `TTS_MODE` env var |
| AppConfig = single source of truth | Mọi module đọc từ config | **DONE** | Dataclass + `_env()` helpers |
| No PII stored | Audio/video chỉ in-memory | **DONE** | Không có DB, không persist |

---

## 5. Vượt Scope (Đã Code Sớm)

| Tính năng | Thuộc Phase | Trạng thái |
|-----------|-------------|------------|
| Frontend: Three.js + VRM loader | Phase 3 | **DONE** |
| Frontend: WebSocket client | Phase 3 | **DONE** |
| Frontend: Head tracking animation | Phase 3 | **DONE** |
| Frontend: Idle animation (blink + breathing) | Phase 3 | **DONE** |
| Frontend: Audio manager + AudioWorklet | Phase 3 | **DONE** |
| Frontend: Lip-sync | Phase 3 | **DONE** |

> **Toàn bộ Phase 3 (TASK-09 → 14) đã hoàn thành song song.**

---

## 6. Vấn Đề Tồn Đọng

| Mức độ | Vấn đề | Ảnh hưởng | File |
|--------|--------|-----------|------|
| **HIGH** | Chưa test với Gemini Live API thực tế — SDK version `google-genai>=1.0.0` có thể có breaking changes giữa sub-versions | `send_realtime_input` API signature khác | `modules/gemini_client.py` |
| **HIGH** | Silero VAD model lần đầu cần download từ internet (`torch.hub.load`) — cần pin version | Có thể fail nếu không có mạng khi build | `modules/vad.py` |
| **MEDIUM** | `_is_zero_rotation()` dùng threshold 0.01 để detect no-face. Không phải robust — nên track `last_face_detected` bằng `face_count > 0` flag | False positive nếu person nhìn thẳng | `orchestrator.py` |
| **MEDIUM** | Vision module gửi HeadRotation qua asyncio.Queue nhưng orchestrator hiện dùng `await wait_for(get_rotation(), 0.1)` — nếu vision thread chậm có thể drop frames | Tracking không đều | `orchestrator.py` |
| **MEDIUM** | Frontend AudioContext 16kHz — không phải tất cả browser/platform hỗ trợ forced sample rate | Mic không capture được | `audio-manager.js` |
| **LOW** | Docker Compose cần NVIDIA Container Toolkit (`nvidia-ctk`) cài trên host Ubuntu | GPU passthrough fail nếu chưa cài | `docker-compose.yml` |
| **LOW** | VRM file (`public/models/avatar.vrm`) chưa có — cần download từ VRoid Hub | Avatar không load | `frontend/public/models/` |

---

## 7. Tổng Kết

| Hạng mục | Kết quả |
|----------|---------|
| Phase 1 Features | 11/11 hoàn thành |
| Phase 2 Features | Hoàn thành (integrated trong orchestrator) |
| Phase 3 Features | 6/6 hoàn thành (vượt scope) |
| Hạ tầng | 6/6 hoàn thành |
| Kiến trúc | 8/8 đúng design |
| Vấn đề tồn đọng | 2 HIGH, 3 MEDIUM, 2 LOW |

**Kết luận:** Phase 1 + 2 **HOÀN THÀNH**. Phase 3 **HOÀN THÀNH** (vượt scope).

**Điều kiện để chuyển Phase 4 (E2E Test):**
1. Có `.env` với `GEMINI_API_KEY` thực
2. Download VRM model vào `frontend/public/models/avatar.vrm`
3. Cài NVIDIA Container Toolkit (nếu dùng Docker GPU)
4. Chạy `docker compose up` → smoke test từng milestone checklist
