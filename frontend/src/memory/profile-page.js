/**
 * Profile page — what the avatar knows about the person, laid out as their
 * configuration: who they are and how to address them, then their preferences and
 * personality. Each group is a list with an "add" row; an item is edited in place or
 * deleted, and shows where it comes from. Above the lists, while there is one, the
 * proposals: what the avatar heard in a conversation and does not use until the person
 * approves it (corrected or not) or rejects it. Under the lists, the imports: a description
 * of the person given as a file or a pasted text, which the backend reads into items,
 * and the list of what was given, each with its original.
 *
 * The page is an overlay over the avatar, opened by #profile-toggle: a modal <dialog>,
 * so the browser keeps the keyboard inside it and closes it on Escape. Its frame is in
 * index.html (#profile-page); the groups are built here, one per category. The texts
 * come from the person, from their files and from the LLM: they are only ever written
 * with textContent. The lists are redrawn from what the backend holds after every
 * change.
 */

import {
  MEMORY_IMPORT_EXTENSIONS,
  MEMORY_IMPORT_MAX_FILE_MB,
  MEMORY_ITEM_MAX_CHARS,
} from '../config.js'
import {
  CATEGORIES,
  UNAUTHORIZED,
  approvedMessage,
  categoryLabel,
  fileRefusal,
  groupItems,
  importLabel,
  importedMessage,
  itemCountLabel,
  itemSourceLabel,
  proposals,
  proposalsHeading,
  rejectedMessage,
  replacedItem,
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
const NO_IMPORT_TEXT = 'Chưa nhập bản mô tả nào.'
const PASTED_TEXT_NAME = 'đoạn văn bản'   // how the busy message names a pasted text

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
    this._editingId = null  // the item or proposal being edited in place, one at a time
    this._draft = null      // what was typed in that edit, kept across redraws
    this._busy = new Set()  // ids of the items with a request running: their rows wait

    this._proposals = document.getElementById('profile-proposals')
    this._proposalsTitle = document.getElementById('profile-proposals-title')
    this._proposalList = document.getElementById('profile-proposal-list')

    this._imports = []
    this._importDrop = document.getElementById('profile-import-drop')
    this._importInput = document.getElementById('profile-import-input')
    this._importText = document.getElementById('profile-import-text')
    this._importSubmit = document.getElementById('profile-import-submit')
    this._importMessage = document.getElementById('profile-import-message')
    this._importList = document.getElementById('profile-import-list')
    this._importing = false         // one description is read at a time
    this._removingImportId = null   // the import whose row asks what to do with its items
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
    // A file dropped beside the drop zone would open in place of the avatar page:
    // anywhere on the page, a dragged file is taken from the browser and ignored.
    for (const type of ['dragover', 'drop']) {
      this._page.addEventListener(type, (event) => {
        if (!event.dataTransfer?.types.includes('Files')) return   // a dragged text
        event.preventDefault()
        if (!this._importDrop.contains(event.target)) event.dataTransfer.dropEffect = 'none'
      })
    }
    this._initImports()
  }

  _initImports() {
    this._importInput.accept = MEMORY_IMPORT_EXTENSIONS.join(',')
    document.getElementById('profile-import-hint').textContent =
      `${MEMORY_IMPORT_EXTENSIONS.join(' ')} · tối đa ${MEMORY_IMPORT_MAX_FILE_MB} MB`

    this._importDrop.addEventListener('click', () => this._importInput.click())
    this._importInput.addEventListener('change', () => {
      const files = [...this._importInput.files]
      this._importInput.value = ''   // choosing the same file again fires `change` again
      this._importFiles(files)
    })
    this._importDrop.addEventListener('dragover', (event) => {
      event.preventDefault()   // also while busy: a refused drop must not open the file
      if (!this._importing) this._importDrop.classList.add('dragging')
    })
    this._importDrop.addEventListener('dragleave', () => {
      this._importDrop.classList.remove('dragging')
    })
    this._importDrop.addEventListener('drop', (event) => {
      event.preventDefault()
      this._importDrop.classList.remove('dragging')
      this._importFiles([...event.dataTransfer.files])
    })

    this._importSubmit.addEventListener('click', () => this._importPasted())
    this._importText.addEventListener('keydown', (event) => {
      if (event.isComposing) return   // the key belongs to the input method (Telex, VNI)
      if (event.key === 'Escape' && this._importText.value) {
        // A pasted text is long and cannot be typed again: Escape neither empties it
        // nor closes the page from here.
        event.preventDefault()
      } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        this._importPasted()   // Enter alone is a new line
      }
    })
  }

  open() {
    this._stopEditing()
    this._removingImportId = null
    this._say('')
    // A reading in progress keeps its message; a pasted text is never emptied here.
    if (!this._importing) this._sayImport('')
    this._page.showModal()   // modal: the page behind is inert, Tab stays inside
    this._close.focus()
    this.refresh()
  }

  close() {
    this._page.close()
  }

  /** Read the items and the imports: an item names the import it comes from. */
  async _read() {
    const [items, imports] = await Promise.all([
      this._client.list(), this._client.listImports(),
    ])
    this._items = items
    this._imports = imports
  }

  async refresh() {
    try {
      await this._read()
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
   * redraw with what was typed in it. `done` is the message of a success, or a
   * function giving it once the call has answered; `say` is where the outcome is
   * written (the message on top of the page, or the one of the imports block).
   */
  async _change(call, done, say = (text, isError) => this._say(text, isError)) {
    let failed = false
    try {
      await call()
      say(typeof done === 'function' ? done() : done)
    } catch (err) {
      if (err.status === UNAUTHORIZED) {
        this._fail(err)
        return false
      }
      say(err.message, true)
      failed = true
    }
    try {
      await this._read()
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

  /**
   * Run the request of one item or proposal. Until it has answered, the row of that
   * item takes no action, also when it is redrawn meanwhile (a change elsewhere, the
   * page opened again): one request per item at a time.
   */
  async _request(id, send) {
    this._busy.add(id)
    try {
      return await send()
    } finally {
      this._busy.delete(id)
    }
  }

  // ── Proposals ───────────────────────────────────────────────────────────

  /**
   * Approve a proposal, with `changes` ({ text }) when the person corrected it first:
   * one call. `index` is its place among the proposals, for the keyboard afterwards.
   */
  async _approve(item, index, changes = {}) {
    const replaced = replacedItem(item, this._items) !== null
    await this._change(async () => {
      await this._request(item.id, () => this._client.approve(item.id, changes))
      if (this._editingId === item.id) this._stopEditing()
    }, approvedMessage(replaced))
    this._afterProposal(item, index)
  }

  async _reject(item, index) {
    await this._change(
      () => this._request(item.id, () => this._client.remove(item.id)), rejectedMessage(),
    )
    this._afterProposal(item, index)
  }

  /**
   * Where the keyboard goes once a proposal was handled and the page redrawn: on the
   * proposal that took its place (the next one, or the last one left), on "Đóng" when
   * none is left. A proposal still listed was refused: the keyboard stays on it, in its
   * field when it was being corrected.
   */
  _afterProposal(item, index) {
    if (this._body.hidden) return this._close.focus()   // a guest by now
    const left = proposals(this._items)
    if (left.some((entry) => entry.id === item.id)) {
      if (this._editingId === item.id) this._focusEdit()
      else this._focusProposal(item.id)
      return
    }
    const next = left[Math.min(index, left.length - 1)]
    if (next) this._focusProposal(next.id)
    else this._close.focus()
  }

  /** Put the keyboard on a proposal ("Ghi nhớ" unless another control is asked for). */
  _focusProposal(id, selector = 'button') {
    const rows = [...this._proposalList.querySelectorAll('.profile-proposal')]
    rows.find((row) => row.dataset.id === String(id))?.querySelector(selector)?.focus()
  }

  // ── Imports ─────────────────────────────────────────────────────────────

  /** A file chosen or dropped: refused here when the backend would refuse it anyway. */
  _importFiles(files) {
    if (this._importing || files.length === 0) return
    if (files.length > 1) return this._sayImport('Mỗi lần chỉ nhập một tệp', true)
    const [file] = files
    const refusal = fileRefusal(file, MEMORY_IMPORT_EXTENSIONS, MEMORY_IMPORT_MAX_FILE_MB)
    if (refusal) return this._sayImport(`${file.name}: ${refusal}`, true)
    this._import(file.name, () => this._client.importFile(file), this._importDrop)
  }

  async _importPasted() {
    const text = this._importText.value.trim()
    if (!text || this._importing) return
    const imported = await this._import(
      PASTED_TEXT_NAME, () => this._client.importText(text), this._importText,
    )
    // A refused text stays in its field, to be sent again or corrected.
    if (imported) this._importText.value = ''
  }

  /**
   * Send one description and wait for the backend to read it (several seconds): the
   * block says so and takes no other description meanwhile. `origin` is the control
   * that started it and gets the keyboard back. Resolves with whether it went through.
   */
  async _import(name, send, origin) {
    this._setImporting(true)
    this._sayImport(`Đang đọc ${name}… Việc này mất vài giây.`)
    let result = null
    const imported = await this._change(
      async () => { result = await send() },
      () => importedMessage(result.items.length),
      (text, isError) => this._sayImport(text, isError),
    )
    this._setImporting(false)
    // Disabled while it held the focus, the control dropped it: the keyboard goes back
    // to it, unless the person moved on to something else meanwhile.
    const active = document.activeElement
    const lost = active === document.body || active === this._page
    if (lost && this._page.open && !this._body.hidden) origin.focus()
    return imported
  }

  _setImporting(importing) {
    this._importing = importing
    for (const control of [
      this._importDrop, this._importInput, this._importText, this._importSubmit,
    ]) control.disabled = importing
    this._importMessage.classList.toggle('busy', importing)
    document.getElementById('profile-import-form').setAttribute('aria-busy', String(importing))
  }

  async _removeImport(entry, { keepItems }) {
    this._removingImportId = null
    let done = `Đã xoá ${entry.name}`
    if (entry.item_count) done += keepItems ? ', các mục được giữ lại' : ' và các mục của nó'
    const removed = await this._change(
      () => this._client.removeImport(entry.id, { keepItems }),
      done,
      (text, isError) => this._sayImport(text, isError),
    )
    // The row is gone: the keyboard goes to the drop zone. After a refused delete it
    // stays on the import, whose row was redrawn.
    if (this._body.hidden) return
    if (removed) this._importDrop.focus()
    else this._focusImport(entry.id, 'button')
  }

  /**
   * Save the original of an import as a file. The bytes are fetched and handed to the
   * browser through a temporary link, so a refusal (the import is gone, the store
   * fails) is a message here: the avatar page never leaves for the answer of the API.
   */
  async _saveOriginal(entry) {
    await this._change(
      async () => {
        const { blob, name } = await this._client.importContent(entry)
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = name
        link.click()
        setTimeout(() => URL.revokeObjectURL(url))   // once the browser took the click
      },
      `Đã tải ${entry.name}`,
      (text, isError) => this._sayImport(text, isError),
    )
    if (!this._body.hidden) this._focusImport(entry.id, 'button')
  }

  /** Put the keyboard on a control of an import after its row was redrawn. */
  _focusImport(id, selector) {
    const rows = [...this._importList.querySelectorAll('.profile-import')]
    rows.find((row) => row.dataset.id === String(id))?.querySelector(selector)?.focus()
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
    this._renderProposals()
    // The lists show what the avatar uses; what it has not confirmed is above them.
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
    this._renderImports()
  }

  /** The proposals block, on top of the lists and only while there is a proposal. */
  _renderProposals() {
    const pending = proposals(this._items)
    this._proposals.hidden = pending.length === 0
    this._proposalsTitle.textContent = proposalsHeading(pending.length)
    this._proposalList.replaceChildren(...pending.map((item, index) => (
      item.id === this._editingId
        ? this._proposalEditRow(item, index)
        : this._proposalRow(item, index)
    )))
  }

  /**
   * What a proposal says, around `main` (its text, or the field correcting it): its
   * category, the sentence it was heard in, and the item it would replace.
   */
  _proposalContent(item, main) {
    const category = document.createElement('div')
    category.className = 'profile-item-source'
    category.textContent = categoryLabel(item.category)
    const content = document.createElement('div')
    content.className = 'profile-item-content'
    content.append(category, main)
    if (item.evidence) {
      const quote = document.createElement('q')
      quote.className = 'profile-item-quote'
      quote.textContent = item.evidence
      content.append(quote)
    }
    const replaced = replacedItem(item, this._items)
    if (replaced) {
      const old = document.createElement('s')
      old.textContent = replaced.text
      const replaces = document.createElement('div')
      replaces.className = 'profile-proposal-replaces'
      replaces.append('Thay cho: ', old)
      content.append(replaces)
    }
    return content
  }

  _proposalRow(item, index) {
    const row = document.createElement('li')
    row.className = 'profile-item profile-proposal'
    row.dataset.id = item.id

    const text = document.createElement('div')
    text.className = 'profile-item-text'
    text.textContent = item.text

    const approve = button('Ghi nhớ', 'profile-button primary')
    const edit = button('Sửa', 'profile-button profile-proposal-edit')
    const reject = button('Bỏ qua', 'profile-button danger')
    const busy = () => { approve.disabled = edit.disabled = reject.disabled = true }
    if (this._busy.has(item.id)) busy()   // redrawn while its request is running
    approve.addEventListener('click', () => {
      busy()
      this._approve(item, index)
    })
    edit.addEventListener('click', () => {
      this._editingId = item.id
      this._draft = null
      this._say('')
      this._render()
      this._focusEdit()
    })
    reject.addEventListener('click', () => {
      busy()
      this._reject(item, index)
    })

    row.append(this._proposalContent(item, text), approve, edit, reject)
    return row
  }

  /** A proposal being corrected: saving it approves it, with the corrected text. */
  _proposalEditRow(item, index) {
    const row = document.createElement('li')
    row.className = 'profile-item profile-proposal editing'
    row.dataset.id = item.id

    const input = textField('profile-input profile-edit-input', 'Sửa nội dung trước khi ghi nhớ')
    input.value = this._draft ?? item.text
    input.addEventListener('input', () => { this._draft = input.value })
    const save = button('Lưu và ghi nhớ', 'profile-button primary')
    const cancel = button('Huỷ', 'profile-button')
    save.disabled = cancel.disabled = this._busy.has(item.id)

    const stop = () => {
      this._stopEditing()
      this._render()
      this._focusProposal(item.id, '.profile-proposal-edit')   // back on "Sửa"
    }
    const submit = () => {
      const text = input.value.trim()
      if (!text || save.disabled) return
      save.disabled = cancel.disabled = true
      // A text left as it was is approved as it is.
      this._approve(item, index, text === item.text ? {} : { text })
    }
    save.addEventListener('click', submit)
    cancel.addEventListener('click', stop)
    row.addEventListener('keydown', (event) => {
      if (event.isComposing) return   // the key belongs to the input method (Telex, VNI)
      if (event.key === 'Escape') {
        event.preventDefault()   // cancels the correction, does not close the page
        if (!save.disabled) stop()   // while it is being saved, Escape does nothing
      } else if (event.key === 'Enter' && event.target === input) {
        submit()
      }
    })

    row.append(this._proposalContent(item, input), save, cancel)
    return row
  }

  _renderImports() {
    // An import that is gone (deleted elsewhere) no longer asks its question.
    if (!this._imports.some((entry) => entry.id === this._removingImportId)) {
      this._removingImportId = null
    }
    const rows = this._imports.map((entry) => (
      entry.id === this._removingImportId ? this._importRemoveRow(entry) : this._importRow(entry)
    ))
    if (rows.length === 0) {
      const empty = document.createElement('li')
      empty.className = 'profile-empty'
      empty.textContent = NO_IMPORT_TEXT
      rows.push(empty)
    }
    this._importList.replaceChildren(...rows)
  }

  /** The name of an import and, under it, its size, its items and its date. */
  _importContent(entry) {
    const name = document.createElement('div')
    name.className = 'profile-import-name'
    name.textContent = entry.name
    name.title = entry.name
    const detail = document.createElement('div')
    detail.className = 'profile-item-source'
    detail.textContent = importLabel(entry)
    const content = document.createElement('div')
    content.className = 'profile-item-content'
    content.append(name, detail)
    return content
  }

  _importRow(entry) {
    const row = document.createElement('li')
    row.className = 'profile-item profile-import'
    row.dataset.id = entry.id

    const original = button('Mở bản gốc', 'profile-button')
    original.addEventListener('click', () => {
      original.disabled = true   // until the row is redrawn
      this._saveOriginal(entry)
    })

    const remove = button('Xoá', 'profile-button danger')
    remove.addEventListener('click', () => {
      // Nothing was made from it: there is nothing to choose.
      if (!entry.item_count) return this._removeImport(entry, { keepItems: true })
      this._removingImportId = entry.id
      this._sayImport('')
      this._renderImports()
      this._focusImport(entry.id, 'button')   // "Huỷ", the harmless choice
    })

    row.append(this._importContent(entry), original, remove)
    return row
  }

  /** The row of an import being deleted: what happens to the items made from it. */
  _importRemoveRow(entry) {
    const row = document.createElement('li')
    row.className = 'profile-item profile-import removing'
    row.dataset.id = entry.id

    const question = document.createElement('div')
    question.className = 'profile-import-question'
    question.textContent = `Xoá bản gốc này? Có ${itemCountLabel(entry.item_count)} `
      + 'trong hồ sơ được tạo từ nó: giữ lại hay xoá luôn?'

    const cancel = button('Huỷ', 'profile-button')
    const keep = button('Giữ lại các mục', 'profile-button')
    const drop = button('Xoá cả các mục', 'profile-button danger')
    const stop = () => {
      this._removingImportId = null
      this._renderImports()
      this._focusImport(entry.id, '.danger')   // back on "Xoá", the button that asked
    }
    const choose = (keepItems) => {
      cancel.disabled = keep.disabled = drop.disabled = true
      this._removeImport(entry, { keepItems })
    }
    cancel.addEventListener('click', stop)
    keep.addEventListener('click', () => choose(true))
    drop.addEventListener('click', () => choose(false))
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()   // cancels the question, does not close the page
        stop()
      }
    })

    const choices = document.createElement('div')
    choices.className = 'profile-import-choices'
    choices.append(cancel, keep, drop)
    const content = this._importContent(entry)
    content.append(question)
    row.append(content, choices)
    return row
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
    source.textContent = itemSourceLabel(item, this._imports)
    const content = document.createElement('div')
    content.className = 'profile-item-content'
    content.append(text, source)
    if (item.evidence) {
      // The sentence the item was read from, in the person's own words.
      const quote = document.createElement('q')
      quote.className = 'profile-item-quote'
      quote.textContent = item.evidence
      content.append(quote)
    }

    const edit = button('Sửa', 'profile-button')
    edit.addEventListener('click', () => {
      this._editingId = item.id
      this._draft = null
      this._say('')
      this._render()
      this._focusEdit()
    })
    const remove = button('Xoá', 'profile-button danger')
    edit.disabled = remove.disabled = this._busy.has(item.id)
    remove.addEventListener('click', async () => {
      edit.disabled = remove.disabled = true
      const removed = await this._change(
        () => this._request(item.id, () => this._client.remove(item.id)), 'Đã xoá',
      )
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
    save.disabled = cancel.disabled = this._busy.has(item.id)

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
        await this._request(item.id, () => this._client.update(item.id, { text }))
        if (this._editingId === item.id) this._stopEditing()
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
        if (!save.disabled) stop()   // while it is being saved, Escape does nothing
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

  /** The message of the imports block, which is too far down for the one on top. */
  _sayImport(text, isError = false) {
    this._importMessage.textContent = text
    this._importMessage.classList.toggle('error', isError)
  }
}
