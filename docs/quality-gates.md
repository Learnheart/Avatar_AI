---
author: linhbk3
date: 2026-10-08
status: todo
agent: avatar
modules: vision, vad, stt, llm, tts, orchestrator, expression
summary: Per-module, integration, and pipeline quality gates for the modular Avatar AI pipeline.
---

# Quality Gates — Avatar AI Modular Pipeline

Gates that decide when a module is "good enough" to wire into the pipeline. A gate
tests the module's **contract** (input → output + constraints), not its internal code,
so it stays valid when the implementation changes and applies equally to every backend
behind the Device Capability Router (e.g. an STT gate must pass on both the Metal and
the CUDA backend).

## How to read a gate

Each module gate has:

- **Contract** — what goes in, what comes out, and the invariants on the output.
- **Functional** — does it do the right thing? (unit tests, sandbox, automated)
- **Performance** — is it inside the latency / resource budget? (measured, numeric)
- **Resilience** — does it fail safely when inputs are bad or a dependency breaks?

Three levels of verification (per AGENTS.md §2 — unit in sandbox, e2e costs money):

| Level | What it checks | Cost | How often |
|---|---|---|---|
| **Module gate** | One module in isolation, neighbors mocked | Cheap, automated | Every change |
| **Integration gate** | Two/three modules — their contracts line up | Medium | On wiring changes |
| **Pipeline gate** | One real end-to-end turn | Expensive (cloud $) | Before a milestone |

**The gate that matters most is the contract match between modules**, not "the module
works alone." Most pipeline breakage is a format mismatch at a boundary (STT returns a
shape the LLM step didn't expect; TTS returns audio the frontend can't play).

Latency budget referenced below comes from `ARCHITECTURE.html`: **end-to-end < 10s**,
**first audio < ~3.5s**.

---

## 1. Module Gates

### 1.1 Vision (presence + head orientation)

**Contract:** camera frame in → `{present: bool, head_orientation: {pitch, yaw, roll}}`
or "no face". Runs continuously, off the speech critical path.

| Type | Gate criteria |
|---|---|
| Functional | Detects a present face in a known test image; returns "no face" on an empty frame; orientation values within documented clamp range; picks the largest face when several are present |
| Performance | Sustains **≥ 30 FPS** on the dev machine (M5); per-frame processing does not block the async loop; vision never adds latency to the speech path (parallel plane) |
| Resilience | Camera disconnect → emits "no face" / last-known, retries, does not crash; corrupt/black frame handled; after N seconds no face → defined idle behavior |

### 1.2 VAD (speech start/stop) — golden-dataset gated

**Contract:** audio chunk in → `speech_start` / `speech_end` / none.

| Type | Gate criteria |
|---|---|
| Functional | On the **VAD golden dataset** (simple binary: human voice vs background noise), **recall ≥ 95%** on `speech` clips (does not miss real voice) and **false-trigger rate ≤ 5%** on `noise` clips (does not fire on background noise) — thresholds confirmed once the set exists |
| Performance | Per-chunk processing **< 10ms**; end-of-speech decision latency within budget (the "silence confirmation" window is explicit and configurable) |
| Resilience | Noise-only stream never fires; truncated/empty chunk ignored, no crash; threshold is config-driven, not hardcoded |

> See §4.3 for the VAD dataset shape. It is deliberately a simple speech-vs-noise
> classification set — no timeline/segment labels — matching the MVP need.

### 1.3 STT (speech → Vietnamese text) — golden-dataset gated

**Contract:** utterance audio in → Vietnamese text out, **one fixed output shape**
regardless of backend (Metal / CUDA / CPU).

| Type | Gate criteria |
|---|---|
| Functional | On the **STT golden dataset**, **WER ≤ 15%** (target; threshold confirmed once the set exists). Output shape identical across all backends — same fields, same encoding |
| Performance | For a short utterance (~3–5s), transcription **< ~1.5s** on the dev machine; model loaded once at startup and warmed (first-call cold start excluded from the per-request budget) |
| Resilience | Empty/silence audio → empty text, not an error; very long audio handled or safely rejected; unsupported sample rate rejected with a clear error; **every backend must pass this same gate** |

> WER is measured per the method defined in §4 (STT golden dataset). Each router
> backend is gated independently against the same dataset and the same threshold.

