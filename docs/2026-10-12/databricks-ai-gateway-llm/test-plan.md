---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: llm
summary: Tests for the Responses API LLM backend and its Databricks catalog entry
---

# Test plan — Databricks AI Gateway LLM backend

## Unit tests (sandbox, `tests/unit/`)

`AsyncOpenAI` is replaced by a fake that records the request and replays scripted
stream events; no network.

| Area | File | Cases |
|---|---|---|
| Request shape | `test_llm_responses.py` | model, `stream`, `max_output_tokens`, `input` = system prompt + user text as `input_text` parts; client built with the gateway URL, the token, `LLM_TIMEOUT_SEC`, `GROQ_MAX_RETRIES` |
| Stream | `test_llm_responses.py` | only `response.output_text.delta` is yielded (reasoning and lifecycle events dropped, empty deltas skipped); the stream is closed |
| Errors | `test_llm_responses.py` | SDK error on request and mid-stream → `LLMError`; `error` and `response.failed` events → `LLMError`; a long error body is summarized |
| Catalog | `test_catalog.py` | `databricks` builds `OpenAIResponsesLLM` with `DATABRICKS_BASE_URL` / `DATABRICKS_TOKEN` |
| Validation | `test_config.py` | `databricks` requires the URL and the token; `groq_api_key` not required when no stage uses Groq; the token is absent from `repr` |

## End-to-end tests (real environment, `RUN_E2E=1`)

- `tests/e2e/test_real_modules.py` and `test_real_turn.py` build the LLM through the
  catalog, so they exercise this backend when `.env` sets `LLM_BACKEND=databricks`,
  `DATABRICKS_BASE_URL`, `DATABRICKS_TOKEN` and `LLM_MODEL`. Targets unchanged: first
  audio under 3.5 s, end to end under 10 s (CLAUDE.md §6).
- To confirm on the first real call: the gateway streams (`stream: true`) and accepts
  the `system` message inside `input`.
