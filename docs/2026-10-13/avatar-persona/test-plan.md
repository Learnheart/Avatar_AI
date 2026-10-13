---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: persona, llm, expression
summary: Tests for the personality a signed-in person gives the avatar
---

# Test plan — avatar persona

## Unit tests (sandbox)

| Area | File | Cases |
|---|---|---|
| Messages | `test_history.py` | the persona is a second system message, after `SYSTEM_PROMPT` and before the profile; no persona → the request of today |
| LLM backends | `test_llm.py`, `test_llm_responses.py`, `test_fallback.py` | the persona given to `stream_answer` is in the request; the fallback hands it to the primary and to the backup |
| Engine | `test_engine.py` | the LLM receives the persona of the turn's user, together with the profile; a guest makes no persona read; no persona wired, none set, a failing or a slow read (`PERSONA_TIMEOUT_SEC`) → the turn completes with `""`; an empty transcript makes no read |
| Tracing | `test_engine_tracing.py` | a `persona` span with its outcome (`hit`, `empty`, `timeout`, `error`); `llm.persona` on the `llm` span |
| Texts | `test_persona_service.py` | `PERSONA_FIELD_LABELS` has exactly the keys of `PERSONA_FIELDS`, and `PERSONA_IMPORT_PROMPT` names every one of them |
| Config | `test_config.py` | `PERSONA_FIELD_MAX_CHARS` and `PERSONA_IMPORT_MAX_CHARS` under 1, `PERSONA_TIMEOUT_SEC` not positive or not finite → refused |
| Store | `test_persona_store.py` | nothing stored → `{}`; a saved dictionary is read back; saving again replaces it whole (a field left out is gone); clearing; one user never reads or changes another's; the rows survive a reopen |
| Service | `test_persona_service.py` | the prompt lists the set fields in the order of `PERSONA_FIELDS`, each with its label; nothing set → `""`; the prompt is cached and dropped by a save and by a clear; a value is stored on one line; an empty value unsets its field; an unknown key or a value over `PERSONA_FIELD_MAX_CHARS` → `InvalidPersona`, nothing stored; braces in a value do not break the prompt; a failing store → `PersonaError` |
| Extraction | `test_persona_extraction.py` | an object with known keys → those fields; an unknown key, a value that is not text, an empty or too long one → dropped, the others kept; `{}` → no field; prose, a code fence, an array → `InvalidPersonaAnswer` |
| Reading | `test_persona_service.py` | the LLM is given `PERSONA_IMPORT_PROMPT` and the description between delimiters it cannot close; the fields come back and nothing is stored; an empty text → `EmptyDescription`; one over `PERSONA_IMPORT_MAX_CHARS` → `DescriptionTooLong`, no LLM call; an LLM failure or an invalid answer → `PersonaReadFailed`; no field found → `NothingToRead` |
| API | `test_persona_api.py` | `GET` starts with `{}`; `PUT` stores and `GET` gives it back; `PUT` replaces; `DELETE` → 204 and `{}`; an unknown key, a value too long, a body without `fields` → 422; `POST /persona/read` → the fields, and `GET` still `{}`; 413, 422 (empty, nothing found), 502 (LLM); a guest → 401 on every route; a failing database → 502; a saved persona is in the LLM request of the next turn |
| Frontend | `frontend/tests/persona.test.js` | the fields of the page have the keys of the backend; `formValues` trims and leaves out empty fields; `mergeRead` overwrites only the fields that were read; `isDirty`; `readMessage`; `personaErrorMessage` for 401, 413, 422, 502, a network failure |

## End-to-end tests (real environment, `RUN_E2E=1`)

- `tests/e2e/test_real_turn.py`: a description of the avatar read by the real LLM gives
  the name and the way of addressing it states; with those fields saved, a turn asking
  "bạn tên là gì" is answered with that name. First audio and end-to-end times are
  reported against the gate (3.5 s / 10 s).
- Manual, in the browser: paste a description, "Đọc mô tả", correct one field, "Lưu",
  reload the page (the fields are still there), talk to the avatar, "Xoá tính cách".
