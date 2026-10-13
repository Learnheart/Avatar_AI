# Avatar AI (Magic Mirror) — instructions for coding agents

> `CLAUDE.md` and `AGENTS.md` hold this same text, for the tools that read one or the
> other. Edit both in the same change: `tests/unit/test_docs.py` fails when they differ.

A kiosk avatar that notices a person in front of it, listens, and answers in a natural
Vietnamese voice while a 3D VRM avatar turns its head and lip-syncs to the audio. The MVP
proves one complete turn: presence → listen → understand → answer → speak.

- Architecture (source of truth): `docs/ARCHITECTURE.html`
- Architecture records (each official architecture, with its context and consequences):
  `docs/adr/` (§8)
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
agent: avatar
modules: <comma-separated module IDs affected>
summary: <one line>
---
```

- `agent:` names the agent the change belongs to. This repository holds one, `avatar`
  (the kiosk avatar). A second agent, if one is ever added or called, gets its own ID
  here; it is never listed under `modules:`.
- `modules:` lists the parts of that agent the change touches. They are components of
  one pipeline, not agents: `vision`, `vad`, `stt`, `llm`, `tts`, `orchestrator`,
  `expression`, `knowledge`, `memory`, `persona`, `accounts`.
- A plan contains: problem statement, requirements, decisions made, implementation
  approach, and ends with a `## To-do` checklist (`- [ ] task`).
- A plan that changes the overall architecture carries the new architecture record
  (§8), written before the code.
- Status: `todo` = not started, `in-progress` = implementation started, `done` =
  finished **and verified by a human**. Agents never set `done` on their own.
- To implement a plan, follow its design exactly, tick tasks as they finish, and report
  only when every task is checked (or when blocked). Use the `incremental-implementation`
  skill for the work and `pm-orchestrator` when a task is handed off end to end.
- After every verified code change or fix, commit it with the `git-auto-commit` skill,
  together with its `CHANGELOG.md` entry (§7).
- Never push unless the user asks for it.

## §1. No hardcoded values

Load API keys, URLs, model names, credentials and tunable parameters from `.env` or a
config file. Secrets go in `.env` only. Append every new key to `.env.example` without
removing existing entries.

Prompts and other texts live in `backend/prompts.py` only: they are not read from the
environment and must not be added to `.env` or `.env.example`. Every other backend key is
declared once, with its env name and default, in `backend/tuning.py` (behaviour
parameters) or `backend/config.py` (secrets, per-machine and provider values). Defaults are tuned in
those files; `.env` only has to hold the API key (`groq_api_key` by default), and any other key set there
overrides the default on that machine. Optional entries in `.env.example` stay commented
out.

