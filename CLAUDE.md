# CLAUDE.md — Avatar AI (Magic Mirror)

A kiosk avatar that notices a person in front of it, listens, and answers in a natural
Vietnamese voice while a 3D VRM avatar turns its head and lip-syncs to the audio. The MVP
proves one complete turn: presence → listen → understand → answer → speak.

- Architecture (source of truth): `docs/ARCHITECTURE.md`
- Quality gates per module / integration / pipeline: `docs/quality-gates.md`
- Language: talk to the user in Vietnamese; write code, comments, commit messages and
  docs in English.

---
## §0. Plan before code

Before writing any code for a feature, enhancement or bugfix, create
`docs/YYYY-MM-DD/<feature-name>/plan.md` (start date, kebab-case name).
Optional siblings: `design.md`, `notes.md`. Required for new features: `test-plan.md` (§2).

Every doc starts with:

```markdown
---
author: <name or email>
date: YYYY-MM-DD
status: todo | in-progress | done
agents: <comma-separated module IDs affected>
summary: <one line>
---
```

- Module IDs for `agents:` are `vision`, `vad`, `stt`, `llm`, `tts`, `orchestrator`,
  `expression`.
- A plan contains: problem statement, requirements, decisions made, implementation
  approach, and ends with a `## To-do` checklist (`- [ ] task`).
- Status: `todo` = not started, `in-progress` = implementation started, `done` =
  finished **and verified by a human**. Agents never set `done` on their own.
- To implement a plan, follow its design exactly, tick tasks as they finish, and report
  only when every task is checked (or when blocked). Use the `incremental-implementation`
  skill for the work and `pm-orchestrator` when a task is handed off end to end.
- After every verified code change or fix, commit it with the `git-auto-commit` skill.
- Never push unless the user asks for it.

## §1. No hardcoded values

Load API keys, URLs, model names, credentials and tunable parameters from `.env` or a
config file. Secrets go in `.env` only. Append every new key to `.env.example` without
removing existing entries. The frontend reads its own values from `VITE_*` variables
(`frontend/.env.example`).

## §2. Tests

Every new feature has a `test-plan.md` covering unit tests and end-to-end tests. Unit
tests run in the sandbox. End-to-end tests run in the real environment and cost money, so
cover as much logic as possible with unit tests.

- Backend unit tests: `tests/unit/` (pytest; neighbours mocked — no network, no model
  download, no camera). Frontend unit tests: `frontend/tests/` (`node --test`).
- End-to-end tests: `tests/e2e/`, marked `e2e`, skipped unless `RUN_E2E=1`. They make
  real Groq calls and load ZeroTTS.
- A gate tests a module's contract (input → output + constraints), not its internals;
  thresholds are in `docs/quality-gates.md`.

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
| **Conversation — STT** | Whisper qua **Groq API** (OpenAI-compatible client) - gọi qua `groq_api_key` trong .env | Speech → text tiếng Việt (cloud, không tốn GPU máy) |
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

### Trạng thái code so với techstack (cập nhật 2026-10-09)

- Code đã được rebuild theo pipeline tách rời ở trên (plan:
  `docs/2026-10-09/modular-pipeline-rebuild/`): Groq Whisper STT, Groq LLM
  (`openai/gpt-oss-20b`), ZeroTTS, MediaPipe head tracking. Gemini Live và nhánh TTS
  Azure/Viettel đã bị gỡ.
- Mới qua unit test + code review. **Chưa chạy được một turn thật** (Groq, Hugging Face
  và npm bị chặn trên mạng công ty) — xem To-do trong plan.

## §4. Source code structure

All source stays under `backend/`, `frontend/` and `tests/`. Folders follow the three
capability blocks of `docs/ARCHITECTURE.md`; a module's contract lives in the `base.py`
of its package, and backends are swapped by config, not by editing callers.

