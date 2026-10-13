---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: accounts, orchestrator, expression, memory, persona
summary: Tests for sign-in — the two modes, sessions, accounts, the WebSocket messages and signing out on leaving
---

# Test plan — sign-in

## Unit tests (sandbox)

| Area | File | Cases |
|---|---|---|
| Passwords | `test_accounts_passwords.py` | a hash verifies its password and no other; two hashes of one password differ (salt); the stored parameters are the ones used to verify; a damaged hash verifies nothing and raises nothing |
| Store | `test_accounts_store.py` | credentials are read back by user name whatever its case; a user name is unique; a session is found by the hash of its token and never by the token; revoking; expiring by idle time and by age; the failed attempts of a user name are counted and reset; the tables survive a reopen; `users` is unchanged |
| Authenticator | `test_accounts_auth.py` | the right password gives the user id; a wrong password and an unknown user name give the same refusal; a held user name is refused with the right password until the window has passed; registration creates the user and its credentials, refuses a taken name, a short password, a name that is not acceptable, and everything when it is switched off |
| Sessions | `test_accounts_sessions.py` | a token opened resolves to its user and moves `last_seen_at`; an unknown, a revoked, an idle and an old token resolve to nobody; revoking one session leaves the user's other sessions; expired rows are deleted |
| Identity | `test_identity.py` | `AUTH_DEV_USER_ID` set → that user whatever the token; empty → the user of a valid token; no token, a bad token → `None`; a failing database raises `DatabaseError` |
| `/auth` routes | `test_auth_api.py` | register → 201 with a token that then works on `/memory`; login → 200; a wrong password → 401 with the body of an unknown name; too many attempts → 429; logout → 204 and the token no longer works; `/auth/session` → the user, and `null` for a guest, an expired and a revoked token; registration switched off → 403; a failing database → 502 without its text; no response, no log line and no span holds a password or a token |
| Other routes | `test_memory_api.py`, `test_persona_api.py` | a valid token reaches the user's data; no token, a bad one, an expired one → 401; one user's token never reaches another's data; `AUTH_DEV_USER_ID` still works without a token |
| WebSocket | `test_ws.py`, `test_main.py` | a connection starts as a guest; `auth` with a valid token → `session` with the user, and the next turn is given that user id; `auth` with `null` or a bad token → `session` with `null`; a token is never handed to the orchestrator; a failing database while resolving → a guest, the connection kept |
| Orchestrator | `test_orchestrator.py` | changing the user closes the conversation in progress (`user_changed`) and cancels the running turn; setting the same user changes nothing; the wiring is told when a user's conversation closes and why; nothing is told for a guest |
| Expiry | `test_accounts_sessions.py`, `test_auth_api.py` | a session used within `AUTH_SESSION_IDLE_MINUTES` stays and its end moves; one not used for that long is refused; one older than `AUTH_SESSION_MAX_HOURS` is refused however recently it was used; both follow the config; `expires_at` given to the page is the earlier of the two |
| Signed out on leaving | `test_main.py` | by default (`AUTH_LOGOUT_ON_LEAVE=0`) `absence` and `idle` keep the session; with 1 they revoke it and send `session` with `null`, and `reset` does not; the memory still reads the closed conversation |
| Providers | `test_auth_providers.py` (the provider's HTTP faked) | `start` redirects with a `state` and a PKCE challenge; a callback with another `state`, or without the verifier, is refused; a new identity creates a user and a session; a known one finds its user; the access token is stored nowhere; the one-time code works once, within its minute, and is not the token; a provider without keys → 404 and is absent from `/auth/providers`; a provider that fails or refuses → the page is told, nothing is created |
| Linking | `test_auth_providers.py` | a signed-in user links an identity and then reaches the same user id by either way; an identity of another account → refused, nothing changed; two accounts with the same email are not merged; unlinking the only way to sign in → 409; a guest → 401 |
| Config | `test_config.py` | the defaults; a minimum password length, an attempt limit or a duration under 1 → refused |
| Frontend | `frontend/tests/auth.test.js` | the session of the page: starts as a guest, holds and drops the token, tells its listeners once per change; the `Authorization` header with and without a token; a token kept across a restart of the page and dropped when the server says it expired; the one-time code read from the address and removed from it; the messages of the form for 401, 403, 409, 422, 429, 502 and a network failure; what a user name and a password are refused for before any request |

## End-to-end tests (real environment, `RUN_E2E=1`)

- `tests/e2e/test_real_sign_in.py` (a backend of its own on a temporary database,
  `AUTH_DEV_USER_ID` empty): register over HTTP, present the token on the WebSocket, add
  a memory item naming the person, ask the avatar for the person's name → the answer
  holds it; sign out, ask again → it does not. First audio and end-to-end times are
  reported against the gate (3.5 s / 10 s).
- Manual, at the kiosk, with `AUTH_SESSION_IDLE_MINUTES=2`: sign in, talk, wait three
  minutes → the page shows a guest and the avatar does not know the person. With
  `AUTH_LOGOUT_ON_LEAVE=1`: walk out of the camera's view for
  `CONVERSATION_ABSENCE_SEC` → the same, without waiting for the timeout.
- Manual, with real keys: sign in with Google, then with Facebook; link Google to a
  local account and sign in both ways → the same profile.
- Manual, in the browser: create an account, reload the page (still signed in), sign
  out, open "Hồ sơ" (the sign-in form).
