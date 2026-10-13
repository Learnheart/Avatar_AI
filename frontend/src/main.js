/**
 * Frontend Entry Point
 *
 * Init sequence:
 *   1. AvatarRenderer → Three.js scene + VRM model
 *   2. WSClient → WebSocket connection
 *   3. AudioManager → mic capture + playback context
 *   4. LipSync → hook into audio + avatar
 *   5. Wire WS message handlers
 *
 * The knowledge panel, the profile page, the sign-in and the transcript window are set
 * up first: they do not wait for the avatar.
 * The transcript window is filled from the WebSocket messages of the turn.
 */

import { AvatarRenderer } from './avatar/avatar-renderer.js'
import { WSClient } from './ws-client.js'
import { AudioManager } from './audio/audio-manager.js'
import { AuthClient } from './auth/auth-client.js'
import { AuthPanel } from './auth/auth-panel.js'
import { pageSession } from './auth/session.js'
import { LipSync } from './avatar/lip-sync.js'
import { KnowledgeClient } from './knowledge/knowledge-client.js'
import { KnowledgePanel } from './knowledge/knowledge-panel.js'
import { MemoryClient } from './memory/memory-client.js'
import { ProfilePage } from './memory/profile-page.js'
import { PersonaClient } from './persona/persona-client.js'
import { PersonaPanel } from './persona/persona-panel.js'
import { TranscriptPanel } from './transcript/transcript-panel.js'

async function main() {
  new KnowledgePanel(new KnowledgeClient()).init()
  const persona = new PersonaPanel(new PersonaClient())
  persona.init()
  const profile = new ProfilePage(new MemoryClient(), persona)
  profile.init()
  // Sign-in: the page starts as a guest, or as the user of the session it kept.
  const auth = new AuthPanel(new AuthClient(), pageSession, () => profile.open())
  pageSession.onChange(() => profile.sessionChanged())
  auth.init()

  // The WebSocket client is made now and connected last: the transcript window, which
  // only needs it for its button, works while the avatar is still loading.
  const ws = new WSClient(pageSession)

  // The button only asks: the window is emptied by the `conversation` message that
  // follows, so the screen cannot disagree with the backend.
  const transcript = new TranscriptPanel()
  transcript.init(() => ws.sendConversationReset())

  const container = document.getElementById('canvas-container')

  // ── 1. Avatar Renderer ────────────────────────────────────────────────
  const avatar = new AvatarRenderer(container)
  await avatar.init()

  // ── 2. WebSocket Client (created above, connected in step 6) ──────────

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

  // speech_output → schedule audio for playback; its sentence joins the avatar's line
  ws.on('speech_output', (msg) => {
    audio.queueAudio(msg.audio_data, msg.audio_mime_type, msg.is_final === true)
    transcript.appendAvatarSentence(msg.text_content)
  })

  // transcript → what the person said, once speech-to-text finished
  ws.on('transcript', (msg) => {
    transcript.addPersonLine(msg.text)
  })

  // conversation → the conversation in progress (null: none); another one than the
  // one on screen empties the transcript window
  ws.on('conversation', (msg) => {
    transcript.showConversation(msg.conversation_id)
  })

  // Queue drained after is_final → tell the backend the avatar stopped speaking
  audio.onPlaybackEnded(() => ws.sendPlaybackEnded())

  // interrupt → stop playback immediately (barge-in), mark the avatar's line as cut
  ws.on('interrupt', () => {
    audio.interrupt()
    transcript.cutAvatarLine()
  })

  // state → status label + "thinking" head tilt
  ws.on('state', (msg) => {
    ws.setStatus(msg.state)
    avatar.setState(msg.state)
  })

  // session → the mode the backend holds this connection in: it ended the session
  // (expired, signed out elsewhere, the person left), or confirms who is signed in
  ws.on('session', (msg) => {
    if (pageSession.token) pageSession.confirm(msg.user)
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
