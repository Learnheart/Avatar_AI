---
author: linhbk3
date: 2026-10-09
status: in-progress
agent: avatar
modules: vision, vad, stt, llm, tts, orchestrator, expression
summary: Contracts, WebSocket protocol, state machine and config keys for the modular pipeline rebuild.
---

# Design — Modular pipeline rebuild

Layout is CLAUDE.md §4. This document fixes the boundaries between modules.

## 1. Module contracts (backend)

All contracts are `typing.Protocol` / ABC classes in the `base.py` of each package.
Shared dataclasses live in `backend/models.py`.

### Perception

- `perception/camera.py` — `Camera`: opens the device (`CAMERA_INDEX` / `CAMERA_URL`),
  reads frames in its own thread, retries on disconnect. Yields BGR frames.
- `perception/tracking.py` — `HeadTracker.process(frame) -> TrackingResult`
  where `TrackingResult = {present: bool, head_rotation: HeadRotation | None}`.
  Picks the largest face; clamps pitch/yaw/roll to `HEAD_*_MAX`. A pure helper does the
  clamp so it is unit-testable without MediaPipe.
- `perception/vad.py` — `VADModule.process_chunk(pcm16: bytes) -> list[VADEvent]`
  (`speech_start` / `speech_end`, in order, possibly both in one call). Odd-length or
  empty input returns `[]`. `load()` and `process_chunk()` are synchronous and are
  called off the loop by the orchestrator.

### Conversation

```
STT   async transcribe(pcm16: bytes, sample_rate: int) -> Transcript{text: str, language: str}
LLM   stream_answer(user_text: str) -> AsyncIterator[str]          # text deltas, no reasoning
TTS   sample_rate: int
      synthesize_stream(text: str) -> AsyncIterator[bytes]         # Int16 LE mono PCM
```

- STT: empty / silent audio → `Transcript(text="")`, not an error. Audio longer than
  `STT_MAX_UTTERANCE_SEC` is truncated by the orchestrator before the call. Unsupported
  sample rate → `ValueError`. Provider failures raise `STTError`.
- STT router: `detect_device() -> "metal" | "cuda" | "cpu"`;
  `create_stt(config) -> STT` picks `STT_BACKEND` if set, otherwise the backend mapped to
  the detected device. Unknown backend name → `ValueError` at startup.
- LLM: fixed system prompt from `SYSTEM_PROMPT`, no history, no tools. Reasoning is
  minimised (`LLM_REASONING_EFFORT`) and excluded from the stream. Failures raise
  `LLMError`.
- TTS: empty text yields nothing. ZeroTTS runs in a worker thread; its float32 48 kHz
  frames are converted to Int16 and aggregated to about `TTS_CHUNK_MS` per yielded chunk.
  Text is normalised with `normalize_vi_text` first. Failures raise `TTSError`.

### ConversationEngine (`conversation/engine.py`)

The swappable boundary: "audio in → text + audio out".

```
async respond(pcm16: bytes, sample_rate: int) -> AsyncIterator[EngineEvent]

EngineEvent =
    TranscriptEvent(text)             # once, after STT
  | AudioEvent(text, pcm, sample_rate) # text = the sentence this audio belongs to,
                                       # set on the first chunk of a sentence, "" after
  | DoneEvent()
```

- Empty transcript → `TranscriptEvent("")` then `DoneEvent`; no LLM call.
- LLM deltas are cut into sentences by a pure `split_sentences` helper (`. ! ? …` and
  newline, keeping a tail buffer). Each completed sentence goes to TTS immediately, while
  the LLM keeps streaming.
- LLM failure or empty answer → the engine speaks `LLM_FALLBACK_TEXT`.
- TTS failure on a sentence → that sentence is emitted as `AudioEvent(text, b"", rate)`
  (text only) and the turn continues.
- The generator is cancellation-safe: cancelling the consumer stops the LLM stream and
  the TTS worker.

## 2. Orchestrator

Only `Orchestrator._transition()` changes `AvatarState`.

```
idle ──speech_start──► listening ──speech_end──► thinking ──first audio──► speaking
  ▲                        ▲                         │                        │
  │                        └──── speech_start (barge-in: cancel turn, send interrupt)
  └── empty transcript / turn finished + playback ended / error / timeout ───┘
```

- Mic chunks are queued by the WS receive loop and consumed by one audio worker task, so
  a slow stage never blocks the socket. VAD inference runs in a single-thread executor.
- A ring buffer keeps the last `VAD_PREROLL_MS` of audio. On `speech_start` the utterance
  buffer starts from that pre-roll; it is capped at `STT_MAX_UTTERANCE_SEC`.
- `speech_end` starts the turn as an `asyncio.Task` wrapped in `TURN_TIMEOUT_SEC`.
- Barge-in: `speech_start` while `thinking` or `speaking` cancels the turn task, sends
  `interrupt`, and enters `listening`.
- After the last audio is sent (`is_final`), the orchestrator stays in `speaking` until
  the client sends `playback_ended` or `PLAYBACK_END_TIMEOUT_SEC` after the estimated
  audio duration elapses, then goes `idle`.
- Any exception or timeout in a turn → `error` message to the client, state `idle`.
- WS disconnect cancels the running turn and resets the state to `idle`.
- Vision runs in parallel: `tracking_update` is sent at `TRACKING_FPS` regardless of the
  turn state.

