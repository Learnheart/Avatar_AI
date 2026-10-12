---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: llm
summary: Add an LLM backend that calls the Databricks AI Gateway through the OpenAI Responses API
---

# Databricks AI Gateway LLM backend

## Problem statement

- The LLM stage can only reach chat-completions endpoints (`groq`, `openai_compatible`).
- The models served by the Databricks AI Gateway
  (`<workspace>/ai-gateway/mlflow/v1/responses`) are called through the OpenAI
  **Responses** API, which has another request shape (`input`, `max_output_tokens`) and
  another stream (typed events instead of chat chunks).

## Requirements

- `LLM_BACKEND=databricks` answers a turn through the gateway, streamed, from `.env`
  alone: workspace URL, token and model name are configuration (§1).
- The `LLM` contract and the conversation engine do not change.
- The default configuration (Groq) behaves as before.

## Decisions made

- **One generic class, one catalog entry.** `OpenAIResponsesLLM`
  (`providers/llm/openai_responses.py`) speaks the Responses API to any endpoint passed
  in; the `databricks` entry of `providers/catalog.py` gives it the gateway URL and the
  token. It reuses the `openai` SDK already in the requirements (`client.responses`).
- **Keys.** `DATABRICKS_BASE_URL` (up to `/ai-gateway/mlflow/v1`; the SDK appends
  `/responses`) and `DATABRICKS_TOKEN` (secret, kept out of `repr`). Both are required
  when `LLM_BACKEND=databricks`. The model is the existing `LLM_MODEL`.
  (Superseded the same day by `../databricks-stt-and-profile-auth/plan.md`: the model is
  `DATABRICKS_LLM_MODEL`, and a CLI profile can replace the token.)
- **System prompt as a `system` message inside `input`**, not the `instructions` field:
  it goes through the same message path as the user text, so a gateway that ignores
  unknown top-level fields cannot drop it silently.
- **Only `response.output_text.delta` events are yielded.** Reasoning and lifecycle
  events are skipped; an `error` or `response.failed` event raises `LLMError`.
- **Text only.** The model is multimodal, but the engine hands the LLM the transcript of
  the utterance (`stream_answer(user_text)`), so only its text path is used. Sending
  camera frames or raw audio to the model changes the `LLM` contract and the engine and
  is a separate feature.
- Timeout and retries reuse `LLM_TIMEOUT_SEC` and `GROQ_MAX_RETRIES`, as the
  `openai_compatible` backend does.

## Implementation approach

1. `OpenAIResponsesLLM` + unit tests (request shape, delta filtering, errors).
2. `DATABRICKS_BASE_URL`, `DATABRICKS_TOKEN` in `config.py`, validation, `databricks`
   catalog entry + unit tests.
3. `.env.example`, `CLAUDE.md` §4.

## To-do

- [x] `backend/providers/llm/openai_responses.py`
- [x] `DATABRICKS_BASE_URL`, `DATABRICKS_TOKEN` in `config.py` with validation
- [x] `databricks` entry in `providers/catalog.py`
- [x] Unit tests per `test-plan.md`
- [x] `.env.example`, `CLAUDE.md` §4
- [x] Real call against the gateway with a valid `DATABRICKS_TOKEN` (confirm streaming and
      the `system` message are accepted) — 2026-10-12, `glm-5-3-flash-dev-tai`: streamed,
      system prompt followed, reasoning kept out of the text deltas, first delta 1.5–2.0 s
- [ ] Human verification (one real turn, `tests/e2e`, first audio < 3.5 s, end to end < 10 s)
