---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: knowledge, expression
summary: Tests for choosing which uploaded documents the avatar answers from
---

# Test plan — choosing the knowledge in use

## Unit tests (sandbox)

| Area | File | Cases |
|---|---|---|
| Store | `test_knowledge_store.py` | a new document is enabled; a disabled document is not returned, and the nearest enabled chunks fill the result; enabling it again returns it; unknown id → `None`; the flag survives a reopen; a store created without the column is migrated with its documents enabled |
| Knowledge base | `test_knowledge_base.py` | a disabled document is not found; with no document enabled the question is not embedded; replacing a disabled document enables it |
| API | `test_knowledge_api.py` | `PATCH` → 200 with `enabled`, reflected in the list; unknown id → 404; a body without `enabled` → 422 |
| Frontend | `frontend/tests/knowledge.test.js` | `usageSummary` for none, some and all documents in use |

## End-to-end tests (real environment, `RUN_E2E=1`)

- `tests/e2e/test_real_turn.py::test_turn_answers_from_an_uploaded_document`: after the
  upload the document is unselected and the question is asked — the answer must not
  carry the document's fact; it is selected again and the same question must carry it.
- Manual, in the browser: untick a document, reload the page, the box stays unticked.
