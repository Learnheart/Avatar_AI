/**
 * Client of the backend knowledge API (backend/features/knowledge/api.py).
 *
 * Every method rejects with an Error whose message can be shown to the user.
 */

import { API_URL, KNOWLEDGE_MAX_FILE_MB } from '../config.js'
import { apiErrorMessage } from './format.js'

const DOCUMENTS_URL = `${API_URL}/knowledge/documents`

async function request(url, options) {
  let response
  try {
    response = await fetch(url, options)
  } catch {
    throw new Error('Không kết nối được máy chủ')
  }
  if (!response.ok) {
    throw new Error(apiErrorMessage(response.status, KNOWLEDGE_MAX_FILE_MB))
  }
  return response
}

export class KnowledgeClient {
  /** → [{ id, name, size_bytes, chunk_count, created_at }] */
  async list() {
    const response = await request(DOCUMENTS_URL)
    return (await response.json()).documents
  }

  /** Upload one File; resolves with the indexed document. */
  async upload(file) {
    const body = new FormData()
    body.append('file', file, file.name)
    const response = await request(DOCUMENTS_URL, { method: 'POST', body })
    return response.json()
  }

  async remove(id) {
    await request(`${DOCUMENTS_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  }
}