### 1.4 LLM (text → Vietnamese answer) — golden-dataset gated

**Contract:** user text (+ fixed system prompt, + the earlier turns of the conversation,
+ the profile of a known person as a second system message) in → streamed Vietnamese
answer text out. No tools. The passages of the knowledge base and the profile are
gated in §1.8 and §1.10.

| Type | Gate criteria |
|---|---|
| Functional | On the **LLM golden dataset**, each response satisfies the **form constraints**: Vietnamese, in-persona (financial advisor), length ≤ 2–3 sentences, non-empty, no tool-call/markup leakage. Pass rate **≥ 95%** of golden cases (threshold confirmed once the set exists) |
| Performance | **First token < ~1.5s**; response streams incrementally (not delivered as one final blob) so TTS can start early |
| Resilience | Provider error / timeout → defined fallback (graceful message, no crash); empty or non-Vietnamese model output caught by the form check; network failure surfaces as a recoverable error |

> MVP gates LLM on **form, not content quality** — content scoring is subjective and
> out of MVP scope. The golden dataset fixes inputs + expected form so the gate is
> deterministic. Content-quality scoring is a backlog item.

### 1.5 TTS (Vietnamese text → audio)

**Contract:** answer text in → audio chunks out, in a format the frontend can decode
and play, streamable sentence-by-sentence.

| Type | Gate criteria |
|---|---|
| Functional | Produces audio for a known Vietnamese sentence; output format matches exactly what the frontend's audio player expects (sample rate, encoding, container); supports streaming per sentence |
| Performance | First audio chunk for a short sentence **< ~1s**; streaming so playback can begin before the full answer is synthesized |
| Resilience | Provider error → defined fallback (alternate provider or graceful skip), no crash; empty text → no audio, no error; swapping provider via config does not change the output contract |

### 1.6 Orchestrator (turn state + routing)

**Contract:** owns the turn state machine (idle → listening → thinking → speaking →
idle); routes data between modules; enforces timing. The **only** owner of state.

| Type | Gate criteria |
|---|---|
| Functional | State transitions follow the defined machine; VAD `speech_end` triggers the STT→LLM→TTS flow; barge-in (speech during speaking) interrupts correctly; no module mutates state directly |
| Performance | Routing overhead negligible vs module budgets; a GPU/blocking call never stalls the async event loop (inference runs off the loop) |
| Resilience | Any module error becomes a recoverable signal, not a crash; session timeout handled; a stuck module cannot wedge the state machine (timeout → safe state) |

### 1.7 Expression (frontend: avatar + lip-sync)

**Contract:** receives head-orientation updates and speech-audio chunks → avatar reacts
to those parameters: head motion from rotation values, mouth motion from audio
loudness, plus idle animation.

**MVP scope of this gate:** confirm only that **the avatar moves correctly according to
the parameters it receives** — i.e. the parameter-in → motion-out mapping is right.
Perceptual quality (smoothness, naturalness, how well lip-sync "feels" matched) is NOT
gated here; it is left to visual review.

| Type | Gate criteria |
|---|---|
| Functional (parameter → motion) | A given head_rotation moves the head in the **correct direction and axis** (no mirror/sign flip), and the applied rotation stays **within the clamp range** (no "broken neck"); a given audio loudness drives the mouth value **proportionally** (louder → more open); when audio is silent/ended the mouth returns to 0; an `interrupt` stops motion |
| Performance | Renders without stalling on the dev machine; motion updates track the incoming parameter stream (no accumulating lag) |
| Resilience | Missing avatar blendshape → skip that feature + warn, no crash; audio decode error → skip chunk; WebSocket drop → reconnect; interrupt stops playback immediately and clears the queue |

> **Architecture note for testability:** the motion mapping (loudness → mouth value with
> clamp/lerp; rotation values → bone rotation with clamp) should be a pure function that
> returns the numeric motion value, separate from the render call. This lets the gate
> assert "parameter X in → motion value Y out" without running a real WebGL render.
> Perceptual quality (smoothness, phase sync, naturalness) is deferred to visual review,
> not an automated gate.

### 1.8 Knowledge (uploaded documents → passages for a question)