The frontend reads its own values from `VITE_*` variables
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
> chân lý về kiến trúc: `docs/ARCHITECTURE.html`. Mọi API key / URL / model name load từ
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
| **Knowledge base** | sqlite-vec (SQLite extension) + embedding qua `EMBEDDING_BACKEND` (`local_hash` \| `openai_compatible` \| `databricks` \| `ollama`), backup qua `EMBEDDING_BACKEND_BACKUP`; pypdf, python-docx | Upload tài liệu (txt, md, csv, pdf, docx) → chunk → vector; tra cứu đoạn liên quan cho mỗi câu hỏi trước khi gọi LLM |
| **Application database** | SQLAlchemy Core trên `DATABASE_URL` (file SQLite local; PostgreSQL khi deploy) | Lưu user, memory (những điều avatar nhớ về từng user) và các turn của cuộc trò chuyện chưa được đọc; bảng được tạo lúc startup, chưa có migration tool |
| **Sign-in (accounts)** | logic nội bộ trên application database; scrypt (thư viện chuẩn) cho mật khẩu; OAuth 2 + PKCE qua httpx cho Google / Facebook | Hai chế độ: guest (mặc định) và user đã đăng nhập. Tài khoản local là email + password, chỉ được tạo sau khi nhập đúng mã 6 số gửi về email đó, password được gửi kèm cùng lúc với mã (`MAIL_BACKEND` = `smtp`, hoặc `log` để ghi mã ra log khi không có mail server; số mail gửi mã bị giới hạn theo `AUTH_OTP_*`); một email một tài khoản. Hoặc Google / Facebook, liên kết được về cùng một user; một lần đăng nhập qua provider chỉ kết thúc ở đúng trang đã bắt đầu nó (trang giữ một verifier), và việc liên kết do trang đang đăng nhập xác nhận. Đăng nhập mở một session: token ngẫu nhiên, database chỉ giữ hash; session được giữ và hết hạn theo `AUTH_SESSION_IDLE_MINUTES` / `AUTH_SESSION_MAX_HOURS` (tuỳ chọn `AUTH_LOGOUT_ON_LEAVE`). `AUTH_DEV_USER_ID` vẫn dùng cho dev / test |
| **Memory** | logic nội bộ trên application database; import và cuộc trò chuyện đã đóng được đọc bằng một lần gọi LLM ngoài turn (`LLM.complete`) | Hồ sơ của user đã đăng nhập (các memory item `active`) đưa vào LLM mỗi turn; import file / đoạn văn tự giới thiệu → memory item; cuộc trò chuyện đã đóng → memory item `pending` (đề xuất) chờ user duyệt hoặc từ chối, sở thích / tính cách được nhắc lại ở cuộc trò chuyện sau thì tự `active`; turn đã lưu bị xoá sau khi đọc; guest không có memory |
| **Persona** | logic nội bộ trên application database; đoạn mô tả dán vào được đọc bằng một lần gọi LLM ngoài turn (`LLM.complete`) | Tính cách user đã đăng nhập đặt cho avatar: một dict các trường định nghĩa trước (`name`, `role`, `addressing`, `personality`, `answer_style`, `avoid`), được ghi vào chỗ `{persona}` của `SYSTEM_PROMPT` mỗi turn: trường nào user khai báo thì thay giá trị mặc định (`PERSONA_DEFAULT`) của trường đó, trường còn lại giữ mặc định; không bắt buộc, guest dùng persona mặc định |
| **Object store** | thư mục local `OBJECT_STORE_DIR` (thay được bằng S3-compatible) | Giữ nguyên bản gốc của file user import; chủ sở hữu chỉ ghi trong application database |
| Observability | OpenTelemetry SDK (traces + metrics) + OTLP/HTTP exporter; JSON log trên stdout | Mỗi turn một trace, mỗi bước một span (listen, stt, knowledge, embedding, persona, memory, llm, tts, playback) kèm input/output; số đo vòng lặp perception; `CAPTURE_ENABLED` lưu frame camera + audio + trace ra đĩa |
| Device Capability Router | logic nội bộ (phát hiện Metal/CUDA/CPU lúc startup) | Chọn backend inference phù hợp máy; cho phép override qua config |

### Frontend (browser)

| Thành phần | Tech | Vai trò / Ability |
|---|---|---|
| Build | Vite | Dev server + bundle |
| 3D avatar | Three.js + @pixiv/three-vrm | Render avatar VRM, head motion, idle |
| Audio | Web Audio API (AudioWorklet + AnalyserNode) | Thu mic (PCM 16kHz), phát audio, đo loudness cho lip-sync |
| Lip-sync | logic thuần (RMS → blendshape mồm) | Amplitude-driven, bám audio đang phát |
| Knowledge panel | DOM thuần + fetch (`VITE_API_URL`) | Upload / liệt kê / xoá tài liệu tri thức; tick chọn tài liệu avatar được dùng để trả lời |
| Sign-in | DOM thuần + fetch (`VITE_API_URL`), `localStorage` | Hiển thị chế độ (Khách / tên user) cạnh nút "Hồ sơ"; form đăng nhập / tạo tài khoản (password chỉ được gửi cùng với mã) và nút đăng nhập với Google / Facebook cho guest (trang giữ verifier trong `sessionStorage`); khối "Tài khoản liên kết"; giữ token của session và gửi kèm mọi request và trên WebSocket; chỉ bỏ token khi backend nói token đó không mở session nào; đổi người thì xoá những gì của người trước trên trang |
| Profile page ("Hồ sơ") | DOM thuần + fetch (`VITE_API_URL`) | Xem / thêm / sửa / xoá những điều avatar nhớ về user; duyệt / từ chối những điều avatar thu thập được từ cuộc trò chuyện; khối "Tính cách của avatar": dán mô tả để LLM tách thành các trường, sửa từng trường, lưu / xoá |

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

