---
author: linhbk3
date: 2026-10-12
status: todo
agent: avatar
modules: memory, llm, stt, orchestrator, expression
summary: Tests for the conversation memory — history in the turn, conversation boundary and reset, transcript window, memory items, import, consolidation, API and profile page
---

# Test plan — conversation memory

## Unit tests (sandbox, `tests/unit/`, `frontend/tests/`)

No network and no model: the LLM and STT are scripted fakes (`tests/unit/fakes.py`), the
application database is a real SQLite file in `tmp_path` (`DATABASE_URL`), time is injected.

| Area | File | Cases |
|---|---|---|
| History | `test_history.py` | a conversation carries its id and its user id (`None` for a guest); append keeps the order; past `MEMORY_HISTORY_MAX_TURNS` the oldest turn is dropped; a turn holds the sentences it is given and its `interrupted` flag; a turn with a transcript and an empty answer is kept; a turn without a transcript is not recorded; as chat messages: a turn with an empty answer gives a user message alone, user messages that follow each other are joined (the current utterance included), the roles always alternate |
| LLM backends | `test_llm.py`, `test_llm_responses.py` | history becomes alternating user / assistant messages before the current utterance; `profile` is a second system message after `SYSTEM_PROMPT`; no history and no profile → the request of today; `complete` sends the given system text, not the persona, and returns the whole text; provider error → `LLMError` |
| Fallback | `test_fallback.py` | history and profile reach the backup unchanged; `complete` falls back on failure and timeout |
| STT | `test_stt.py`, `test_stt_chat_audio.py` | `verbose_json` segments → confidence in 0–1; no segments → `None`; chat-audio backend → `None`; the text is unchanged; every segment above `STT_NO_SPEECH_MAX_PROB` → empty transcript, one segment under it → the text; the default (1.0) refuses nothing |
| Engine | `test_engine.py`, `test_engine_tracing.py` | the LLM receives the history and the profile; with knowledge passages only the current utterance is wrapped; a slow or failing memory → the turn completes without profile; a guest → no memory read; `TranscriptEvent` carries the confidence; `memory` span and `llm.history_turns`; the knowledge search query is the previous `KNOWLEDGE_QUERY_HISTORY_TURNS` utterances + the current one while the LLM is asked the current one, with 0 or no history the current utterance alone, and the `knowledge` span carries the query searched; the sentence of `LLM_FALLBACK_TEXT` is marked on its event, a real answer is not |
| Conversation boundary | `test_orchestrator.py` | first utterance opens a conversation; a second turn sees the first; closes after `CONVERSATION_ABSENCE_SEC` without a face, after `CONVERSATION_IDLE_SEC` of silence, on a change of user; not closed by a reconnect of the same user, by a short absence, by absence with the camera disconnected, or while a turn runs; barge-in records the turn as interrupted; closing a guest's conversation writes nothing and calls no LLM; the history given to the engine is the one of the turn's user, and a turn after a change of user sees none of the previous user's turns |
| Recording a turn | `test_orchestrator.py` | a completed turn records every sentence; a barge-in while speaking records the sentences that had started to play at the cut (time injected: after the first sentence of three → one sentence) and `interrupted`; a barge-in while thinking, a timeout and a failure after the transcript record the utterance with an empty answer; the same before the transcript record nothing; an LLM failure records the utterance and not the fallback text; a sentence whose TTS failed is not in the answer; two utterances cut by a barge-in while thinking reach the LLM as one user message |
| Reset and wire messages | `test_orchestrator.py`, `test_ws_manager.py` | `conversation_reset` while idle closes the conversation and the next turn sees no history and has a new id; during a turn it sends `interrupt`, returns to `idle`, records the turn as interrupted in the conversation being closed, then closes it; with no conversation open it changes nothing and sends no error; a logged-in user's reset conversation is handed to the memory like any close; `conversation` is sent with the id on open, with `null` on every kind of close, and after `connected` (the open id on a reconnect of the same user, else `null`); `transcript` is sent once per turn with the text, and not for an empty transcript; the shapes of the two new messages |
| Database | `test_database.py` | the tables are created in an empty file at startup; a second start changes nothing and keeps the data; an unreachable `DATABASE_URL` fails startup with the reason; no store module imports `sqlite3` or names an engine |
| Identity | `test_identity.py` | empty `AUTH_DEV_USER_ID` → guest, no user row; a value → that user, row created once and `last_seen_at` updated |
| Object store | `test_object_store.py` | put then get returns the same bytes; delete removes them, a second delete is harmless; a missing key → not found; a key cannot leave the store directory (`..`, absolute path); survives a new instance on the same directory |
| Loaders | `test_knowledge_loaders.py` | unchanged cases, imported from `backend/utils/loaders.py` |
| Imports | `test_memory_imports.py` | a file → object stored unchanged (sha256 matches), row written, items `active` with `import_id` and evidence; a pasted text → a `.txt` object; the extracted text is in no table; LLM failure or nothing extracted → no object, no row, no item; a text over `MEMORY_IMPORT_MAX_CHARS` refused; delete with `items=delete` removes object, row and items, with `items=keep` the items stay without `import_id`; one user cannot read or delete another's import |
| Store | `test_memory_store.py` | add / list / update / delete of items per user; one user never sees another's items; turns and conversations written and read back; survives close + reopen; turns deleted on consolidation and past the TTL |
| Memory service | `test_memory_service.py` | profile = active items only, `identity` and `addressing` first, capped at `MEMORY_PROMPT_MAX_ITEMS`; second read hits the cache; a write drops it; manual and import items are `active`, conversation items `pending` |
| Extraction | `test_memory_extraction.py` | valid JSON → operations; unknown category, missing text, an id that does not exist → that operation dropped; invalid JSON or prose around it → no operation; empty list accepted |
| Consolidation | `test_memory_consolidation.py` | `add` → pending with its evidence; `confirm` in a later conversation → `seen_count` up and `active` at `MEMORY_PROMOTE_SEEN_COUNT`; `confirm` inside the same conversation does not promote; an `identity` or `addressing` item is never promoted by `confirm`, whatever its `seen_count`, and becomes `active` on approval only; `replace` → pending with `replaces_id`, the active item untouched until approval; rejected items are sent to the extractor and not re-added; turns under `MEMORY_MIN_STT_CONFIDENCE` are left out, `None` confidence kept; LLM failure → nothing stored, conversation stays unconsolidated; an unconsolidated conversation is picked up at start |
| API | `test_memory_api.py` | guest → 401 on every route; create → 201 and listed; patch text / category; approving a pending item, with and without `replaces_id`; delete → 204 then 404, a conversation item becomes `rejected` and disappears from the list; imports: upload a file and a text → 201 with the import and its items, listed with `item_count`, content returns the original bytes, delete with both `items` values, 404 on another user's import; 413, 415, 422, 502 |
| Config | `test_config.py` | new keys have defaults; invalid values (negative timeouts, zero history) are refused |
| Docs | `test_docs.py` | `CLAUDE.md` and `AGENTS.md` stay identical |
| Frontend — transcript | `frontend/tests/transcript.test.js` | a person line and an avatar line per turn; the sentences of one turn are appended to the same avatar line, empty `text_content` adds nothing; a cut marks the running avatar line only; past `TRANSCRIPT_MAX_LINES` the oldest lines are dropped; a `conversation` message with another id (or `null`) empties the lines, the same id keeps them |
| Frontend — memory | `frontend/tests/memory.test.js` | grouping items by category and status, the label of a category, the message for each API error |