**Contract:** a file in → an indexed document (or a defined refusal); a question in →
the most relevant passages out, best first, each with its source and score.

| Type | Gate criteria |
|---|---|
| Functional | An uploaded txt / md / csv / pdf / docx is listed with its chunk count; a question about a fact of an uploaded document returns the passage holding it within the top `KNOWLEDGE_TOP_K`; a deleted document, or one the user has not selected, is never returned; the index and the selection survive a restart |
| Performance | Lookup in a turn **< 1 s**, and never longer than `KNOWLEDGE_TIMEOUT_SEC`; an empty base, or one with no document selected, costs no embedding call |
| Resilience | Unsupported, unreadable, empty or oversized file → refused with a reason, nothing stored; embedding failure on upload → nothing stored; lookup failure or timeout in a turn → the question is answered without passages, the turn completes |

> Retrieval quality (recall on a golden set of question → passage pairs) is not gated
> yet: there is no golden set. Backlog.

### 1.9 Observability (traces, perception measurements, capture)

**Contract:** a turn in → one trace whose spans name every step with its duration,
input and output; the perception loops → metrics and events; with capture on → frames,
turn audio and spans on disk.

| Type | Gate criteria |
|---|---|
| Functional | Each turn is one trace rooted at `turn` (from speech start) with `listen`, `stt`, `knowledge` (when wired), `persona` and `memory` (for a known person), `llm`, one `tts` per sentence and `playback`; a failed, timed-out or cancelled turn says so on its root; every log line of the turn carries its `trace_id`; capture writes a playable `utterance.wav` / `answer.wav` and a `trace.json` per turn |
| Performance | No measurable cost on a turn: with capture on, local stages (TTS first chunk, tracking time per frame) stay within their run-to-run variation; nothing is written or exported on the event loop |
| Resilience | No OTLP endpoint, an unreachable one, a full capture queue or a disk error → the turn is unaffected; dropped capture items are counted; the capture directory stays under `CAPTURE_MAX_GB` once an hour has passed |

### 1.10 Memory (what is kept about a known person → the profile given to the LLM; imports; gathering from conversations)

**Contract:** a user id in → the profile text given to the LLM out, written from that
user's *active* items (who the person is and how to address them first, then the most
recently updated, at most `MEMORY_PROMPT_MAX_ITEMS`), or nothing when there is none; an
item typed on the profile page in → stored for that user and used from the next turn.
A file or a pasted text in → its original kept unchanged, and what it says about the
user stored as active items naming it, or nothing at all. The turns of a known
person's conversation and its close in → items *proposed* (`pending`) with the words
they come from, used only once approved or, for a preference or a personality trait,
stated again in a later conversation; the turns deleted. A guest has no profile and
stores nothing.