All source stays under `backend/`, `frontend/` and `tests/`. The backend has four parts:
`core/` (one turn, following the capability blocks of `docs/ARCHITECTURE.html`),
`providers/` (model backends, API and self-hosted), `runtime/` (what depends on the
machine) and `features/` (built beside the core). The contracts of the model stages live
in `core/contracts.py`, and backends are swapped by config, not by editing callers.

```
backend/
  main.py                      # FastAPI app, /ws, /health, /knowledge, /memory, /persona, /auth; wires core + providers + features; resolves who is behind a connection
  config.py                    # AppConfig — secrets, per-machine and provider values
  tuning.py                    # behaviour parameters (VAD, timeouts, conversation, head clamps, voice)
  prompts.py                   # system prompt and other texts
  models.py                    # shared dataclasses and enums
  core/                        # ── one turn; imports no provider, runtime or feature ──
    contracts.py               # STT, LLM, TTS, Embedder, Knowledge, Memory, Persona interfaces and their errors
    orchestrator/
      orchestrator.py          # turn state machine + routing (only owner of state); owns the conversation, tells the memory
      state.py                 # allowed state transitions
      ws_manager.py            # the single WebSocket connection + outgoing messages
    perception/                # ── CAPABILITY 1 ──
      camera.py                # OpenCV frame capture, retry on disconnect
      tracking.py              # MediaPipe presence + head rotation
      vad.py                   # Silero VAD: speech_start / speech_end
    conversation/              # ── CAPABILITY 2 (separated pipeline) ──
      engine.py                # ConversationEngine: audio + earlier turns in → (knowledge lookup, persona, profile) → text + audio out
      history.py               # Conversation (one user session, its turns), record of a turn, chat messages (pure)
  providers/                   # ── model backends behind core/contracts.py ──
    catalog.py                 # backend name → builder, for every stage (+ device router)
    llm/openai_compatible.py   # Groq, OpenRouter, vLLM, Ollama, ...
    llm/openai_responses.py    # OpenAI Responses API: Databricks AI Gateway
    stt/openai_compatible.py   # Groq Whisper or a self-hosted Whisper server; confidence, non-speech refused
    stt/chat_audio.py          # a chat model that is sent the audio: Gemini on Databricks
    databricks.py              # Databricks credential: static token or CLI profile
    fallback.py                # primary + backup backend (STT_, LLM_, EMBEDDING_BACKEND_BACKUP)
    tts/zerotts.py             # ZeroTTS backend
    embedding/local_hash.py    # hashed words, no model: the knowledge flow runs anywhere
    embedding/openai_compatible.py  # any /embeddings API; Databricks endpoints, Ollama
  runtime/                     # ── what depends on the machine (macOS / Windows) ──
    device.py                  # metal / cuda / cpu detection
  features/                    # ── built beside the core: knowledge (RAG), memory, persona ──
    knowledge/
      knowledge_base.py        # KnowledgeBase: add / list / select / delete documents, search
      chunking.py              # document text → chunks (pure)
      store.py                 # sqlite-vec store: documents, chunks, vectors (all the SQL)
      api.py                   # HTTP routes /knowledge/documents
    accounts/
      identity.py              # current_user_id(): who is behind a connection or a request (a session token, or AUTH_DEV_USER_ID)
      sessions.py              # SessionService: open, resolve, revoke; a session ends by idle time or age
      accounts.py              # AccountService: local accounts (an email proved by a code + a password), the attempt limit
      passwords.py             # scrypt hash / verify (pure)
      providers.py             # ExternalSignIn: Google, Facebook (OAuth 2 + PKCE), linking and unlinking
      store.py                 # tables users, credentials, identities, sessions (all the SQL of the accounts)
      api.py                   # HTTP routes /auth
    memory/
      memory.py                # MemoryService: memory items, the profile given to the LLM and its cache
      imports.py               # MemoryImports: a file or a pasted text → original kept + memory items (one LLM call)
      consolidation.py         # MemoryConsolidation: turns kept, a closed conversation → proposed items (one LLM call)
      reading.py               # shared by the two: delimiters, quote check, waiting for a store call
      extraction.py            # LLM answer (JSON) → validated memory items or operations on them (pure)
      store.py                 # tables memory_items, imports, conversations, turns (all the SQL of the memory)
      api.py                   # HTTP routes /memory/items, /memory/imports
    persona/
      persona.py               # PersonaService: the fields a user gives the avatar, the text given to the LLM and its cache, reading a description (one LLM call)
      extraction.py            # LLM answer (JSON object) → validated persona fields (pure)
      store.py                 # table persona_fields (all the SQL of the persona)
      api.py                   # HTTP routes /persona, /persona/read, /persona/defaults
  data/                        # knowledge.db, app.db, objects/, capture/ (gitignored, per machine)
  utils/
    env.py                     # dataclass fields read from the environment
    telemetry.py               # tracer, meter, OTLP export, `Span` log lines
    capture.py                 # CaptureStore: frames, turn audio, spans on local disk
    database.py                # application database: engine from DATABASE_URL, shared MetaData, tables created at startup
    object_store.py            # ObjectStore: put / get / delete bytes by key, a directory (OBJECT_STORE_DIR)
    loaders.py                 # txt, md, csv, pdf, docx → text (knowledge documents, memory imports)
    errors.py
    logging.py
    mailer.py                  # Mailer: one plain-text mail by SMTP, or to the log (MAIL_BACKEND)
  assets/
    face_landmarker.task       # MediaPipe model
  requirements.txt             # runtime deps
  requirements-dev.txt         # test deps

frontend/                      # ── CAPABILITY 3: Expression ──
  src/
    main.js                    # wiring
    config.js                  # tunables + VITE_* values
    ws-client.js               # WebSocket client, reconnect; presents the session token (`auth`)
    audio/
      audio-manager.js         # mic capture + gapless playback
    knowledge/
      knowledge-client.js      # calls the backend /knowledge API
      knowledge-panel.js       # upload / choose (checkbox) / delete panel
      format.js                # pure helpers (unit-tested)
    transcript/
      transcript.js            # the lines of the conversation on screen (pure, unit-tested)
      transcript-panel.js      # transcript window + "Cuộc trò chuyện mới" button
    auth/
      session.js               # the session of the page: the token and the user, kept in the browser (unit-tested)
      auth-client.js           # calls the backend /auth API
      auth-panel.js            # the mode beside "Hồ sơ", the sign-in form of a guest, the linked accounts
      format.js                # pure helpers (unit-tested)
    memory/
      memory-client.js         # calls the backend /memory API
      profile-page.js          # "Hồ sơ" page: what the avatar remembers, add / edit / delete
      format.js                # pure helpers (unit-tested)
    persona/
      persona-client.js        # calls the backend /persona API
      persona-panel.js         # "Tính cách của avatar" block of the profile page: read a description, edit the fields, save / delete; shows the default of an empty field
      format.js                # pure helpers (unit-tested)
    ui/
      collapsible.js           # a drop-down: a header that opens and closes a body, remembered (unit-tested)
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
  ARCHITECTURE.html            # the current shape of the system: one HTML page, edited by hand (open it in a browser)
  adr/                         # NNNN-<name>.md architecture records, README.md (index), template.md (§8)
  quality-gates.md
  YYYY-MM-DD/<feature-name>/   # plan.md, design.md, test-plan.md (§0)
```

