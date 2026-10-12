/**
 * Profile page — what the avatar knows about the person, laid out as their
 * configuration: who they are and how to address them, then their preferences and
 * personality. Each group is a list with an "add" row; an item is edited in place or
 * deleted, and shows where it comes from.
 *
 * The page is an overlay over the avatar, opened by #profile-toggle: a modal <dialog>,
 * so the browser keeps the keyboard inside it and closes it on Escape. Its frame is in
 * index.html (#profile-page); the groups are built here, one per category. The texts
 * come from the person, from their files and from the LLM: they are only ever written
 * with textContent. The lists are redrawn from what the backend holds after every
 * change.
 */

import { MEMORY_ITEM_MAX_CHARS } from '../config.js'
import {
  CATEGORIES,
  UNAUTHORIZED,
  categoryLabel,
  groupItems,
  sourceLabel,
} from './format.js'

// What to write in a group, shown under its title and in its "add" field.
const GROUP_TEXTS = {
  identity: {
    hint: 'Tên, tuổi, công việc, nơi ở...',
    placeholder: 'Ví dụ: Tôi tên là Linh, làm kỹ sư phần mềm',
  },
  addressing: {
    hint: 'Avatar gọi bạn và xưng với bạn như thế nào.',
    placeholder: 'Ví dụ: Gọi tôi là anh, xưng em',
  },
  preference: {
    hint: 'Những điều bạn thích hoặc không thích.',
    placeholder: 'Ví dụ: Thích câu trả lời ngắn gọn',
  },
  personality: {
    hint: 'Tính cách và cách bạn muốn được trò chuyện.',
    placeholder: 'Ví dụ: Thẳng thắn, thích đùa',
  },
}

const EMPTY_TEXT = 'Chưa có mục nào.'

/** A text field of the page, stopped at the length the backend accepts. */
function textField(className, label) {
  const element = document.createElement('input')
  element.type = 'text'
  element.className = className
  element.maxLength = MEMORY_ITEM_MAX_CHARS
  element.setAttribute('aria-label', label)
  return element
}

function button(text, className) {
  const element = document.createElement('button')
  element.type = 'button'
  element.className = className
  element.textContent = text
  return element
}

export class ProfilePage {
  constructor(client) {
    this._client = client
    this._page = document.getElementById('profile-page')
    this._toggle = document.getElementById('profile-toggle')
    this._close = document.getElementById('profile-close')
    this._message = document.getElementById('profile-message')
    this._guest = document.getElementById('profile-guest')
    this._body = document.getElementById('profile-body')
    this._lists = {}        // category → <ul>
    this._addInputs = {}    // category → the field of its "add" row
    this._items = []
    this._editingId = null  // the item being edited in place, one at a time
    this._draft = null      // what was typed in that edit, kept across redraws
  }

