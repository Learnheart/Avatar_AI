---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: stt, llm
summary: Tests for the primary + backup wrappers of the STT and LLM stages
---

# Test plan — backup backends

## Unit tests (sandbox, `tests/unit/`)

Scripted STT / LLM backends (text, error, delay); the cooldown clock is patched.

| Area | File | Cases |
|---|---|---|
| STT | `test_fallback.py` | healthy primary answers, backup not called; backup on `STTError` and on timeout; primary skipped during the cooldown and retried after it; `ValueError` raised without fallback or cooldown; the backup's error reaches the caller |
| LLM | `test_fallback.py` | healthy primary streams, backup not called; backup on `LLMError` and on a late first delta; failure after the first delta is raised, not replayed; only the first delta is bounded; empty answer is not a failure; cooldown and retry; closing the answer closes the backend stream |
| Catalog | `test_fallback.py` | a named backup wraps the primary (both stages); empty or identical backup builds the primary alone; unknown backup raises |
| Validation | `test_config.py` | a Databricks backup requires the gateway URL and a credential; Groq as a backup requires the Groq key |

## End-to-end tests (real environment, `RUN_E2E=1`)

- On the company network: default Groq backends with
  `STT_BACKEND_BACKUP=databricks`, `LLM_BACKEND_BACKUP=databricks`. Expect the
  `Primary backend failed, using the backup` warning once per stage and a complete turn.
- On an open network, same settings: no warning, timings of the Groq path unchanged
  (first audio under 3.5 s, end to end under 10 s).
