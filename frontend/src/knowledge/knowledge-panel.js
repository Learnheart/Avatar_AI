/**
 * Knowledge panel — upload documents, choose which ones the avatar answers from (the
 * checkbox of each row), delete them.
 *
 * The markup is in index.html (#knowledge-toggle, #knowledge-panel). File names come
 * from the user: they are only ever written with textContent.
 */

import { KNOWLEDGE_EXTENSIONS, KNOWLEDGE_MAX_FILE_MB } from '../config.js'
import { formatBytes, isSupportedFile, usageSummary } from './format.js'

export class KnowledgePanel {
  constructor(client) {
    this._client = client
    this._panel = document.getElementById('knowledge-panel')
    this._toggle = document.getElementById('knowledge-toggle')
    this._drop = document.getElementById('knowledge-drop')
    this._input = document.getElementById('knowledge-input')
    this._list = document.getElementById('knowledge-list')
    this._message = document.getElementById('knowledge-message')
    this._summary = document.getElementById('knowledge-summary')
  }

  init() {
    this._input.accept = KNOWLEDGE_EXTENSIONS.join(',')
    document.getElementById('knowledge-hint').textContent =
      `${KNOWLEDGE_EXTENSIONS.join(' ')} · tối đa ${KNOWLEDGE_MAX_FILE_MB} MB`

    this._toggle.addEventListener('click', () => {
      const opening = this._panel.hidden
      this._panel.hidden = !opening
      if (opening) this.refresh()
    })
    document.getElementById('knowledge-close').addEventListener('click', () => {
      this._panel.hidden = true
    })

    this._drop.addEventListener('click', () => this._input.click())
    this._input.addEventListener('change', () => {
      this._uploadAll([...this._input.files])
      this._input.value = ''   // choosing the same file again fires `change` again
    })
    this._drop.addEventListener('dragover', (event) => {
      event.preventDefault()
      this._drop.classList.add('dragging')
    })
    this._drop.addEventListener('dragleave', () => this._drop.classList.remove('dragging'))
    this._drop.addEventListener('drop', (event) => {
      event.preventDefault()
      this._drop.classList.remove('dragging')
      this._uploadAll([...event.dataTransfer.files])
    })
  }

  async refresh() {
    try {
      this._render(await this._client.list())
    } catch (err) {
      this._say(err.message, true)
    }
  }

  // ── Upload ──────────────────────────────────────────────────────────────

  async _uploadAll(files) {
    const failures = []
    let uploaded = 0
    for (const [index, file] of files.entries()) {
      this._say(`Đang xử lý ${file.name} (${index + 1}/${files.length})...`)
      try {
        this._check(file)
        await this._client.upload(file)
        uploaded += 1
      } catch (err) {
        failures.push(`${file.name}: ${err.message}`)
      }
    }
    await this.refresh()
    if (failures.length) {
      this._say(failures.join('\n'), true)
    } else if (uploaded) {
      this._say(`Đã thêm ${uploaded} tài liệu`)
    }
  }

  /** Refuse before uploading what the backend would refuse anyway. */
  _check(file) {
    if (!isSupportedFile(file.name, KNOWLEDGE_EXTENSIONS)) {
      throw new Error('Định dạng tệp không được hỗ trợ')
    }
    if (file.size > KNOWLEDGE_MAX_FILE_MB * 1024 * 1024) {
      throw new Error(`Tệp vượt quá ${KNOWLEDGE_MAX_FILE_MB} MB`)
    }
  }

  // ── Rendering ───────────────────────────────────────────────────────────

  _render(documents) {
    this._list.replaceChildren(...documents.map((doc) => this._row(doc)))
    this._summary.textContent = usageSummary(documents)
  }

  _row(doc) {
    const row = document.createElement('li')
    row.classList.toggle('unused', !doc.enabled)

    // Checked = the avatar may answer from this document.
    const use = document.createElement('input')
    use.type = 'checkbox'
    use.className = 'knowledge-use'
    use.checked = doc.enabled
    use.title = 'Dùng tài liệu này để trả lời'
    use.addEventListener('change', async () => {
      use.disabled = true
      try {
        await this._client.setEnabled(doc.id, use.checked)
        this._say(use.checked ? `Đang dùng ${doc.name}` : `Đã bỏ chọn ${doc.name}`)
      } catch (err) {
        this._say(err.message, true)
      }
      await this.refresh()   // the list is redrawn from what the backend holds
    })

    const text = document.createElement('label')
    text.className = 'knowledge-doc'
    const name = document.createElement('div')
    name.className = 'knowledge-doc-name'
    name.textContent = doc.name
    name.title = doc.name
    const detail = document.createElement('div')
    detail.className = 'knowledge-doc-detail'
    detail.textContent = `${formatBytes(doc.size_bytes)} · ${doc.chunk_count} đoạn`
    text.append(name, detail)

    const remove = document.createElement('button')
    remove.type = 'button'
    remove.className = 'knowledge-remove'
    remove.textContent = 'Xoá'
    remove.addEventListener('click', async () => {
      remove.disabled = true
      try {
        await this._client.remove(doc.id)
        this._say(`Đã xoá ${doc.name}`)
      } catch (err) {
        this._say(err.message, true)
      }
      await this.refresh()
    })

    text.prepend(use)
    row.append(text, remove)
    return row
  }

  _say(text, isError = false) {
    this._message.textContent = text
    this._message.classList.toggle('error', isError)
  }
}
