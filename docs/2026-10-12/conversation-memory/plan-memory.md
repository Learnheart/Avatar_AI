---
author: linhbk3
date: 2026-10-12
status: in-progress
agent: avatar
modules: memory, llm
summary: Conversation memory, the long-term part — the Memory contract, memory items and the profile given to the LLM, gathering from conversations, import, the HTTP API
---

# Conversation memory — the `Memory` contract and long-term memory

## Scope

Part of [Conversation memory](plan.md): the problem, the requirements, the diagrams of
the whole system and the slices are there. This file holds what the avatar
keeps about a logged-in user across conversations. Its tables and the object store are in
`plan-database.md`, its page in `plan-frontend.md`.


## Decisions made

### The `Memory` contract

- The core reaches the memory through one contract, `Memory` (`core/contracts.py`),
  implemented by `features/memory/` and wired in `main.py`:

  | Call | Made by | When |
  |---|---|---|
  | `profile(user id)` | the engine | each turn of a logged-in user, before the LLM; answered from the cache, bounded by `MEMORY_TIMEOUT_SEC` |
  | `record_turn` | the orchestrator | after the audio was sent, not awaited by the turn |
  | `close_conversation` | the orchestrator | when the conversation closes, not awaited |

- A guest: no profile read, nothing recorded, nothing to do at the close.
- The memory never costs the turn: empty, slow or failing, the turn runs without it.
- Slice 2 builds `profile` alone; `record_turn` and `close_conversation` join the
  contract with slice 4. `profile` returns the text given to the LLM ("" when nothing
  is known) together with the number of items it was written from, which the engine
  puts on its `memory` span. The error of the contract is `MemoryServiceError`
  (`MemoryError` is a Python builtin).
- The engine does not know who speaks: the orchestrator hands `respond` the user id of
  the turn's conversation, next to its history.

### Long-term memory: items

- A **memory item** is one short Vietnamese sentence about the user:
  `id, user_id, category, text, status, source, import_id, evidence, replaces_id,
  seen_count, created_at, updated_at`.
  - `category`: `identity` (name, role), `addressing` (anh / chị / em, how the avatar
    calls itself), `preference` (answer style, topics), `personality`.
  - `source`: `manual` | `import` | `conversation`.
  - `status`: `active` (given to the LLM) | `pending` (proposed, not used) | `rejected`
    (a tombstone, see below).
- **The text of an item is one line of plain characters**, whatever wrote it (the
  profile page now, an import or the extractor later): the service normalises it on add
  and on update, before the length check. Every run of whitespace, line breaks and tabs
  included, becomes one space; control characters and invisible format characters (NUL,
  zero-width space, ...) are removed; a text with nothing left is refused like an empty
  one. An item is rendered as one `- …` line of the profile message, so a line break in
  it could write further lines of that message, or its rules. Letters and accents are
  stored as given, composed (NFC) or not (NFD).
- **Only `active` items reach the LLM.** Which source may write which status:

  | Source | Status when created | Why |
  |---|---|---|
  | `manual` | `active` | the user typed it |
  | `import` | `active` | a written source, no STT noise; the profile page shows the result at once for correction |
  | `conversation` | `pending` | heard through STT and inferred by an LLM |

- A `pending` item becomes `active` when the user approves it on the profile page, or when a
  **later** conversation states the same thing again (`seen_count` ≥
  `MEMORY_PROMOTE_SEEN_COUNT`, default 2). Repetition promotes `preference` and
  `personality` only. An `identity` or `addressing` item heard in a conversation stays
  `pending` until it is approved, however often it is heard: STT mishears a name the
  same way every time, so hearing it twice proves nothing, and these are the items
  placed first in the prompt. A conversation never overwrites an `active`
  item: a contradiction becomes a `pending` item with `replaces_id`, and approving it
  replaces the old one.
- Deleting an item that came from a conversation, or rejecting a pending one, keeps it
  as `rejected`: it is not listed and not used, and the extractor is told not to propose
  it again. Items of the other sources are deleted for good.
- Asking back by voice ("ý anh là X đúng không?") is not built: the profile page does that job
  without spending a spoken turn.
