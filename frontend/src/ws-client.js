/**
 * WebSocket Client — TASK-10
 *
 * - Auto-reconnect với exponential backoff (3s → 6s → 12s → max 30s)
 * - on(type, handler) pattern để dispatch messages
 * - Expose send() để gửi audio_chunk lên backend
 */

const WS_URL = import.meta.env.VITE_WS_URL || `ws://${window.location.hostname}:8000/ws`

const BACKOFF_INITIAL = 3000
const BACKOFF_MAX = 30000
const BACKOFF_FACTOR = 2

export class WSClient {
  constructor() {
    this._ws = null
    this._handlers = {}         // { type: [fn, ...] }
    this._backoffMs = BACKOFF_INITIAL
    this._reconnectTimer = null
    this._intentionalClose = false
    this._statusEl = document.getElementById('status')
  }

  // ── Connect ─────────────────────────────────────────────────────────────

  connect() {
    this._intentionalClose = false
    this._openSocket()
  }

  disconnect() {
    this._intentionalClose = true
    clearTimeout(this._reconnectTimer)
    if (this._ws) {
      this._ws.close()
      this._ws = null
    }
  }

  // ── Send ─────────────────────────────────────────────────────────────────

  send(message) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(message))
    }
  }

  sendAudioChunk(base64Data) {
    this.send({ type: 'audio_chunk', audio_data: base64Data })
  }

  // ── Event subscription ───────────────────────────────────────────────────

  on(type, handler) {
    if (!this._handlers[type]) this._handlers[type] = []
    this._handlers[type].push(handler)
  }

  off(type, handler) {
    if (!this._handlers[type]) return
    this._handlers[type] = this._handlers[type].filter(h => h !== handler)
  }

  // ── Internal ─────────────────────────────────────────────────────────────

  _openSocket() {
    console.log(`[WS] Connecting to ${WS_URL}`)
    this._setStatus('reconnecting')

    this._ws = new WebSocket(WS_URL)

    this._ws.onopen = () => {
      console.log('[WS] Connected')
      this._backoffMs = BACKOFF_INITIAL
      this._setStatus('connected')
    }

    this._ws.onmessage = (event) => {
      let msg
      try {
        msg = JSON.parse(event.data)
      } catch {
        console.warn('[WS] Invalid JSON message')
        return
      }
      this._dispatch(msg)
    }

    this._ws.onerror = (err) => {
      console.warn('[WS] Error', err)
    }

    this._ws.onclose = () => {
      this._ws = null
      if (this._intentionalClose) return
      console.log(`[WS] Disconnected. Reconnecting in ${this._backoffMs}ms`)
      this._setStatus('disconnected')
      this._scheduleReconnect()
    }
  }

  _scheduleReconnect() {
    clearTimeout(this._reconnectTimer)
    this._reconnectTimer = setTimeout(() => {
      this._openSocket()
      this._backoffMs = Math.min(this._backoffMs * BACKOFF_FACTOR, BACKOFF_MAX)
    }, this._backoffMs)
  }

  _dispatch(msg) {
    const type = msg.type
    const handlers = this._handlers[type] || []
    handlers.forEach(fn => {
      try { fn(msg) } catch (e) { console.error('[WS] Handler error', e) }
    })
    // Also dispatch to wildcard handlers
    ;(this._handlers['*'] || []).forEach(fn => {
      try { fn(msg) } catch (e) { console.error('[WS] Wildcard handler error', e) }
    })
  }

  _setStatus(state) {
    if (!this._statusEl) return
    this._statusEl.className = state
    const labels = {
      connected: '● ONLINE',
      disconnected: '● OFFLINE',
      reconnecting: '◌ KẾT NỐI...',
    }
    this._statusEl.textContent = labels[state] || state
  }
}
