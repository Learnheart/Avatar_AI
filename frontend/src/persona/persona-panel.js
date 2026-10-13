/**
 * "Tính cách của avatar" — the block of the profile page where the person gives the
 * avatar a personality: a box to paste a description written freely, which the backend
 * reads into the fields, and one input per field, typed or corrected by hand. Nothing
 * is used by the avatar until "Lưu"; "Xoá tính cách" brings back the default character.
 * Under a field that is empty, the block writes the default the avatar uses for it.
 *
 * The frame of the block is in index.html (#persona-block); the fields are built here,
 * one per entry of PERSONA_FIELDS. The profile page opens it (`open`) each time it is
 * shown. The texts come from the person and from the LLM: they are only ever written
 * as the value of a field or with textContent.
 */

import { PERSONA_FIELD_MAX_CHARS, PERSONA_IMPORT_MAX_CHARS } from '../config.js'
import { SUPERSEDED, UNAUTHORIZED } from '../memory/format.js'
import {
  PERSONA_FIELDS,
  clearedMessage,
  defaultHint,
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
    this._hints = {}      // key → the line under it: the default used while it is empty
    this._defaults = null // the default persona, read once
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

  /**
   * Who is signed in changed: what was typed belongs to the person before. While a
   * request of that person is running, the fields are read once it has answered (its
   * answer is superseded, see `_run`).
   */
  reset() {
    this._saved = {}
    this._description.value = ''
    this._fill({})
    this._say('')
    if (!this._busy) this.refresh()
  }

  async refresh() {
    try {
      // The defaults do not change while the page is open: one read is enough.
      const [saved, defaults] = await Promise.all([
        this._client.get(), this._defaults ?? this._client.defaults(),
      ])
      this._saved = saved
      this._defaults = defaults
      this._fill(this._saved)
    } catch (err) {
      // An answer for the person before is shown to nobody: `reset` reads again.
      if (err.status === SUPERSEDED) return
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
    let superseded = false
    try {
      this._say(await send())
    } catch (err) {
      superseded = err.status === SUPERSEDED
      if (!superseded) this._say(err.message, true)
    }
    this._setBusy(false)
    if (superseded) {
      // The person changed while it ran: its outcome is dropped, and the fields of
      // who is there now are read, which `reset` could not do while the block was busy.
      this._say('')
      this.refresh()
      return
    }
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
    input.addEventListener('input', () => this._showDefault(key))
    this._inputs[key] = input

    const hint = document.createElement('div')
    hint.id = `persona-default-${key}`
    hint.className = 'profile-hint persona-default'
    hint.hidden = true
    input.setAttribute('aria-describedby', hint.id)
    this._hints[key] = hint

    const title = document.createElement('label')
    title.htmlFor = input.id
    title.textContent = label

    const field = document.createElement('div')
    field.className = 'persona-field'
    field.append(title, input, hint)
    return field
  }

  /** Under an empty field, the default the avatar uses for it; nothing under a filled one. */
  _showDefault(key) {
    const text = this._inputs[key].value.trim() ? '' : defaultHint(this._defaults, key)
    this._hints[key].textContent = text
    this._hints[key].hidden = !text
  }

  /** What the form holds, as typed. */
  _form() {
    const raw = {}
    for (const [key, input] of Object.entries(this._inputs)) raw[key] = input.value
    return raw
  }

  _fill(fields) {
    for (const [key, input] of Object.entries(this._inputs)) {
      input.value = fields[key] ?? ''
      this._showDefault(key)
    }
  }

  _say(text, isError = false, isBusy = false) {
    this._message.textContent = text
    this._message.classList.toggle('error', isError)
    this._message.classList.toggle('busy', isBusy)
  }
}