- **Reading**: all `active` items of the user go into `MEMORY_PROMPT`
  (`prompts.py`), `identity` and `addressing` first, then the most recently updated, up
  to `MEMORY_PROMPT_MAX_ITEMS`. No vector search: a profile is tens of items, and "what
  the profile page shows is what the LLM gets" is easy to reason about. The service caches the
  profile per user and drops the cache on every write, so a turn reads from RAM and an
  edit on the profile page applies to the next turn. A miss is bounded by
  `MEMORY_TIMEOUT_SEC`; on timeout or error the turn runs without the profile.
  The order is `identity`, then `addressing`, then the other categories, the most
  recently updated first inside each; an empty profile is cached too. A profile read
  from the store while one of the user's items was being written is not cached.
- A category outside the four, a text that is empty once trimmed or one longer than
  `MEMORY_ITEM_MAX_CHARS` (an item is one short sentence, and every active item is in
  every prompt) is refused by the service (`InvalidMemoryItem`, a `422` on the routes),
  on add and on update, whatever the caller; the stored text is trimmed.
- `MEMORY_PROMPT` is the items between an introduction and three rules. The person
  types the items in their own voice ("Gọi tôi là chị Linh, xưng em"), so the text says
  that "tôi" in an item is the person, that the way of addressing they ask for binds
  every answer (also when they say "bạn" to the avatar), that the facts are theirs and
  known, and that no profile is mentioned. It names the avatar "trợ lý", never "bạn".
  The wording was chosen by replaying a recorded request on the real model (counts in
  the slice 2 report); it is changed only with a new measurement.
- The cache holds one entry per user seen and is never evicted: enough for the one
  development user, and to be bounded when the login brings many users.
- **Storage**: the application database. All the SQL of the memory is in
  `features/memory/store.py`; calls run in a thread.

### Gathering from conversations (consolidation)

- For a logged-in user each recorded turn is also written to the database, after the
  audio was sent and without being awaited by the turn.
- When the conversation closes, one background LLM call receives the transcript and the
  user's existing items (active, pending, rejected) and returns JSON operations:
  `add` (a new fact), `confirm` (an existing pending item was stated again), `replace`
  (contradicts an existing item). The prompt (`MEMORY_EXTRACT_PROMPT`) keeps only what
  the user said about themselves that stays true across visits, each with the quote it
  comes from (`evidence`).
- User turns whose STT confidence is below `MEMORY_MIN_STT_CONFIDENCE` are left out of
  the transcript sent to the extractor. A backend that reports no confidence is not
  filtered; its items are `pending` like all the others. Confidence only excludes, it
  never promotes: a misheard name often has a high score.
- The stored turns are deleted once the conversation is consolidated; only the evidence
  quotes remain. A conversation left unconsolidated (process killed, LLM failure) is
  consolidated at the next start; turns older than `MEMORY_TRANSCRIPT_TTL_DAYS` are
  deleted either way.

### `LLM.complete`

- Extraction needs a call without the persona and without streaming: the `LLM` contract
  gains `complete(system, user) -> str`, implemented by both backends and
  `FallbackLLM`. Invalid JSON or a failure → no item, an error on the span.
- Slice 3: `complete` is an abstract method of the contract. It sends `system` in place
  of the persona and `user` as the one user message, is not streamed and returns the
  answer text alone (no reasoning text). It has its own limits, because those of
  `stream_answer` are sized for a spoken answer: `LLM_COMPLETE_TIMEOUT_SEC` (default
  30) and `LLM_COMPLETE_MAX_TOKENS` (default 2048).
- `FallbackLLM.complete` gives the primary `LLM_COMPLETE_TIMEOUT_SEC` and asks the
  backup when it fails or is too slow. Such a failure does **not** start the cooldown
  the turns share (a background request never degrades the conversation); a primary the
  turns already put in cooldown is skipped. For that to hold, `main.py` builds one LLM
  and gives it to both the engine and the imports.

### Import

- **The original is the single source of truth.** An import is a file (`.txt`, `.md`,
  `.csv`, `.pdf`, `.docx`, up to `MEMORY_IMPORT_MAX_FILE_MB`) or a pasted text, which is
  stored as a `.txt` object. The bytes are kept unchanged in an object store; the
  extracted text is derived from them on demand and stored nowhere.
- The original is kept in the object store of `plan-database.md`, under the key
  `imports/<import_id>`; who owns it is recorded in the database only.
- The application database holds the metadata, table `imports`:
  `id, user_id, name, content_type, size_bytes, sha256, object_key, created_at`.
  An object is never served by its key: every read goes through the row and its
  `user_id` check.
