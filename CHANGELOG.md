# Changelog

What changed in Avatar AI (Magic Mirror), newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versions follow
[Semantic Versioning](https://semver.org/). Each entry names the module(s) it touches;
the rules are in `CLAUDE.md` / `AGENTS.md` §7.

## [Unreleased]

### Added

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
- `[expression]` A profile page, "Hồ sơ": see, add, edit and delete what the avatar
  remembers about you.
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

### Changed

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
