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

const STORAGE_KEY = 'avatar.session'

export class PageSession {
  /** `storage`: something like localStorage, or null to keep the session in memory. */
  constructor(storage = null) {
    this._storage = storage
    this._listeners = []
    this._token = null
    this._user = null
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

  /** The backend confirmed, or corrected, who the token signs in. */
  confirm(user) {
    if (user) this._set(this._token, user)
    else this.signOut()
  }

  /** The page is a guest: it signed out, or the backend said the session is over. */
  signOut() {
    this._set(null, null)
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