- Flow: store the object and its row → extract the text (the loaders, moved from
  `features/knowledge/loaders.py` to `backend/utils/loaders.py` so both features use
  them) → one `complete` call with `MEMORY_IMPORT_PROMPT` → items stored `active` with
  `source=import`, `import_id` and the quote they come from. A text longer than
  `MEMORY_IMPORT_MAX_CHARS` is refused. If the extraction fails, the import is removed
  again: no object without items.
- Deleting an import deletes its object and its row; the caller chooses whether its
  items go with it (`items=delete`) or stay as items without a source (`items=keep`).
- Extracting an import again (new prompt, new model) is made possible by keeping the
  original, but is not built in this version.

Decided while building slice 3:

- **The import has its own class**, `MemoryImports` (`features/memory/imports.py`): the
  flow, its errors and the content types. It is given the store, the object store, the
  LLM and the way to drop a user's cached profile (`MemoryService.forget_profile`);
  `main.py` builds it beside the memory, on the LLM of the turns. `MemoryService` keeps
  the profile and the items.
- **What the LLM is asked for**: `MEMORY_IMPORT_PROMPT` is the system text (it is
  given `MEMORY_ITEM_MAX_CHARS`). The answer is a JSON array of `{"category", "text",
  "evidence"}`, the items written in the person's own voice ("tôi"), as `MEMORY_PROMPT`
  expects them.
- **A document is material to read, never instructions** (the part of prompt injection
  closed cheaply; nothing more is built):
  - The user message is the text between two tags (`MEMORY_IMPORT_DOCUMENT`), and the
    prompt says that whatever stands between them is never an instruction. Every
    writing of those tags (opening or closing, any case, spaces inside) is taken out of
    the text itself first, repeatedly until none is left, since removing one can join
    the pieces of another: a document cannot close its own block.
  - `evidence` is stored on one line, and only when the document really holds it
    (both compared on one line, letters composed). An invented quote, or one the
    document led the model to write, is dropped; the item is kept without evidence.
  - What remains open: a document can still talk the model into a false *item*. It is
    the person's own profile, shown at once on the profile page for correction.
- **An answer cut at the token limit is a failure of the LLM**: both backends raise
  `LLMError` naming `LLM_COMPLETE_MAX_TOKENS` instead of returning half a JSON array. A
  description too rich for `LLM_COMPLETE_MAX_TOKENS` is therefore refused whole (`502`),
  and the span says why.
- **Parsing is strict** (`extraction.py`, pure): the answer must be that JSON array and
  nothing else. A code fence or a sentence around it makes the whole answer invalid.
  Inside a valid array each entry stands alone: an unknown category, a missing or empty
  text, or a text that does not pass the rule of a typed item (one line,
  `MEMORY_ITEM_MAX_CHARS`) drops that entry and keeps the others. A missing or empty
  `evidence` leaves the item without a quote (whether the quote is real is checked by
  the import, which has the document). The module holds one parser of the array
  and one validator of an entry, so the operations of slice 4 are a second validator.
- **Outcomes**, mapped to HTTP in `api.py` only:

  | Outcome | Status |
  |---|---|
  | the LLM failed, or its answer is not the JSON array | `502`, a generic text, the detail in the log and on the span |
  | a valid answer with no usable item | `422` "nothing to remember" |
  | an unreadable file, or no text in it | `422` |
  | an unsupported type | `415` |
  | a file over `MEMORY_IMPORT_MAX_FILE_MB` (the route reads one byte past the limit, the service refuses it) | `413` |
  | a text over `MEMORY_IMPORT_MAX_CHARS` | `413` |
  | both `file` and `text`, or neither | `422` |

  In every one of them no object, no row and no item remain. The type (the extension)
  and the file size are checked before anything is written. The object and its row are
  then written first, as the flow says, and removed again by whatever stops the import,
  a cancelled request included: a store call that is running is waited for, and what it
  wrote is removed after it, so there is never an object without a row or a row
  without an object. A client that goes away does not cancel the request: the server
  goes on reading and the import is stored whole, which is what the profile page
  relies on when it is closed during a reading (seen in the real run). The removal drops the cached profile like any other write; when
  the removal itself fails, the error is logged and the one that stopped the import
  reaches the caller. While the LLM reads (up to `LLM_COMPLETE_TIMEOUT_SEC`) the import
  is listed with 0 items. A process killed in between leaves such an import, which can
  be deleted.
