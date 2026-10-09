/**
 * Audio Manager
 *
 * Capture:
 *   - getUserMedia with echo cancellation, noise suppression, auto gain control
 *   - AudioWorklet at 16kHz (forced sample rate) → Int16 PCM → Base64 → WS
 *
 * Playback:
 *   - Receives base64 audio from speech_output messages ("audio/pcm;rate=N", Int16 LE mono)
 *   - Decode PCM → AudioBuffer → scheduled gapless on the AudioContext clock
 *   - Queued audio is never dropped; a bad chunk is skipped with a warning
 *   - Queue drained after is_final → playback_ended callback
 *
 * Interrupt:
 *   - Stops every scheduled source at once and bumps the generation so stale
 *     callbacks are ignored
 */

import {
  ANALYSER_FFT_SIZE,
  CAPTURE_CHUNK_SAMPLES,
  CAPTURE_SAMPLE_RATE,
  CAPTURE_WORKLET_URL,
} from '../config.js'

const CAPTURE_PROCESSOR_NAME = 'audio-capture-processor'  // registered by the worklet
const PCM_MIME_PATTERN = /^audio\/pcm;\s*rate=(\d+)/
const INT16_SCALE = 32768
const GESTURE_EVENTS = ['click', 'keydown', 'touchstart']

export class AudioManager {
  constructor(wsClient) {
    this._ws = wsClient
    this._captureCtx = null
    this._playbackCtx = null
    this._workletNode = null
    this._micStream = null

    // Analyser for lip-sync (connected to playback chain)
    this._analyser = null

    this._sources = new Set()      // scheduled or playing AudioBufferSourceNodes
    this._nextStartTime = 0        // AudioContext time where the last buffer ends
    this._generation = 0           // bumped by interrupt() — older work is ignored
    this._finalReceived = false    // is_final seen, waiting for the queue to drain
    this._onPlaybackEnded = null   // callback → WS playback_ended
  }

  // ── Public API ─────────────────────────────────────────────────────────

  /**
   * Register the callback fired once when all audio of a turn has played.
   */
  onPlaybackEnded(fn) { this._onPlaybackEnded = fn }

  /**
   * Get the AnalyserNode for RMS measurement by LipSync.
   */
  getAnalyser() { return this._analyser }

  /**
   * True while any scheduled audio has not finished playing.
   */
  isPlaying() { return this._sources.size > 0 }

  /**
   * Start microphone capture and audio playback context.
   */
  async start() {
    await this._startCapture()
    this._startPlaybackContext()
    this._resumeOnUserGesture()
    console.log('[Audio] Manager started')
  }

  /**
   * Queue audio data (base64 string) for playback.
   * Called when speech_output message arrives. Empty audio is valid
   * (text-only message or the final marker).
   */
  queueAudio(base64Data, mimeType, isFinal) {
    if (base64Data) {
      const audioBuffer = this._decodePCM(base64Data, mimeType)
      if (audioBuffer) this._schedule(audioBuffer)
    }

    if (isFinal) {
      this._finalReceived = true
      this._emitPlaybackEndedIfDrained()
    }
  }

  /**
   * Interrupt playback immediately — called on barge-in and on WS drop.
   */
  interrupt() {
    console.log('[Audio] Interrupt — clearing queue')
    this._generation++
    for (const source of this._sources) {
      source.stop()
      source.disconnect()
    }
    this._sources.clear()
    this._nextStartTime = 0
    this._finalReceived = false
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

      await this._captureCtx.audioWorklet.addModule(CAPTURE_WORKLET_URL)
      this._workletNode = new AudioWorkletNode(this._captureCtx, CAPTURE_PROCESSOR_NAME, {
        processorOptions: { chunkSamples: CAPTURE_CHUNK_SAMPLES },
      })

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

    this._analyser = this._playbackCtx.createAnalyser()
    this._analyser.fftSize = ANALYSER_FFT_SIZE
    this._analyser.connect(this._playbackCtx.destination)
  }

  /**
   * Autoplay policy: an AudioContext may be created suspended. Audio scheduled
   * meanwhile waits on the frozen context clock and plays once resumed.
   */
  _resumeOnUserGesture() {
    const resume = () => {
      GESTURE_EVENTS.forEach((type) => window.removeEventListener(type, resume))
      for (const ctx of [this._captureCtx, this._playbackCtx]) {
        if (ctx?.state === 'suspended') {
          ctx.resume().catch((e) => console.warn('[Audio] AudioContext resume failed:', e))
        }
      }
    }
    GESTURE_EVENTS.forEach((type) => window.addEventListener(type, resume))
  }

  /**
   * Schedule a buffer right after the previous one (gapless), through the analyser.
   */
  _schedule(audioBuffer) {
    const generation = this._generation
    const source = this._playbackCtx.createBufferSource()
    source.buffer = audioBuffer
    source.connect(this._analyser)

    source.onended = () => {
      if (generation !== this._generation) return  // stopped by interrupt()
      this._sources.delete(source)
      this._emitPlaybackEndedIfDrained()
    }

    const startTime = Math.max(this._playbackCtx.currentTime, this._nextStartTime)
    source.start(startTime)
    this._nextStartTime = startTime + audioBuffer.duration
    this._sources.add(source)
  }

  _emitPlaybackEndedIfDrained() {
    if (!this._finalReceived || this._sources.size > 0) return
    this._finalReceived = false
    if (this._onPlaybackEnded) this._onPlaybackEnded()
  }

  /**
   * Decode base64 headerless Int16 LE mono PCM to an AudioBuffer at the rate
   * named in the mime type ("audio/pcm;rate=48000"). Returns null (chunk
   * skipped) on an unsupported mime type, odd byte length or decode failure.
   */
  _decodePCM(base64Data, mimeType) {
    const rateMatch = PCM_MIME_PATTERN.exec(mimeType || '')
    if (!rateMatch) {
      console.warn('[Audio] Unsupported audio mime type, skipping chunk:', mimeType)
      return null
    }

    try {
      const binary = atob(base64Data)
      if (binary.length % 2 !== 0) {
        console.warn('[Audio] Odd PCM byte length, skipping chunk:', binary.length)
        return null
      }

      const view = new DataView(new ArrayBuffer(binary.length))
      for (let i = 0; i < binary.length; i++) view.setUint8(i, binary.charCodeAt(i))

      const float32 = new Float32Array(binary.length / 2)
      for (let i = 0; i < float32.length; i++) {
        float32[i] = view.getInt16(i * 2, true) / INT16_SCALE
      }

      const sampleRate = parseInt(rateMatch[1], 10)
      const audioBuffer = this._playbackCtx.createBuffer(1, float32.length, sampleRate)
      audioBuffer.copyToChannel(float32, 0)
      return audioBuffer
    } catch (e) {
      console.warn('[Audio] Decode error, skipping chunk:', e)
      return null
    }
  }
}
