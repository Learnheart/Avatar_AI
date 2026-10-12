---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: stt, llm
summary: Run STT and LLM through the Databricks AI Gateway, authenticated by a Databricks CLI profile, for networks where Groq is blocked
---

# Databricks STT and profile authentication

## Problem statement

- On the company network Groq is blocked, so neither Whisper (STT) nor the Groq LLM can
  be called. The Databricks AI Gateway is reachable there.
- The gateway has no transcription endpoint. Probed on 2026-10-12: GLM rejects audio;
  Gemini (`gemini-3-6-flash`) transcribes an utterance sent as a `file` or `audio_url`
  part of `/chat/completions` (`input_audio` is rejected on both endpoints). No model
  returns audio, so TTS stays ZeroTTS (local).
- The only credential at hand is the OAuth token of a Databricks CLI profile, which
  expires after about an hour: a static `DATABRICKS_TOKEN` in `.env` stops working.

## Requirements

- `STT_BACKEND=databricks` transcribes through the gateway; with
  `LLM_BACKEND=databricks` a whole turn runs without Groq. Groq stays the default.
- The Databricks backends authenticate with `DATABRICKS_TOKEN`, or else with
  `DATABRICKS_PROFILE`, renewing the token without a restart.
- The `STT` / `LLM` contracts and the engine do not change.

## Decisions made

- **Chosen by configuration, not an automatic fallback.** Trying Groq first would spend
  the STT timeout on every turn of a network that always blocks it.
- **`ChatAudioSTT`** (`providers/stt/chat_audio.py`): one chat completion holding the
  transcription prompt (`prompts.py`, `STT_TRANSCRIBE_PROMPT`) and the utterance as a
  WAV `file` part. The reply text is read from a string or from a list of parts (Gemini
  returns parts). All-zero audio is answered without a call, as in the Whisper backend.
  `STT_REASONING_EFFORT` (default `minimal`) and `STT_MAX_TOKENS` are sent; `temperature`
  is not (the model rejects it).
- **The gateway has its own model keys**, `DATABRICKS_LLM_MODEL` (GLM flash) and
  `DATABRICKS_STT_MODEL` (Gemini flash), with defaults, so switching a machine is two
  backend names. This replaces the use of `LLM_MODEL` decided in
  `../databricks-ai-gateway-llm/plan.md`.
- **The LLM request sends `reasoning.effort`** from `LLM_REASONING_EFFORT` (`low`):
  measured first delta about 0.8 s instead of 2.3 s with GLM.
- **Profile token** (`providers/databricks.py`): an async callable given to the OpenAI
  client as its key. It runs `databricks auth token --profile <name>`, caches the token
  and renews it `DATABRICKS_TOKEN_REFRESH_MARGIN_SEC` before the expiry. A CLI failure
  is an `OpenAIError`, so the backends turn it into `STTError` / `LLMError`.
- **TLS behind the company proxy** is configuration, not code: `SSL_CERT_FILE` (read by
  the OpenAI client) points at a CA bundle holding the proxy's certificate. Certificate
  verification is never disabled.

## Implementation approach

1. `providers/databricks.py` (credential) + unit tests.
2. `providers/stt/chat_audio.py`, catalog entry, config keys and validation + unit tests.
3. `OpenAIResponsesLLM` takes its model and reasoning effort from the catalog.
4. `.env.example`, `CLAUDE.md` §4, real turn through the gateway.

## To-do

- [x] `backend/providers/databricks.py`: static token or CLI profile token
- [x] `backend/providers/stt/chat_audio.py` and the `databricks` STT entry
- [x] `DATABRICKS_PROFILE`, `DATABRICKS_LLM_MODEL`, `DATABRICKS_STT_MODEL`,
      `STT_REASONING_EFFORT`, `STT_MAX_TOKENS`, `DATABRICKS_TOKEN_REFRESH_MARGIN_SEC`
- [x] `STT_TRANSCRIBE_PROMPT` in `prompts.py`
- [x] LLM: own model key and `reasoning.effort`
- [x] Unit tests per `test-plan.md`
- [x] `.env.example`, `CLAUDE.md` §4
- [x] Real turn through the gateway (`tests/e2e`, profile auth) — runs end to end
- [ ] **Latency gate not met**: first audio 4.8–4.9 s (budget 3.5 s) on two runs of
      2026-10-12; end to end 8.3–9.5 s (budget 10 s). STT 1.5–2.3 s, LLM first token
      1.0–2.3 s
- [ ] Transcription quality with a real voice through the microphone (only a synthetic
      voice was tried)
- [ ] Human verification