- **Limits enforced by the framework, not by this code**: the size limit of an upload
  is checked after the body was received (the multipart body is spooled first), and a
  pasted text field over the framework's 1 MB field limit is refused with `400`. The
  second only matters if `MEMORY_IMPORT_MAX_CHARS` is raised that far.
- **Defaults**: `MEMORY_IMPORT_MAX_FILE_MB=5`, `MEMORY_IMPORT_MAX_CHARS=20000`.
- **Items of an import**: `source=import`, `status=active`, `import_id`, `evidence` =
  the quote; listed in the order of the answer.
- **A pasted text** is stored as a `.txt` object of type `text/plain`; its display name
  is `MEMORY_IMPORT_TEXT_NAME` (`prompts.py`). It is read back through the same loader
  as an uploaded `.txt`.
- **The name of a file** is its last path part on one line; its content type is taken
  from its extension, never from what the browser sent.
- **Reading the original** always goes through the row and its `user_id`. The bytes
  come with the stored content type, `Content-Disposition: attachment` with the name
  and `X-Content-Type-Options: nosniff`: bytes a user supplied are never rendered
  inline from the origin of the API.
- **Delete**: `?items=keep|delete`, default `keep`, any other value `422`. `keep`
  leaves the items with `import_id` null. The object is removed before the row: when
  that fails nothing has changed, and when the row cannot be removed afterwards the
  import is still listed and deleting it again finishes the job.
- **The list** is `{"imports": [{id, name, size_bytes, item_count, created_at}]}`,
  oldest first like the items; `item_count` counts the items that still name the import.
- An import, its deletion and the removal of a stopped import drop the cached profile
  of the user.
- Not limited in this version: the number of items one import may add (bounded by
  `LLM_COMPLETE_MAX_TOKENS` only), and an item an import repeats from an earlier one.

### HTTP API (`features/memory/api.py`)

Scoped to `current_user_id()`; a guest gets `401` on every route.

- `GET /memory/items` → `{"items": [...]}` (active and pending)
- `POST /memory/items` `{category, text}` → `201` + the item
- `PATCH /memory/items/{id}` `{text?, category?, status?: "active"}` → the item, or `404`
- `DELETE /memory/items/{id}` → `204`, or `404`
- An item is `{"id", "category", "text", "status", "source", "import_id",
  "replaces_id", "evidence", "created_at", "updated_at"}`, `null` where not set, the
  timestamps in ISO 8601 UTC. An item of another user is a `404`. An unknown category
  or an empty text → `422`; a store failure → `502` with a generic text, the detail in
  the log.
- Slice 2: the `POST` body takes `category` and `text` only and the `PATCH` body `text`
  and `category` only; any other field, `status` included, or (for `PATCH`) no field at
  all is a `422`. `status` arrives with the
  approval of slice 4. A deleted item is deleted for good (all items are `manual`).
- `GET /memory/imports` → `{"imports": [{id, name, size_bytes, item_count, created_at}]}`
- `POST /memory/imports` (multipart: field `file`, or field `text`) → `201` +
  `{"import": {...}, "items": [...]}`; `413` too large, `415` unsupported type, `422`
  unreadable or nothing to remember in it, `502` the LLM failed
- `GET /memory/imports/{id}/content` → the original bytes, or `404`
- `DELETE /memory/imports/{id}?items=keep|delete` → `204`, or `404`

### Life of a memory item

```mermaid
stateDiagram-v2
    [*] --> active: typed on the profile page
    [*] --> active: extracted from an import
    [*] --> pending: proposed after a conversation

    pending --> active: approved on the profile page
    pending --> active: stated again in a later conversation (preference, personality)
    pending --> rejected: rejected on the profile page

    active --> active: edited on the profile page
    active --> rejected: deleted, source is a conversation
    active --> [*]: deleted, source is typed or import
    active --> [*]: replaced by an approved proposal

    rejected --> rejected: shown to the extractor, never proposed again

    note right of active
        Only active items are given to the LLM.
    end note
```

### Tunables

