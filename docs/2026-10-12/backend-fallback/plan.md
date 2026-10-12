---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: stt, llm
summary: Name a backup backend for STT and for the LLM that takes over when the primary fails
---

# Backup backends for STT and LLM

## Problem statement

- A backend is fixed at startup. When its provider cannot be reached (Groq is blocked on
  the company network), STT fails the turn and the LLM speaks the apology text; nothing
  switches to the Databricks backends that would work there.

## Requirements

- Two keys per stage: the primary (`LLM_BACKEND`, `STT_BACKEND`) and its backup
  (`LLM_BACKEND_BACKUP`, `STT_BACKEND_BACKUP`).
- Any entry of the catalog can be a backup, so a provider added later needs no fallback
  code: the pairing is changed in `.env`.
- No backup configured = today's behaviour. The contracts and the engine do not change.

## Decisions made

- **Wrappers behind the contracts** (`providers/fallback.py`): `FallbackSTT` and
  `FallbackLLM` hold a primary and a backup and are built by the catalog when a backup
  is named (and differs from the primary).
- **When the backup takes over**: the primary raises its provider error (`STTError`,
  `LLMError`), or does not answer within `FALLBACK_PRIMARY_TIMEOUT_SEC` (3 s) — the whole
  transcription for STT, the first delta for the LLM. The engine's own stage timeouts
  (8 s) would otherwise expire before a backup could run.
- **An answer that has started is not replayed**: an LLM failure after the first delta
  reaches the caller. A `ValueError` (bad input) never triggers the backup.
- **Cooldown**: after a failure the primary is skipped for `FALLBACK_COOLDOWN_SEC`
  (300 s), then tried again. A blocked provider costs one slow turn, not every turn, and
  the primary comes back by itself when the network changes.
- **Validation covers the backup**: a backup backend needs its URL / key like a primary.
- **Defaults stay empty.** The Databricks gateway URL is per workspace, so a default
  backup would make it a required setting for every machine.
- TTS has one backend (local): no backup key.

## Implementation approach

1. `providers/fallback.py` + unit tests with scripted backends.
2. Keys in `config.py` / `tuning.py`, validation, catalog wiring + unit tests.
3. `.env.example`, `CLAUDE.md` §4, real turn with Groq blocked and Databricks as backup.

## To-do

- [x] `FallbackSTT`, `FallbackLLM` in `backend/providers/fallback.py`
- [x] `STT_BACKEND_BACKUP`, `LLM_BACKEND_BACKUP`, `FALLBACK_PRIMARY_TIMEOUT_SEC`,
      `FALLBACK_COOLDOWN_SEC`; validation of the backup's settings
- [x] Catalog builds the wrapper when a backup is named
- [x] Unit tests per `test-plan.md`
- [x] `.env.example`, `CLAUDE.md` §4
- [x] Real turn on the company network (Groq → HTTP 503, Databricks backup answered) —
      2026-10-12; the turn that discovers the failure had first audio 6.6 s
- [ ] Latency gate on the backup path (see `../databricks-stt-and-profile-auth/plan.md`)
- [ ] Real turn with a reachable Groq and a backup configured (no regression on the
      primary path) — Groq cannot be reached from this network
- [ ] Human verification
