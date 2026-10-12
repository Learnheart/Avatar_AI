---
author: linhbk3
date: 2026-10-11
status: in-progress
agent: avatar
modules: stt, llm, tts, orchestrator
summary: Give prompts and tunables their own files and gather the scattered backend factories and WebSocket messages, without changing behaviour
---

# Backend config and layout cleanup

## Problem statement

The backend folders are clear, but the places a developer edits to tune the avatar are
not:

- The system prompt and the fallback sentence are one-line strings buried in
  `backend/config.py`, and repeated in `.env.example`.
- `backend/config.py` spends three lines of boilerplate on each of about 45 keys, and
  mixes per-machine deployment values with product behaviour (VAD, timeouts, head
  clamps).
- Every default is declared twice (`config.py` and `.env.example`). A developer who
  copies `.env.example` to `.env` then edits a default in code sees no effect, because
  the copied value overrides it.
- The backend of each pipeline stage is chosen in three different places
  (`stt/router.py`, `tts/__init__.py`, and a direct `GroqLLM(...)` call in `main.py`).
- The WebSocket messages, a contract with the frontend, are dict literals spread over
  `orchestrator.py` and `ws_manager.py`.

## Requirements

- A developer can tell from the file names where to tune prompts, behaviour and
  deployment values.
- Behaviour does not change: same defaults, same env key names, same override rules,
  same WebSocket message shapes.
- Every value can still be overridden from the environment (needed later when models
  are deployed per machine). `.env` only has to contain the API key.
- The layout leaves an obvious place for future `knowledge` (RAG) and `memory` modules.

## Decisions made

- **Three files, one concern each.** `backend/prompts.py` (texts), `backend/tuning.py`
  (behaviour parameters), `backend/config.py` (secrets, per-machine and provider
  values). Decided with the user on 2026-10-11.
- **Prompts are not overridable** (user decision, 2026-10-11, after the first version):
  `backend/prompts.py` is the only source of the texts; `SYSTEM_PROMPT` and
  `LLM_FALLBACK_TEXT` are no longer read from the environment nor listed in
  `.env.example`.
- **Env override stays for every other key** (user decision, 2026-10-11). A key is declared
  once, on one line, with its env name and its default: `X: float = env_float("X", 0.5)`.
- **`AppConfig` stays the single object modules receive.** It inherits the `Prompts`
  and `Tuning` dataclasses, so `config.X` works unchanged at every call site and in
  every test.
- **`.env.example` keeps every entry but comments out the optional ones**, so a copied
  `.env` no longer shadows the defaults in code. No entry is removed (CLAUDE.md §1).
- **One factory module**, `backend/conversation/factory.py`, builds STT, LLM and TTS.
  The Device Capability Router logic moves there unchanged.
- **WebSocket message builders live in `orchestrator/ws_manager.py`**, next to the
  `connected` message it already builds. No new file.
- **Constants tied to an algorithm stay beside it** (`_SENTENCE_END` in `engine.py`,
  the face model in `tracking.py`, the Silero window in `vad.py`). They are not
  tunables.

## Implementation approach

Three independent slices, each verified by the unit tests and committed on its own.

1. **Prompts and tuning.** Add `backend/utils/env.py` (field helpers reading the
   environment), `backend/prompts.py`, `backend/tuning.py`; shrink `backend/config.py`
   to the deployment keys plus `validate()`. Comment out the optional entries of
   `.env.example`.
2. **One factory.** Create `backend/conversation/factory.py` from `stt/router.py` and
   `tts/__init__.py`, add `create_llm`, and use it in `main.py`. Tests keep their
   assertions; only import paths and the names of the patched registries change.
3. **WebSocket messages in one place.** Move every message dict into builder functions
   in `ws_manager.py`; the orchestrator calls them.

Then update the structure tree in `CLAUDE.md` §4 and the wording of §1.

### Out of scope (noted, not done here)

- Splitting the audio buffer and the turn metrics out of `orchestrator.py`.
- Letting the engine build the LLM messages (needed for RAG and memory; belongs to
  that feature's plan, since it changes the LLM contract).
- Removing the legacy block of `.env.example` and the stale `backend/Dockerfile`
  (both need the user's go-ahead).

### Where future modules go

`backend/conversation/knowledge/` and `backend/conversation/memory/`, each with a
`base.py` contract and one file per backend, registered in `conversation/factory.py`
and composed in `conversation/engine.py`. Their prompt templates go in
`backend/prompts.py`, their parameters in `backend/tuning.py`.

## To-do

- [x] Slice 1: `utils/env.py`, `prompts.py`, `tuning.py`, slimmer `config.py`
- [x] Slice 1: comment out the optional entries of `.env.example`
- [x] Slice 2: `conversation/factory.py` replaces `stt/router.py` and the factory in `tts/__init__.py`
- [x] Slice 3: WebSocket message builders in `ws_manager.py`
- [x] Update `CLAUDE.md` §1 and §4
- [x] Unit tests pass after every slice
- [ ] Human verification (one real turn, `tests/e2e`)
