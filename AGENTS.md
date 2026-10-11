# AGENTS.md
# Avatar AI 

<short description>
---
## §0. Plan before code

Before writing any code for a feature, enhancement or bugfix, create
`<round>/docs/YYYY-MM-DD/<feature-name>/plan.md` (start date, kebab-case name).
Optional siblings: `design.md`, `notes.md`. Required for new features: `test-plan.md` (§2).

Every doc starts with:

```markdown
---
author: <name or email>
date: YYYY-MM-DD
status: todo | in-progress | done
agents: <comma-separated agent IDs affected>
summary: <one line>
---
```

- A plan contains: problem statement, requirements, decisions made, implementation
  approach, and ends with a `## To-do` checklist (`- [ ] task`).
- Status: `todo` = not started, `in-progress` = implementation started, `done` =
  finished **and verified by a human**. Agents never set `done` on their own.
- To implement a plan, use the `implement-plan` skill (`.agents/skills/implement-plan/`):
  follow the design exactly, tick tasks as they finish, and report only when every task
  is checked (or when blocked).
- After every verified code change or fix, commit it with the `git-auto-commit` skill
  (`.agents/skills/git-auto-commit/`).
- Push only when a plan becomes `done`: `implement-plan` then pushes and runs
  `scripts/submit_log.py`. Never push at any other time.

## §1. No hardcoded values

Load API keys, URLs, model names, credentials and tunable parameters from `.env` or a
config file. Secrets go in `.env` only. Append every new key to `.env.example` without
removing the organizers' entries.

## §2. Tests

Every new feature has a `test-plan.md` covering unit tests and end-to-end tests. Unit
tests run in the sandbox. End-to-end tests run in the real environment and cost money, so
cover as much logic as possible with unit tests.

## §3. Techstack

> Pipeline tách rời (STT → LLM → TTS), cross-device (MacBook M5 + Windows/RTX). Nguồn
> chân lý về kiến trúc: `docs/ARCHITECTURE.md`. Mọi API key / URL / model name load từ
> `.env` (§1), không hardcode.

### Backend (Python)

| Thành phần | Tech | Vai trò / Ability |
|---|---|---|
| Runtime | Python 3.10+ + asyncio | Host 1 process, điều phối các module async |
| Web / realtime | FastAPI + uvicorn + websockets | 1 WebSocket channel frontend ↔ backend (audio in, tracking + speech out) |
| Config | python-dotenv + pydantic | Load config/secrets từ `.env` |
| HTTP client | httpx + openai client | Gọi API cloud (Groq STT qua OpenAI-compatible client; LLM) |
| **Perception — camera** | OpenCV (opencv-python-headless) + numpy | Đọc frame từ webcam |
| **Perception — tracking** | MediaPipe Face Landmarker (`face_landmarker.task`) | Presence + head rotation (pitch/yaw/roll) — chỉ cần nếu MVP có head tracking |
| **Perception — VAD** | Silero VAD (open-source) + torch/torchaudio | Phát hiện speech start/stop; chạy CPU |
| **Conversation — STT** | Whisper qua **Groq API** (OpenAI-compatible client) | Speech → text tiếng Việt (cloud, không tốn GPU máy) |
| **Conversation — LLM** | Groq LLM - openai/gpt-oss-20b | Text → câu trả lời tiếng Việt, streaming |
| **Conversation — TTS** | **ZeroTTS** (open-source, tiếng Việt) | Text → audio tiếng Việt; chạy local real-time trên CPU, có streaming |
| Device Capability Router | logic nội bộ (phát hiện Metal/CUDA/CPU lúc startup) | Chọn backend inference phù hợp máy; cho phép override qua config |

### Frontend (browser)

| Thành phần | Tech | Vai trò / Ability |
|---|---|---|
| Build | Vite | Dev server + bundle |
| 3D avatar | Three.js + @pixiv/three-vrm | Render avatar VRM, head motion, idle |
| Audio | Web Audio API (AudioWorklet + AnalyserNode) | Thu mic (PCM 16kHz), phát audio, đo loudness cho lip-sync |
| Lip-sync | logic thuần (RMS → blendshape mồm) | Amplitude-driven, bám audio đang phát |

### External (cloud)

| Dịch vụ | Vai trò |
|---|---|
| Groq API | Whisper STT (và có thể cả LLM) |
| LLM provider | Sinh câu trả lời (Groq hoặc Gemini — chốt sau) |

### Chưa chốt / cần làm rõ

- **LLM provider**: Groq LLM (gộp 1 nhà cung cấp với STT) hay Gemini text — chưa quyết.
- **Head tracking trong MVP?**: nếu không, bỏ MediaPipe, OpenCV đủ cho presence thô.

### Lệch so với code hiện tại (code viết trước kiến trúc mới — cần rebuild)

- Code hiện dùng **Gemini Live multimodal** (`google-genai`, audio-to-audio) → thay bằng
  pipeline tách rời ở trên. **Chưa có STT module**; TTS mới chỉ có nhánh Azure/Viettel,
  **chưa có ZeroTTS**. `requirements.txt` cần cập nhật: thêm Groq/openai client +
  ZeroTTS deps; bỏ dần phụ thuộc Gemini Live nếu LLM không dùng Gemini.

## §4. Source code structure — TODO (all source stays under `<round>/`)

## §5. Deployment — TODO (deployment and running steps)

## §6. Definition of Done — TODO
