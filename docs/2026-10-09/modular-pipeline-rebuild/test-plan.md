---
author: linhbk3
date: 2026-10-09
status: in-progress
agent: avatar
modules: vision, vad, stt, llm, tts, orchestrator, expression
summary: Unit and end-to-end tests for the modular pipeline rebuild.
---

# Test plan — Modular pipeline rebuild

## Unit tests (sandbox: no network, no model download, no camera)

Run: `.venv/bin/python -m pytest tests/unit -q` and `cd frontend && npm test`.

| Area | File | Cases |
|---|---|---|
| Config | `tests/unit/test_config.py` | defaults load; missing `groq_api_key` fails validation; env overrides apply |
| Tracking | `tests/unit/test_tracking.py` | rotation clamp at and beyond limits; largest face chosen; no face → `present=False` |
| VAD | `tests/unit/test_vad.py` | (model mocked) start and end in one call both returned in order; odd-length and empty chunk → `[]`; remainder buffering across calls |
| STT | `tests/unit/test_stt.py` | (client mocked) WAV payload is valid 16 kHz mono; empty audio → empty text without a call; bad sample rate → `ValueError`; provider error → `STTError` |
| STT router | `tests/unit/test_stt_router.py` | each device maps to a backend; `STT_BACKEND` override wins; unknown backend → `ValueError` |
| LLM | `tests/unit/test_llm.py` | (client mocked) content deltas yielded, reasoning deltas dropped; provider error → `LLMError` |
| TTS | `tests/unit/test_tts.py` | (ZeroTTS mocked) float32 → Int16 conversion and clipping; chunks aggregated to `TTS_CHUNK_MS`, even byte length; empty text yields nothing; error → `TTSError` |
| Engine | `tests/unit/test_engine.py` | `split_sentences` on Vietnamese text; TTS starts on sentence 1 before the LLM finishes; empty transcript → no LLM call; LLM error → fallback text spoken; TTS error → text-only event, turn continues; cancellation stops the stream |
| Orchestrator | `tests/unit/test_orchestrator.py` | (engine, VAD, WS mocked) `idle→listening→thinking→speaking→idle`; pre-roll included in the utterance; barge-in in `thinking` and in `speaking` cancels the turn and sends `interrupt`; turn timeout → `error` + `idle`; engine exception → `error` + `idle`; `playback_ended` and its timeout both end `speaking`; disconnect resets state |
| WS endpoint | `tests/unit/test_ws.py` | `connected` on open; unknown type and malformed JSON get `error` and keep the socket; `/health` responds |
| Frontend motion | `frontend/tests/motion.test.js` | head clamp both signs, no sign flip per axis; non-finite input → 0; louder RMS → larger mouth target; below noise floor → 0; `stepMouth` converges to 0 and snaps; head/neck split sums to the input |

## End-to-end tests (real environment, real Groq calls — run sparingly)

| Test | How | Pass criteria |
|---|---|---|
| Module smoke: STT | `tests/e2e/test_real_modules.py` — a real Vietnamese WAV through Groq Whisper | Non-empty Vietnamese text, under ~1.5 s |
| Module smoke: LLM | same file — one question through Groq | Vietnamese answer streamed incrementally, first token under ~1.5 s |
| Module smoke: TTS | same file — one sentence through ZeroTTS | Non-silent Int16 PCM at the declared rate; first chunk time recorded |
| Full turn over WebSocket | `tests/e2e/test_real_turn.py` — start the real backend, stream a real Vietnamese utterance as 20 ms `audio_chunk`s followed by silence | States go `listening → thinking → speaking → idle`; `speech_output` audio is non-silent; **first audio < 3.5 s** and **end to end < 10 s** from end of speech; the returned audio transcribes back to Vietnamese text |
| Barge-in | same file — speak again during `speaking` | `interrupt` received, state returns to `listening` |
| Frontend in a browser | manual / browser-driven: `npm run dev` against the real backend | Page loads without console errors; audio plays; mouth value follows loudness; status shows states |

E2e tests are marked `e2e` and skipped unless `RUN_E2E=1`.