Layering rules:

- Only `Orchestrator` changes the turn state (`idle → listening → thinking → speaking →
  idle`). Modules return values or events; they never touch state or the WebSocket.
- `core/` imports nothing from `providers/`, `runtime/` or `features/`; only `main.py`
  wires them together. Inside `core/`, `perception/` and `conversation/` do not import
  `orchestrator/`.
- A new model backend is one file under `providers/<stage>/` plus one entry in
  `providers/catalog.py`. Another OpenAI-compatible API or a self-hosted server needs no
  code: set `LLM_BACKEND=openai_compatible` with `LLM_BASE_URL`, `LLM_API_KEY` and
  `LLM_MODEL` (same with `STT_*`). The Databricks AI Gateway is `LLM_BACKEND=databricks`
  and/or `STT_BACKEND=databricks`, with `DATABRICKS_BASE_URL` and `DATABRICKS_TOKEN` or
  `DATABRICKS_PROFILE`.
- `STT_BACKEND_BACKUP` and `LLM_BACKEND_BACKUP` name the backend that takes over when
  the primary fails; any catalog entry can be a backup, so a new provider needs no
  fallback code. `EMBEDDING_BACKEND_BACKUP` does the same for the embedding, with one
  rule: the backup serves the same model as the primary (Databricks Qwen3-Embedding-0.6B
  ↔ `ollama` with `qwen3-embedding:0.6b`), because its vectors are searched in the index
  the primary built.
