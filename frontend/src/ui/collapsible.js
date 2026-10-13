/**
 * A drop-down: a header button that opens and closes a body, and remembers how it was
 * left. The header carries `aria-expanded`, which is also what the stylesheet reads to
 * turn its arrow; the body is hidden with the `hidden` attribute.
 *
 * `storedOpen` and `keepOpen` are pure (unit-tested with a fake storage).
 */

/** Whether the drop-down named `key` was left open; `fallback` when nothing is kept. */
export function storedOpen(storage, key, fallback) {
  try {
    const kept = storage?.getItem(key)
    if (kept === 'open') return true
    if (kept === 'closed') return false
  } catch {
    // A storage that cannot be read: as if nothing was kept.
  }
  return fallback
}

/** Remember how the drop-down named `key` was left. */
export function keepOpen(storage, key, open) {
  try {
    storage?.setItem(key, open ? 'open' : 'closed')
  } catch {
    // A storage that refuses: the choice lasts until the page closes.
  }
}

function browserStorage() {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export class Collapsible {
  /**
   * `header`: the button; `body`: the element it opens and closes; `key`: the name the
   * choice is kept under; `open`: how it starts when nothing is kept; `onChange(open)`
   * is called each time it opens or closes, and once at the start.
   */
  constructor({ header, body, key, open = true, onChange = null, storage = browserStorage() }) {
    this._header = header
    this._body = body
    this._key = key
    this._onChange = onChange
    this._storage = storage
    header.setAttribute('aria-controls', body.id)
    header.addEventListener('click', () => this.set(!this.open))
    this._apply(storedOpen(storage, key, open))
  }

  get open() {
    return this._header.getAttribute('aria-expanded') === 'true'
  }

  set(open) {
    if (open === this.open) return
    keepOpen(this._storage, this._key, open)
    this._apply(open)
  }

  _apply(open) {
    this._header.setAttribute('aria-expanded', String(open))
    this._body.hidden = !open
    this._onChange?.(open)
  }
}
