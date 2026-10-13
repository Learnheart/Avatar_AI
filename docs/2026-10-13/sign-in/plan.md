---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: accounts, orchestrator, expression, memory, persona
summary: Sign-in for the kiosk — every person starts as a guest and signs in, with a local account or with Google / Facebook, to get their memory and their avatar's persona; the session is kept and expires after a configurable time; a session token replaces AUTH_DEV_USER_ID as the source of the user
---

# Sign-in: guest and signed-in user

The shape of the layer (diagrams, data, routes, messages, keys) is in
[`design.md`](design.md); the tests in [`test-plan.md`](test-plan.md). This file holds
why, what and the order of the work.

## Problem statement

- The long-term memory and the persona belong to a signed-in user, but there is no
  sign-in. `AUTH_DEV_USER_ID` stands in for it: one id for the whole process, set in
  `.env`. Empty, everyone is a guest and the memory and the persona cannot be used at
  all; set, everyone who walks up to the kiosk is that one user.
- A kiosk is shared. Whatever identifies a person must end when they walk away, or the
  next person talks to the avatar with the previous person's memory.
- The seam is ready: `current_user_id()` (`features/accounts/identity.py`) is the only
  place that says who the user is, the orchestrator already closes a conversation when
  the user changes, and every per-user route already answers `401` to a guest.

## Requirements

- **Two modes, and nothing else.** A person is a *guest* or a *signed-in user*.
  - Guest: the default, with no step to take. Talks to the avatar, with the short-term
    memory of the conversation and the default persona. Nothing about a guest is
    written to disk. The memory and persona routes answer `401`.
  - Signed-in user: everything a guest has, plus their profile in every turn, their
    avatar's persona, what the avatar gathers from their conversations, the profile
    page.
- A person signs in and out on the page without reloading it and without losing the
  microphone or the avatar.
- Signing in or out starts a new conversation: what a guest said is never attributed to
  the user who signs in afterwards.
- A session is kept: the person stays signed in across a reload and a restart of the
  browser. It ends when the person signs out or when it expires, after a time without
  use or at a maximum age, both configurable.
- A person can sign in with a Google or a Facebook account, and link one to the
  account they already have, so that either way reaches the same memory.
- A person creates their own account at the kiosk (this can be switched off).
- No secret is stored as given: neither a password nor a session token can be read
  back from the database, the logs or the traces.
- The core still never resolves identity: `main.py` does, and hands the orchestrator a
  user id or `None`.
- `AUTH_DEV_USER_ID` keeps working for development and for the tests.

## Decisions made

- **A session token, opaque and kept on the server.** Signing in gives the page a
  random token; the server keeps only its hash, with the user and an expiry, in a
  `sessions` table. No signed token (JWT): a session must be revocable the moment a
  person walks away, which a self-contained token is not, and a lookup in a local
  database costs nothing here.
- **The token travels in the `Authorization` header and in a WebSocket message**, not
  in a cookie. The page (Vite, port 3000) and the API (port 8000) are different
  origins, and a browser cannot set a header on a WebSocket. So: HTTP routes read
  `Authorization: Bearer <token>`; on the WebSocket the page sends an `auth` message
  with the token, again whenever it changes. `main.py` resolves it and tells the
  orchestrator the user; the orchestrator never sees a token.
- **Signing in does not reconnect the WebSocket.** The `auth` message changes the user
  of the open connection; the orchestrator closes the conversation in progress as it
  already does for a user change, and answers with a `session` message naming the mode
  the connection is now in.
- **The session is kept and ends by time** (changed on 2026-10-13, on review): it
  expires after `AUTH_SESSION_IDLE_MINUTES` (30) without use and at
  `AUTH_SESSION_MAX_HOURS` (12) of age whatever happened; the page keeps the token
  across restarts of the browser. Signing a person out when they leave the kiosk
  (conversation closed for `absence` or `idle`) becomes an option that is off by
  default, `AUTH_LOGOUT_ON_LEAVE`. **What this costs on a shared kiosk**: until the
  session expires, the next person in front of it is taken for the one who signed in.
  The timeout is the control; the page always shows who is signed in.