- The knowledge base reaches the turn through the `Knowledge` contract only: the engine
  asks it for passages between STT and LLM and answers without them when it is empty,
  slow or failing. Its embedding is a catalog stage (`EMBEDDING_BACKEND`); its vector DB
  is `features/knowledge/store.py`, and replacing the DB means replacing that file.
- The HTTP routes `/knowledge/documents` are a contract between
  `backend/features/knowledge/api.py` and `frontend/src/knowledge/`.
- The memory reaches the turn through the `Memory` contract only: for a logged-in user
  the engine asks it for the profile before the LLM and answers without it when it is
  empty, slow or failing; a guest's turn reads nothing. Who the user is comes from
  the accounts (`features/accounts/`) and nowhere else: `main.py` resolves it and
  hands it to the orchestrator, the core never does (see sign-in below).
- What a logged-in user says is gathered outside the turn. The orchestrator tells the
  memory about each recorded turn and about the close of the conversation
  (`Memory.record_turn`, `Memory.close_conversation`), for a logged-in user only and
  never awaited: they are background tasks it keeps track of, in order per
  conversation, whose failure is logged. The closed conversation is then read once by
  the LLM (`features/memory/consolidation.py`), and again at the next start when that
  failed. What is heard is only proposed: an item from a conversation is `pending` and
  reaches no prompt until the user approves it, or, for a preference or a personality
  trait, until a later conversation states it again; an item the user rejected is kept
  as `rejected` and never proposed again. A reading never undoes what the user did to
  an item meanwhile (its changes are written relative to the row, in one transaction),
  and the conversations of one user are read one at a time. The stored turns are
  deleted once read, and after `MEMORY_TRANSCRIPT_TTL_DAYS` whatever happened.
- A person is a guest or a signed-in user, and nothing else. Who they are is
  `AUTH_DEV_USER_ID` when it is set (development, tests), otherwise the session token
  the page presents, in `Authorization: Bearer` on HTTP and in an `auth` message on the
  WebSocket. An HTTP route asks `current_user_id()` and nothing else; for a connection,
  `main.py` asks it for the configured user and then the session service for the
  token, because it has to tell a guest from a refused token. A token that opens no
  session (none, unknown, revoked, expired) is a guest, never an error; a database
  failing while it is resolved is a guest too. `main.py` resolves the token and tells
  the orchestrator a user id (`Orchestrator.set_user`): the core never sees a token.
  `auth` is the first frame of a page and carries an `id`; each one is answered by a
  `session` naming it (`reply_to`) with a `status` (`signed_in`, `guest`, `rejected`,
  `unresolved` when the database failed, `ended` when the backend ended the session).
  The page drops its token only for `rejected` or `ended`, and only when the message
  names the last `auth` it sent, carrying the token it still holds. The token of the
  connection, the orchestrator's user and that message change under one lock in
  `main.py`. Changing the user of an open connection closes the conversation.
- A session is a random token of which the database keeps the SHA-256 only. It is kept
  until it was not used for `AUTH_SESSION_IDLE_MINUTES` or is `AUTH_SESSION_MAX_HOURS`
  old (checked on every use, never stored), is revoked by a sign-out, and with
  `AUTH_LOGOUT_ON_LEAVE` when the orchestrator closes the user's conversation for
  `absence` or `idle`. A turn counts as a use; looking at a session does not. Sessions
  that are over are deleted at startup and every `AUTH_SESSION_CLEANUP_MINUTES`.