## 3. WebSocket protocol (`/ws`, JSON text frames)

Client → server

| type | fields | notes |
|---|---|---|
| `audio_chunk` | `audio_data` (base64) | PCM Int16 LE, mono, 16 kHz |
| `playback_ended` | — | sent when the playback queue drains after an `is_final` message |

Server → client

| type | fields |
|---|---|
| `connected` | `session_id`, `config: {tracking_fps, audio_format}`; always followed by a `state` message carrying the current state, so a reconnecting client is in sync at once |
| `tracking_update` | `timestamp`, `avatar_control: {present, head_rotation: {pitch, yaw, roll}, is_speaking}` |
| `state` | `state`: `idle` \| `listening` \| `thinking` \| `speaking` |
| `speech_output` | `text_content`, `audio_data` (base64), `audio_mime_type` (`audio/pcm;rate=N`), `is_final` |
| `interrupt` | — |
| `error` | `code`, `message` |

`head_rotation` is in radians and is applied by the frontend to the head bone as a YXZ
Euler rotation (pitch → X, yaw → Y, roll → Z) with no sign flip, so the avatar mirrors the
person: pitch > 0 nods down (looking up is negative), yaw > 0 turns to the person's own
right (own left is negative), roll > 0 tilts toward the person's own left shoulder.
`error.message` is a fixed text per `code`; it never carries a provider response.

Malformed JSON or an unknown `type` gets an `error` reply (`bad_message` /
`unknown_message`); the socket stays open.

## 4. Frontend (Expression)

- `avatar/motion.js` — pure, imports nothing:
  `clampHeadRotation(rotation, limits)`, `splitHeadNeck(rotation, headShare)`,
  `rmsFromTimeDomain(bytes)`, `mouthTargetFromRms(rms, opts)`,
  `stepMouth(current, target, opts)`. Non-finite inputs are treated as 0.
- `avatar/lip-sync.js` reads the AnalyserNode of the audio actually playing and calls
  the `motion.js` functions. `avatar/avatar-renderer.js` applies the numbers to bones and
  blendshapes.
- `audio/audio-manager.js` schedules each buffer at `max(now, endOfPrevious)` on the
  AudioContext clock (gapless), never drops queued audio, and tags work with a
  generation counter so nothing decoded before an `interrupt` plays after it. It resumes
  the AudioContext on the first user gesture. When the queue drains after `is_final` it
  emits `playback_ended`.
- `state` messages drive a status label and a small "thinking" head tilt that covers the
  dead-air gap.
- Tunables come from `import.meta.env` (`VITE_WS_URL`, `VITE_VRM_PATH`) and one
  `config.js` constants module.

## 5. Config keys

New keys (all appended to `.env.example`):

| Key | Default | Purpose |
|---|---|---|
| `groq_api_key` | — (required) | Groq key for STT + LLM |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint |
| `DEVICE` | `auto` | Router override: `auto` / `metal` / `cuda` / `cpu` |
| `STT_BACKEND` | `auto` | Router override: backend name |
| `STT_MODEL` | `whisper-large-v3-turbo` | |
| `STT_LANGUAGE` | `vi` | |
| `STT_TIMEOUT_SEC` | `8` | |
| `STT_MAX_UTTERANCE_SEC` | `30` | |
| `LLM_MODEL` | `openai/gpt-oss-20b` | |
| `LLM_REASONING_EFFORT` | `low` | |
| `LLM_MAX_TOKENS` | `512` | |
| `LLM_TIMEOUT_SEC` | `8` | |
| `LLM_FALLBACK_TEXT` | Vietnamese apology sentence | Spoken when the LLM fails |
| `TTS_BACKEND` | `zerotts` | |
| `ZEROTTS_REPO` | `zeroweight-ai/ZeroTTS` | |
| `ZEROTTS_VOICE` | `maichi` | |
| `TTS_CHUNK_MS` | `240` | Size of each audio message |
| `TTS_TIMEOUT_SEC` | `10` | Per sentence |
| `VAD_MIN_SILENCE_MS` | `500` | End-of-speech confirmation window |
| `VAD_SPEECH_PAD_MS` | `100` | |
| `VAD_PREROLL_MS` | `300` | Audio kept before `speech_start` |
| `TURN_TIMEOUT_SEC` | `30` | |
| `PLAYBACK_END_TIMEOUT_SEC` | `2` | Grace after estimated playback end |
| `LOG_LEVEL` | `INFO` | |
| `GROQ_MAX_RETRIES` | `0` | SDK retries per Groq request; the engine has its own fallback |
| `ERROR_SUMMARY_MAX_CHARS` | `200` | Underlying error text kept in error messages and logs |
| `VITE_WS_URL`, `VITE_VRM_PATH` | — | Frontend |

Kept: `HOST`, `PORT`, `CAMERA_*`, `TRACKING_FPS`, `VAD_THRESHOLD`, `HEAD_*_MAX`,
`NO_FACE_TIMEOUT_SEC`, `SYSTEM_PROMPT`. Legacy (unused, kept in `.env.example` only):
`GEMINI_*`, `TTS_MODE`, `AZURE_*`, `VIETTEL_*`, `SNAPSHOT_*`, `groq`.
