---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: persona, llm, expression
summary: A signed-in person gives the avatar a personality — a few named fields (name, role, addressing, personality, answer style, what to avoid), filled by hand or read by the LLM from a pasted description — and every answer to that person follows it
---

# Avatar persona

## Problem statement

- The character the avatar plays is one fixed text, `SYSTEM_PROMPT` in
  `backend/prompts.py`, the same for everyone. Changing it means editing code.
- The profile page only holds what the avatar knows about the **person**. Its import
  reads a description of the person and is told to ignore anything else, so a text
  describing the avatar is dropped.
- `docs/ARCHITECTURE.md` lists "customising the character the avatar plays" under MVP 2
  as not designed yet, and `docs/2026-10-12/conversation-memory/plan-database.md`
  expects its configuration in the application database.

## Requirements

- A signed-in person can define the personality of the avatar. It is optional: with
  nothing defined the avatar answers exactly as today.
- The personality is a dictionary of predefined fields, each a short text:

  | Key | Shown as | Holds |
  |---|---|---|
  | `name` | Tên | the name the avatar goes by |
  | `role` | Vai trò | who the avatar is to the person (assistant, companion, tutor, ...) |
  | `addressing` | Cách xưng hô | how the avatar calls itself and the person |
  | `personality` | Tính cách | its character and tone |
  | `answer_style` | Định hướng trả lời | how it answers (short, with examples, asks back, ...) |
  | `avoid` | Điều cần tránh | what it must not do or talk about |

  Any subset may be set. A field left empty is simply not part of the personality.
- On the profile page ("Hồ sơ"), a block "Tính cách của avatar" holds:
  - a box to paste a description written freely, as a prompt, and a button that has the
    LLM read it into the fields;
  - one input per field, filled by that reading or typed by hand, editable either way;
  - "Lưu" to store the fields, "Xoá tính cách" to remove them all.
- What the reading finds is **not** stored: it fills the form, and only "Lưu" stores.
- A stored personality shapes every answer to that person from the next turn on, also
  inside a conversation in progress.
- A guest has no personality to set (401 on the routes) and is answered with the
  default character.
- The personality never costs the turn: empty, slow or failing, the turn runs with the
  default character.

## Decisions made

- **Per user, in the application database.** One table `persona_fields`, one row per
  (user, field): `user_id`, `field`, `value`, `updated_at`, primary key (`user_id`,
  `field`). A key-value table is the dictionary as asked, needs nothing specific to one
  engine, and takes a new field without a schema change. Saving replaces the whole
  dictionary of the user in one transaction.
- **The fields are declared once**, `PERSONA_FIELDS` in `backend/models.py`. The label
  each one is written with for the LLM is a text (`PERSONA_FIELD_LABELS` in
  `backend/prompts.py`); the page has its own labels and placeholders
  (`frontend/src/persona/format.js`). A unit test keeps the three lists equal to the
  keys.
- **A feature of its own**, `backend/features/persona/`, beside `memory/`: the memory is
  what the avatar knows about the person, the persona is who the avatar is for that
  person. It gets the module ID `persona`.
- **It reaches the turn through a contract.** `Persona.prompt(user_id)` in
  `core/contracts.py` gives the text for the LLM ("" = none). The engine asks it before
  the LLM, for a signed-in person only, bounded by `PERSONA_TIMEOUT_SEC`, inside a
  `persona` span; `LLM.stream_answer` gains `persona: str = ""`. The service caches the
  text per user and drops it on a save or a delete, like the memory's profile.
- **Where it stands in the request** (changed on 2026-10-13, after the first real run):
  the persona is written into the system prompt. `SYSTEM_PROMPT` is a template with one
  `{persona}` slot, like the other texts of `prompts.py`, followed by the rules that
  hold for every persona. `PERSONA_DEFAULT` holds the default persona (role,
  personality, answer style): it fills the slot for a guest and for a person who set
  nothing, and a field a person set replaces the default of that field only. The first
  version sent the persona as a system message of its own after a fixed `SYSTEM_PROMPT`,
  which described a second character the persona then had to contradict. The memory
  profile stays a second system message, then the history.