- A local account is an email address and a password (scrypt,
  `features/accounts/passwords.py`), created in two steps: a six-digit code is sent to
  the address (`backend/utils/mailer.py`, `MAIL_BACKEND`) and the account exists once
  the code is given back, together with the password: the first step carries the
  address only. What waits for its code is the code alone, held in RAM, hashed, as are
  the counts that limit the mails (`AUTH_OTP_WINDOW_MINUTES`, `AUTH_OTP_MAX_CODES`,
  `AUTH_OTP_MAX_MAILS`); a sign-in attempt is counted in the database before the
  password is looked at. One email has one account. An identity at an external
  provider is the pair (provider, id there), never an email; it is linked to a user by
  that signed-in user, whose page confirms what the provider proved (the callback
  attaches nothing), or by itself when the provider says it verified an email that is
  the email of a local account. Nothing else is merged by email. A sign-in at a
  provider is started with the challenge of a verifier the page keeps, and its
  hand-off code is redeemed only with that verifier. Nothing of a provider is kept but who the person is. A
  password, a session token and a provider's access token never reach a log, a span or
  the capture; with `MAIL_BACKEND=log`, and only then, the mail carrying a code is
  written to the log instead of being sent.
- The HTTP routes `/auth` are a contract between `backend/features/accounts/api.py`
  and `frontend/src/auth/`.
- The persona reaches the turn through the `Persona` contract only: for a logged-in
  user the engine asks it for the personality that user gave the avatar, before the
  LLM; a guest's turn reads nothing. The persona is written into the system prompt:
  `SYSTEM_PROMPT` has one `{persona}` slot and `PERSONA_DEFAULT` (`backend/prompts.py`)
  is what fills it when nobody set anything. A field the user set replaces the default
  of that field, the others keep theirs; with none set, or a slow or failing read, the
  avatar plays the default persona. The profile follows as a second system message, so
  what the person asked for themselves (their own way of being addressed) comes last
  and wins. The fields are declared once (`PERSONA_FIELDS` in `backend/models.py`),
  stored per user and never read across users; a description is read into them by
  `LLM.complete`, given between delimiters as material and stored nowhere: only what
  the user saves is kept.
- Users, memory and persona live in the application database (`DATABASE_URL`).
  `backend/utils/database.py` is the only module that knows engines; a feature declares
  its tables and keeps all its SQL in its `store.py`, written against SQLAlchemy Core
  with nothing specific to one engine. Every per-user table carries `user_id` and every
  store method takes it: no query reads across users (the startup housekeeping of the
  recorded turns is the one exception, and gives back ids only).
- The original of what a user imports is kept unchanged in the object store
  (`backend/utils/object_store.py`, `OBJECT_STORE_DIR`), one store for all users. Its
  keys carry no user: who owns an object is recorded in the application database only,
  and an object is never served by its key — every read goes through the row and its
  `user_id`. The text of an import is derived from the bytes and stored nowhere. It is
  given to the LLM between delimiters, as material to read and never as instructions.
- Work the LLM does outside a turn (reading an import, a closed conversation or a
  description of the avatar) goes
  through `LLM.complete`,
  with its own limits (`LLM_COMPLETE_TIMEOUT_SEC`, `LLM_COMPLETE_MAX_TOKENS`); its
  failure never puts the primary of the turns into the fallback cooldown.
- The HTTP routes `/memory` are a contract between `backend/features/memory/api.py` and
  `frontend/src/memory/`; the routes `/persona`, between
  `backend/features/persona/api.py` and `frontend/src/persona/`.
- Every step of a turn is a span of `backend/utils/telemetry.py` carrying its input and
  output; a new step gets one. Inside an async generator use `detached_span`, never
  `start_as_current_span`. Per-frame and per-chunk work (tracking, VAD) is measured with
  metrics, not spans. Texts go on spans whole; only the `Span` log line cuts them.
- Where traces and metrics go is configuration (`OTLP_ENDPOINT`); the code names no
  backend. `CAPTURE_ENABLED` stores faces and voices and stays off by default.
- Blocking inference (VAD, TTS, vision), the knowledge store (SQLite) and the
  application database run in a thread or executor, never on the event loop.
- The WebSocket message shapes are a contract between `backend/core/orchestrator/` and
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
6. `CHANGELOG.md` has an entry for it under `[Unreleased]` (§7).
7. If it changes the overall architecture, there is a new architecture record and
   `docs/ARCHITECTURE.html` shows the new shape with a Backlog row linking to it (§8).
