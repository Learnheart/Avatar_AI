---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: knowledge, llm, orchestrator, expression
summary: Tests for the knowledge base — embedding stage, indexing, retrieval in the turn, upload API and panel
---

# Test plan — knowledge base

## Unit tests (sandbox, `tests/unit/`, `frontend/tests/`)

No network and no model: the embedder is `local_hash` or a scripted fake; the store is a
real sqlite-vec file in `tmp_path`.

| Area | File | Cases |
|---|---|---|
| Embedding | `test_embedding.py` | `local_hash`: unit-length vectors of `EMBEDDING_HASH_DIM`, same text → same vector across calls, a text sharing words scores above an unrelated one, empty text → zero vector; `openai_compatible`: sends the model and the texts in batches of `EMBEDDING_BATCH_SIZE`, keeps the order, provider error → `EmbeddingError` |
| Catalog / config | `test_embedding.py`, `test_config.py` | each `EMBEDDING_BACKEND` builds its class; unknown name raises; `openai_compatible` requires `EMBEDDING_BASE_URL`; `databricks` requires the serving URL and a credential |
| Chunking | `test_knowledge_chunking.py` | short text → one chunk; paragraphs packed up to the size; overlap carried into the next chunk; an oversized paragraph is cut at sentences, an oversized sentence hard-cut; blank text → no chunk |
| Loaders | `test_knowledge_loaders.py` | txt / md / csv decoded (UTF-8, BOM); docx and pdf text extracted; unsupported extension → `UnsupportedDocument`; broken pdf → `UnreadableDocument` |
| Store | `test_knowledge_store.py` | add → listed with its chunk count; search returns the nearest chunk with source and score; delete removes document, chunks and vectors; the data survives close + reopen; `replace_vectors` changes dimension and signature |
| Knowledge base | `test_knowledge_base.py` | add → search finds the passage; same name replaces; empty file → `EmptyDocument`; empty base → no embedding call; `KNOWLEDGE_MIN_SCORE` filters; embedder failure on add → `KnowledgeError`, nothing stored; a different embedder at start re-indexes |
| Engine | `test_engine.py` | passages → the LLM receives `KNOWLEDGE_PROMPT` with passages and question; no passage → the bare question; a failing or slow knowledge base → the bare question, turn completes; no knowledge wired → unchanged |
| API | `test_knowledge_api.py` | upload → 201 and listed; delete → 204 then 404; 413, 415, 422, 502; CORS header for an allowed origin |
| Frontend | `frontend/tests/knowledge.test.js` | `formatBytes`, `isSupportedFile`, `apiErrorMessage` |

## End-to-end tests (real environment)

- **Knowledge flow** (`tests/e2e/test_real_knowledge.py`, `RUN_E2E=1`, needs the
  embedding backend configured): start the app, upload a document holding a made-up fact
  over HTTP, check the retrieval returns it for a Vietnamese question, and that the LLM
  answer contains the fact. Report the retrieval time.
- **Real turn** (`tests/e2e/test_real_turn.py`) with a document loaded: first audio under
  3.5 s, end to end under 10 s.
- **Manual, in the browser**: open the panel, upload a PDF and a DOCX, see them listed,
  ask a question by voice, delete one.
