/**
 * The session of the page: who is signed in on it, if anyone.
 *
 * A signed-in page holds the token the backend gave it and the user to show; a guest
 * holds nothing. Both are kept in the browser, so the person stays signed in across a
 * reload and a restart, until the backend says the session is over. Everything that
 * depends on who is signed in (the HTTP clients, the WebSocket, the page) reads it here
 * and is told when it changes.
 *
 * No DOM, no network: the storage is handed in (unit-tested with a fake one).
 */

import { SUPERSEDED, UNAUTHORIZED } from '../memory/format.js'

const STORAGE_KEY = 'avatar.session'
const VERIFIER_KEY = 'avatar.auth.verifier'

/** The failure of a request whose answer came after the person on the page changed. */
export function supersededFailure() {
  const error = new Error('')
  error.status = SUPERSEDED
  return error
}

export class PageSession {
  /** `storage`: something like localStorage, or null to keep the session in memory. */
  constructor(storage = null) {
    this._storage = storage
    this._listeners = []
    this._token = null
    this._user = null
    this._generation = 0
    this._restore()
  }

  /** The token to present to the backend, or null for a guest. */
  get token() { return this._token }

  /** The signed-in user ({ id, display_name }), or null for a guest. */
  get user() { return this._user }

  /** The page signed in: `session` is what the backend answered ({ token, user }). */
  signIn(session) {
    this._set(session.token, session.user)
  }

  /**
   * Grows each time another person is on the page: the token or the user id changed.
   * A name the backend corrected is the same person.
   */
  get generation() { return this._generation }

  /**
   * The backend said who `token` signs in: the user, or nobody. It is an answer about
   * that token only: when the page holds another one by now, nothing changes.
   */
  confirm(token, user) {
    if (!token || token !== this._token) return
    if (user) this._set(token, user)
    else this.signOut()
  }

  /**
   * The backend refused `token`. The page becomes a guest only when that is still the
   * token it holds: a late refusal of an earlier token does not sign out whoever
   * signed in since. Returns whether the page was signed out.
   */
  rejected(token) {
    if (!token || token !== this._token) return false
    this.signOut()
    return true
  }

  /** The page is a guest: it signed out, or the backend said the session is over. */
  signOut() {
    this._set(null, null)
  }

  /**
   * Run `call(token)`, a request made for whoever is on the page now. When another
   * person is on the page once it has answered, its outcome belongs to nobody: it is
   * replaced by a SUPERSEDED failure. With `endsOn401`, a 401 means the backend no
   * longer knows the token that was presented, and signs out that token only.
   */
  async whileCurrent(call, { endsOn401 = false } = {}) {
    const generation = this._generation
    const token = this._token
    let result
    let error = null
    try {
      result = await call(token)
    } catch (err) {
      error = err
    }
    if (this._generation !== generation) throw supersededFailure()
    if (!error) return result
    if (endsOn401 && error.status === UNAUTHORIZED) this.rejected(token)
    throw error
  }

  /** `fn(session)` is called each time the token or the user changes. */
  onChange(fn) {
    this._listeners.push(fn)
  }

  _set(token, user) {
    const signedIn = Boolean(token && user)
    const next = signedIn ? { token, user: { id: user.id, display_name: user.display_name } } : null
    const same = (next?.token ?? null) === this._token
      && (next?.user.id ?? null) === (this._user?.id ?? null)
      && (next?.user.display_name ?? null) === (this._user?.display_name ?? null)
    if (same) return
    const samePerson = (next?.token ?? null) === this._token
      && (next?.user.id ?? null) === (this._user?.id ?? null)
    if (!samePerson) this._generation += 1
    this._token = next?.token ?? null
    this._user = next?.user ?? null
    this._keep(next)
    for (const fn of this._listeners) fn(this)
  }

  _restore() {
    try {
      const kept = JSON.parse(this._storage?.getItem(STORAGE_KEY) ?? 'null')
      if (typeof kept?.token === 'string' && kept.token && kept.user?.id) {
        this._token = kept.token
        this._user = { id: kept.user.id, display_name: kept.user.display_name }
      }
    } catch {
      // A storage that cannot be read, or holds something else: a guest.
    }
  }

  _keep(next) {
    try {
      if (next) this._storage?.setItem(STORAGE_KEY, JSON.stringify(next))
      else this._storage?.removeItem(STORAGE_KEY)
    } catch {
      // A storage that refuses (private mode, full): signed in until the page closes.
    }
  }
}

// ── The answer of the backend to an `auth` frame ────────────────────────────

/** What the page does with a `session` message of the WebSocket. */
export const SESSION_VERDICT = Object.freeze({
  IGNORE: 'ignore',   // not about the token the page holds now
  ACCEPT: 'accept',   // the token signs in `message.user`
  DROP: 'drop',       // the token opens no session: the page is a guest
  RETRY: 'retry',     // the backend could not tell: present the token again later
})

/**
 * Decide what a `session` message means for the page (pure).
 *
 * `message`: { user, reply_to, status } as the backend sent it. `lastAuth`: { id, token }
 * of the last `auth` frame the page sent, or null. `token`: the token the page holds now.
 * A message says something about the page's token only when it answers that last frame
 * and that frame carried this token; anything else is the late answer to an earlier
 * frame (the `auth` of page load, the token of the person before) and is ignored.
 */
export function sessionVerdict(message, lastAuth, token) {
  const matched = Boolean(token)
    && lastAuth != null
    && typeof message?.reply_to === 'string'
    && message.reply_to === lastAuth.id
    && lastAuth.token === token
  if (!matched) return SESSION_VERDICT.IGNORE
  switch (message.status) {
    case 'rejected':
    case 'ended':
      return SESSION_VERDICT.DROP
    case 'unresolved':
      return SESSION_VERDICT.RETRY
    case 'signed_in':
      return message.user?.id ? SESSION_VERDICT.ACCEPT : SESSION_VERDICT.IGNORE
    default:
      return SESSION_VERDICT.IGNORE
  }
}

// ── The verifier of a sign-in at a provider ─────────────────────────────────

/** Keep the verifier of the sign-in this page starts, until the person is back. */
export function keepVerifier(storage, verifier) {
  try {
    storage?.setItem(VERIFIER_KEY, verifier)
    return storage?.getItem(VERIFIER_KEY) === verifier
  } catch {
    return false
  }
}

/**
 * The verifier of the sign-in this page started, or null when it started none. It is
 * removed: a verifier proves one sign-in.
 */
export function takeVerifier(storage) {
  try {
    const verifier = storage?.getItem(VERIFIER_KEY) ?? null
    storage?.removeItem(VERIFIER_KEY)
    return verifier || null
  } catch {
    return null
  }
}

/** The storage of the browser, or null where there is none (tests, a locked-down browser). */
function browserStorage() {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

/** The one session of this page. */
export const pageSession = new PageSession(browserStorage())
