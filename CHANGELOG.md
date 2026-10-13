# Changelog

What changed in Avatar AI (Magic Mirror), newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versions follow
[Semantic Versioning](https://semver.org/). Each entry names the module(s) it touches;
the rules are in `CLAUDE.md` / `AGENTS.md` §7.

## [Unreleased]

### Added

- `[persona]` A signed-in person can give the avatar a personality: a few named fields
  (name, role, way of addressing, personality, answer style, what to avoid), each a
  short line of at most `PERSONA_FIELD_MAX_CHARS` (default 300) characters, kept per
  person and followed by every answer to them from the next turn on. New routes
  `GET`, `PUT` and `DELETE /persona`; a guest gets `401` and the default character.
- `[persona]` A description of the avatar written freely is read into those fields by
  one request to the language model, outside the turn (`POST /persona/read`, at most
  `PERSONA_IMPORT_MAX_CHARS`, default 8000, characters). What is read is only given
  back: nothing is stored until the person saves the fields.
- `[persona]` `[expression]` The profile page has a block "Tính cách của avatar": a box
  to paste a description and have it read into the fields, one input per field to type
  or correct, "Lưu" to store them and "Xoá tính cách" to go back to the default
  character.
- `[persona]` `[llm]` The turn can carry a personality for the avatar: it is given to
  the LLM as a system message of its own, after the fixed character and before what is
  known about the person. Slow or failing, the turn is answered in the default character
  (`PERSONA_TIMEOUT_SEC`, default 1.0).
- `[orchestrator]` `[llm]` Short-term memory: inside one conversation the avatar sees
  the earlier turns, so a follow-up question is understood. A conversation opens at the
  first utterance and closes when the person left, stayed silent, changed, or asked for
  a new one; a page reload does not close it.
- `[orchestrator]` Each turn is remembered as it happened: an answer cut by speaking
  over the avatar is kept up to the sentence that had started to play, and the apology
  spoken when the LLM fails is not remembered as an answer.
- `[orchestrator]` Three WebSocket messages: `conversation_reset` (client → server)
  ends the conversation, also in the middle of an answer; `conversation` (server →
  client) tells the id of the open conversation or `null`; `transcript` (server →
  client) carries what the avatar heard for the turn.
- `[knowledge]` A follow-up question is searched together with the person's previous
  utterance, so it finds the passages of its subject (`KNOWLEDGE_QUERY_HISTORY_TURNS`,
  default 1, 0 = off).
- `[stt]` Whisper backends report how sure they are of the transcript, and can drop an
  utterance that is not speech (`STT_NO_SPEECH_MAX_PROB`, default 1.0 = nothing
  dropped).
- `[expression]` A transcript window on the right shows what the avatar heard and what
  it answers.
- `[memory]` Long-term memory, first part: the avatar keeps short facts about a known
  person (who they are, how to address them, what they prefer, their personality) and
  is given them with every turn of that person, so it addresses and answers them
  accordingly. A guest has no memory. The memory never costs the turn: empty, slow
  (`MEMORY_TIMEOUT_SEC`, default 1.0) or failing, the avatar answers without it.
- `[memory]` HTTP routes `/memory/items` (`GET`, `POST`, `PATCH /{id}`, `DELETE /{id}`)
  to see, add, edit and delete what is remembered about the current user; a guest gets
  `401`, and one user never sees another's items.
- `[memory]` A remembered fact is one short sentence: a text longer than
  `MEMORY_ITEM_MAX_CHARS` is refused with `422` and the reason.
- `[memory]` A remembered fact is stored on one line: line breaks, tabs and repeated
  spaces become one space, and control or invisible characters are removed; a text with
  nothing visible is refused with `422`.
- `[memory]` Import: a person describes themselves with a file (`.txt`, `.md`, `.csv`,
  `.pdf`, `.docx`, up to `MEMORY_IMPORT_MAX_FILE_MB`) or a pasted text; the avatar reads
  it once and keeps what it says about them as remembered facts, used from the next
  turn, each with the sentence it comes from. The original is kept unchanged and can be
  downloaded again; an import that cannot be read leaves nothing behind.
- `[memory]` HTTP routes `/memory/imports`: `GET` (the imports with their number of
  facts), `POST` (multipart field `file` or field `text` → `201` with the import and
  its facts; `413` too large, `415` unsupported type, `422` unreadable, empty or
  nothing to remember, `502` the LLM failed), `GET /{id}/content` (the original, as a
  download) and `DELETE /{id}?items=keep|delete` (the facts stay without a source, or
  go with it; default `keep`). A guest gets `401`; one user never sees, reads or
  deletes another's import.
- `[llm]` Every LLM backend can give one whole answer to an instruction outside a turn
  (used to read an import), with its own limits (`LLM_COMPLETE_TIMEOUT_SEC`,
  `LLM_COMPLETE_MAX_TOKENS`). With a backup backend, a failure of such a request never
  makes the turns skip the primary.
- `[shared]` An object store for the originals of what users import: a directory,
  `OBJECT_STORE_DIR` (default `backend/data/objects`), shared by all users; who owns a
  file is recorded in the application database.
- `[shared]` New config keys: `OBJECT_STORE_DIR` (`backend/data/objects`),
  `MEMORY_IMPORT_MAX_FILE_MB` (5), `MEMORY_IMPORT_MAX_CHARS` (20000),
  `LLM_COMPLETE_TIMEOUT_SEC` (30), `LLM_COMPLETE_MAX_TOKENS` (2048). The instruction an
  import is read with is `MEMORY_IMPORT_PROMPT`.
- `[shared]` The application database gains the table `imports`, and a remembered fact
  now points at its import. A database file created before this change keeps working
  without that link being enforced; delete it (`backend/data/app.db`, created again at
  startup) to get the new shape, since there is no migration tool yet.
- `[expression]` A profile page, "Hồ sơ": see, add, edit and delete what the avatar
  remembers about you.
- `[expression]` On "Hồ sơ", describe yourself with a file or a pasted text: the facts
  read from it appear at once; see your imports, open the original, and delete an
  import with or without its facts.
- `[shared]` An application database for users and memory, opened from `DATABASE_URL`
  (default: the SQLite file `backend/data/app.db`; PostgreSQL by changing the URL). Its
  tables are created at startup; the avatar does not start on a database it cannot
  open.
- `[shared]` `AUTH_DEV_USER_ID` names the user every connection and request belongs to
  until the login exists; empty (the default) means a guest.
- `[shared]` New config keys: `DATABASE_URL` (`sqlite:///backend/data/app.db`),
  `AUTH_DEV_USER_ID` (empty), `MEMORY_PROMPT_MAX_ITEMS` (20), `MEMORY_TIMEOUT_SEC`
  (1.0), `MEMORY_ITEM_MAX_CHARS` (200). The text the facts are given to the LLM in is
  `MEMORY_PROMPT`.
- `[shared]` New dependency: SQLAlchemy (`sqlalchemy>=2.0.0`); run
  `uv pip install -r backend/requirements.txt` after updating.
- `[expression]` A "Cuộc trò chuyện mới" button ends the conversation.
- `[shared]` New config keys: `CONVERSATION_ABSENCE_SEC` (30), `CONVERSATION_IDLE_SEC`
  (120), `MEMORY_HISTORY_MAX_TURNS` (10), `KNOWLEDGE_QUERY_HISTORY_TURNS` (1),
  `STT_NO_SPEECH_MAX_PROB` (1.0).
- `[shared]` Traces: the `turn` span names its conversation and user, the `stt` span
  carries the confidence and the no-speech probability, the `llm` span the number of
  earlier turns, the `knowledge` span the query as searched.
- `[memory]` The avatar gathers what a known person says about themselves: when their
  conversation closes, it is read once by the LLM, outside any turn, and what is worth
  keeping (who they are, how to address them, what they prefer, their personality) is
  proposed, each with the words it comes from. A proposal is used in no answer until
  the person approves it. A guest's conversation is never stored or read.
- `[memory]` A proposed preference or personality trait becomes active by itself once
  a later conversation states it again (`MEMORY_PROMOTE_SEEN_COUNT`, default 2
  conversations); a name or a way of addressing never does, it waits for the approval.
  What a conversation contradicts is never overwritten: it becomes a proposal to
  replace the remembered fact, which only an approval settles. A proposal with no
  quote of the person is never activated by repetition either.
- `[memory]` `PATCH /memory/items/{id}` accepts `{"status": "active"}` (alone or with
  `text` / `category`) to approve a proposal; approving a replacement removes the fact
  it replaces. `DELETE /memory/items/{id}` rejects a proposal. A rejected proposal, or
  a gathered fact that is deleted, is never proposed again; it is no longer listed and
  answers `404`.
- `[memory]` The turns of a known person's conversation are kept in the application
  database only until the conversation has been read, then deleted; turns older than
  `MEMORY_TRANSCRIPT_TTL_DAYS` (default 7) are deleted at startup whatever happened. A
  conversation that could not be read (the LLM failed, the backend was stopped) is
  read at the next start, in the background.
- `[memory]` An utterance the speech-to-text was unsure of
  (`MEMORY_MIN_STT_CONFIDENCE`, default 0.6) is not read for gathering.
- `[orchestrator]` For a known person, each turn and the close of the conversation are
  handed to the memory in the background: the answer and the next turn never wait for
  it, and a failing memory costs no turn.
- `[expression]` "Hồ sơ" shows what the avatar gathered from conversations, each
  proposal with the quote it comes from, to approve or reject.
- `[shared]` New config keys: `MEMORY_PROMOTE_SEEN_COUNT` (2),
  `MEMORY_MIN_STT_CONFIDENCE` (0.6), `MEMORY_TRANSCRIPT_TTL_DAYS` (7),
  `MEMORY_CONSOLIDATE_MAX_CHARS` (20000: a longer conversation is read from its most
  recent turns), `MEMORY_EXTRACT_MAX_REJECTED` (50: the rejected facts shown to the LLM
  that reads a conversation).
- `[shared]` The application database gains the tables `conversations` and `turns`,
  created at startup.
- `[shared]` Traces: each reading of a closed conversation is its own trace,
  `memory.consolidate`, with the transcript read, the operations the LLM answered, what
  was stored and the outcome.

### Changed

- `[llm]` `[tts]` The avatar's answers carry no emoji: the language model is told to
  answer in words only, in the default character and under any personality, and an
  emoji it still writes is removed from the sentence before it is spoken, shown in the
  transcript window or remembered.
- `[stt]` Whisper is asked for the detailed response (`verbose_json`) instead of the
  text alone.

## [0.1.0] - 2026-10-12

MVP 1 for the demo: one complete spoken turn end to end, and answers from uploaded
documents (RAG).

### Added

- `[all]` One complete turn: the avatar notices a person, listens, understands, answers
  and speaks in Vietnamese, as a separated STT → LLM → TTS pipeline that starts speaking
  on the first sentence of the answer.
- `[vision]` Presence detection and head rotation from the camera (MediaPipe); the
  camera is reopened when it disconnects.
- `[vad]` Speech start and end detection (Silero VAD), with a pre-roll so the first
  syllable is not clipped.
- `[orchestrator]` Turn state machine (idle → listening → thinking → speaking) over one
  WebSocket, with barge-in: speaking over the avatar cancels its answer.
- `[stt]` Vietnamese speech-to-text with Whisper through Groq or any OpenAI-compatible
  server, or with a chat model that is sent the audio (Gemini on Databricks).
- `[llm]` Streamed answers from Groq or any OpenAI-compatible API, or from the
  Databricks AI Gateway; the avatar answers as a personal work assistant.
- `[tts]` Vietnamese speech with ZeroTTS, running locally and streamed sentence by
  sentence.
- `[expression]` 3D VRM avatar in the browser: head follows the person, mouth follows
  the loudness of the audio being played, speech plays without gaps.
- `[knowledge]` Knowledge base: upload txt, md, csv, pdf and docx files, which are cut
  into chunks and indexed in an embedded vector DB (sqlite-vec); each question is
  answered with the matching passages, and without them when the base is empty, slow or
  failing.
- `[knowledge]` "Tài liệu" panel: upload, list and delete documents, and tick the ones
  the avatar may answer from.
- `[knowledge]` Embedding backends chosen by config: `local_hash` (no model, no
  network), `openai_compatible`, `databricks`, `ollama`.
- `[stt]` `[llm]` `[knowledge]` A backup backend per stage (`STT_BACKEND_BACKUP`,
  `LLM_BACKEND_BACKUP`, `EMBEDDING_BACKEND_BACKUP`) that takes over when the primary
  fails or is too slow.
- `[shared]` Model backends are a configuration choice (`STT_BACKEND`, `LLM_BACKEND`,
  `EMBEDDING_BACKEND`), with Databricks authentication by token or CLI profile for
  networks where Groq is blocked.
- `[shared]` Observability: one trace per turn with a span per step and its input and
  output, measurements of the tracking and VAD loops, OTLP export, and an optional
  capture of camera frames and audio to disk (`CAPTURE_ENABLED`, off by default).

### Changed

- `[shared]` Prompts live in `backend/prompts.py` and behaviour parameters in
  `backend/tuning.py`; `.env` only has to hold the API key.

### Removed

- `[all]` The Gemini Live proof of concept and the Azure / Viettel TTS branch, replaced
  by the pipeline above.