In `backend/tuning.py`, each overridable from `.env`: `MEMORY_PROMPT_MAX_ITEMS`
(default 20, at least 1), `MEMORY_TIMEOUT_SEC` (default 1.0, positive and finite),
`MEMORY_ITEM_MAX_CHARS` (default 200, at least 1), `MEMORY_PROMOTE_SEEN_COUNT` (default 2), `MEMORY_MIN_STT_CONFIDENCE`,
`MEMORY_TRANSCRIPT_TTL_DAYS`, `MEMORY_IMPORT_MAX_FILE_MB` (default 5, positive),
`MEMORY_IMPORT_MAX_CHARS` (default 20000, at least 1), `LLM_COMPLETE_TIMEOUT_SEC`
(default 30, positive and finite), `LLM_COMPLETE_MAX_TOKENS` (default 2048, at least 1). The
texts `MEMORY_PROMPT` (with `MEMORY_PROMPT_ITEM`, the line of one item),
`MEMORY_EXTRACT_PROMPT` and `MEMORY_IMPORT_PROMPT` (with `MEMORY_IMPORT_DOCUMENT`, the
delimiters its text is given in, and `MEMORY_IMPORT_TEXT_NAME`, the name of a pasted text) are in `backend/prompts.py` only.

### Observability

- A `memory` span in the engine for the profile read: `memory.items` and
  `memory.outcome` (`hit`, `empty`, `timeout`, `error`). A guest's turn has none.
- Consolidation and import are their own traces (`memory.consolidate`,
  `memory.import`) with the transcript / text in and the operations out.
- `memory.import` (slice 3) carries `import.name`, `import.bytes`, `import.text` (the
  whole extracted text), `llm.answer` (what the model said, also when it was refused),
  `import.items` (the stored items as JSON), `import.id`, `memory.items` and
  `memory.outcome`: `stored`, `too_large`, `unsupported`, `unreadable`, `empty`,
  `nothing`, `extraction_failed` or `error`. A failed import is a failed span.

## Implementation approach

```
backend/
  models.py                          + MemoryItem
  core/contracts.py                  + Memory; LLM.complete
  core/conversation/engine.py        profile read
  core/orchestrator/orchestrator.py  record_turn, close_conversation through the contract
  providers/llm/*.py, fallback.py    complete
  features/memory/
    store.py                         items, imports, conversations, turns
    memory.py                        MemoryService: profile cache, items, record, consolidate
    imports.py                       MemoryImports: file / pasted text → object + row + items
    extraction.py                    LLM JSON → operations (pure parsing + validation)
    api.py                           the HTTP routes
  utils/loaders.py                   moved from features/knowledge/ (file bytes → text)
  prompts.py                         MEMORY_PROMPT, MEMORY_EXTRACT_PROMPT, MEMORY_IMPORT_PROMPT
  main.py                            wiring, router, startup consolidation
```

## To-do

- [x] Slice 2 — `Memory` contract, `features/memory/store.py`, `MemoryService` profile
      with its cache, `MEMORY_PROMPT`, engine read with timeout
- [x] Slice 2 — memory routes, wiring in `main.py`
- [ ] Slice 2 — `MEMORY_PROMPT` measured again on the real model: a profile with no
      addressing item must not make the avatar pick "anh" / "cô" by itself (seen in 3 of
      8 answers), and the answer keeps the addressing asked for (34 of 40 today)
- [ ] Slice 2 — decide what happens to active items beyond `MEMORY_PROMPT_MAX_ITEMS`:
      they are listed on the profile page and not given to the LLM
- [x] Slice 3 — loaders moved to `utils/loaders.py` with the knowledge base still passing
      its tests
- [x] Slice 3 — `LLM.complete`, `extraction.py`, `MEMORY_IMPORT_PROMPT`, table
      `imports`, `/memory/imports` routes
- [ ] Slice 3 — `MEMORY_IMPORT_PROMPT` and `LLM_COMPLETE_TIMEOUT_SEC` measured on the
      model of the deployment (`tests/e2e`): checked by hand on a local qwen3:8b only,
      where a first request with the model not loaded yet ran past the 30 s
- [ ] Slice 4 — turn persistence, consolidation at close and at startup, transcript
      deletion and TTL, `MEMORY_EXTRACT_PROMPT`
- [ ] Slice 4 — pending / confirm / replace / rejected rules (repetition promotes
      `preference` and `personality` only), approval through `PATCH /memory/items/{id}`
- [ ] Unit tests per `test-plan.md`: LLM backends and Fallback (`complete`), Engine
      (profile), Loaders, Imports, Store, Memory service, Extraction, Consolidation, API,
      Config
