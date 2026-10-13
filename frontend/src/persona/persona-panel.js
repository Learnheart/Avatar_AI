/**
 * "Tính cách của avatar" — the block of the profile page where the person gives the
 * avatar a personality: a box to paste a description written freely, which the backend
 * reads into the fields, and one input per field, typed or corrected by hand. Nothing
 * is used by the avatar until "Lưu"; "Xoá tính cách" brings back the default character.
 *
 * The frame of the block is in index.html (#persona-block); the fields are built here,
 * one per entry of PERSONA_FIELDS. The profile page opens it (`open`) each time it is
 * shown. The texts come from the person and from the LLM: they are only ever written
 * as the value of a field or with textContent.
 */

import { PERSONA_FIELD_MAX_CHARS, PERSONA_IMPORT_MAX_CHARS } from '../config.js'
import { UNAUTHORIZED } from '../memory/format.js'
import {
  PERSONA_FIELDS,
  clearedMessage,
  formValues,
  mergeRead,
  readMessage,
  sameFields,
  savedMessage,
} from './format.js'

export class PersonaPanel {
  constructor(client) {
    this._client = client
    this._description = document.getElementById('persona-description')
    this._read = document.getElementById('persona-read')
    this._message = document.getElementById('persona-message')
    this._fields = document.getElementById('persona-fields')
    this._save = document.getElementById('persona-save')
    this._clear = document.getElementById('persona-clear')
    this._inputs = {}     // key → the input of that field
    this._saved = {}      // the fields the backend holds, as last read from it
    this._busy = false    // one request at a time: a reading, a save or a delete
  }

  init() {
    this._description.maxLength = PERSONA_IMPORT_MAX_CHARS
    this._fields.append(...PERSONA_FIELDS.map((field) => this._field(field)))

    this._read.addEventListener('click', () => this._readDescription())
    this._description.addEventListener('keydown', (event) => {
      if (event.isComposing) return   // the key belongs to the input method (Telex, VNI)
      if (event.key === 'Escape' && this._description.value) {
        // A pasted description is long and cannot be typed again: Escape neither
        // empties it nor closes the page from here.
        event.preventDefault()
      } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        this._readDescription()   // Enter alone is a new line
      }
    })
    this._save.addEventListener('click', () => this._saveFields())
    this._clear.addEventListener('click', () => this._clearPersona())
  }

  /**
   * The profile page is shown. The fields are read again from the backend, unless the
   * form holds something that was not saved (typed, or filled by a reading): that is
   * kept as it is.
   */
  open() {
    if (this._busy) return
    this._say('')
    if (!sameFields(this._form(), this._saved)) return
    this.refresh()
  }

  async refresh() {
    try {
      this._saved = await this._client.get()
      this._fill(this._saved)
    } catch (err) {
      // A guest: the profile page says so itself, and hides this block.
      if (err.status !== UNAUTHORIZED) this._say(err.message, true)
    }
  }

  // ── Changes ─────────────────────────────────────────────────────────────

  async _readDescription() {
    const text = this._description.value.trim()
    if (!text || this._busy) return
    await this._run(this._description, 'Đang đọc mô tả… Việc này mất vài giây.', async () => {
      const found = await this._client.read(text)
      this._fill(mergeRead(this._form(), found))
      return readMessage(Object.keys(formValues(found)).length)
    })
  }

  async _saveFields() {
    if (this._busy) return
    await this._run(this._save, 'Đang lưu…', async () => {
      this._saved = await this._client.save(formValues(this._form()))
      this._fill(this._saved)
      return savedMessage(Object.keys(this._saved).length)
    })
  }

  async _clearPersona() {
    if (this._busy) return
    await this._run(this._clear, 'Đang xoá…', async () => {
      await this._client.clear()
      this._saved = {}
      this._fill({})
      return clearedMessage()
    })
  }

  /**
   * Run one request: the block says `waiting` and takes no other action until it has
   * answered, then says how it went (`send` resolves with the message of a success).
   * `origin` is the control that started it and gets the keyboard back.
   */
  async _run(origin, waiting, send) {
    this._setBusy(true)
    this._say(waiting, false, true)
    try {
      this._say(await send())
    } catch (err) {
      this._say(err.message, true)
    }
    this._setBusy(false)
    // Disabled while it held the focus, the control dropped it: the keyboard goes back
    // to it, unless the person moved on to something else meanwhile.
    const active = document.activeElement
    if (active === document.body || active?.tagName === 'DIALOG') origin.focus()
  }

  _setBusy(busy) {
    this._busy = busy
    for (const control of [
      this._description, this._read, this._save, this._clear, ...Object.values(this._inputs),
    ]) control.disabled = busy
    document.getElementById('persona-block').setAttribute('aria-busy', String(busy))
  }

  // ── Rendering ───────────────────────────────────────────────────────────

  /** One field of the form: its label and its input. */
  _field({ key, label, placeholder }) {
    const input = document.createElement('input')
    input.type = 'text'
    input.id = `persona-field-${key}`
    input.className = 'profile-input'
    input.maxLength = PERSONA_FIELD_MAX_CHARS
    input.placeholder = placeholder
    input.addEventListener('keydown', (event) => {
      if (event.isComposing) return   // the key belongs to the input method (Telex, VNI)
      if (event.key === 'Enter') this._saveFields()
    })
    this._inputs[key] = input

    const title = document.createElement('label')
    title.htmlFor = input.id
    title.textContent = label

    const field = document.createElement('div')
    field.className = 'persona-field'
    field.append(title, input)
    return field
  }

  /** What the form holds, as typed. */
  _form() {
    const raw = {}
    for (const [key, input] of Object.entries(this._inputs)) raw[key] = input.value
    return raw
  }

  _fill(fields) {
    for (const [key, input] of Object.entries(this._inputs)) input.value = fields[key] ?? ''
  }

  _say(text, isError = false, isBusy = false) {
    this._message.textContent = text
    this._message.classList.toggle('error', isError)
    this._message.classList.toggle('busy', isBusy)
  }
}
