/**
 * Client of the backend memory API (backend/features/memory/api.py): the items of
 * the person's profile and the descriptions they imported.
 *
 * Every method rejects with an Error whose message can be shown to the user and whose
 * `status` is the HTTP status (NETWORK_FAILURE when the request got no answer, SUPERSEDED
 * when the person on the page changed before the answer came: to be dropped silently).
 */

import { API_URL, MEMORY_IMPORT_MAX_FILE_MB } from '../config.js'
import { authHeaders } from '../auth/format.js'
import { pageSession } from '../auth/session.js'
import { NETWORK_FAILURE, apiErrorMessage, importErrorMessage } from './format.js'

const ITEMS_URL = `${API_URL}/memory/items`
const IMPORTS_URL = `${API_URL}/memory/imports`

function failure(status, message) {
  const error = new Error(message(status))
  error.status = status
  return error
}

/** The text of a refused import, which has refusals of its own (size, type). */
function importFailure(status) {
  return importErrorMessage(status, MEMORY_IMPORT_MAX_FILE_MB)
}

const JSON_BODY = (response) => response.json()

/**
 * One call made for whoever is on the page now. `read` takes what the caller needs out
 * of the answer (null: nothing); `message` turns the status of a refusal into its text.
 * It fails with SUPERSEDED when another person is on the page once it has answered, and
 * a 401 makes the page a guest only if it still holds the token that was presented.
 */
function request(read, url, options, message = apiErrorMessage) {
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

export class MemoryClient {
  /** → [{ id, category, text, status, source, ... }], active and pending. */
  async list() {
    return (await request(JSON_BODY, ITEMS_URL)).items
  }

  /** Add an item typed by the person; resolves with the item. */
  async add(category, text) {
    return request(JSON_BODY, ITEMS_URL, json('POST', { category, text }))
  }

  /** Change the fields given in `changes` ({ text?, category? }); resolves with the item. */
  async update(id, changes) {
    return request(JSON_BODY, `${ITEMS_URL}/${encodeURIComponent(id)}`, json('PATCH', changes))
  }

  /**
   * Approve a proposal (a pending item): it becomes active, and the item it proposed
   * to replace is gone. `changes` ({ text?, category? }) corrects it in the same call.
   * Resolves with the item.
   */
  async approve(id, changes = {}) {
    return this.update(id, { ...changes, status: 'active' })
  }

  /** Delete an item; for a proposal, this rejects it. */
  async remove(id) {
    await request(null, `${ITEMS_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  }

  // ── Imports ─────────────────────────────────────────────────────────────

  /** → [{ id, name, size_bytes, item_count, created_at }] */
  async listImports() {
    return (await request(JSON_BODY, IMPORTS_URL)).imports
  }

  /**
   * Send a description of the person, a File or a pasted text, for the backend to
   * read: several seconds. Resolves with { import, items }, the items it made.
   */
  async importFile(file) {
    const body = new FormData()
    body.append('file', file, file.name)
    return this._import(body)
  }

  async importText(text) {
    const body = new FormData()
    body.append('text', text)
    return this._import(body)
  }

  async _import(body) {
    return request(JSON_BODY, IMPORTS_URL, { method: 'POST', body }, importFailure)
  }

  /**
   * The original of an import (an entry of listImports()), unchanged: resolves with
   * { blob, name }, the bytes and the file name to save them under.
   */
  async importContent(entry) {
    const blob = await request(
      (response) => response.blob(), `${IMPORTS_URL}/${encodeURIComponent(entry.id)}/content`,
    )
    return { blob, name: entry.name }
  }

  /** Delete an import, and the items made from it unless `keepItems`. */
  async removeImport(id, { keepItems }) {
    const items = keepItems ? 'keep' : 'delete'
    await request(
      null, `${IMPORTS_URL}/${encodeURIComponent(id)}?items=${items}`, { method: 'DELETE' },
    )
  }
}
