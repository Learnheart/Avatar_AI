---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: knowledge
summary: Name a backup embedding backend (Ollama) that takes over when the primary fails
---

# Backup backend for the embedding

## Problem statement

- The embedding has one backend. When it fails (expired Databricks token, network), a
  turn is answered without the documents and an upload is refused; STT and the LLM have a
  backup, the embedding has none.

## Requirements

- `EMBEDDING_BACKEND_BACKUP` names the catalog backend that takes over when
  `EMBEDDING_BACKEND` fails, like `STT_BACKEND_BACKUP` and `LLM_BACKEND_BACKUP`.
- A local Ollama server is a backend (`ollama`), serving Qwen3-Embedding-0.6B — the model
  the Databricks endpoint serves.
- No backup configured = today's behaviour. The store and the knowledge base do not
  change.

## Decisions made

- **The backup must serve the same model as the primary.** Vectors of two models cannot
  be compared, and the index holds one vector per chunk. With the same model on both
  sides, a query embedded by the backup searches the index built by the primary, so a
  wrapper behind the `Embedder` contract is enough (`FallbackEmbedder` in
  `providers/fallback.py`). It reports the primary's signature: the index is not rebuilt
  when the backup answers. This is a rule for whoever sets the keys; the code cannot
  check that two endpoints serve the same weights.
- **When the backup takes over**: the primary raises `EmbeddingError` (which includes its
  own request timeout, `EMBEDDING_TIMEOUT_SEC`), or the caller gives up on it — the
  engine cancels a lookup after `KNOWLEDGE_TIMEOUT_SEC`. A hung primary therefore costs
  one turn without documents, then is skipped for `FALLBACK_COOLDOWN_SEC`. No shorter
  timeout of its own: an upload embeds many chunks and legitimately takes seconds.
- **`ollama` backend**: the OpenAI-compatible embedder at `OLLAMA_BASE_URL`
  (`http://localhost:11434/v1`) with `OLLAMA_EMBEDDING_MODEL` (`qwen3-embedding:0.6b`).
  It can be a primary too.
- **Defaults stay empty**: no backup unless `EMBEDDING_BACKEND_BACKUP` is set.

## Implementation approach

1. `FallbackEmbedder` + unit tests.
2. `ollama` catalog entry, keys, validation, wrapping in `create_embedder` + unit tests.
3. `.env.example`, `CLAUDE.md`; real run against the local Ollama server.

## To-do

- [x] `FallbackEmbedder` in `backend/providers/fallback.py`
- [x] `ollama` backend, `EMBEDDING_BACKEND_BACKUP`, `OLLAMA_BASE_URL`,
      `OLLAMA_EMBEDDING_MODEL`; catalog wiring
- [x] Unit tests per `test-plan.md`
- [x] `.env.example`, `CLAUDE.md`
- [x] Real run (2026-10-12): primary pointed at an unreachable URL, backup the local
      Ollama server (with `bge-m3:latest`, the embedding model present on the machine):
      the warning appears once, the upload is indexed (0.37 s) and the lookup finds the
      passage (35 ms, score 0.68)
- [ ] Real run with `qwen3-embedding:0.6b` in Ollama: a document indexed through
      Databricks is found by a query embedded by Ollama, with a score close to the
      primary's (the model is not downloaded yet; until this is measured, the
      compatibility of the two endpoints' vectors is an assumption)
- [ ] Human verification
