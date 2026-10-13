---
author: linhbk3
date: 2026-10-13
status: todo
agent: avatar
modules: orchestrator, expression, memory, persona
summary: Sign-in for the kiosk — every person starts as a guest, signs in with an account to get their memory and their avatar's persona, and is signed out when they leave; a session token replaces AUTH_DEV_USER_ID as the source of the user
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
- A session ends when the person signs out, when the person leaves the kiosk, or after
  a time without use; the next person is a guest.
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
- **The kiosk signs a person out when they leave.** When the orchestrator closes a
  signed-in user's conversation because nobody is there (`absence`) or nothing was
  said (`idle`), the session is revoked and the page goes back to guest. A new
  conversation asked with the button does not sign out. Without a conversation (the
  person only uses the profile page), the session ends after
  `AUTH_SESSION_IDLE_MINUTES` without a request.
- **Accounts are local first: a user name and a password**, behind one small boundary
  (an *authenticator*: credentials in → a user or a refusal out), so another way of
  proving who one is can be added without touching sessions, modes or the turn. The
  password is hashed with scrypt from the standard library: no new dependency.
  Company single sign-on, and signing in from a phone by scanning a code shown on the
  kiosk (so that no password is typed on a public screen), are the obvious next
  authenticators and are not designed here.
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
- Company single sign-on, sign-in by phone, recognising a person by face or voice.
- Changing or resetting a password, deleting an account and its data.
- Several people talking at once: the kiosk still has one connection and one
  conversation.
- Serving the page and the API from one origin over HTTPS (which would allow an
  HttpOnly cookie instead of a token held by the page).

## Implementation approach

Five slices, each tested and committed on its own.

1. **Sessions and accounts on the server.** Tables `credentials` and `sessions`, their
   store, the password hashing, the local authenticator, the session service (open,
   resolve, revoke, expire). No route yet.
2. **Identity from a token.** `current_user_id()` takes the token; one shared
   dependency for the HTTP routes; the routes `/auth/register`, `/auth/login`,
   `/auth/logout`, `/auth/session`; the attempt limit.
3. **The WebSocket.** The `auth` and `session` messages; `main.py` resolving the token
   and the orchestrator changing the user of the open connection.
4. **Signed out on leaving.** The orchestrator tells the wiring why a user's
   conversation closed; the session is revoked on `absence` and `idle`; idle sessions
   expire.
5. **The page.** `frontend/src/auth/`: the session held by the page, the sign-in form
   in place of the guest note, the mode shown beside the profile button, the token on
   every request and on the WebSocket.

Then the docs: `CLAUDE.md` / `AGENTS.md`, `docs/ARCHITECTURE.md` (sign-in is no longer
planned), `docs/quality-gates.md`, `CHANGELOG.md`, `.env.example`.

## To-do

- [ ] The decisions above reviewed by a human, in particular: accounts by user name and
      password, self-registration at the kiosk, signing out on leaving
- [ ] Slice 1 — credentials, sessions, password hashing, local authenticator, with unit
      tests
- [ ] Slice 2 — `current_user_id()` from a token, the shared dependency, the `/auth`
      routes and the attempt limit, with unit tests
- [ ] Slice 3 — the `auth` and `session` WebSocket messages, with unit tests
- [ ] Slice 4 — signed out on `absence` and `idle`, idle sessions expired, with unit
      tests
- [ ] Slice 5 — the sign-in form and the mode on the page, with unit tests,
      `npm run build`
- [ ] `CLAUDE.md` / `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/quality-gates.md`,
      `CHANGELOG.md`, `.env.example`
- [ ] Real run: register, sign in, a turn answered with the profile, walk away and come
      back as a guest; first audio and end-to-end times reported
- [ ] The page in a real browser: register, sign in, reload, sign out
- [ ] Human verification