| Type | Gate criteria |
|---|---|
| Functional | An item added, edited or deleted over `/memory/items` is listed as it was left and changes the profile of the next turn; only `active` items reach the LLM, each as one line whatever was typed (line breaks, control and invisible characters never reach the prompt); one user never lists, reads, changes or deletes another's items (an item of someone else is a `404`); a guest gets `401` on every route and no row is written for them; items and users survive a restart; the tables are created in an empty database at startup and a second start changes nothing |
| Functional — import | A file (`.txt`, `.md`, `.csv`, `.pdf`, `.docx`) or a pasted text sent to `/memory/imports` → `201` with the import and its items (`active`, `source` `import`, naming the import, each with its quote), listed with its `item_count`, and in the profile of the next turn; the original read back over `/memory/imports/{id}/content` is byte-identical to what was sent (same SHA-256), served as a download (`attachment`, `nosniff`); the extracted text is in no table; deleting with `items=delete` removes the object, the row and the items, with `items=keep` (the default) the items stay without an import; one user never lists, reads or deletes another's import (`404`) and the object key names no user; the LLM is given the import instruction, never the persona, and the document between delimiters it cannot close; a quote is kept as evidence only when the document really holds it |
| Functional — gathering | Each recorded turn of a logged-in user is stored (`conversations`, `turns`) in the order of recording; a closed conversation is read by ONE `LLM.complete` call given `MEMORY_EXTRACT_PROMPT` (never the persona), the items already known (active, pending, rejected) and the transcript between delimiters it cannot close, with the utterances under `MEMORY_MIN_STT_CONFIDENCE` left out (no confidence = kept); an `add` → a `pending` item, `source` `conversation`, with its quote only when the person really said it; a `confirm` counts once per conversation and makes a pending `preference` / `personality` item `active` at `MEMORY_PROMOTE_SEEN_COUNT`, never an `identity` / `addressing` one, never a proposal to replace another item and never one without a quote; a `replace` → a pending item naming the active item it would replace, which is untouched until approval; a rejected item is never proposed again; after the reading the turns are deleted and `consolidated_at` is set, in one transaction with the item changes; `PATCH /memory/items/{id}` `{"status": "active"}` approves (the replaced item is removed), `DELETE` rejects a proposal and turns a gathered item into `rejected`; `GET` lists active and pending items, never rejected ones; a pending item is in no prompt; each reading is one `memory.consolidate` trace with the transcript in, the operations out and its outcome |
| Performance | No turn and no close of a conversation waits for the memory: recording and reading run in background tasks; a conversation with nothing left of the person costs no LLM call; a guest's conversation makes no memory call at all |
| Performance | The profile read in a turn is answered from RAM after the first read of a user, and never takes longer than `MEMORY_TIMEOUT_SEC`; a guest's turn makes no memory read at all |
| Resilience | An unknown category, an empty text or one with nothing visible, a text longer than `MEMORY_ITEM_MAX_CHARS` or a field that cannot be changed → refused (`422`) with the reason, nothing stored; a failing store → `502` without the database's text, the detail in the log; an empty, slow or failing profile read in a turn → the turn completes without the profile; a database that cannot be opened → startup stops with the reason |
| Resilience — gathering | The LLM failing or answering anything but the JSON array (prose, a code fence) → nothing stored, the turns kept, the conversation read again at the next start; an operation with an unknown category, no valid text or an id that was not listed → dropped, the others kept; a failing `record_turn` or `close_conversation` is logged and costs no turn; the close is never read before the last turn was written; what the person did to an item while a conversation was being read (approved, rejected, deleted) is never undone by that reading; two conversations of one user are read one after the other, so neither proposes what the other proposed; the request lists at most `MEMORY_EXTRACT_MAX_REJECTED` rejected items and none of them, listed or not, is proposed again; at shutdown a reading in progress is dropped without loss and the turns being written are waited for at most `MEMORY_TIMEOUT_SEC`; at startup the turns older than `MEMORY_TRANSCRIPT_TTL_DAYS` are deleted and every unread conversation (closed, or left open by a killed process) is read without delaying the start, whatever fails on one of them; one user's conversation never reads or changes another's items; a rejected item answers `404` to `PATCH` and `DELETE` |
| Resilience — import | A file over `MEMORY_IMPORT_MAX_FILE_MB` or a text over `MEMORY_IMPORT_MAX_CHARS` → `413`; an unsupported type → `415`; an unreadable file, no text, both or neither of `file` and `text`, or an answer with nothing to remember → `422`; an LLM failure or an answer that is not the JSON array asked for (prose or a code fence around it) or one cut at `LLM_COMPLETE_MAX_TOKENS` → `502` without the provider's text; in every one of these, and when the request is cancelled at any step (while the object, the row or the items are written, or while the LLM reads), no object, no row and no item remain and the cached profile is dropped; a client that only disconnects does not cancel it: the import is completed and stored whole; an unsupported type is refused before anything is written; an entry of the answer with an unknown category or a text that is no valid item is dropped and the others kept; with a backup LLM the import falls back on failure and timeout, and never puts the primary of the turns in cooldown; an object key cannot leave the store directory |

> Gated here: the items a person types, the imports and the profile written from them.
> Facts gathered from conversations and their approval are not built yet and have no
> gate. Whether the model extracts the right facts from an import is checked on the
> real model (`tests/e2e`), not by an automated gate. Whether the answer actually follows the profile (the way of addressing) is
> checked by a real turn (`tests/e2e`), not by an automated gate.

### 1.11 Persona (the personality a known person gives the avatar → written into the system prompt of the turn; reading a description)

