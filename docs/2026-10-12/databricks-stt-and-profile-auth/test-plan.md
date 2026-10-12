---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: stt, llm
summary: Tests for the chat-audio STT backend and the Databricks profile credential
---

# Test plan — Databricks STT and profile authentication

## Unit tests (sandbox, `tests/unit/`)

`AsyncOpenAI` and the CLI subprocess are replaced by fakes; no network, no CLI.

| Area | File | Cases |
|---|---|---|
| STT request | `test_stt_chat_audio.py` | model, prompt, `max_completion_tokens`, `reasoning_effort`; the utterance is a valid 16 kHz mono WAV in a `file` part; client built with the gateway URL, the key, `STT_TIMEOUT_SEC`, `GROQ_MAX_RETRIES` |
| STT reply | `test_stt_chat_audio.py` | text from a string, from parts (non-text parts ignored), `None`, no choices |
| STT contract | `test_stt_chat_audio.py` | empty / silent audio → empty text without a call; bad sample rate → `ValueError`; provider error → summarized `STTError` |
| Credential | `test_databricks_auth.py` | static token wins; profile token fetched once and reused; renewed inside the margin; concurrent requests share one CLI call; unknown expiry not cached; CLI failure, bad output, missing CLI → `DatabricksAuthError` (an `OpenAIError`) |
| Catalog | `test_catalog.py` | `databricks` STT and LLM use the gateway URL and their own model keys; the profile token is the key when no static token is set |
| Validation | `test_config.py` | either stage on `databricks` requires the URL and a token or a profile; no Groq key needed when no stage uses Groq |
| LLM request | `test_llm_responses.py` | model from `DATABRICKS_LLM_MODEL`, `reasoning.effort` from `LLM_REASONING_EFFORT` |

## End-to-end tests (real environment, `RUN_E2E=1`)

Run `tests/e2e` with `LLM_BACKEND=databricks`, `STT_BACKEND=databricks`,
`DATABRICKS_BASE_URL`, `DATABRICKS_PROFILE` (and `SSL_CERT_FILE` behind the proxy). The
tests build every stage through the catalog, so they need no change. Targets unchanged:
first audio under 3.5 s, end to end under 10 s, STT and LLM first token under 1.5 s.

Not automated: an hour-long session (token renewal against the real CLI), and
transcription of a real voice through the microphone.
