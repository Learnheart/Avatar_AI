/**
 * Audio Manager — TASK-13
 *
 * Capture:
 *   - getUserMedia với echo cancellation, noise suppression, auto gain control
 *   - AudioWorklet tại 16kHz (forced sample rate) → Int16 PCM → Base64 → WS
 *
 * Playback:
 *   - Nhận base64 audio từ speech_output message
 *   - Decode PCM/MP3 → AudioBuffer → FIFO queue playback
 *   - Queue overflow (>5) → drop oldest (EC-007)
 *
 * Interrupt:
 *   - Dừng playback ngay, xóa queue, recreate AudioContext (EC-005)
 */

const CAPTURE_SAMPLE_RATE = 16000
const MAX_QUEUE_SIZE = 5
const WORKLET_URL = '/worklet/audio-processor.js'

export class AudioManager {
  constructor(wsClient) {
    this._ws = wsClient
    this._captureCtx = null
    this._playbackCtx = null
    this._workletNode = null
    this._micStream = null

    this._playbackQueue = []       // { audioBuffer, startTime }[]
    this._isPlaying = false
    this._onPlaybackEnd = null     // callback for LipSync
    this._onPlaybackStart = null   // callback for LipSync

    // Analyser for lip-sync (connected to playback chain)
    this._analyser = null
  }

  // ── Public API ─────────────────────────────────────────────────────────

  /**
   * Register callbacks so LipSync module can hook into audio.
   */
  onPlaybackStart(fn) { this._onPlaybackStart = fn }
  onPlaybackEnd(fn)   { this._onPlaybackEnd = fn }

  /**
   * Get the AnalyserNode for RMS measurement by LipSync.
   */
  getAnalyser() { return this._analyser }

  /**
   * Start microphone capture and audio playback context.
   */
  async start() {
    await this._startCapture()
    this._startPlaybackContext()
    console.log('[Audio] Manager started')
  }

  /**
   * Queue audio data (base64 string) for playback.
   * Called when speech_output message arrives.
   */
  async queueAudio(base64Data, mimeType) {
    if (!base64Data) return

    // Decode base64 → ArrayBuffer
    let arrayBuffer
    try {
      const binary = atob(base64Data)
      const bytes = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
      arrayBuffer = bytes.buffer
    } catch (e) {
      console.error('[Audio] Base64 decode error:', e)
      return
    }

    // Decode audio
    let audioBuffer
    try {
      if (mimeType && mimeType.startsWith('audio/pcm')) {
        audioBuffer = this._decodePCM(arrayBuffer, mimeType)
      } else {
        audioBuffer = await this._playbackCtx.decodeAudioData(arrayBuffer)
      }
    } catch (e) {
      console.error('[Audio] Decode error, dropping chunk:', e)  // BR-003
      return
    }

    if (!audioBuffer) return

    // EC-007: Queue overflow → drop oldest
    if (this._playbackQueue.length >= MAX_QUEUE_SIZE) {
      console.warn('[Audio] Queue overflow, dropping oldest chunk')
      this._playbackQueue.shift()
    }

    this._playbackQueue.push(audioBuffer)

    if (!this._isPlaying) {
      this._playNext()
    }
  }

  /**
   * Interrupt playback immediately — called on barge-in.
   */
  interrupt() {
    console.log('[Audio] Interrupt — clearing queue')
    this._playbackQueue = []
    this._isPlaying = false

    // Recreate playback context to stop all running sources (EC-005)
    if (this._playbackCtx) {
      this._playbackCtx.close().catch(() => {})
    }
    this._startPlaybackContext()

    if (this._onPlaybackEnd) this._onPlaybackEnd()
  }

  // ── Capture ──────────────────────────────────────────────────────────

  async _startCapture() {
    try {
      this._micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          sampleRate: CAPTURE_SAMPLE_RATE,
        },
        video: false,
      })

      // Force 16kHz by creating AudioContext at that rate
      this._captureCtx = new AudioContext({ sampleRate: CAPTURE_SAMPLE_RATE })
      const source = this._captureCtx.createMediaStreamSource(this._micStream)

      // Load AudioWorklet
      await this._captureCtx.audioWorklet.addModule(WORKLET_URL)
      this._workletNode = new AudioWorkletNode(this._captureCtx, 'audio-capture-processor')

      // Receive Int16 chunks from worklet → Base64 → WebSocket
      this._workletNode.port.onmessage = (event) => {
        if (event.data.type !== 'audio') return
        const base64 = this._int16ToBase64(event.data.buffer)
        this._ws.sendAudioChunk(base64)
      }

      source.connect(this._workletNode)
      // Don't connect workletNode to destination (no playback of mic)

      console.log('[Audio] Microphone capture started at', CAPTURE_SAMPLE_RATE, 'Hz')
    } catch (e) {
      if (e.name === 'NotAllowedError') {
        console.warn('[Audio] Mic permission denied — voice input disabled')
      } else {
        console.error('[Audio] Capture init error:', e)
      }
    }
  }

  _int16ToBase64(buffer) {
    const bytes = new Uint8Array(buffer)
    let binary = ''
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i])
    }
    return btoa(binary)
  }

  // ── Playback ──────────────────────────────────────────────────────────

  _startPlaybackContext() {
    this._playbackCtx = new AudioContext()

    // AnalyserNode for lip-sync
    this._analyser = this._playbackCtx.createAnalyser()
    this._analyser.fftSize = 256
    this._analyser.connect(this._playbackCtx.destination)
  }

  _playNext() {
    if (this._playbackQueue.length === 0) {
      this._isPlaying = false
      if (this._onPlaybackEnd) this._onPlaybackEnd()
      return
    }

    this._isPlaying = true
    const audioBuffer = this._playbackQueue.shift()

    const source = this._playbackCtx.createBufferSource()
    source.buffer = audioBuffer
    source.connect(this._analyser)

    source.onended = () => {
      this._playNext()
    }

    if (this._onPlaybackStart) this._onPlaybackStart()
    source.start()
  }

  /**
   * Decode raw PCM bytes to AudioBuffer.
   * Supports "audio/pcm;rate=24000" and "audio/pcm;rate=16000".
   */
  _decodePCM(arrayBuffer, mimeType) {
    // Parse sample rate from mime type (default 24000)
    const rateMatch = mimeType.match(/rate=(\d+)/)
    const sampleRate = rateMatch ? parseInt(rateMatch[1]) : 24000

    const int16 = new Int16Array(arrayBuffer)
    const float32 = new Float32Array(int16.length)
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768.0
    }

    const audioBuffer = this._playbackCtx.createBuffer(1, float32.length, sampleRate)
    audioBuffer.copyToChannel(float32, 0)
    return audioBuffer
  }
}