**Contract:** a user id in → the persona written into the `{persona}` slot of
`SYSTEM_PROMPT` out: the default fields (`PERSONA_DEFAULT`), each replaced by the one
that user set (`name`, `role`, `addressing`, `personality`, `answer_style`, `avoid`, in
that order, each with its label), or nothing when none is set — the slot then holds
the default persona. A dictionary of fields
in → the whole persona of that user, used from the next turn. A description written
freely in → the fields it states out, stored nowhere. A guest has no persona.

| Type | Gate criteria |
|---|---|
| Functional | `PUT /persona` stores the fields it is given as the whole persona of the user (a field left out or empty is gone), `GET` gives back the fields that are set, `DELETE` removes them; `GET /persona/defaults` gives the default persona, the same whatever the user set; the turn of that user carries them inside its one system prompt, over the default fields they replace, followed by the profile as a second system message; a turn without persona, a guest's included, carries the default persona in the same place; each value reaches the prompt on one line whatever was typed (line breaks, control and invisible characters never do) and braces in it are plain text; one user never reads or changes another's persona; a guest gets `401` on every route and no row is written for them; the fields survive a restart |
| Functional — reading | `POST /persona/read` gives the description to ONE `LLM.complete` call with `PERSONA_IMPORT_PROMPT` (never the persona of the turns), between delimiters it cannot close; the fields of the answer come back and nothing is stored (`GET` is unchanged); each reading is one `persona.read` trace with the description in, the fields out and its outcome |
| Performance | The persona read in a turn is answered from RAM after the first read of a user, also when none is set, and never takes longer than `PERSONA_TIMEOUT_SEC`; a save or a delete applies to the next turn; a guest's turn makes no persona read at all |
| Resilience | An unknown field, a value longer than `PERSONA_FIELD_MAX_CHARS`, a value that is not text or a body without `fields` → `422`, the stored persona unchanged; a failing store → `502` without the database's text; none set, a slow or a failing read in a turn → the turn completes in the default character |
| Resilience — reading | An empty description → `422`; one over `PERSONA_IMPORT_MAX_CHARS` → `413`, without an LLM call; an LLM failure, or an answer that is not the JSON object asked for (prose, a code fence, an array) → `502` without the provider's text; in a valid object a key that is no field, a value that is not text, an empty one or one over the limit is dropped and the others kept; no field left → `422` |

> Whether the model reads a description into the right fields, and whether the answer
> actually follows the persona (the name, the way of addressing), is checked on the real
> model (`tests/e2e/test_real_persona.py`), not by an automated gate.

### 1.12 Accounts (sign-in: a guest or a signed-in user → the user id every per-user feature is given)

**Contract:** a session token in → the id of the user it signs in, or nobody: no token,
an unknown one, a revoked one and an expired one are all a guest. A user name and a
password, or the code of an external provider, in → a user and a session for them. The
orchestrator is given a user id or `None`, never a token.

