---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: accounts, orchestrator, expression, stt, llm, tts, memory, persona
summary: Tests of the sign-in hardening — each finding of the review has a unit test that fails on the old code; the races are replayed against a running backend
---

# Test plan: sign-in hardening

Decisions `D1`–`D13` are those of `plan.md`.

## Unit tests (sandbox)

### Accounts — `tests/unit/test_accounts_auth.py`, `test_accounts_store.py`, `test_auth_api.py`

| Decision | Test |
|---|---|
| D1 | Two people ask for the same address with different passwords: the account has the password given with the code, the other password does not sign in |
| D1 | Nothing derived from a password waits in RAM between the two steps |
| D1 | An unacceptable password or name at verify answers 422 and consumes no try |
| D2 | 32 parallel wrong sign-ins with a limit of 5: at most 5 password checks, the rest are "too many" |
| D2 | A right password after the limit is still refused; a success resets the counter; the window restarts it |
| D2 | An unknown email still costs one password check (timing equaliser) |
| D3 | 16 parallel requests for one address send one mail; the others are told to wait |
| D3 | A mail that fails leaves nothing waiting, and does not remove a newer record |
| D4 | The sixth code for an address in the window is refused with the time to wait; so is the mail past the overall limit; both open again after the window |
| D4 | Wrong tries are not restarted by asking for a new code while the one before is still valid |
| D1, D4 | Routes: bodies with an unknown field are refused; 429 carries `Retry-After` |

### Providers — `tests/unit/test_auth_providers.py`, `test_auth_api.py`

| Decision | Test |
|---|---|
| D5 | A start without a challenge is refused |
| D5 | Exchange with the right verifier signs in; with a wrong or missing one answers 401 and the code is spent |
| D5 | The callback of a link attaches nothing; the confirmation by the ticket's user attaches it; by another user it is refused and nothing is attached; an unknown or expired code is 410; an identity that is another user's, or a second one of that provider, is 409 |
| D5 | The state cookie is `Secure` with an https public URL |
| D6 | Two parallel unlinks of the two last identities of an account without a password: one succeeds, one is refused, one way in remains |
| — | Still true: state single-use and checked against the cookie, PKCE S256, redirects only to configured URLs |

### Connection — `tests/unit/test_ws_auth.py`, `test_orchestrator.py`, `test_accounts_sessions.py`

| Decision | Test |
|---|---|
| D8 | Each `auth` is answered with its `id` and the right status: `signed_in`, `guest`, `rejected`, `unresolved` (database failing) |
| D8 | A session ended by the backend is told as `ended` with the id of the frame that opened it |
| D9 | `set_user("A")` waiting on the lock, then `set_user(None)`: the orchestrator ends as a guest |
| D9 | A sign-in and the end of the session close together, with a slow user lookup: the last `session` message, the orchestrator's user and the kept token agree |
| D9 | The end of a session that the connection no longer holds changes nothing |
| D7 | The periodic task deletes ended sessions and survives a failing database (`tests/unit/test_ws_auth.py`: the task belongs to the application lifespan) |

### Turn — `tests/unit/test_engine.py`, `test_history.py`

| Decision | Test |
|---|---|
| D11 | Kept unchanged: "Tôi muốn đăng ký kênh SMS Banking", "Ngân hàng có ủng hộ kênh thanh toán QR không", "Đăng ký kênh SMS", "Ngân hàng có ủng hộ cho kênh thanh toán QR không", the existing real sentences |
| D11 | Dropped whole: "Cảm ơn các bạn đã theo dõi.", "cám ơn các bạn đã xem video", "Hãy đăng ký kênh để ủng hộ mình nhé!", "Hẹn gặp lại các bạn trong những video tiếp theo.", "Thanks for watching!", the existing invented transcript |
| D11 | Mixed: "Lãi suất tiết kiệm bao nhiêu? Cảm ơn các bạn đã theo dõi." → the question; invented first, real second → the second; no punctuation → kept whole |
| D11 | The engine answers the kept part; the span carries the original and what was dropped; the threshold follows the config |
| D12 | Three kept turns, then one larger than the budget: the three remain |
| D13 | "3 ➕ 2 ➡ 5" keeps its meaning in words, and so does a plain arrow ("A → B"); "5 ★ sao" → "5 sao"; "▶️ Bắt đầu" → "Bắt đầu"; a keycap keeps its digit; the existing emoji cases |

### Page — `frontend/tests/` (`node --test`)

| Decision | Test |
|---|---|
| D8 | The decision helper: token dropped only for the last `auth` id, carrying the current token, with `rejected` or `ended`; kept for `unresolved`, for an older id, for a frame that carried another token; `user` taken only under the match |
| D10 | `generation` grows on sign-in, sign-out and a change of user, not on a confirmation of the same user |
| D10 | `rejected(token)` signs out for the current token, does nothing for another or none |
| D10 | 401 on a session route reads as an ended session; on sign-in it still reads as wrong credentials |
| D10 | The delay before the form forgets follows `expires_in_sec` |
| D5 | The verifier and its challenge: base64url SHA-256, a fresh verifier per start |

The DOM of the panels has no harness; it is covered by the real run.

## End-to-end (real environment)

No Groq call is needed: the checks drive the HTTP routes and the WebSocket of a running
backend (`MAIL_BACKEND=log`, a scratch `DATABASE_URL`) and the page in a browser.

1. Register → code from the log → account → sign in → sign out, in the page.
2. Finding 1 replayed: a second register of the same address with another password; the
   account has the password typed with the code.
3. Findings 2, 3 replayed with parallel HTTP requests against the running backend.
4. Finding 8: page load followed at once by a sign-in keeps the session; with the
   database made to fail the token stays in `localStorage`.
5. Findings 5, 6, G: sign in as A, sign out, sign in as B — no email, linked account,
   profile or persona of A on screen.
6. Finding 7: a revoked token → the "session ended" message, the page is a guest.
7. `tests/e2e` real turn is run once if Groq is reachable (the speech path changed:
   D11, D13); the numbers are reported, or the reason it could not run.

Not testable here: Google and Facebook (no client registered), SMTP.
