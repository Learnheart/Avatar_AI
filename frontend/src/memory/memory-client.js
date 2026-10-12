/**
 * Client of the backend memory API (backend/features/memory/api.py): the items of
 * the person's profile.
 *
 * Every method rejects with an Error whose message can be shown to the user and whose
 * `status` is the HTTP status (NETWORK_FAILURE when the request got no answer).
 */

import { API_URL } from '../config.js'
import { NETWORK_FAILURE, apiErrorMessage } from './format.js'

const ITEMS_URL = `${API_URL}/memory/items`

function failure(status) {
  const error = new Error(apiErrorMessage(status))
  error.status = status
  return error
}

async function request(url, options) {
  let response
  try {
    response = await fetch(url, options)
  } catch {
    throw failure(NETWORK_FAILURE)
  }
  if (!response.ok) throw failure(response.status)
  return response
}

function json(method, body) {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export class MemoryClient {
  /** → [{ id, category, text, status, source, ... }], active and pending. */
  async list() {
    const response = await request(ITEMS_URL)
    return (await response.json()).items
  }

  /** Add an item typed by the person; resolves with the item. */
  async add(category, text) {
    const response = await request(ITEMS_URL, json('POST', { category, text }))
    return response.json()
  }

  /** Change the fields given in `changes` ({ text?, category? }); resolves with the item. */
  async update(id, changes) {
    const response = await request(
      `${ITEMS_URL}/${encodeURIComponent(id)}`, json('PATCH', changes),
    )
    return response.json()
  }

  async remove(id) {
    await request(`${ITEMS_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  }
}
