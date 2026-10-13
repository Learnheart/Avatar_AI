/**
 * Client of the backend persona API (backend/features/persona/api.py): the personality
 * the person gives the avatar, as a dictionary of fields.
 *
 * Every method rejects with an Error whose message can be shown to the user and whose
 * `status` is the HTTP status (NETWORK_FAILURE when the request got no answer).
 */

import { API_URL, PERSONA_FIELD_MAX_CHARS } from '../config.js'
import { authHeaders } from '../auth/format.js'
import { pageSession } from '../auth/session.js'
import { NETWORK_FAILURE, UNAUTHORIZED } from '../memory/format.js'
import { personaErrorMessage, readErrorMessage } from './format.js'

const PERSONA_URL = `${API_URL}/persona`

function failure(status, message) {
  const error = new Error(message(status, PERSONA_FIELD_MAX_CHARS))
  error.status = status
  return error
}

/** `message` turns the status of a refusal into its text. */
async function request(url, options, message = personaErrorMessage) {
  let response
  try {
    response = await fetch(url, {
      ...options,
      headers: { ...options?.headers, ...authHeaders(pageSession.token) },
    })
  } catch {
    throw failure(NETWORK_FAILURE, message)
  }
  // The backend no longer knows the session the page presents: the page is a guest.
  if (response.status === UNAUTHORIZED && pageSession.token) pageSession.signOut()
  if (!response.ok) throw failure(response.status, message)
  return response
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
    const response = await request(PERSONA_URL)
    return (await response.json()).fields
  }

  /** → { key: text }, the default persona: what the avatar uses for an empty field. */
  async defaults() {
    const response = await request(`${PERSONA_URL}/defaults`)
    return (await response.json()).fields
  }

  /** Make `fields` the whole persona; resolves with the fields as they are stored. */
  async save(fields) {
    const response = await request(PERSONA_URL, json('PUT', { fields }))
    return (await response.json()).fields
  }

  /** Remove the persona: the avatar is the default character again. */
  async clear() {
    await request(PERSONA_URL, { method: 'DELETE' })
  }

  /**
   * Have the backend read a description of the avatar: several seconds. Resolves with
   * the fields it found; nothing is stored.
   */
  async read(text) {
    const response = await request(
      `${PERSONA_URL}/read`, json('POST', { text }), readErrorMessage,
    )
    return (await response.json()).fields
  }
}
