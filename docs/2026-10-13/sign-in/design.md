---
author: linhbk3
date: 2026-10-13
status: in-progress
agent: avatar
modules: accounts, orchestrator, expression, memory, persona
summary: Design of the sign-in layer — the two modes, where identity is resolved, the session and its lifetime on a kiosk, the data, the routes, the WebSocket messages and the keys
---

# Sign-in — design

Why and what are in [`plan.md`](plan.md). This file is the shape.

## 1. The two modes

| | Guest | Signed-in user |
|---|---|---|
| How one gets there | Default: first page load, sign-out, an invalid or expired token | Signing in (a local account, or Google / Facebook), then the token is presented; or opening the page with a session that has not expired |
| User id in the backend | `None` | The opaque id of the `users` row |
| Talks to the avatar | Yes | Yes |
| Short-term memory (the conversation in progress) | Yes, in RAM | Yes, in RAM |
| Knowledge base answers | Yes | Yes |
| Profile in the prompt (long-term memory) | No | Yes |
| Persona | The default one | Their fields over the default ones |
| Turns recorded, gathered into proposed facts | No, nothing is written | Yes |
| `/memory`, `/persona` | `401` | Their own data only |
| Profile page | The sign-in form | Their profile and their avatar's persona |

There is no third state. A request or a connection that cannot be tied to a valid
session is a guest; it is never an error to be a guest.

```mermaid
stateDiagram-v2
    [*] --> Guest: the page loads
    Guest --> SignedIn: signs in, or creates an account
    SignedIn --> Guest: signs out
    SignedIn --> Guest: the session expires (not used for too long, or too old)
    SignedIn --> Guest: leaves the kiosk, only when AUTH_LOGOUT_ON_LEAVE is on
    SignedIn --> SignedIn: reloads or reopens the page (the session has not expired)
    Guest --> Guest: reloads the page
```

Every arrow starts a new conversation: a conversation belongs to one mode and one user
from its first utterance to its close.

## 2. Where identity is resolved

```mermaid
flowchart TB
    subgraph FE["Frontend (browser)"]
        authui["Sign-in form, mode shown on the page"]
        session["Session of the page: the token, the user shown"]
        clients["HTTP clients: memory, persona"]
        wsclient["WebSocket client"]
    end

    subgraph BE["Backend"]
        authapi["/auth routes"]
        dependency["One dependency: who is behind this request"]
        wsendpoint["WebSocket endpoint (main)"]
        identity["current_user_id(): the single seam"]
        sessions["Sessions: open, resolve, revoke, expire"]
        authenticator["Authenticator: credentials in, a user out (local accounts first)"]
        orch["Orchestrator: a user id or None, never a token"]
        features["Memory, persona routes"]
    end

    appdb[("Application database: users, credentials, sessions")]

    authui -->|"user name + password"| authapi
    authapi --> authenticator
    authapi --> sessions
    authapi -->|"token, user"| session
    session --> clients
    session --> wsclient
    clients -->|"Authorization: Bearer"| dependency
    wsclient -->|"auth message"| wsendpoint
    dependency --> identity
    wsendpoint --> identity
    identity --> sessions
    dependency --> features
    wsendpoint -->|"user id or None"| orch
    orch -->|"a user's conversation closed: why"| wsendpoint
    wsendpoint -->|"revoke on leaving"| sessions
    sessions --> appdb
    authenticator --> appdb
```

- `current_user_id()` is still the only function that answers "who is this". Order:
  `AUTH_DEV_USER_ID` when it is set (development, tests); otherwise the token → its
  session, if it exists, is not revoked and has not expired → its user; otherwise
  `None`.
- The HTTP routes of the memory and the persona take the user from one shared
  dependency in `features/accounts/`; it reads the `Authorization` header. A guest is
  `401` there, as today.
- The WebSocket endpoint in `main.py` handles the `auth` message itself: it resolves
  the token and calls the orchestrator with a user id or `None`. Every other message
  goes to the orchestrator untouched.
- The core imports nothing of this. The orchestrator gains one operation (change the
  user of the open connection) and one notification to its wiring (a user's
  conversation closed, and why).

## 3. The session

- **Token**: 32 random bytes from the system generator, given once to the page. The
  database holds its SHA-256, so a copy of the database does not sign anyone in.
- **Kept by the page** in `localStorage`: the person stays signed in across a reload,
  a closed tab and a restart of the browser, until the session expires. The page
  writes every text with `textContent`, which is what keeps a token readable by
  scripts acceptable here; one origin over HTTPS with an HttpOnly cookie is the
  stronger form and is left for the deployment.