- **Accounts are local first: a user name and a password**, behind one small boundary
  (an *authenticator*: credentials in → a user or a refusal out), so another way of
  proving who one is can be added without touching sessions, modes or the turn. The
  password is hashed with scrypt from the standard library: no new dependency.
- **Google and Facebook are authenticators of the same kind**: the OAuth 2
  authorization-code flow with PKCE, the person's identity read from the provider and
  nothing else kept (no access to their Google or Facebook data). An identity is the
  pair (provider, id at the provider); linking is an explicit act of a signed-in
  person, and two accounts are never merged because their emails match. A provider
  whose keys are not set is not offered, so the feature costs nothing where it is not
  wanted. Company single sign-on plugs in the same way. Signing in from one's own
  phone by a code shown on the kiosk, so that no Google or Facebook password is typed
  on a public screen, is the better form for a shared kiosk; it is named in the design
  and not designed.
- **A local account is an email address, proved by a code** (changed on 2026-10-13,
  after the first manual test, where the "user name" of the form refused what the
  person typed and nothing told who was behind an account). The name a person signs in
  with is their email. Creating an account takes two steps: the email and a password
  are given, a six-digit code is sent to that address, and the account exists only once
  the code is given back, within `AUTH_OTP_TTL_MINUTES` (10) and
  `AUTH_OTP_MAX_ATTEMPTS` (5) tries; a new code can be asked for every
  `AUTH_OTP_RESEND_SEC` (60). What waits for its code is held in RAM, the password
  already hashed and the code hashed too.
  - **One email, one account.** The email is the key of the local account. A sign-in
    with Google whose email Google says is verified, and which is the email of a local
    account, signs in that account and links Google to it: the address was proved on
    both sides. Nothing else is merged by email: Facebook does not say whether an email
    is verified, so a Facebook identity stays an account of its own even with the same
    address. That gap is open.
  - **Sending the code** goes through one small boundary (`backend/utils/mailer.py`):
    `MAIL_BACKEND=smtp` sends it (standard library, `SMTP_*` keys), `MAIL_BACKEND=log`,
    the default, writes the mail to the backend log instead, so that a dev machine
    without a mail server can still create accounts. The `log` backend proves nothing
    about who owns the address and says so at startup: a deployment sets `smtp`.
- **`current_user_id()` stays the single seam** and gains the token: `AUTH_DEV_USER_ID`
  set → that user, as today; otherwise the token → its session → its user; otherwise a
  guest. One FastAPI dependency in `features/accounts/` replaces the copies in the
  memory and persona routes.
- **The user name is not the user id.** The id is opaque and is what every per-user
  table and every trace carries; the user name lives with the password only.
- **No change to the `users` table.** There is no migration tool: the tables that are
  missing are created at startup, and an existing table is never altered. Credentials
  and sessions are new tables.
- **Failing safe.** A database that fails while a token is resolved means a guest, not
  a refused connection: the person can still talk. An unknown, expired or revoked
  token is a guest too, and the page is told so it can drop the token.

### Not in this plan

- Roles and who may manage the knowledge base. `/knowledge/documents` is open to
  everyone today, guests included, and stays so here. It deserves its own decision.
- Company single sign-on, sign-in by a code approved on one's phone, recognising a
  person by face or voice.
- Anything done on the person's Google or Facebook account: only who they are is read.
- Changing or resetting a password, deleting an account and its data.
- Several people talking at once: the kiosk still has one connection and one
  conversation.
- Serving the page and the API from one origin over HTTPS (which would allow an
  HttpOnly cookie instead of a token held by the page).

## Implementation approach

Six slices, each tested and committed on its own.

1. **Sessions and accounts on the server.** Tables `credentials` and `sessions`, their
   store, the password hashing, the local authenticator, the session service (open,
   resolve, revoke, expire). No route yet.
2. **Identity from a token.** `current_user_id()` takes the token; one shared
   dependency for the HTTP routes; the routes `/auth/register`, `/auth/login`,
   `/auth/logout`, `/auth/session`; the attempt limit.
