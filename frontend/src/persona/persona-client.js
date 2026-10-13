/**
 * Client of the backend persona API (backend/features/persona/api.py): the personality
 * the person gives the avatar, as a dictionary of fields.
 *
 * Every method rejects with an Error whose message can be shown to the user and whose
 * `status` is the HTTP status (NETWORK_FAILURE when the request got no answer, SUPERSEDED
 * when the person on the page changed before the answer came: to be dropped silently).
 */

import { API_URL, PERSONA_FIELD_MAX_CHARS } from '../config.js'
import { authHeaders } from '../auth/format.js'
import { pageSession } from '../auth/session.js'
import { NETWORK_FAILURE } from '../memory/format.js'
import { personaErrorMessage, readErrorMessage } from './format.js'

const PERSONA_URL = `${API_URL}/persona`

function failure(status, message) {
  const error = new Error(message(status, PERSONA_FIELD_MAX_CHARS))
  error.status = status
  return error
}

const FIELDS = async (response) => (await response.json()).fields

/**
 * One call made for whoever is on the page now. `read` takes what the caller needs out
 * of the answer (null: nothing); `message` turns the status of a refusal into its text.
 * It fails with SUPERSEDED when another person is on the page once it has answered, and
 * a 401 makes the page a guest only if it still holds the token that was presented.
 */
function request(read, url, options, message = personaErrorMessage) {
  return pageSession.whileCurrent(async (token) => {
    let response
    try {
      response = await fetch(url, {
        ...options,
        headers: { ...options?.headers, ...authHeaders(token) },
      })
    } catch {
      throw failure(NETWORK_FAILURE, message)
    }
    if (!response.ok) throw failure(response.status, message)
    return read ? read(response) : null
  }, { endsOn401: true })
}

function json(method, body) {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export class PersonaClient {
  /** → { key: text }, the fields that are set. */
  async get() {
    return request(FIELDS, PERSONA_URL)
  }

  /** → { key: text }, the default persona: what the avatar uses for an empty field. */
  async defaults() {
    return request(FIELDS, `${PERSONA_URL}/defaults`)
  }

  /** Make `fields` the whole persona; resolves with the fields as they are stored. */
  async save(fields) {
    return request(FIELDS, PERSONA_URL, json('PUT', { fields }))
  }

  /** Remove the persona: the avatar is the default character again. */
  async clear() {
    await request(null, PERSONA_URL, { method: 'DELETE' })
  }

  /**
   * Have the backend read a description of the avatar: several seconds. Resolves with
   * the fields it found; nothing is stored.
   */
  async read(text) {
    return request(FIELDS, `${PERSONA_URL}/read`, json('POST', { text }), readErrorMessage)
  }
}