- **The session is kept and expires by time.** Whichever comes first ends it:

  | What ends it | Rule | Key |
  |---|---|---|
  | Signing out | The session is revoked at once | |
  | No use | No request and no turn for this long; every use pushes the end back | `AUTH_SESSION_IDLE_MINUTES` (30) |
  | Age | Whatever happened, counted from the sign-in | `AUTH_SESSION_MAX_HOURS` (12) |
  | Leaving the kiosk (optional) | The orchestrator closed the user's conversation for `absence` or `idle` | `AUTH_LOGOUT_ON_LEAVE` (0 = off) |

  Both durations are settings: a kiosk in a public place takes minutes, a device used
  by one person takes days (`AUTH_SESSION_IDLE_MINUTES=43200`,
  `AUTH_SESSION_MAX_HOURS=720`). The expiry is enforced by the server on every use of
  the token; the page is only told.
- **What a kept session means on a shared kiosk**: between the moment a person walks
  away and the moment the session expires, whoever stands in front of the kiosk talks
  to the avatar as that person, with their profile, and can open their profile page.
  The timeout is the only thing that bounds it, unless `AUTH_LOGOUT_ON_LEAVE` is
  switched on. The page shows who is signed in beside the "Hồ sơ" button at all times,
  with "Đăng xuất" one click away.

- **One session per sign-in.** Signing in again elsewhere does not end the first one;
  each ends by the rules above. Expired and revoked rows are deleted at startup and
  when a session of the same user is opened.

### Signing in while talking

```mermaid
sequenceDiagram
    autonumber
    actor P as Person
    participant F as Browser
    participant A as /auth routes
    participant M as WebSocket endpoint (main)
    participant O as Orchestrator
    participant D as Application database

    Note over F,O: connected as a guest, possibly in a conversation
    P->>F: user name, password
    F->>A: POST /auth/login
    A->>D: check the password, open a session
    A-->>F: token, user
    F->>M: auth (token)
    M->>D: resolve the token
    M->>O: the user of this connection is now this id
    O-->>F: conversation (none): the guest conversation is closed
    M-->>F: session (the user)
    Note over F: the page shows the user, the profile page opens on their data
    P->>F: speaks
    Note over O: a new conversation opens for the user, with their profile and persona
```

### Leaving the kiosk (only with `AUTH_LOGOUT_ON_LEAVE=1`)

Off by default: the session is kept and ends by its timeouts. Switched on, for a kiosk
where nobody should inherit a session:

```mermaid
sequenceDiagram
    autonumber
    participant F as Browser
    participant M as WebSocket endpoint (main)
    participant O as Orchestrator
    participant S as Sessions
    participant Y as Memory

    Note over O: nobody seen for CONVERSATION_ABSENCE_SEC, or nothing said for CONVERSATION_IDLE_SEC
    O->>Y: the conversation of the user is closed (read in the background, as today)
    O-->>F: conversation (none)
    O->>M: a user's conversation closed: absence
    M->>S: revoke the session of this connection
    M->>O: the user of this connection is now a guest
    M-->>F: session (guest)
    Note over F: the token is dropped, the page shows a guest
```

A conversation closed by the "Cuộc trò chuyện mới" button (`reset`) does not sign out:
the person is still there.

## 4. Accounts

- **Authenticator**: one boundary, a proof of who one is in → a user id, or a refusal.
  Two kinds: `local` (a user name and a password kept here) and external providers
  (Google, Facebook: §4.1). Whatever the kind, the outcome is the same session.
- **Local accounts**: an email address (compared without case) and a password
  (`AUTH_PASSWORD_MIN_CHARS`, 8). The password is stored as an scrypt hash with its own
  random salt and its parameters, so they can be raised later; the comparison takes the
  same time whether it matches or not.
- **Creating an account** at the kiosk takes two steps, allowed while
  `AUTH_ALLOW_REGISTRATION` is 1 (the default):
  1. `POST /auth/register` with the email and a password: a six-digit code is sent to
     the address. Nothing is created; what waits is held in RAM, the password hashed
     and the code hashed.
  2. `POST /auth/register/verify` with the code: the account is created and the person
     signed in. The code is good for `AUTH_OTP_TTL_MINUTES` (10) and
     `AUTH_OTP_MAX_ATTEMPTS` (5) tries, after which a new one has to be asked for; a
     new one can be asked for every `AUTH_OTP_RESEND_SEC` (60) and replaces the one
     before.
- **One email, one account.** The email is the key of the local account, so a second
  account on it cannot exist. See §4.1 for what this means with Google and Facebook.