| Type | Gate criteria |
|---|---|
| Functional — modes | Without a valid session: `/memory`, `/persona` and the per-user `/auth` routes answer `401`, a turn carries no user id, nothing is written; with one: the same routes reach that user's data only, and the turn carries their id. `GET /auth/session` answers `200` to both, with the user or `null`. `AUTH_DEV_USER_ID` set → that user on every route and connection, whatever is presented |
| Functional — accounts | `POST /auth/register` sends a six-digit code to the email and creates nothing (`202`, the code in no response); `POST /auth/register/verify` with that code creates a user with an opaque id and signs them in (`201`); a wrong code → `401` and the right one still works, until `AUTH_OTP_MAX_ATTEMPTS` wrong tries or `AUTH_OTP_TTL_MINUTES` have passed (`410`: a new code has to be asked for); a code works once and for its email only; another code only after `AUTH_OTP_RESEND_SEC` (`429`, `Retry-After`), and it replaces the one before; one email has one account (`409`, no mail); an account waiting for its code cannot sign in; `POST /auth/login` signs in; an email is compared without case; a wrong password and an unknown email get the same `401`; after `AUTH_LOGIN_MAX_ATTEMPTS` failures within `AUTH_LOGIN_WINDOW_MINUTES` the email is held (`429`), the right password included, until the window has passed; `AUTH_ALLOW_REGISTRATION=0` → `403` |
| Functional — mail | `MAIL_BACKEND=smtp`: the mail is handed to the server after STARTTLS (or over SSL) and a login when a user name is set; `MAIL_BACKEND=log`: the mail is one log line and nothing is sent, and the start of the backend says so; a server that fails or cannot be reached → `502` without its text, nothing left waiting |
| Functional — sessions | A session is kept: used within `AUTH_SESSION_IDLE_MINUTES` it stays and its end moves; not used for that long, or `AUTH_SESSION_MAX_HOURS` old however recently used, it signs nobody in; both follow the config for sessions that are open; `POST /auth/logout` ends that session and no other; a turn of the user counts as a use, the periodic look at the session of the connected page does not |
| Functional — WebSocket | `auth` with a valid token → `session` with the user and the next turn is that user's; with `null` or a token that opens nothing → `session` with `null`; a connection whose first frame is not `auth` is a guest; signing in or out on an open connection closes the conversation in progress and cancels the running turn; a page that reconnects with the same session keeps its conversation; a session that ends while the page is connected (expired, signed out elsewhere, and with `AUTH_LOGOUT_ON_LEAVE` the conversation closed for `absence` or `idle`) → `session` with `null` and the next turn is a guest's; a conversation closed by the reset button signs nobody out |
| Functional — providers | `GET /auth/{provider}/start` sends the browser to the provider with a `state` (also bound to that browser) and a PKCE challenge; the callback exchanges the code with the secret and the verifier, asks once who the person is, and sends the browser back to the page with a one-time code that `POST /auth/exchange` turns into a session, once, within a minute; a new identity creates a user, a known one finds it; a Google identity whose verified email is the email of a local account signs that account in and is linked to it, an unverified or a Facebook email joins nothing; a signed-in user links an identity through `POST /auth/{provider}/link` and then signs in either way as the same user; an identity cannot be unlinked while it is the only way to sign in (`409`); a provider without keys is not offered and answers `404` |
| Resilience | A database failing while a token is resolved on the WebSocket → a guest, the connection kept; on a route → `502` without the database's text; a callback with another `state`, from another browser, replayed, or for another provider → refused, nothing created; a provider that fails, refuses or names nobody → the page is told, nothing is created; an identity that belongs to another account is not linked |
| Secrets | A password is stored as an scrypt hash with its own salt and parameters; the code waiting for an email as a salted hash, in RAM; a session token as its SHA-256; no access token of a provider is stored; none of the three, nor a client secret, is in a response it does not belong to, a log line or a span |

> Gated here with the provider's HTTP and the SMTP server replaced by fakes. A real
> sign-in with Google and with Facebook needs an application registered at each, a real
> code by email needs an SMTP account: neither has been run.

---

## 2. Integration Gates

Each gate wires a boundary and proves the two contracts line up.

| Gate | Boundary checked | Pass criteria |
|---|---|---|
| **VAD → Orchestrator → STT** | speech_end actually triggers transcription of the buffered utterance | A spoken clip produces correct text; buffer boundaries are right (no clipped first/last word) |
| **STT → LLM** | STT output shape is exactly what the LLM step consumes | Golden STT text flows into LLM with no reformatting glue; non-ASCII Vietnamese survives intact |
| **LLM → TTS (streaming)** | sentence-by-sentence text drives sentence-by-sentence audio | TTS starts on the first sentence while the LLM is still generating; no wait-for-whole |
| **TTS → Frontend (audio)** | audio format out == audio format the player decodes | Audio plays cleanly; lip-sync reacts to it; no decode errors |
| **Orchestrator ↔ Frontend (WebSocket)** | message types/shapes match both ways | tracking updates, speech-out, and interrupt all round-trip; unknown message rejected safely |
| **Device Router → STT** | both backends satisfy the same STT contract | Metal backend and CUDA backend produce the same output shape and both pass the §1.3 gate |

---

## 3. Pipeline Gate (end-to-end turn)

The expensive gate — a real turn, run sparingly, before a milestone.