8. It is committed with `git-auto-commit`. The plan status becomes `done` only after a
   human has verified it.

## §7. Changelog

`CHANGELOG.md` at the repository root is the one record of what changed for someone
using or operating the avatar. Plans (`docs/YYYY-MM-DD/`) say why and how; the changelog
says what.

- Add an entry in the same commit as the change, for every change that alters what the
  avatar does or how it is run: a feature, a fix, a changed contract (WebSocket message,
  HTTP route), a new, renamed or removed config key, a changed default or prompt, a new
  dependency. A commit that only refactors, adds tests or writes a plan needs none.
- New entries go under `## [Unreleased]`, at the top of the file. A version is cut only
  when a human asks for a release: `[Unreleased]` then becomes `## [X.Y.Z] - YYYY-MM-DD`
  and a new empty `[Unreleased]` is opened. Agents never cut a version on their own.
- Versions follow [Semantic Versioning](https://semver.org/), `MAJOR.MINOR.PATCH`:
  - **MAJOR**: a breaking change. A WebSocket message or HTTP route changed or removed,
    a config key renamed or removed, stored data that the new version cannot read, a
    removed feature.
  - **MINOR**: a new feature, module, model backend or config key.
  - **PATCH**: a bug fix, prompt or default tuning, a styling tweak, a documentation
    update.
- Group the entries of a version under `### Added`, `### Changed`, `### Fixed`,
  `### Removed`, in that order, leaving out the empty groups.
- Start each entry with the module(s) it touches in brackets, using the module IDs of
  §0: `[vision]`, `[vad]`, `[stt]`, `[llm]`, `[tts]`, `[orchestrator]`, `[expression]`,
  `[knowledge]`, `[memory]`, `[persona]`, `[accounts]`. Use `[shared]` for what belongs to no single module (config,
  providers, observability, packaging) and `[all]` for a change across the whole turn.
- One line per entry, written for a reader who has not seen the code: what the avatar
  now does, not which file changed. Mark a breaking change with `**Breaking:**`.

## §8. Architecture records

`docs/adr/` is the log of the **official architectures** of the system: one record per
architecture, each showing the whole system, not one decision. `docs/ARCHITECTURE.html`
is the full detail of the current one. `tests/unit/test_docs.py` checks the rules below.

- Write a record only when a new overall architecture is adopted, typically once per
  milestone (MVP 1, MVP 2). A feature, a single design choice, a bug fix or a tuning
  change gets no record: it goes in its plan and, if it shows in the architecture, in
  `docs/ARCHITECTURE.html` and its Backlog.
- A record is `docs/adr/NNNN-<kebab-case-name>.md`, numbered in sequence without gaps,
  copied from `docs/adr/template.md`: front matter (`adr`, `title`, `date`, `status`,
  `agent`, `modules`) and exactly three sections:
  - **Context**: what made this architecture necessary (the need, the starting point,
    the constraints).
  - **High-level diagram**: the whole system in one Mermaid diagram, what is new marked,
    followed by the shape in words.
  - **Consequences**: what it gives, what it costs, what it leaves open.
- It is written at the altitude of the architecture document: responsibilities and
  boundaries, no functions or file contents.
- `status` is `accepted` or `superseded`. Only an architecture a human has approved is
  logged; agents do not add a record on their own initiative.
- A record is never rewritten to follow later changes. A new architecture is a new
  record; the previous one changes its status to `superseded` and gains
  `superseded_by: NNNN`. Exactly one record is not superseded: the current architecture.
- Every record is listed in `docs/adr/README.md` with its status.
- With a new record, update `docs/ARCHITECTURE.html` to the new shape in the same change
  and add a row to its Backlog linking to the record. The version in that document's
  header changes with each new record.
- A milestone is also a git tag, created by a human or on request:
  `git show <tag>:docs/ARCHITECTURE.html` is the detailed document as it was then.
- `docs/ARCHITECTURE.html` is one self-contained HTML page, written and edited by
  hand with the `architecture-doc` skill: plain HTML, Mermaid diagrams as text, no
  Markdown copy and no build step. After an edit, open it in a browser and check that
  every diagram is drawn.