- **Sending the code**: `MAIL_BACKEND=smtp` sends it through an SMTP server (`SMTP_*`
  keys, standard library). `MAIL_BACKEND=log`, the default, writes the mail to the
  backend log and sends nothing, so that a machine without a mail server can create
  accounts; it proves nothing about who owns the address, and the backend says so at
  startup. A mail that cannot be sent answers `502` and leaves nothing waiting.
- **Refusals say nothing useful to a guesser**: a wrong email and a wrong password give
  the same `401` and take the same time. After `AUTH_LOGIN_MAX_ATTEMPTS` (5) failures
  on one email within `AUTH_LOGIN_WINDOW_MINUTES` (10), further attempts on it answer
  `429` until the window has passed, right password or not. Asking for an account on
  an email that has one answers `409`: the form has to tell the person to sign in, so
  whether an address has an account can be learned from it.

### 4.1 Google and Facebook

A person can sign in with a Google or a Facebook account instead of a password kept
here, and a signed-in person can link one to the account they already have.

- **How**: the standard OAuth 2 authorization-code flow with PKCE. The backend sends
  the browser to the provider, receives the code on its callback route, exchanges it
  for an access token and asks the provider who the person is (their id at that
  provider, a display name, an email when given). Nothing of the provider is kept
  after that: no access token, no refresh token. The avatar never acts on the person's
  Google or Facebook account; it only learns who they are.
- **No new dependency**: the calls are plain HTTPS requests (`httpx`, already used),
  and the identity is read from the provider's own "who am I" endpoint, so no token
  signature has to be verified here.