| Type | Gate criteria |
|---|---|
| Functional | One full turn works: person present → speaks a Vietnamese utterance → avatar answers in Vietnamese voice with lip-sync. Barge-in works. No tool involved; the knowledge base, the memory and the persona are optional beside the turn and gated on their own (§1.8, §1.10, §1.11); so is sign-in (§1.12) |
| Performance | **First audio < ~3.5s** (the committed internal target); **end-to-end < 10s** (SLA); dead-air gap is covered by an immediate acknowledging cue |
| Resilience | Runs a sustained session without crashing or leaking resources; a mid-turn cloud error (LLM/TTS) degrades gracefully; weak-network behavior is observed and noted (cloud calls are the real latency risk) |
| Cross-device | The pipeline gate passes on **both** target machines — MacBook M5 (Metal) and Windows + RTX (CUDA) |

---

## 4. Golden Dataset Shapes (what the gates need)

The gates above depend on two golden datasets. Defining their **shape now** ensures the
sets you build later actually feed the gates. (Content of the sets is created
separately.)

### 4.1 STT golden dataset

Purpose: measure **WER** of the STT module per backend.

| Field | Description |
|---|---|
| `id` | Stable identifier for the sample |
| `audio` | Path to a Vietnamese utterance recording (realistic kiosk conditions: short, conversational, some with background noise) |
| `reference_text` | The exact correct Vietnamese transcription (ground truth) |
| `tags` (optional) | e.g. `clean`, `noisy`, `fast-speech`, `numbers`, `finance-terms` — to see WER per condition |

- **Size (MVP):** ~20–30 utterances is enough for a meaningful gate; weighted toward
  realistic kiosk phrasing and the finance vocabulary the avatar will hear.
- **WER method:** normalize both sides the same way (lowercase, strip punctuation,
  normalize Vietnamese diacritics + numbers), then standard WER = (S+D+I)/N. Fix the
  normalization once and reuse it, so results are comparable across backends and runs.
- **Gate use:** mean WER ≤ 15% (confirm threshold against the first real set); report
  WER per `tag` so a weak condition (e.g. noisy) is visible, not hidden in the average.

### 4.2 LLM golden dataset

Purpose: deterministically check the **form** of the answer (not content quality).

| Field | Description |
|---|---|
| `id` | Stable identifier |
| `input_text` | The user utterance text fed to the LLM (as if from STT) |
| `expect` | Form expectations, not an exact string: `language: vi`, `max_sentences: 3`, `non_empty: true`, `in_persona: true`, `no_tool_or_markup: true` |
| `tags` (optional) | e.g. `greeting`, `finance-question`, `off-topic`, `edge-empty` — to cover the input variety |

- **Size (MVP):** ~20–30 representative inputs, including a few edge cases (empty-ish
  input, off-topic, very long input).
- **Scoring:** each `expect` field is a programmatic check (language detect, sentence
  count, emptiness, persona heuristic, markup/tool-leak scan) → pass/fail per case.
- **Gate use:** ≥ 95% of cases pass all form checks (confirm threshold against the
  first real set). Content/answer-quality scoring is explicitly deferred to backlog.

### 4.3 VAD golden dataset

Purpose: check the one thing that matters for MVP — **tell human voice from background
noise**. Deliberately simple: binary classification, no timeline/segment labels.

| Field | Description |
|---|---|
| `id` | Stable identifier |
| `audio` | A short clip |
| `label` | `speech` (contains human voice) or `noise` (background noise / silence only) |

- **Size (MVP):** ~20–30 clips, split between `speech` and `noise`, with the `noise`
  side drawn from realistic kiosk background (ambient hum, distant chatter, silence).
- **Metric:** **recall on `speech`** (did it catch the voice?) and **false-trigger
  rate on `noise`** (did it wrongly fire?). No WER, no boundary timing.
- **Gate use:** recall ≥ 95% on `speech`, false-trigger ≤ 5% on `noise` (confirm
  thresholds against the first real set).

> Boundary accuracy (start/end timing) and end-of-speech latency are checked by direct
> measurement during integration/pipeline testing, not by this dataset — keeping the
> golden set simple.

---

## Backlog

- Content-quality scoring for LLM answers (beyond form checks) — deferred; MVP gates
  form only.
- Viseme/phoneme-accurate lip-sync gate — deferred (MVP is amplitude-driven).
- Expand golden datasets beyond ~30 samples and add per-condition WER thresholds once
  real kiosk recordings are available.
- Knowledge retrieval golden set (question → expected passage) and a recall@K gate —
  deferred; the first version gates the flow, not the retrieval quality.
