---
author: linhbk3
date: 2026-10-11
status: in-progress
agents: stt, llm
summary: Tests for the backend catalog and the OpenAI-compatible LLM and STT backends
---

# Test plan — core / providers layout

## Unit tests (sandbox, `tests/unit/`)

The move itself adds no test: the existing suite must pass with only import paths
changed.

| Area | File | Cases |
|---|---|---|
| Catalog, LLM | `test_catalog.py` | default backend is Groq with its reasoning parameters; `openai_compatible` uses `LLM_BASE_URL` / `LLM_API_KEY` and sends no Groq parameter; unknown `LLM_BACKEND` raises |
| Catalog, STT | `test_catalog.py` | `openai_compatible` uses `STT_BASE_URL` / `STT_API_KEY`; device routing and `STT_BACKEND` override unchanged |
| Keyless server | `test_catalog.py` | both stages build against the real client with an empty key |
| Validation | `test_config.py` | `groq_api_key` required only when a stage uses Groq; `*_BASE_URL` required for `openai_compatible`; endpoint keys absent from `repr` |
| Request shape | `test_llm.py`, `test_stt.py` | unchanged assertions, built through the catalog |

## End-to-end tests (real environment, `RUN_E2E=1`)

- `tests/e2e/test_real_modules.py` and `test_real_turn.py`, unchanged in what they
  assert, with the default Groq backends: first audio under 3.5 s, end to end under
  10 s (CLAUDE.md §6).
- Not automated: a turn against a second provider (`LLM_BACKEND=openai_compatible`).
  Run it by hand once such an endpoint and key are available.