- **Two ways of addressing can be set**: the persona's `addressing` and the person's own
  `addressing` memory item. The memory item wins: it comes later in the request and
  `MEMORY_PROMPT` already makes it binding. `SYSTEM_PROMPT` says so too.
- **Reading a description is one `LLM.complete` call outside any turn**, like a memory
  import: `PERSONA_IMPORT_PROMPT` as the instruction, the description between
  delimiters it cannot close (`PERSONA_IMPORT_DOCUMENT`), read as material and never
  obeyed as instructions. The answer must be one JSON object and nothing else; a key
  that is not a field, a value that is not text, an empty one or one longer than
  `PERSONA_FIELD_MAX_CHARS` is dropped and the other fields are kept.
- **Nothing of the description is kept**: no object, no row. It is short, the person has
  it, and what matters is the fields they review and save. (A memory import keeps its
  original because items point back at it; nothing points back here.)
- **Field values**: one line (line breaks, control and invisible characters removed),
  at most `PERSONA_FIELD_MAX_CHARS` (300). They are written into the prompt as
  arguments of a template, so braces in them are plain text.
- **The fields are instructions by design.** The person writes them for their own
  avatar and they reach their own turns only: nothing is read across users.
- **HTTP routes**, a contract between `backend/features/persona/api.py` and
  `frontend/src/persona/`:

  | Route | Body | Answer |
  |---|---|---|
  | `GET /persona` | | `200 {"fields": {...}}`, only the fields that are set |
  | `PUT /persona` | `{"fields": {key: text}}` | `200 {"fields": {...}}` as stored; an empty value unsets the field; `422` for an unknown key or a value too long |
  | `DELETE /persona` | | `204` |
  | `POST /persona/read` | `{"text": "..."}` | `200 {"fields": {...}}`, not stored; `413` longer than `PERSONA_IMPORT_MAX_CHARS`; `422` empty, or nothing about the avatar in it; `502` the LLM failed |

  Every route: `401` for a guest, `502` when the database fails.
- **New keys** (`backend/tuning.py`, commented out in `.env.example`):
  `PERSONA_FIELD_MAX_CHARS` (300), `PERSONA_IMPORT_MAX_CHARS` (8000),
  `PERSONA_TIMEOUT_SEC` (1.0).
- **No architecture record**: this is a feature beside the turn, like the knowledge
  base. `docs/ARCHITECTURE.md` gets the persona in its diagrams and a Backlog row.
- **`one_line` and `as_document` are reused from the memory feature**
  (`features/memory/memory.py`, `features/memory/reading.py`) rather than copied.

### Not in this plan

- Importing the description from a file (only a pasted text).
- A personality for guests, or one shared by the whole kiosk.
- A voice, an avatar model or a look per persona.
- Free-form fields the person names themselves.

## Implementation approach

Four slices, each tested and committed on its own.

1. **The persona in the turn.** `PERSONA_FIELDS`, the `Persona` contract,
   `chat_messages` and `LLM.stream_answer` with `persona`, the two LLM backends and the
   fallback, the engine's `persona` step and span, the texts and the keys. With no
   persona wired nothing changes.
2. **Storing it.** `features/persona/store.py` and `persona.py` (get, save, clear, the
   cached prompt), the routes `GET` / `PUT` / `DELETE /persona`, the wiring in
   `main.py`.
3. **Reading a description.** `features/persona/extraction.py` (pure), the service's
   `read`, `POST /persona/read`.
4. **The page.** `frontend/src/persona/` (client, panel, pure helpers), the block in
   `index.html`, the profile page opening it.

Then the docs: `CLAUDE.md` / `AGENTS.md`, `docs/ARCHITECTURE.md`,
`docs/quality-gates.md`, `CHANGELOG.md`, `.env.example`.

## To-do

- [x] Slice 1 — the persona in the turn (contract, messages, backends, engine, texts,
      keys) with unit tests
- [x] Slice 2 — store, service and the `GET` / `PUT` / `DELETE /persona` routes with
      unit tests
