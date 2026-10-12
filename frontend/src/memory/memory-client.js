/**
 * Client of the backend memory API (backend/features/memory/api.py): the items of
 * the person's profile and the descriptions they imported.
 *
 * Every method rejects with an Error whose message can be shown to the user and whose
 * `status` is the HTTP status (NETWORK_FAILURE when the request got no answer).
 */

import { API_URL, MEMORY_IMPORT_MAX_FILE_MB } from '../config.js'
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

/** `message` turns the status of a refusal into its text (apiErrorMessage by default). */
async function request(url, options, message = apiErrorMessage) {
  let response
  try {
    response = await fetch(url, options)
  } catch {
    throw failure(NETWORK_FAILURE, message)
  }
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
    await request(`${ITEMS_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  }

  // ── Imports ─────────────────────────────────────────────────────────────

  /** → [{ id, name, size_bytes, item_count, created_at }] */
  async listImports() {
    const response = await request(IMPORTS_URL)
    return (await response.json()).imports
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
    const response = await request(IMPORTS_URL, { method: 'POST', body }, importFailure)
    return response.json()
  }

  /**
   * The original of an import (an entry of listImports()), unchanged: resolves with
   * { blob, name }, the bytes and the file name to save them under.
   */
  async importContent(entry) {
    const response = await request(`${IMPORTS_URL}/${encodeURIComponent(entry.id)}/content`)
    return { blob: await response.blob(), name: entry.name }
  }

  /** Delete an import, and the items made from it unless `keepItems`. */
  async removeImport(id, { keepItems }) {
    const items = keepItems ? 'keep' : 'delete'
    await request(`${IMPORTS_URL}/${encodeURIComponent(id)}?items=${items}`, { method: 'DELETE' })
  }
}
