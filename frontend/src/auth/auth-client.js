/**
 * Client of the backend sign-in API (backend/features/accounts/api.py).
 *
 * Every method rejects with an Error whose message can be shown to the user and whose
 * `status` is the HTTP status (NETWORK_FAILURE when the request got no answer).
 */

import { API_URL } from '../config.js'
import { NETWORK_FAILURE } from '../memory/format.js'
import {
  authErrorMessage, authHeaders, registerErrorMessage, unlinkErrorMessage, verifyErrorMessage,
} from './format.js'
import { pageSession } from './session.js'

const AUTH_URL = `${API_URL}/auth`

function failure(status, message) {
  const error = new Error(message(status))
  error.status = status
  return error
}

/** `message` turns the status of a refusal into its text. */
async function request(path, options = {}, message = authErrorMessage) {
  let response
  try {
    response = await fetch(`${AUTH_URL}${path}`, {
      ...options,
      headers: { ...options.headers, ...authHeaders(pageSession.token) },
    })
  } catch {
    throw failure(NETWORK_FAILURE, message)
  }
  if (!response.ok) throw failure(response.status, message)
  return response
}

function json(body) {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export class AuthClient {
  /** → { providers: [...], registration: bool }: what the form may offer. */
  async offered() {
    return (await request('/providers')).json()
  }

  /**
   * Ask for an account: the backend sends a code to the email and creates nothing yet.
   * Resolves with { email, expires_in_sec, resend_in_sec }.
   */
  async register(email, password) {
    const response = await request('/register', json({ email, password }), registerErrorMessage)
    return response.json()
  }

  /**
   * Give back the code sent to the email: the account is created. Resolves with the
   * session ({ token, user, expires_at }).
   */
  async verify(email, code) {
    const response = await request(
      '/register/verify', json({ email, code }), verifyErrorMessage,
    )
    return response.json()
  }

  /** Sign in; resolves with the session. */
  async login(email, password) {
    return (await request('/login', json({ email, password }))).json()
  }

  /** End the session of the page on the backend. */
  async logout() {
    await request('/logout', { method: 'POST' })
  }

  /** → the user the page's token signs in, or null: it is over, or there is none. */
  async user() {
    return (await (await request('/session')).json()).user
  }

  /** Collect the sign-in a provider's callback left for the page; resolves with the session. */
  async exchange(code) {
    return (await request('/exchange', json({ code }))).json()
  }

  /** Where the browser goes to sign in with a provider. */
  startUrl(provider) {
    return `${AUTH_URL}/${encodeURIComponent(provider)}/start`
  }

  /** → where the browser of the signed-in user goes to link a provider to their account. */
  async linkUrl(provider) {
    const response = await request(`/${encodeURIComponent(provider)}/link`, { method: 'POST' })
    return (await response.json()).url
  }

  /** → [{ provider, email, created_at }], the accounts linked to the signed-in user. */
  async identities() {
    return (await (await request('/identities')).json()).identities
  }

  /** Detach a provider from the account. */
  async unlink(provider) {
    await request(
      `/identities/${encodeURIComponent(provider)}`, { method: 'DELETE' }, unlinkErrorMessage,
    )
  }
}
