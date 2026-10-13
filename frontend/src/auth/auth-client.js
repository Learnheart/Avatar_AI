/**
 * Client of the backend sign-in API (backend/features/accounts/api.py).
 *
 * Every method rejects with an Error whose message can be shown to the user and whose
 * `status` is the HTTP status (NETWORK_FAILURE when the request got no answer, SUPERSEDED
 * when the person on the page changed before the answer came: to be dropped silently).
 */

import { API_URL } from '../config.js'
import { NETWORK_FAILURE } from '../memory/format.js'
import {
  authErrorMessage,
  authHeaders,
  linkErrorMessage,
  registerErrorMessage,
  sessionErrorMessage,
  unlinkErrorMessage,
  verifyErrorMessage,
} from './format.js'
import { pageSession } from './session.js'

const AUTH_URL = `${API_URL}/auth`

const JSON_BODY = (response) => response.json()

function failure(status, message) {
  const error = new Error(message(status))
  error.status = status
  return error
}

/**
 * One call presenting `token`. `read` takes what the caller needs out of the answer
 * (null: nothing); `message` turns the status of a refusal into its text.
 */
async function send(token, read, path, options, message) {
  let response
  try {
    response = await fetch(`${AUTH_URL}${path}`, {
      ...options,
      headers: { ...options.headers, ...authHeaders(token) },
    })
  } catch {
    throw failure(NETWORK_FAILURE, message)
  }
  if (!response.ok) throw failure(response.status, message)
  return read ? read(response) : null
}

/**
 * A call made for whoever is on the page now: it fails with SUPERSEDED when another
 * person is on the page once it has answered (session.js).
 */
function request(read, path, options = {}, message = authErrorMessage) {
  return pageSession.whileCurrent((token) => send(token, read, path, options, message))
}

/**
 * A call of a route that acts for the signed-in user. A 401 there means the session
 * is over: it reads so, and the page becomes a guest if it still holds that token.
 */
function sessionRequest(read, path, options = {}, message = authErrorMessage) {
  const text = (status) => sessionErrorMessage(status, message)
  return pageSession.whileCurrent(
    (token) => send(token, read, path, options, text), { endsOn401: true },
  )
}

function json(body) {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export class AuthClient {
  /**
   * → { providers: [...], registration: bool }: what the form may offer. The same for
   * everyone: it is not an answer about who is on the page.
   */
  async offered() {
    return send(null, JSON_BODY, '/providers', {}, authErrorMessage)
  }

  /**
   * Ask for an account: the backend sends a code to the email and creates nothing yet.
   * Only the address is sent; the password is given with the code.
   * Resolves with { email, expires_in_sec, resend_in_sec }.
   */
  async register(email) {
    return request(JSON_BODY, '/register', json({ email }), registerErrorMessage)
  }

  /**
   * Give back the code sent to the email, with the password of the new account: the
   * account is created. Resolves with the session ({ token, user, expires_at }).
   */
  async verify(email, code, password) {
    return request(
      JSON_BODY, '/register/verify', json({ email, code, password }), verifyErrorMessage,
    )
  }

  /** Sign in; resolves with the session. */
  async login(email, password) {
    return request(JSON_BODY, '/login', json({ email, password }))
  }

  /**
   * End on the backend the session `token` opens. The token is handed in: the page
   * signs out first, so it holds none (or another person's) when this is sent.
   */
  async logout(token) {
    await send(token, null, '/logout', { method: 'POST' }, authErrorMessage)
  }

  /** → the user the page's token signs in, or null: it is over, or there is none. */
  async user() {
    return (await request(JSON_BODY, '/session')).user
  }

  /**
   * Collect the sign-in a provider's callback left for the page; resolves with the
   * session. `verifier` proves this page started that sign-in.
   */
  async exchange(code, verifier) {
    return request(JSON_BODY, '/exchange', json({ code, verifier }))
  }

  /** Where the browser goes to sign in with a provider, bound to `challenge`. */
  startUrl(provider, challenge) {
    const query = new URLSearchParams({ challenge })
    return `${AUTH_URL}/${encodeURIComponent(provider)}/start?${query}`
  }

  /** → where the browser of the signed-in user goes to link a provider to their account. */
  async linkUrl(provider) {
    const path = `/${encodeURIComponent(provider)}/link`
    return (await sessionRequest(JSON_BODY, path, { method: 'POST' })).url
  }

  /**
   * Attach to the signed-in user the identity a provider proved: `code` is what the
   * callback left for the page. Resolves with { provider }.
   */
  async confirmLink(code) {
    return sessionRequest(JSON_BODY, '/identities', json({ code }), linkErrorMessage)
  }

  /** → [{ provider, email, created_at }], the accounts linked to the signed-in user. */
  async identities() {
    return (await sessionRequest(JSON_BODY, '/identities')).identities
  }

  /** Detach a provider from the account. */
  async unlink(provider) {
    await sessionRequest(
      null, `/identities/${encodeURIComponent(provider)}`, { method: 'DELETE' },
      unlinkErrorMessage,
    )
  }
}
