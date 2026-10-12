---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: knowledge
summary: Tests for the backup embedding backend
---

# Test plan — backup embedding backend

## Unit tests (sandbox, `tests/unit/`)

Scripted embedders (vectors, error, hang); the cooldown clock is patched.

| Area | File | Cases |
|---|---|---|
| Wrapper | `test_fallback.py` | healthy primary answers, backup not called; backup on `EmbeddingError`; primary skipped during the cooldown and retried after it; a caller that gives up on a hung primary puts it in cooldown; the backup's error reaches the caller; the signature is the primary's |
| Catalog | `test_embedding.py` | `ollama` uses `OLLAMA_BASE_URL` and `OLLAMA_EMBEDDING_MODEL`; a named backup wraps the primary; empty or identical backup builds the primary alone; unknown backup raises |
| Validation | `test_config.py` | a backup needs its settings like a primary |

## End-to-end tests (real environment)

- Local Ollama, primary pointed at an unreachable URL, `EMBEDDING_BACKEND_BACKUP=ollama`:
  a document is uploaded and found; the `Primary backend failed` warning appears once.
- Once `qwen3-embedding:0.6b` is pulled: index a document through Databricks, stop the
  primary (bad URL), ask — the passage is found with a score close to the primary's.
