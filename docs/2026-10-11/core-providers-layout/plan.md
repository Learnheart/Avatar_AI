---
author: linhbk3
date: 2026-10-11
status: in-progress
agent: avatar
modules: vision, vad, stt, llm, tts, orchestrator
summary: Reorganize the backend into core / providers / runtime / features and make the LLM and STT provider a configuration choice
---

# Core, providers, runtime and features layout

## Problem statement

- The backend folders mix two things that change for different reasons: the turn
  pipeline (stable) and the model backends behind it (swapped per provider or machine).
- The LLM is tied to Groq: `create_llm` returns `GroqLLM`, STT and LLM share one key
  and one base URL, and Groq-only request parameters sit inside the LLM backend. Moving
  to another API, or to a self-hosted model, means editing code.
- There is no obvious place for machine-specific adjustments (macOS / Windows) or for
  features that grow beside the pipeline (RAG, memory).

## Requirements

- The turn pipeline (vision → VAD → conversation → client) lives in one module that
  does not know which provider runs.
- Switching the LLM or the STT to another OpenAI-compatible API, or to a self-hosted
  server, is done in `.env` alone.
- Machine-dependent decisions have one home.
- RAG and memory can be built in parallel, in their own folder.
- Behaviour with the default configuration does not change: same env keys, same
  defaults, same WebSocket messages.

## Decisions made

Layout approved by the user on 2026-10-11:

```
backend/
  main.py                  # the only place that wires core + providers + features
  config.py  tuning.py  prompts.py  models.py
  core/                    # one turn; imports no provider, runtime or feature
    contracts.py           # STT, LLM, TTS interfaces and their errors
    orchestrator/          # state machine, ws_manager
    perception/            # camera, tracking, vad
    conversation/engine.py # STT → LLM → TTS
  providers/               # model backends, API and self-hosted
    catalog.py             # name → backend, for every stage
    llm/openai_compatible.py
    stt/openai_compatible.py
    tts/zerotts.py
  runtime/                 # what depends on the machine
    device.py              # metal / cuda / cpu detection
  features/                # built beside the core (knowledge, memory)
```

- **Dependency rule.** `core` imports only `core`, `config`, `models` and `utils`.
  `providers` implement `core/contracts.py`. Only `main.py` imports `providers`.
- **A plugin is a table entry.** `providers/catalog.py` maps a backend name to the
  function that builds it. No runtime discovery.
- **One class serves every OpenAI-compatible endpoint.** `OpenAICompatibleLLM` and
  `OpenAICompatibleSTT` take the base URL, the key and the provider-specific request
  parameters as arguments. The `groq` / `groq_whisper` entries pass the Groq values;
  the new `openai_compatible` entry passes `LLM_BASE_URL` / `LLM_API_KEY` (and
  `STT_BASE_URL` / `STT_API_KEY`), which covers OpenRouter, vLLM, Ollama and similar.
- **Selection keys.** `LLM_BACKEND` is new (default `groq`); `STT_BACKEND` and
  `TTS_BACKEND` keep their names and values. Chosen over the `*_PROVIDER` names
  floated in the discussion, to match the two keys that already exist.
- **`groq_api_key` is required only when a Groq backend is selected.**
- **`runtime/` starts with what exists**: the device detection. No Windows-specific
  branch is written until it can be run on a Windows machine.
- **`features/` starts as a described, empty package.** The engine's extension points
  for RAG and memory change the LLM contract and belong to that feature's plan.

## Implementation approach

1. **Move** files into the new layout with `git mv`, merge the three `base.py`
   contracts into `core/contracts.py`, fix imports. No logic change; one commit.
2. **Provider layer**: rename the Groq classes to the OpenAI-compatible ones, add
   `LLM_BACKEND` and the generic endpoint keys, register `openai_compatible`, relax
   the key validation. Unit tests per `test-plan.md`.
3. **Docs**: `CLAUDE.md` §1/§4, `docs/ARCHITECTURE.md` (router wording), `.env.example`.

## To-do

- [x] Move into `core/`, `providers/`, `runtime/`, `features/`; contracts in one file
- [x] Unit tests pass after the move
- [x] `OpenAICompatibleLLM` / `OpenAICompatibleSTT` with endpoint passed in
- [x] `LLM_BACKEND`, `LLM_BASE_URL`, `LLM_API_KEY`, `STT_BASE_URL`, `STT_API_KEY` in `config.py` and `.env.example`
- [x] `openai_compatible` entries in `providers/catalog.py`; key validation per selected backend
- [x] Unit tests for the new entries and the validation
- [x] Update `CLAUDE.md`, `docs/ARCHITECTURE.md`
- [x] Commit and push to `main`
- [ ] Human verification (one real turn, `tests/e2e`)
