/**
 * Frontend Entry Point
 *
 * Init sequence:
 *   1. AvatarRenderer → Three.js scene + VRM model
 *   2. WSClient → WebSocket connection
 *   3. AudioManager → mic capture + playback context
 *   4. LipSync → hook into audio + avatar
 *   5. Wire WS message handlers
 */

import { AvatarRenderer } from './avatar/avatar-renderer.js'
import { WSClient } from './ws-client.js'
import { AudioManager } from './audio/audio-manager.js'
import { LipSync } from './avatar/lip-sync.js'

async function main() {
  const container = document.getElementById('canvas-container')

  // ── 1. Avatar Renderer ────────────────────────────────────────────────
  const avatar = new AvatarRenderer(container)
  await avatar.init()

  // ── 2. WebSocket Client ───────────────────────────────────────────────
  const ws = new WSClient()

  // ── 3. Audio Manager ─────────────────────────────────────────────────
  const audio = new AudioManager(ws)
  await audio.start()

  // ── 4. Lip-sync ───────────────────────────────────────────────────────
  const lipSync = new LipSync(audio, avatar)
  lipSync.start()

  // ── 5. Wire WebSocket message handlers ────────────────────────────────

  // tracking_update → avatar head rotation. The backend holds the last rotation
  // while a face is briefly lost and returns it to neutral itself after its
  // no-face timeout, so the rotation is applied as sent.
  ws.on('tracking_update', (msg) => {
    avatar.applyTrackingData(msg.avatar_control.head_rotation)
  })

  // speech_output → schedule audio for playback
  ws.on('speech_output', (msg) => {
    audio.queueAudio(msg.audio_data, msg.audio_mime_type, msg.is_final === true)
  })

  // Queue drained after is_final → tell the backend the avatar stopped speaking
  audio.onPlaybackEnded(() => ws.sendPlaybackEnded())

  // interrupt → stop playback immediately (barge-in)
  ws.on('interrupt', () => {
    audio.interrupt()
  })

  // state → status label + "thinking" head tilt
  ws.on('state', (msg) => {
    ws.setStatus(msg.state)
    avatar.setState(msg.state)
  })

  // connected → log session info
  ws.on('connected', (msg) => {
    console.log('[Main] Connected to backend, session:', msg.session_id)
  })

  // error → log only, keep running; `state` messages own the status label
  ws.on('error', (msg) => {
    console.error('[Main] Backend error:', msg.code, msg.message)
  })

  // WS drop → stop playback, clear the queue, avatar back to neutral
  ws.onDrop(() => {
    audio.interrupt()
    avatar.setState('idle')
    avatar.applyTrackingData(null)
  })

  // ── 6. Start WebSocket connection ─────────────────────────────────────
  ws.connect()

  console.log('[Main] Magic Mirror AI Avatar ready')
}

main().catch((err) => {
  console.error('[Main] Fatal error during init:', err)
  const loadingText = document.getElementById('loading-text')
  if (loadingText) {
    loadingText.textContent = 'Lỗi khởi động: ' + err.message
    loadingText.style.color = '#ff4444'
  }
})
