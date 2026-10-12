---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: knowledge, llm, orchestrator, expression
summary: Knowledge base for the avatar — upload files in the UI, index them in a lightweight vector DB, answer from them
---

# Knowledge base (RAG) for internal content

## Problem statement

- The avatar answers from the LLM's general knowledge only. It cannot look up internal
  content (policies, product sheets, procedures).
- There is no way to give it documents: `backend/features/` is an empty placeholder.

## Requirements

- A user uploads files in the browser; they become knowledge the avatar answers from.
  The UI also lists the uploaded documents and deletes one.
- Vectors live in a lightweight, embedded vector DB (no server to run) — a first version
  to prove the whole flow: upload → index → retrieve → answer.
- A turn still works when the knowledge base is empty, slow or failing: the avatar then
  answers without it.
- The pieces are replaceable: embedding model by config, vector DB behind one class.
- Works on the company network (Hugging Face, Groq and OpenRouter are blocked there;
  PyPI through the configured index and the Databricks workspace are reachable).

## Decisions made

- **Vector DB: `sqlite-vec`** (a SQLite extension, one wheel, no dependencies). One file
  (`KNOWLEDGE_DB_PATH`) holds the documents, the chunk texts and the vectors, so the
  index survives a restart. Chroma and LanceDB were set aside: far heavier dependency
  trees for the same job at this size. All SQL is in `features/knowledge/store.py`;
  another DB replaces that one file.
- **Embedding is a model stage** like STT / LLM: contract `Embedder` in
  `core/contracts.py`, backends in `providers/embedding/`, chosen by
  `EMBEDDING_BACKEND` in the catalog:
  - `local_hash` (default) — hashed word unigrams and bigrams, computed locally. No
    model, no network, no key: the flow runs on any machine. It matches words, not
    meaning.
  - `openai_compatible` — any `/embeddings` API (`EMBEDDING_BASE_URL`,
    `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`).
  - `databricks` — a workspace embedding endpoint with the Databricks credential
    already used by the LLM/STT. Probed 2026-10-12: `databricks-qwen3-embedding-0-6b`
    (multilingual, 1024 dimensions) answers on `<workspace>/serving-endpoints`; the AI
    Gateway URL refuses it (rate limit 0), hence a separate `DATABRICKS_SERVING_BASE_URL`.
  - A local semantic model was set aside for now: its weights come from Hugging Face,
    which is blocked on this network.
- **Changing the embedding re-indexes at startup.** The store records which embedder
  built its vectors; chunk texts are stored, so a different embedder re-embeds them
  instead of asking the user to upload again. If that fails, startup fails with the
  reason.
- **Retrieval sits in the engine, between STT and LLM**, behind a `Knowledge` contract
  (`search(query) → passages`). The engine puts the passages and the question into
  `KNOWLEDGE_PROMPT` (`prompts.py`) and sends that as the user text, so the `LLM`
  contract, its backends and the fallback wrapper do not change. No passage (empty
  base, nothing above `KNOWLEDGE_MIN_SCORE`, timeout `KNOWLEDGE_TIMEOUT_SEC`, error) →
  the question is sent as it is today.
- **HTTP API beside the WebSocket** (`features/knowledge/api.py`): the WebSocket message
  shapes are untouched.
  - `GET /knowledge/documents` → `{"documents": [{id, name, size_bytes, chunk_count, created_at}]}`
  - `POST /knowledge/documents` (multipart field `file`) → `201` + the document.
    `413` too large, `415` unsupported type, `422` no text in the file, `502` embedding failed.
  - `DELETE /knowledge/documents/{id}` → `204`, or `404`.
- **Files**: `.txt`, `.md`, `.csv`, `.pdf` (text layer only, no OCR), `.docx`. Only the
  extracted text is kept, not the original file. Uploading a name that exists replaces
  that document.
- **Chunking**: paragraphs packed into chunks of `KNOWLEDGE_CHUNK_CHARS` with
  `KNOWLEDGE_CHUNK_OVERLAP_CHARS` carried over; long paragraphs are cut at sentences.
- **Frontend**: a "Tài liệu" button opens a panel (choose or drop files, list, delete).
  It calls the backend at `VITE_API_URL`; the backend allows the page's origin with
  `CORS_ALLOW_ORIGINS`.
- **Not in this version**: authentication on the API, OCR, conversation history, a
  query instruction for the embedding model, hybrid / re-ranked retrieval, showing the
  sources in the UI.

## Implementation approach

```
backend/
  core/contracts.py                    + Embedder, Knowledge
  core/conversation/engine.py          retrieval between STT and LLM
  providers/embedding/local_hash.py    offline lexical embedder
  providers/embedding/openai_compatible.py
  providers/catalog.py                 + create_embedder
  features/knowledge/
    chunking.py                        text → chunks (pure)
    loaders.py                         file bytes → text
    store.py                           sqlite-vec: documents, chunks, vectors
    knowledge_base.py                  ingest / list / delete / search (implements Knowledge)
    api.py                             the HTTP routes
  main.py                              wires embedder → knowledge base → engine, router, CORS
frontend/src/knowledge/                client, panel, pure helpers
```

1. Embedding stage: contract, two backends, catalog, config + unit tests.
2. Knowledge feature: chunking, loaders, store, knowledge base + unit tests.
3. Turn + API: engine retrieval, routes, wiring in `main.py` + unit tests.
4. Frontend panel + unit tests + build.
5. `.env.example`, `CLAUDE.md`, `ARCHITECTURE.md` backlog, quality gate; real run.

## To-do

- [x] `Embedder` contract, `local_hash` and `openai_compatible` backends, catalog entry,
      config keys and validation
- [x] `features/knowledge/`: chunking, loaders, sqlite-vec store, knowledge base
- [x] Engine retrieval and `KNOWLEDGE_PROMPT`
- [x] HTTP routes, CORS, wiring in `main.py`
- [x] Frontend panel: upload, list, delete
- [x] Unit tests per `test-plan.md` (backend + frontend), `npm run build`
- [x] `.env.example`, `frontend/.env.example`, `CLAUDE.md`, `ARCHITECTURE.md` backlog,
      `quality-gates.md`
- [x] Real run (2026-10-12, company network, STT + LLM + embedding through Databricks,
      `tests/e2e/test_real_turn.py::test_turn_answers_from_an_uploaded_document`):
      upload over HTTP 0.75 s; lookup 485 ms, top score 0.74; the spoken answer gives the
      rate of the uploaded document (7,35 %) where the same question without it gets a
      generic range
- [ ] Real turn inside the latency gate. Measured on the Databricks path: first audio
      5.1 s with the document (end to end 6.6 s), 5.8 s without any (end to end 10.3 s)
      — the gate already fails there without the knowledge base (STT alone takes 2.2 s;
      see `../databricks-stt-and-profile-auth/plan.md`). The lookup adds about 0.5 s.
      The Groq path cannot be measured from this network
- [x] The panel opened in a real browser (headless Chrome, 2026-10-12: txt, docx and pdf
      uploaded, listed, deleted) — see `../knowledge-selection/plan.md`
- [ ] Human verification
