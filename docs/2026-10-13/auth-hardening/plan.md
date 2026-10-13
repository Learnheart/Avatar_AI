---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: accounts, orchestrator, expression, stt, llm, tts, memory, persona
summary: Close the findings of the security review of sign-in — an account's password is set by whoever reads the mailbox, the limits hold under parallel requests, the page and the backend agree on who is signed in, nothing of one person is shown to the next — and fix the invented-sentence filter that swallowed real speech
---

# Sign-in hardening

## Problem

A review of the sign-in layer (`docs/2026-10-13/sign-in/`) and of the fixes that went in
with it found nine defects that must be closed and seven smaller ones. Three analysts
confirmed every one of them against the code. They fall into four groups.

**Accounts (backend)**

1. The code sent to an address is bound to the address, not to who asked. A second
   person who registers the same address replaces the waiting password; the owner of the
   mailbox types the newest code and the account is created with the other person's
   password.
2. The limit on wrong sign-ins is read, checked and written in separate steps: 32
   parallel wrong guesses with a limit of 5 are all checked and the counter ends at 1.
3. The wait before a new code is checked under a lock but the record is written after
   the mail: 16 parallel requests send 16 mails.
- A. `/auth/register` sends a mail to any address without limit, and a new code restarts
  the count of wrong tries.
- B. The hand-off code a provider sign-in leaves for the page, and the ticket that links a
  provider, are not bound to the browser that started: a link made by someone else signs
  the page into their account, or attaches the reader's provider identity to their
  account.
- C. Two parallel unlinks can remove the last way to sign in.
- D. Ended sessions are deleted at startup only.

**Who the page and the backend think is signed in**

7. A 401 on the session routes of `/auth` is shown as "Sai email hoặc mật khẩu" and the
   page stays signed in.
8. A `session` message with no user makes the page drop whatever token it holds now: the
   late answer to the `auth(null)` of page load undoes a sign-in that just finished, and a
   database failure (reported as "guest") deletes a good token.
9. `Orchestrator.set_user` compares before it takes the lock, and `main.py` writes the
   token, tells the orchestrator and tells the page in three unserialised steps: two
   changes close together leave the three disagreeing.

**What one person leaves for the next (frontend)**

5. The email stays in the form after sign-out; at the code step the password stays in
   page memory and in the hidden input with no expiry.
