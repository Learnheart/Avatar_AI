/**
 * WebSocket Client
 *
 * - Auto-reconnect with exponential backoff (WS_BACKOFF_* in config.js)
 * - on(type, handler) subscription to dispatch incoming messages
 * - send() helpers for audio_chunk / playback_ended / conversation_reset to the backend
 * - Says who the page is: an `auth` message with the session token, first on every
 *   connection and again whenever the page signs in or out
 * - Status label: connection status + avatar state (idle/listening/thinking/speaking)
 */

import {
  WS_BACKOFF_FACTOR,
  WS_BACKOFF_INITIAL_MS,
  WS_BACKOFF_MAX_MS,
  WS_URL,
} from './config.js'

export class WSClient {
  /** `session`: the session of the page (auth/session.js), whose token is presented. */
  constructor(session) {
    this._session = session
    this._presented = undefined // the token last presented on the open connection
    session.onChange(() => this._sendAuth())
    this._ws = null
    this._handlers = {}         // { type: [fn, ...] }
    this._backoffMs = WS_BACKOFF_INITIAL_MS
    this._reconnectTimer = null
    this._wasOpen = false       // true between onopen and onclose
    this._onDrop = null         // callback when an open connection is lost
    this._statusEl = document.getElementById('status')
  }

  // ── Connect ─────────────────────────────────────────────────────────────

  connect() {
    console.log(`[WS] Connecting to ${WS_URL}`)
    this.setStatus('reconnecting')

    this._ws = new WebSocket(WS_URL)

    this._ws.onopen = () => {
      console.log('[WS] Connected')
      this._backoffMs = WS_BACKOFF_INITIAL_MS
      this._wasOpen = true
      this._presented = undefined
      this._sendAuth()   // the first frame: before any audio of the microphone
      this.setStatus('connected')
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
      if (this._wasOpen) {
        this._wasOpen = false
        if (this._onDrop) this._onDrop()
      }
      console.log(`[WS] Disconnected. Reconnecting in ${this._backoffMs}ms`)
      this.setStatus('disconnected')
      this._scheduleReconnect()
    }
  }

  // ── Send ─────────────────────────────────────────────────────────────────

  send(message) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(message))
    }
  }

  /** Say who the page is; nothing is sent again for the token already presented. */
  _sendAuth() {
    const token = this._session.token
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN || token === this._presented) return
    this._presented = token
    this.send({ type: 'auth', token })
  }

  sendAudioChunk(base64Data) {
    this.send({ type: 'audio_chunk', audio_data: base64Data })
  }

  sendPlaybackEnded() {
    this.send({ type: 'playback_ended' })
  }

  /** Ask the backend to close the conversation; it answers with a `conversation` message. */
  sendConversationReset() {
    this.send({ type: 'conversation_reset' })
  }

  // ── Event subscription ───────────────────────────────────────────────────

  on(type, handler) {
    if (!this._handlers[type]) this._handlers[type] = []
    this._handlers[type].push(handler)
  }

  /**
   * Register the callback fired when an established connection drops.
   */
  onDrop(fn) { this._onDrop = fn }

  // ── Status label ─────────────────────────────────────────────────────────

  /**
   * Show a connection status or a server state in the status label.
   */
  setStatus(state) {
    if (!this._statusEl) return
    this._statusEl.className = state
    const labels = {
      connected: '● ONLINE',
      disconnected: '● OFFLINE',
      reconnecting: '◌ KẾT NỐI...',
      idle: '● SẴN SÀNG',
      listening: '● ĐANG NGHE',
      thinking: '◌ ĐANG NGHĨ...',
      speaking: '● ĐANG NÓI',
    }
    this._statusEl.textContent = labels[state] || state
  }

  // ── Internal ─────────────────────────────────────────────────────────────

  _scheduleReconnect() {
    clearTimeout(this._reconnectTimer)
    this._reconnectTimer = setTimeout(() => {
      this.connect()
      this._backoffMs = Math.min(this._backoffMs * WS_BACKOFF_FACTOR, WS_BACKOFF_MAX_MS)
    }, this._backoffMs)
  }

  _dispatch(msg) {
    const handlers = this._handlers[msg.type] || []
    handlers.forEach(fn => {
      try { fn(msg) } catch (e) { console.error('[WS] Handler error', e) }
    })
  }
}