- [x] Slice 3 — reading a pasted description (`POST /persona/read`) with unit tests
- [x] Slice 4 — the "Tính cách của avatar" block on the profile page with unit tests,
      `npm run build`
- [x] `CLAUDE.md` / `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/quality-gates.md`,
      `CHANGELOG.md`, `.env.example`
- [x] Real run (2026-10-13, Databricks STT + LLM, `LLM_BACKEND=databricks`
      `STT_BACKEND=databricks`; `api.groq.com` answered 503 on that network):
      `tests/e2e/test_real_persona.py` run twice. The description is read in 2.3 s into
      `name` "Bông", `addressing` "Tự xưng là em, gọi người dùng là anh" and
      `answer_style` "Trả lời thật ngắn gọn". Asked its name, the default character
      answers "Mình là trợ lý cá nhân của bạn…"; with the fields saved, "Dạ em chào
      anh! Em là Bông…". The persona read costs the turn under 1 ms. Asked directly of
      the model with a persona (em / anh) and a profile holding "Gọi tôi là chị, xưng
      em": three answers out of three call the person "chị Linh" and keep the name
      Bông, so the person's own `addressing` wins; `avoid` "Không nói về chính trị"
      declines a question about an election
- [ ] The latency gate on this path: end to end 5.5 s (under 10 s), first audio 4.0 s
      and 4.2 s against 3.5 s, so the e2e test fails on its last assertion. In the
      measured turn the STT took 1.9 s and the first sentence of the LLM 1.6 s; the same
      gate fails on Databricks without the persona (`../../2026-10-12/knowledge-base/plan.md`).
      Not measured on Groq
- [x] Seen in the real run: with a playful persona the model writes emoji in its
      answers ("😊", "💕"), which go to the TTS as text — handled in
      `../no-emoji-in-speech/plan.md`
- [x] The block in a real browser (headless Chrome driven over CDP, 2026-10-13, on the
      real app and routes with a temporary database, a fake orchestrator and a scripted
      LLM): the six fields shown; a description read into three of them and nothing
      stored; a field corrected, the page closed and reopened with the unsaved form
      kept; saved, and still there after a reload; a value over the limit refused with
      its reason and the stored persona unchanged; deleted
- [x] The persona written into `SYSTEM_PROMPT` with `PERSONA_DEFAULT`, with unit tests
- [x] Real run of the rewritten `SYSTEM_PROMPT` (2026-10-13, Databricks LLM, asked
      directly of the model, two rounds). First wording: the default character invented
      a name for itself ("mình là Mimi") and, under a playful persona, 2 answers of 14
      held an emoji followed by a spoken apology for it ("À nhầm, không dùng biểu tượng
      được"). The rules were tightened (no name of its own without one above; nothing
      but words and ordinary punctuation; never mention the rules) and 31 answers
      measured: the default character says it is "trợ lý ảo" three times out of three
      and no longer names the model or its maker; a persona with the name alone answers
      "mình là Bông, trợ lý ảo của bạn" (the other fields keep their defaults); a full
      persona is followed in 20 playful answers, none with an emoji or a word about the
      rules; with a profile holding "Gọi tôi là chị, xưng em" three answers of three say
      "chị Linh". One of the 31 answers held a run of garbled words from the model
- [x] Real turn with it (`tests/e2e/test_real_persona.py`, Groq answering 503, the
      Databricks backup taking over): passed, first audio 3.4 s, end to end 4.7 s.
      The earlier runs of the same path gave 4.0 to 4.2 s for the first audio, so the
      3.5 s gate is not held with a margin
- [x] The page shows the default persona: `GET /persona/defaults` gives the default
      fields (`PERSONA_DEFAULT`), and under each input that is empty the block writes
      the default the avatar uses for it, so the person sees what an empty field means.
      Seen in headless Chrome (2026-10-13, scripted LLM): the three defaults shown with
      nothing set; the line of a field gone as soon as it is typed in or filled by a
      reading; all three back after "Xoá tính cách"
- [ ] Human verification