6. The linked accounts of the previous person stay on screen (and their "Gỡ" button
   would act on the next person's account).
- G. A late answer of a profile, persona or identities request is written into the next
  person's page; a late 401 for the previous token signs the next person out.

**The turn**

4. The filter for sentences Whisper invents drops the whole transcript on any phrase
   match, and the list holds phrases that are ordinary banking Vietnamese ("đăng ký
   kênh", "ủng hộ kênh"): real questions are answered with silence.
- E. A turn larger than the whole history budget evicts every kept turn.
- F. The emoji filter deletes symbols that carry meaning (➕ ➡) and leaves some emoji
  (▶ of "▶️").

## Requirements

- Every finding above is closed, with a unit test that fails on the old code.
- What the review found sound does not regress: session token hashing, scrypt and the
  timing equaliser for unknown emails, the code compared and counted inside the lock,
  OAuth state + PKCE, redirect targets from config only, no secret in a log.
- Limits that protect an account hold across workers where they are in the database
  (sign-in attempts, unlink). What waits in RAM (codes, OAuth flows) stays per process:
  one backend process serves one kiosk (stated in the sign-in design).
- The sign-in flow is drawn in the Dataflow part of `docs/ARCHITECTURE.html`.

## Decisions

**D1. The password is given with the code (finding 1).** `POST /auth/register` takes the
address only; `POST /auth/register/verify` takes the address, the code, the password and
the display name. Whoever can read the mailbox sets the password; a second asker can
only replace the code. Chosen over an opaque ticket: no extra state, and scrypt leaves
the unauthenticated route. The page still asks for email and password on one form and
keeps the password in memory until the code is typed, never in the hidden input; it
forgets everything when the code expires (finding 5).

**D2. A wrong sign-in is charged before the password is checked (finding 2)**, by one
conditional `UPDATE` (`AccountsStore.charge_attempt`): `rowcount == 0` is "too many".
Success resets the counter. No lock around scrypt; holds on PostgreSQL.

**D3. The waiting record is written in the critical section that checks the wait
(finding 3)**; the mail is sent outside the lock and a failed mail removes the record
only if it is still that record.

**D4. Mail limits (A).** Per address and for all addresses, per window, in RAM:
`AUTH_OTP_WINDOW_MINUTES` (60), `AUTH_OTP_MAX_CODES` (5), `AUTH_OTP_MAX_MAILS` (30);
both answer 429 with `Retry-After`. Guessing is bounded by codes per window × tries
per code: the wrong tries made on a code that is still valid carry over to the code
that replaces it (a code that expired or was dropped takes its tries with it). Known cost: a flood can close registration for the window; a limit per
client belongs to a reverse proxy.

**D5. Provider sign-in is bound to the page that started it (B).**
- Sign-in: the page keeps a random verifier in `sessionStorage` and opens
  `GET /auth/{provider}/start?challenge=<base64url(sha256(verifier))>`. The challenge
  travels with the flow to the hand-off code. `POST /auth/exchange` takes
  `{code, verifier}` and answers 401 when they do not match. A flow started without a
  challenge is refused (400).
- Link: the callback attaches nothing. It keeps the proved identity for one minute
  and leaves `#auth_link=<code>` (replacing `#auth_linked=<provider>`). The page posts
  `POST /auth/identities {code}` with its token; the identity is attached only when the
  token's user is the user the ticket was issued to. 200 `{provider}`; 401 no session;
  403 another user; 409 already linked elsewhere, or this user already has an identity
  of that provider; 410 unknown or expired code.
- The state cookie is `Secure` when `AUTH_PUBLIC_URL` is https.

**D6. Unlink is one transaction (C)**: list, check for another way in, delete, in
`AccountsStore` under its lock, the user's row locked for update.

**D7. Ended sessions are deleted every `AUTH_SESSION_CLEANUP_MINUTES` (60) (D)**, by a
task of the application lifespan, in a thread.

**D8. `auth` and `session` are matched (finding 8).** WebSocket contract:

```
page → backend   {"type": "auth", "token": <string|null>, "id": <string>}
backend → page   {"type": "session", "user": <{id, display_name}|null>,
                  "reply_to": <id of the auth frame|null>, "status": <status>}
```

`status`: `signed_in` (the token opened a session; `user` set), `guest` (no token was
presented), `rejected` (the token opens no session), `unresolved` (the database failed;
the connection is a guest's for now), `ended` (the backend ended the session of the
connection; `reply_to` is the frame that opened it). The page drops its token only when
`reply_to` is the last `auth` it sent, that frame carried the token it holds now, and
`status` is `rejected` or `ended`. It takes `user` only under the same match. On
`unresolved` it keeps the token and presents it again after a delay
(`VITE_AUTH_RETRY_MS`). An `auth` without `id` is answered with `reply_to: null`.

**D9. One lock for who is behind the connection (finding 9).** `Orchestrator.set_user`
compares inside `_turn_lock`. `main.py` holds one `asyncio.Lock` across "resolve the
token → remember it → `set_user` → send `session`", in `set_connection_user` and
`end_connection_session`; the latter is given the token that ended and does nothing
when the connection no longer holds it.

**D10. The page forgets on a change of person (5, 6, 7, G).**
- `PageSession.generation` grows whenever the token or the user id changes. The three
  HTTP clients (`auth`, `memory`, `persona`) note it before a request and fail with
  `SUPERSEDED` when it changed; panels drop such a failure silently (the persona panel
  reloads). `PageSession.rejected(token)` signs out only when `token` is still the
  current one; a 401 uses it instead of `signOut()`.
- The auth panel clears email, password, code, the waiting state and its messages on
  every session change and when the code expires; it empties the linked-account list
  when the user changes and when its request fails.
- 401 on the session routes (`GET/POST/DELETE /auth/identities`, `POST
  /auth/{provider}/link`) reads "Phiên đăng nhập đã hết, vui lòng đăng nhập lại", shown
  in the main message of the panel.

**D11. An invented sentence is cut, the rest is kept (finding 4).** The transcript is
split into sentences (`. ! ? …`, newline; no punctuation = one sentence). A sentence is
invented when it holds a listed phrase and at most `STT_HALLUCINATION_MAX_REST_WORDS`
(3) words remain without the phrases. Invented sentences are removed, the others kept
verbatim; the turn is refused only when nothing remains. The span keeps the original
text and what was dropped. The list holds only phrases
that name a video or its audience by themselves ("cảm ơn các bạn đã theo dõi", "like và
đăng ký kênh", "đăng ký kênh để ủng hộ", "không bỏ lỡ những video", "phụ đề được thực
hiện bởi"); a verb with "kênh" alone ("đăng ký kênh", "hãy đăng ký kênh", "ủng hộ kênh")
is not listed, because a bank has channels ("kênh SMS", "kênh thông báo"). When the two
aims conflict, keeping a real sentence wins: an invented sentence that gets through
reaches the LLM, which is less bad than silence at a real question. Known borderline
entries: "đăng ký kênh nhé", "cảm ơn các bạn đã xem" and "thank you for watching". Cost: an unpunctuated mix of a real question and
an invented tail is kept whole.

**D12. A turn larger than the budget is not kept and evicts nothing (E).**

**D13. Symbols with a spoken form are spoken (F).** Before the removal: ➕ "cộng", ➖
"trừ", ✖ ✕ "nhân", ➗ "chia"; arrows become a pause (", "), the plain ones (← ↑ → ↓ ↔,
⇒ …) as well as the emoji ones (➡ ⬅ …). The table is text
(`backend/prompts.py`). Any non-alphanumeric character followed by U+FE0F is an emoji
and is removed with it (a keycap keeps its digit). ★ and the rest of the blocks are
still deleted ("5 ★ sao" must not become "5 sao sao").

Not in this change: the behaviour with several backend processes or two tabs; a real run
against SMTP, Google and Facebook (no credentials on the dev machine).

## Implementation approach

Four slices, disjoint enough to be built side by side:

| Slice | Findings | Where |
|---|---|---|
| S1 turn | 4, E, F, 9 (orchestrator half) | `core/conversation/engine.py`, `history.py`, `prompts.py`, `tuning.py`, `core/orchestrator/orchestrator.py` |
| S2 accounts | 1, 2, 3, A | `features/accounts/accounts.py`, `store.py` (credentials), `api.py` (register routes), `tuning.py`, `config.py` |
| S3 connection + providers | 8 (backend), 9 (`main.py` half), B, C, D | `main.py`, `core/orchestrator/ws_manager.py`, `features/accounts/providers.py`, `store.py` (identities), `api.py` (provider routes) |
| S4 page | 5, 6, 7, 8 (page), G, and the page side of D1, D5, D8 | `frontend/src/auth/`, `main.js`, `ws-client.js`, `memory/`, `persona/`, `config.js` |

Then: `docs/2026-10-13/sign-in/design.md` follows the new contracts, the Dataflow part of
`docs/ARCHITECTURE.html` gains the sign-in flow with a Backlog row, `CHANGELOG.md` gets
its entries (the `/auth` and WebSocket changes are **Breaking**), `CLAUDE.md` and
`AGENTS.md` follow where their text names the changed behaviour.

## To-do

- [x] S1: invented sentences cut per sentence, phrase list revised (D11)
- [x] S1: oversized turn evicts nothing (D12)
- [x] S1: spoken symbols, U+FE0F rule (D13)
- [x] S1: `set_user` compares inside the lock (D9)
- [x] S2: password given with the code (D1)
- [x] S2: sign-in attempt charged in one statement (D2)
- [x] S2: waiting record written with the wait check (D3)
- [x] S2: mail limits per address and overall (D4)
- [x] S3: `auth` / `session` matched, tri-state resolution (D8)
- [x] S3: one lock for who is behind the connection (D9)
- [x] S3: provider sign-in bound by a page verifier, link confirmed by the page (D5)
- [x] S3: unlink in one transaction (D6)
- [x] S3: periodic deletion of ended sessions (D7)
- [x] S4: page side of D1, D5, D8
- [x] S4: generation guard, `rejected(token)`, panels forget on a change (D10)
- [x] `.env.example`, `frontend/.env.example` carry the new keys
- [x] Sign-in design doc, `ARCHITECTURE.html` Dataflow + Backlog, `CHANGELOG.md`, `CLAUDE.md` / `AGENTS.md`
- [x] Code review PASS
- [x] Real run: backend + page, register with `MAIL_BACKEND=log`, sign in, sign out, the races replayed against the running backend
- [x] QA sign-off