## End-to-end tests (real environment, `RUN_E2E=1`)

- **Follow-up** (`tests/e2e/test_real_memory.py`): two spoken turns in one conversation,
  the second only answerable from the first; the answer uses it.
- **Profile**: with `AUTH_DEV_USER_ID` set and one item created over HTTP (a way of
  addressing), the spoken answer follows it.
- **Import**: a DOCX and a pasted paragraph of self-description → items of the expected
  categories, each naming its import; the stored object is byte-identical to the upload.
- **Consolidation**: a conversation in which the user states a preference, closed →
  one pending item with the quote as evidence; the same statement in a second
  conversation → active.
- **Latency** (`tests/e2e/test_real_turn.py`): with `MEMORY_HISTORY_MAX_TURNS` turns of
  history and a full profile, first audio under 3.5 s and end to end under 10 s; the
  STT time with `verbose_json` compared with the time before.
- **Follow-up on a document**: a question answered from an uploaded document, then a
  follow-up that does not name its subject: the answer uses the passages of the first
  subject. Then a question on another subject: it is answered from its own passages.
- **Barge-in**: an answer of three sentences interrupted during the first: the next
  turn's history holds that sentence only.
- **Non-speech**: `stt.no_speech_prob` read from the traces for real utterances, short
  ones included ("ừ", "có"), and for noise; `STT_NO_SPEECH_MAX_PROB` set from them.
- **Reset**: the follow-up pair again with `conversation_reset` sent between the two
  turns: the second answer no longer uses the first, and the `conversation` messages
  carry two different ids.
- **Manual, in the browser — transcript**: both sides of a turn appear in the window on
  the right as they happen; a long conversation keeps scrolling to the newest line and
  drops the oldest; "Cuộc trò chuyện mới" empties the window, also while the avatar is
  speaking (it stops); the window empties by itself after the person left.
- **Manual, in the browser — profile**: add, edit and delete an item; import a file and a pasted
  description, open the original, delete the import with and without its items;
  approve and reject a proposal; as a guest, the profile page asks for login.
