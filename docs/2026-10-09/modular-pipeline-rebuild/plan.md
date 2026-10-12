---
author: linhbk3
date: 2026-10-09
status: in-progress
agent: avatar
modules: vision, vad, stt, llm, tts, orchestrator, expression
summary: Rebuild the Gemini Live POC into the modular VAD → STT → LLM → TTS pipeline described in docs/ARCHITECTURE.md.
---

# Plan — Modular pipeline rebuild

## Problem statement

The code was written before the current architecture. It drives one Gemini Live
audio-to-audio session, has no STT module, no ZeroTTS, no `thinking` state, and its
layout does not match CLAUDE.md §4. `docs/ARCHITECTURE.md` and `docs/quality-gates.md`
describe a separated pipeline behind capability boundaries (Perception / Conversation /
Expression). The code must be rebuilt to match, without changing the tech stack fixed in
CLAUDE.md §3.

## Requirements

1. One complete turn works end to end: mic audio → VAD → STT → LLM (streamed) → TTS
   (per sentence) → audio over the WebSocket → frontend playback + amplitude lip-sync.
2. Source layout follows CLAUDE.md §4.
3. Tech stack is exactly CLAUDE.md §3: Silero VAD, Groq Whisper STT, Groq LLM
   (`openai/gpt-oss-20b`), ZeroTTS, MediaPipe head tracking, Three.js + VRM frontend.
4. The Orchestrator is the only owner of turn state: `idle → listening → thinking →
   speaking → idle`. Barge-in interrupts `thinking` and `speaking`.
5. No module failure crashes the process or wedges the state machine (quality-gates
   §1.6): every stage has a timeout and a defined fallback.
6. Blocking inference (VAD, TTS, vision) never runs on the event loop.
7. No hardcoded keys, URLs, model names or tunables (CLAUDE.md §1).
8. Unit tests cover the logic with neighbours mocked; e2e tests exercise a real turn
   (CLAUDE.md §2, `test-plan.md`).

## Decisions made

| # | Decision | Why |
|---|---|---|
| D1 | LLM provider is Groq (`openai/gpt-oss-20b`), model id from `.env` | CLAUDE.md §3 table names it; one provider and one key for STT + LLM |
| D2 | Head tracking stays in the MVP (MediaPipe) | ARCHITECTURE §1 lists head motion as an output; the code already exists |
| D3 | Presence is not a turn state. Turn states are `idle/listening/thinking/speaking`; presence travels as `present` in `tracking_update` | Matches the state machine in quality-gates §1.6; vision is a parallel plane |
| D4 | The WebSocket stays JSON with base64 audio; three messages are added: `state` (server→client), `error` (server→client), `playback_ended` (client→server) | Smallest change to a working contract. `state` lets the frontend cover dead air; `playback_ended` keeps the barge-in window open while audio is still playing, since ZeroTTS synthesizes faster than real time |
| D5 | TTS audio is sent as `audio/pcm;rate=48000`, Int16 mono, aggregated to `TTS_CHUNK_MS` pieces | ZeroTTS emits 48 kHz float32 in 80 ms frames; the frontend already plays headerless Int16 PCM at the rate named in the mime type |
| D6 | The Device Capability Router lives in `conversation/stt/router.py`: detect `metal/cuda/cpu` once, map device → backend name, `STT_BACKEND` overrides. Today every device maps to `groq_whisper` | Keeps the ARCHITECTURE boundary so a local backend drops in later; STT is cloud in the current stack |
| D7 | The Azure / Viettel TTS branch and all Gemini code are deleted, not kept as fallback | No keys to verify them, and unverifiable fallback code is dead code. TTS failure falls back to "skip audio, keep text" |
| D8 | `.env.example`: new keys are appended; existing entries are kept and the unused ones are grouped under a "legacy" header | CLAUDE.md §1 forbids removing entries |
| D9 | The Groq key is read from `groq_api_key` (the name CLAUDE.md §3 and `.env` use) | Single source; the SDK default name is not used |
| D10 | Old tests and `quicktest.py` are deleted and replaced by `tests/unit` + `tests/e2e` | They import the removed Gemini modules and old paths |
| D11 | Frontend playback is scheduled gaplessly on the AudioContext clock and never drops queued audio | The current 5-chunk drop-oldest queue would discard streamed speech |

Out of scope: golden-dataset content and its WER / form-check tooling (quality-gates
§4), Docker packaging (CLAUDE.md §5 is still TODO), the Windows + CUDA machine.

## Implementation approach

Contracts, message shapes and config keys are fixed in `design.md`. Work splits into two
independent tracks that only meet at the WebSocket contract.

**Backend**
1. Restructure into `orchestrator/`, `perception/`, `conversation/`, `assets/`.
2. Perception: split `vision.py` into `camera.py` + `tracking.py`; VAD returns every
   event of a call and takes its tunables from config.
3. Conversation: `stt/` (contract, Groq Whisper, router), `llm/` (contract, Groq
   streaming), `tts/` (contract, ZeroTTS), `engine.py` composing them with a sentence
   splitter.
4. Orchestrator: state machine, utterance buffer with pre-roll, cancellable turn task,
   barge-in, timeouts, WS routing.
5. Config, `.env.example`, requirements, unit tests.

**Frontend**
1. Move files into `audio/` and `avatar/`; extract pure mapping into `avatar/motion.js`.
2. Gapless playback, interrupt-safe queue, `playback_ended`, `state` handling.
3. Unit tests for `motion.js` with `node --test`.

## To-do

- [x] Update CLAUDE.md for this project (tech stack untouched)
- [x] Backend: folder restructure per CLAUDE.md §4, Gemini / Azure / Viettel code removed
- [x] Backend: perception (`camera.py`, `tracking.py`, `vad.py`)
- [x] Backend: STT contract + Groq Whisper backend + Device Capability Router
- [x] Backend: LLM contract + Groq streaming backend
- [x] Backend: TTS contract + ZeroTTS backend
- [x] Backend: `ConversationEngine` with sentence streaming
- [x] Backend: orchestrator state machine, barge-in, timeouts, WS protocol
- [x] Backend: config keys, `.env.example`, requirements
- [x] Backend: unit tests pass
- [x] Frontend: restructure + `motion.js` + unit tests pass
- [x] Frontend: gapless playback, interrupt safety, `state` / `playback_ended`
- [ ] Frontend: `npm install` + `npm run build` + browser check with a VRM model (blocked: npm tarballs return 503 on the corporate network; `package-lock.json` still pins the old `three`)
- [x] Code review gate: PASS
- [ ] Real test: one full turn on the MacBook, first-audio and end-to-end latency measured (blocked: Groq and Hugging Face return 503 on the corporate network; ZeroTTS has never been run)
- [ ] QA sign-off