  init() {
    for (const category of CATEGORIES) {
      document.getElementById(`profile-group-${category}`).append(...this._group(category))
    }
    this._toggle.addEventListener('click', () => this.open())
    this._close.addEventListener('click', () => this.close())
    // Escape closes the dialog (the browser does it) unless its keydown was
    // cancelled. A field that has something to cancel does that itself; here, an
    // Escape that belongs to the input method (Telex, VNI composing) is kept from
    // closing the page.
    this._page.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && event.isComposing) event.preventDefault()
    })
    this._page.addEventListener('close', () => this._toggle.focus())
  }

  open() {
    this._stopEditing()
    this._say('')
    this._page.showModal()   // modal: the page behind is inert, Tab stays inside
    this._close.focus()
    this.refresh()
  }

  close() {
    this._page.close()
  }

  async refresh() {
    try {
      this._items = await this._client.list()
      this._show({ guest: false })
      this._render()
    } catch (err) {
      this._fail(err)
    }
  }

  // ── Changes ─────────────────────────────────────────────────────────────

  /**
   * Run one change, say how it went, then redraw from what the backend holds.
   * Resolves with whether the change went through. An edit in progress survives the
   * redraw with what was typed in it.
   */
  async _change(call, done) {
    let failed = false
    try {
      await call()
      this._say(done)
    } catch (err) {
      if (err.status === UNAUTHORIZED) {
        this._fail(err)
        return false
      }
      this._say(err.message, true)
      failed = true
    }
    try {
      this._items = await this._client.list()
    } catch (err) {
      // The message of a failed change is not replaced by the one of a failed refresh.
      if (!failed || err.status === UNAUTHORIZED) this._fail(err)
    }
    this._render()
    return !failed
  }

  _stopEditing() {
    this._editingId = null
    this._draft = null
  }

  /** A guest sees the login note instead of the lists; another failure, its message. */
  _fail(err) {
    if (err.status === UNAUTHORIZED) {
      this._say('')
      this._show({ guest: true })
    } else {
      // Not known to be a guest: no login note beside the error.
      this._guest.hidden = true
      this._say(err.message, true)
    }
  }

  // ── Rendering ───────────────────────────────────────────────────────────

  /** The lists stay hidden until they were read once: an empty list is a statement. */
  _show({ guest }) {
    this._guest.hidden = !guest
    this._body.hidden = guest
  }

  /** The fixed parts of one group: title, hint, list and the "add" row. */
  _group(category) {
    const title = document.createElement('h3')
    title.textContent = categoryLabel(category)
    const hint = document.createElement('p')
    hint.className = 'profile-hint'
    hint.textContent = GROUP_TEXTS[category].hint

    const list = document.createElement('ul')
    list.className = 'profile-list'
    this._lists[category] = list

    const input = textField('profile-input', `Thêm vào ${categoryLabel(category)}`)
    this._addInputs[category] = input
    input.placeholder = GROUP_TEXTS[category].placeholder
    const add = button('Thêm', 'profile-button primary')

    const submit = async () => {
      const text = input.value.trim()
      if (!text || add.disabled) return
      add.disabled = true
      await this._change(async () => {
        await this._client.add(category, text)
        input.value = ''
      }, 'Đã thêm')
      add.disabled = false
      // Disabled while it held the focus, the button dropped it: the keyboard goes
      // back to the field, unless the person moved on to something else meanwhile.
      if (!this._page.contains(document.activeElement)) input.focus()
    }
    add.addEventListener('click', submit)
    input.addEventListener('keydown', (event) => {
      if (event.isComposing) return   // the key belongs to the input method (Telex, VNI)
      if (event.key === 'Enter') {
        submit()
      } else if (event.key === 'Escape' && input.value) {
        event.preventDefault()   // Escape empties a started row before it closes the page
        input.value = ''
      }
    })

    const row = document.createElement('div')
    row.className = 'profile-add'
    row.append(input, add)
    return [title, hint, list, row]
  }

  _render() {
    // An item that is gone (deleted elsewhere) is no longer being edited.
    if (!this._items.some((item) => item.id === this._editingId)) this._stopEditing()
    // This page shows what the avatar uses; what it has not confirmed is not listed.
    const active = groupItems(this._items).active
    for (const category of CATEGORIES) {
      const rows = active[category].map((item) => (
        item.id === this._editingId ? this._editRow(item) : this._row(item)
      ))
      if (rows.length === 0) {
        const empty = document.createElement('li')
        empty.className = 'profile-empty'
        empty.textContent = EMPTY_TEXT
        rows.push(empty)
      }
      this._lists[category].replaceChildren(...rows)
    }
  }

  _focusEdit() {
    this._page.querySelector('.profile-edit-input')?.focus()
  }

  /** Put the keyboard on an item (its "Sửa" button) after its row was redrawn. */
  _focusItem(id) {
    const rows = [...this._page.querySelectorAll('.profile-item')]
    rows.find((row) => row.dataset.id === String(id))?.querySelector('button')?.focus()
  }

  _row(item) {
    const row = document.createElement('li')
    row.className = 'profile-item'
    row.dataset.id = item.id

    const text = document.createElement('div')
    text.className = 'profile-item-text'
    text.textContent = item.text
    const source = document.createElement('div')
    source.className = 'profile-item-source'
    source.textContent = sourceLabel(item.source)
    const content = document.createElement('div')
    content.className = 'profile-item-content'
    content.append(text, source)

    const edit = button('Sửa', 'profile-button')
    edit.addEventListener('click', () => {
      this._editingId = item.id
      this._draft = null
      this._say('')
      this._render()
      this._focusEdit()
    })
    const remove = button('Xoá', 'profile-button danger')
    remove.addEventListener('click', async () => {
      edit.disabled = remove.disabled = true
      const removed = await this._change(() => this._client.remove(item.id), 'Đã xoá')
      // The row is gone: the keyboard goes to the "add" field of its group. After a
      // refused delete it stays on the item, whose row was redrawn.
      if (removed) this._addInputs[item.category].focus()
      else this._focusItem(item.id)
    })

    row.append(content, edit, remove)
    return row
  }

  _editRow(item) {
    const row = document.createElement('li')
    row.className = 'profile-item editing'

    const input = textField('profile-input profile-edit-input', 'Sửa nội dung')
    input.value = this._draft ?? item.text
    input.addEventListener('input', () => { this._draft = input.value })
    const save = button('Lưu', 'profile-button primary')
    const cancel = button('Huỷ', 'profile-button')

    const stop = () => {
      this._stopEditing()
      this._render()
      this._focusItem(item.id)   // the keyboard stays on the item that was being edited
    }
    const submit = async () => {
      const text = input.value.trim()
      if (!text || save.disabled) return
      if (text === item.text) return stop()
      save.disabled = cancel.disabled = true
      const saved = await this._change(async () => {
        await this._client.update(item.id, { text })
        this._stopEditing()
      }, 'Đã lưu')
      // A refused edit stays open with what was typed, under the error message.
      if (saved) this._focusItem(item.id)
      else this._focusEdit()
    }
    save.addEventListener('click', submit)
    cancel.addEventListener('click', stop)
    // On the row, so Escape cancels the edit from the field and from its buttons.
    row.addEventListener('keydown', (event) => {
      if (event.isComposing) return   // the key belongs to the input method (Telex, VNI)
      if (event.key === 'Escape') {
        event.preventDefault()   // cancels the edit, does not close the page
        stop()
      } else if (event.key === 'Enter' && event.target === input) {
        submit()
      }
    })

    row.append(input, save, cancel)
    return row
  }

  _say(text, isError = false) {
    this._message.textContent = text
    this._message.classList.toggle('error', isError)
  }
}
