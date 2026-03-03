/**
 * Frontend Entry Point — TASK-09
 *
 * Init sequence:
 *   1. AvatarRenderer → Three.js scene + VRM model
 *   2. WSClient → WebSocket connection
 *   3. AudioManager → mic capture + playback context
 *   4. LipSync → hook into audio + avatar
 *   5. Wire WS message handlers
 */

import { AvatarRenderer } from './avatar-renderer.js'
import { WSClient } from './ws-client.js'
import { AudioManager } from './audio-manager.js'
import { LipSync } from './lip-sync.js'

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

  // Wire audio callbacks to lip-sync
  audio.onPlaybackStart(() => lipSync.onAudioStart())
  audio.onPlaybackEnd(() => lipSync.onAudioEnd())
  lipSync.start()

  // ── 5. Wire WebSocket message handlers ────────────────────────────────

  // tracking_update → avatar head rotation (FR-001)
  ws.on('tracking_update', (msg) => {
    const control = msg.avatar_control
    if (control?.head_rotation) {
      avatar.applyTrackingData(control.head_rotation)
    }
  })

  // speech_output → queue audio for playback (FR-002)
  ws.on('speech_output', async (msg) => {
    if (msg.audio_data) {
      await audio.queueAudio(msg.audio_data, msg.audio_mime_type || 'audio/pcm;rate=24000')
    }
  })

  // interrupt → stop playback immediately (EC-005 barge-in)
  ws.on('interrupt', () => {
    audio.interrupt()
  })

  // connected → log session info
  ws.on('connected', (msg) => {
    console.log('[Main] Connected to backend, session:', msg.session_id)
  })

  // error → log (EC handling)
  ws.on('error', (msg) => {
    console.error('[Main] Backend error:', msg.code, msg.message)
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