```
backend/
  main.py                      # FastAPI app, /ws endpoint, /health
  config.py                    # AppConfig — every env key is read here
  models.py                    # shared dataclasses and enums
  orchestrator/
    orchestrator.py            # turn state machine + routing (only owner of state)
    state.py                   # allowed state transitions
    ws_manager.py              # the single WebSocket connection
  perception/                  # ── CAPABILITY 1 ──
    camera.py                  # OpenCV frame capture, retry on disconnect
    tracking.py                # MediaPipe presence + head rotation
    vad.py                     # Silero VAD: speech_start / speech_end
  conversation/                # ── CAPABILITY 2 (separated pipeline) ──
    engine.py                  # ConversationEngine: audio in → text + audio out
    stt/
      base.py                  # STT contract
      groq_whisper.py          # Groq Whisper backend
      router.py                # Device Capability Router (picks the backend)
    llm/
      base.py                  # LLM contract
      groq_llm.py              # Groq streaming backend
    tts/
      base.py                  # TTS contract
      zerotts.py               # ZeroTTS backend
  utils/
    logging.py
  assets/
    face_landmarker.task       # MediaPipe model
  requirements.txt             # runtime deps
  requirements-dev.txt         # test deps

frontend/                      # ── CAPABILITY 3: Expression ──
  src/
    main.js                    # wiring
    config.js                  # tunables + VITE_* values
    ws-client.js               # WebSocket client, reconnect
    audio/
      audio-manager.js         # mic capture + gapless playback
    avatar/
      avatar-renderer.js       # VRM render, applies head / mouth values
      lip-sync.js              # reads loudness of the audio being played
      motion.js                # pure mapping functions (unit-tested, no WebGL)
  public/worklet/              # AudioWorklet mic processor
  public/models/               # VRM avatar file (gitignored, supplied per machine)
  tests/                       # node --test

tests/
  unit/                        # pytest, sandbox
  e2e/                         # pytest -m e2e, real environment (RUN_E2E=1)

docs/
  ARCHITECTURE.md
  quality-gates.md
  YYYY-MM-DD/<feature-name>/   # plan.md, design.md, test-plan.md (§0)
```

Layering rules:

- Only `Orchestrator` changes the turn state (`idle → listening → thinking → speaking →
  idle`). Modules return values or events; they never touch state or the WebSocket.
- `perception/` and `conversation/` do not import `orchestrator/`.
- Blocking inference (VAD, TTS, vision) runs in a thread or executor, never on the event
  loop.
- The WebSocket message shapes are a contract between `backend/orchestrator/` and
  `frontend/src/`; change both sides and the design doc together.
- Frontend motion math goes in `avatar/motion.js` as pure functions; the renderer only
  applies the numbers.

## §5. Deployment and running

Local run on the dev machine (MacBook, Apple Silicon). Windows + RTX and Docker
packaging are not set up yet.

```bash
# Backend — Python 3.10+ (3.12 used)
uv venv --python 3.12 .venv
uv pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cp .env.example .env            # then fill in groq_api_key
.venv/bin/python -m backend.main     # HOST / PORT / LOG_LEVEL come from .env

# Frontend
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

- The first backend start downloads the ZeroTTS weights (about 910 MB) into the Hugging
  Face cache; `/health` answers once the models are loaded.
- Put a VRM avatar at `frontend/public/models/avatar.vrm` (or set `VITE_VRM_PATH`).
  Without it the page still runs, with audio and no avatar.
- The browser needs microphone permission and one user gesture before audio plays.
- Package indexes are whatever the machine is configured with; do not override them in
  commands or project files.

Checks:

```bash
.venv/bin/python -m pytest tests/unit -q                 # backend unit tests
cd frontend && npm test && npm run build                 # frontend unit tests + bundle
RUN_E2E=1 .venv/bin/python -m pytest tests/e2e -q -s     # real turn (costs Groq calls)
```

## §6. Definition of Done

A change is done when all of these hold:

1. It has a plan under `docs/YYYY-MM-DD/<feature-name>/` and every To-do item is ticked.
2. Backend and frontend unit tests pass, and `npm run build` succeeds if the frontend
   changed.
3. The quality gates in `docs/quality-gates.md` for the touched modules still hold; a
   change to a module boundary also re-runs the matching integration gate.
4. A change on the speech path was exercised by a real turn (`tests/e2e`) with first
   audio under 3.5 s and end to end under 10 s, and the measured numbers are reported.
5. No hardcoded keys, URLs, model names or tunables (§1); new keys are in `.env.example`.
6. It is committed with `git-auto-commit`. The plan status becomes `done` only after a
   human has verified it.
