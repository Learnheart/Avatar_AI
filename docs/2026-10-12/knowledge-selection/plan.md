---
author: linhbk3
date: 2026-10-12
status: in-progress
agents: knowledge, expression
summary: Let the user choose in the panel which uploaded documents the avatar answers from
---

# Choosing the knowledge in use

## Problem statement

- Every uploaded document is searched in every turn. The only way to keep the avatar
  from using one is to delete it, and uploading it again re-indexes it.

## Requirements

- In the "Tài liệu" panel, each document has a checkbox: checked = the avatar may answer
  from it. The panel shows how many documents are in use.
- The choice is kept by the backend: it survives a page reload and a restart, and is the
  same for every browser of the kiosk.
- A document that is not selected is never returned by a lookup; with none selected the
  turn pays no embedding call.
- A new upload is selected. Upload, list and delete keep working as they do.

## Decisions made

- **A flag per document** (`enabled`, default on) in the `documents` table, not named
  collections: it is what the panel needs, and a grouping can be added on top later. A
  store created before this change gets the column at open, with every document enabled.
- **The filter is applied inside the vector search** (`rowid IN (chunks of enabled
  documents)`), so the lookup still returns `KNOWLEDGE_TOP_K` passages when unselected
  documents hold nearer chunks. Vectors are kept: selecting a document again costs
  nothing.
- **API**: `PATCH /knowledge/documents/{id}` with `{"enabled": true|false}` → `200` + the
  document, `404` if unknown. Documents carry `enabled` in every response.
- Uploading a name that exists replaces the document; the new one is selected.

## Implementation approach

1. Store: column + migration, `set_enabled`, filtered search and chunk count; knowledge
   base `set_enabled`; route. Unit tests.
2. Panel: checkbox per row, "in use" summary, client `setEnabled`. Unit tests, build.
3. Docs.

## To-do

- [ ] Store, knowledge base and `PATCH` route with unit tests
- [ ] Panel checkbox and summary with unit tests, `npm run build`
- [ ] `CLAUDE.md` route note, `quality-gates.md`
- [ ] Real run: a turn with the document unselected, then selected
- [ ] Human verification