3. **The WebSocket.** The `auth` and `session` messages; `main.py` resolving the token
   and the orchestrator changing the user of the open connection.
4. **Expiry, and signing out on leaving.** Sessions expire by idle time and by age,
   on every use and at startup; with `AUTH_LOGOUT_ON_LEAVE`, the orchestrator tells the
   wiring why a user's conversation closed and the session is revoked on `absence` and
   `idle`.
5. **Google and Facebook.** The `identities` table, the start, callback and exchange
   routes, linking and unlinking. Needs an application registered at each provider.
6. **The page.** `frontend/src/auth/`: the session held by the page, the sign-in form
   in place of the guest note with its provider buttons, the mode shown beside the
   profile button, the linked accounts on the profile page, the token on every request
   and on the WebSocket.

Then the docs: `CLAUDE.md` / `AGENTS.md`, `docs/ARCHITECTURE.md` (sign-in is no longer
planned), `docs/quality-gates.md`, `CHANGELOG.md`, `.env.example`.

## To-do

- [x] The decisions above reviewed by a human (2026-10-13): the session is kept and
      expires by timeout, Google and Facebook are added, and the code is written before
      the applications are registered at the providers
- [ ] Outside the code, by a human: an application registered at Google and at Meta,
      their client ids and secrets, the callback URL declared at each
- [x] Slice 1 — credentials, sessions, password hashing, local authenticator, with unit
      tests
- [x] Slice 2 — `current_user_id()` from a token, the shared dependency, the `/auth`
      routes and the attempt limit, with unit tests
- [x] Slice 3 — the `auth` and `session` WebSocket messages, with unit tests
- [x] Slice 4 — sessions expired by idle time and age, signed out on `absence` and
      `idle` when asked, with unit tests
- [x] Slice 5 — Google and Facebook: sign-in, linking, unlinking, with unit tests (the
      provider faked)
- [x] Slice 6 — the sign-in form, the mode and the linked accounts on the page, with
      unit tests, `npm run build`
- [x] `CLAUDE.md` / `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/quality-gates.md`,
      `CHANGELOG.md`, `.env.example`
- [x] Real run (2026-10-13, `tests/e2e/test_real_sign_in.py`, Groq answering 503 and
      the Databricks backup taking over): an account is created over HTTP and its
      avatar named "Bông"; on one WebSocket connection, asked its name, the avatar
      answers a guest "mình là trợ lý ảo của bạn", the signed-in user "mình là Bông,
      trợ lý ảo của bạn đây", and after the sign-out "Mình là trợ lý ảo" again, the
      revoked token being answered with a guest's session
- [x] The latency gate. On that first run, on the Databricks backup: end to end 6.3 s,
      first audio 5.2 s against 3.5 s, the test failing on its last assertion. Run again
      the same day once Groq answered (the whole `tests/e2e` suite, 8 passed, no
      fallback and no warning in the backend logs): first audio 1.9 s, end to end 3.0 s,
      with the same three answers. A session that expires by its
      timeout was run in the unit tests only
- [ ] Real sign-in with Google and with Facebook, and one of them linked to a local
      account
- [x] The page in a real browser (headless Chrome driven over CDP, 2026-10-13, on the
      real app and routes with a temporary database, a fake orchestrator and a fake
      Google answering at once): a guest shown as "Khách" with the form, "Tạo tài
      khoản" and "Đăng nhập với Google"; a user name refused before any request; an
      account created and the page signed in, its token reaching `/memory`; still signed
      in after a reload; Google linked from the profile page and listed with its email;
      signed out and the browser's kept session gone; signed in again with Google as the
      same user; a wrong password refused with its message. In the backend log the
      connection changes to the user and back to a guest
- [ ] Accounts by email with a code sent to the address, one account per email, with
      unit tests; the form in two steps on the page
- [ ] By a human: an SMTP account for `MAIL_BACKEND=smtp`, and one real code received
      by email
- [ ] Seen while writing, to decide: the knowledge routes are still open to guests;
      there is no way to change a password or to delete an account
- [ ] Human verification