- **An identity is (provider, the person's id at that provider)**, never an email. The
  first sign-in with an identity nobody has creates a user; the next ones find it.
- **Linking is explicit.** A signed-in person starts the same flow from the profile
  page and the identity is attached to their account; afterwards either way of signing
  in reaches the same user, the same memory, the same persona.
- **One email, one account, where both sides proved the address.** An identity nobody
  has yet, whose email the provider says it verified (Google's `email_verified`), and
  which is the email of a local account, signs that account in and is attached to it:
  the address was proved here by a code and there by the provider. Nothing else is
  joined by email: Facebook does not say whether an email is verified, and joining on
  it would hand an account to whoever claims the address, so a Facebook identity with
  the same email stays a user of its own. That gap in "one email, one account" is
  open, as is a person who signs in with a provider first and creates a local account
  on the same email afterwards.
  An identity that already belongs to another account is refused (`409`).
- **Unlinking** is allowed while another way of signing in remains.
- **Coming back from the provider**: the callback does not put the session token in a
  URL. It redirects to the page with a one-time code, valid once and for one minute,
  which the page exchanges for the token (`POST /auth/exchange`). The redirect reloads
  the page, so the WebSocket reconnects; the conversation starts anew, as with any
  change of mode.
- **What it needs outside the code**, per provider: an application registered at Google
  / Meta, its client id and secret in `.env`, and the callback URL declared there
  (`https://…` or `http://localhost…`: both providers refuse anything else). The kiosk
  must reach `accounts.google.com` / `facebook.com`. A provider whose keys are not set
  is simply not offered.
- **On a public screen** the person types their Google or Facebook password into the
  kiosk's browser, which they have no reason to trust. Both providers have a flow made
  for this (the kiosk shows a short code or a QR, the person approves on their own
  phone): it plugs in behind the same boundary and is the better form for a shared
  kiosk. It is named here and not designed.

```mermaid
sequenceDiagram
    autonumber
    actor P as Person
    participant F as Browser
    participant A as /auth routes
    participant G as Google or Facebook
    participant D as Application database

    P->>F: "Đăng nhập với Google"
    F->>A: GET /auth/google/start
    A-->>F: redirect to the provider (state, PKCE challenge)
    F->>G: the person signs in and agrees
    G-->>F: redirect to the callback, with a code
    F->>A: GET /auth/google/callback
    A->>G: exchange the code, ask who this is
    G-->>A: id at the provider, display name
    A->>D: find the identity, or create the user; open a session
    A-->>F: redirect to the page with a one-time code
    F->>A: POST /auth/exchange
    A-->>F: token, user
    Note over F: from here on, as after any sign-in
```

## 5. Data

Three new tables in the application database; `users` is unchanged.

```mermaid
erDiagram
    users ||--o| credentials : "signs in with"
    users ||--o{ identities : "is known at a provider as"
    users ||--o{ sessions : "is signed in through"
    users {
        string id PK "opaque"
        string display_name
        datetime created_at
        datetime last_seen_at
    }
    credentials {
        string email PK "lower case, proved by a code"
        string user_id FK "unique"
        string password_hash "scrypt, with its salt and its parameters"
        int failed_attempts
        datetime first_failure_at
        datetime updated_at
    }
    identities {
        string provider PK "google, facebook"
        string subject PK "the person's id at the provider"
        string user_id FK
        string email "as given, shown only"
        datetime created_at
    }
    sessions {
        string id PK "SHA-256 of the token"
        string user_id FK
        datetime created_at
        datetime last_seen_at
        datetime revoked_at
    }
```

`features/accounts/store.py` keeps all their SQL, on SQLAlchemy Core like the rest. The
end of a session is not stored: it is computed from `created_at`, `last_seen_at` and the
two timeouts each time the token is used, so a change of either timeout applies to the
sessions that are open.

## 6. HTTP routes

A contract between `backend/features/accounts/api.py` and `frontend/src/auth/`.

| Route | Body | Answer |
|---|---|---|
| `POST /auth/register` | `{"email", "password", "display_name"?}` | `202 {"email", "expires_in_sec", "resend_in_sec"}`: a code was sent, no account exists yet; `409` the email has an account; `422` an email or a password that is not acceptable; `403` registration is switched off; `429` a code was just sent (`Retry-After`); `502` the mail could not be sent |
| `POST /auth/register/verify` | `{"email", "code"}` | `201 {"token", "user": {"id", "display_name"}, "expires_at"}`: the account is created; `401` a wrong code; `410` no code waits for this email (expired, tried too often, never asked): ask for a new one |
| `POST /auth/login` | `{"email", "password"}` | `200`, same shape as verify; `401` wrong email or password; `429` too many attempts |
| `POST /auth/logout` | | `204`, with or without a valid token |
| `GET /auth/session` | | `200 {"user": {"id", "display_name"}, "expires_at"}`, or `200 {"user": null}` for a guest |
| `GET /auth/providers` | | `200 {"providers": ["google", ...], "registration": true}`: what the form may offer |
| `POST /auth/{provider}/link` | | `200 {"url"}`: where the signed-in user's browser goes to attach the provider to their account (a start with a one-time ticket, since a navigation carries no token); `401` a guest; `404` a provider that is not configured |
| `GET /auth/{provider}/start` | `?ticket=` from the link | `302` to the provider, with the state of the flow in a cookie; `404` a provider that is not configured |
| `GET /auth/{provider}/callback` | the provider's `code` and `state` | `302` to the page with a one-time code, or with an error name; `409` (as an error name) the identity belongs to another account |
| `POST /auth/exchange` | `{"code"}` | `200`, same shape as login; `401` an unknown, used or expired code |
| `GET /auth/identities` | | `200 {"identities": [{"provider", "email", "created_at"}]}` of the signed-in user; `401` a guest |
| `DELETE /auth/identities/{provider}` | | `204`; `409` it is the only way left to sign in; `401` a guest |

Every route: `502` when the database fails, without its text. The token is read from
`Authorization: Bearer <token>`. `GET /auth/session` is how the page learns, after a
reload, whether the token it kept is still good.

Unchanged: `/memory` and `/persona` (their `401` now means "no valid session"),
`/knowledge` and `/health` (no identity).

## 7. WebSocket messages

| Message | Direction | Shape | Meaning |
|---|---|---|---|
| `auth` | client → server | `{"type": "auth", "token": "<token>" \| null}` | Who is behind this connection from now on; `null` = a guest. Sent after `connected` and whenever the page signs in or out |
| `session` | server → client | `{"type": "session", "user": {"id", "display_name"} \| null}` | The mode the connection is in. Answers every `auth`, and is sent unasked when the server ends the session (leaving, expiry) |

- `auth` is the first frame a page sends, before the microphone is streamed, with
  `null` when it has no token. A page that reconnects (a reload, a network drop) is
  taken for the user of the session the connection had until that first frame, so the
  conversation of a signed-in user survives a reload; a connection whose first frame
  is anything else is a guest from that frame on.
- An `auth` that changes the user closes the conversation in progress (`user_changed`)
  and cancels the turn that is running; an `auth` naming the user already set changes
  nothing.
- A token that resolves to nobody answers `session` with `null`: the page drops it.

## 8. Keys

In `backend/tuning.py` unless `config.py` is named (secrets and addresses), commented
out in `.env.example`.

| Key | Default | Meaning |
|---|---|---|
| `AUTH_DEV_USER_ID` (`config.py`, existing) | empty | Set: every connection and request is this user, without sign-in (development, tests) |
| `AUTH_ALLOW_REGISTRATION` | 1 | 0: accounts cannot be created at the kiosk |
| `AUTH_PASSWORD_MIN_CHARS` | 8 | Shortest password accepted |
| `AUTH_OTP_TTL_MINUTES` | 10 | How long the code sent to an email is good for |
| `AUTH_OTP_MAX_ATTEMPTS` | 5 | Wrong tries before a new code has to be asked for |
| `AUTH_OTP_RESEND_SEC` | 60 | The wait before another code can be asked for |
| `MAIL_BACKEND` (`config.py`) | `log` | `smtp` sends the code; `log` writes it to the backend log and sends nothing |
| `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` (`starttls`) (`config.py`), `SMTP_TIMEOUT_SEC` (10) | | The SMTP server of `MAIL_BACKEND=smtp` |
| `AUTH_SESSION_IDLE_MINUTES` | 30 | A session without a request or a turn for this long has expired |
| `AUTH_SESSION_MAX_HOURS` | 12 | A session never lives longer, counted from the sign-in |
| `AUTH_LOGOUT_ON_LEAVE` | 0 | 1: the session also ends when the person leaves or goes silent |
| `AUTH_SESSION_CHECK_SEC` | 30 | How often the session of the connected page is looked at, to tell the page when it has expired |
| `AUTH_PROVIDER_TIMEOUT_SEC` | 10 | Max wait for a provider during a sign-in |
| `AUTH_GOOGLE_CLIENT_ID`, `AUTH_GOOGLE_CLIENT_SECRET` (`config.py`) | empty | The application registered at Google; empty = Google is not offered |
| `AUTH_FACEBOOK_CLIENT_ID`, `AUTH_FACEBOOK_CLIENT_SECRET` (`config.py`) | empty | The application registered at Meta; empty = Facebook is not offered |
| `AUTH_PUBLIC_URL` (`config.py`) | empty | The address of the backend as the provider reaches it back, for the callback |
| `AUTH_PAGE_URL` (`config.py`) | empty | The address of the page the callback returns to |
| `AUTH_GOOGLE_*_URL`, `AUTH_GOOGLE_SCOPE`, `AUTH_FACEBOOK_*_URL`, `AUTH_FACEBOOK_SCOPE` (`config.py`) | the public endpoints | Where each provider is asked, and for what |
| `AUTH_LOGIN_MAX_ATTEMPTS` | 5 | Failed sign-ins on one email before it is held |
| `AUTH_LOGIN_WINDOW_MINUTES` | 10 | The window those attempts are counted in, and how long it is held |

The frontend needs no new `VITE_*` value.

## 9. The page

- Beside the "Hồ sơ" button, the mode: "Khách" with "Đăng nhập", or the display name
  with "Đăng xuất".
- The profile page of a guest shows the sign-in form in place of today's note: email,
  password, "Đăng nhập", "Tạo tài khoản" while registration is allowed, and one button
  per provider that is configured ("Đăng nhập với Google", "… với Facebook"). "Tạo tài
  khoản" leads to a second step: the address the code was sent to, a field for the
  code, "Xác nhận", "Gửi lại mã" and "Quay lại".
- The profile page of a signed-in user has a block "Tài khoản liên kết": the providers
  attached to the account, "Liên kết" for those that are not, "Gỡ" for those that are.
- `frontend/src/auth/`: the session of the page (the token, the user, who to tell when
  it changes), the client of `/auth`, the form. The memory and persona clients and the
  WebSocket client take the token from it; a `401` on any of them, or a `session`
  message with `null`, puts the page back in guest mode.

## 10. What is protected, and what is not

- A password and a token are never written to a log, a span or the capture folder; a
  span names the user by its opaque id only. The one exception is chosen, not
  accidental: with `MAIL_BACKEND=log` the mail carrying a code is the log line.
- Passwords are hashed, tokens are hashed, the comparison of either is constant-time.
- The provider's flow is protected the usual way: a `state` tied to the browser that
  started it, PKCE, a callback URL fixed in the configuration and never taken from the
  request, a one-time code instead of a token in the URL.
- One user never reaches another's data: this was already true of every store and does
  not change; sign-in only decides which id is used.
- **Not solved here**: the page and the API speak plain HTTP on the dev machine, so a
  password and a token cross the local network in clear; outside `localhost` this needs
  HTTPS (the browser already requires it for the microphone). `CORS_ALLOW_ORIGINS` is
  `*` by default and should name the page's origin in a deployment. The knowledge
  routes stay open to everyone.
