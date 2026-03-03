/**
 * Lip-sync Module — TASK-14
 *
 * Reads RMS amplitude từ AnalyserNode mỗi requestAnimationFrame.
 * Map RMS → blendshape "aa" value (0.0 → 1.0) → gọi avatarRenderer.setMouthOpen().
 *
 * FR-003-A: Lip-sync khi avatar nói
 * FR-003-B: Lerp miệng về 0 khi audio kết thúc
 */

const LERP_OPEN  = 0.4   // Mouth open speed (per frame, ~16ms)
const LERP_CLOSE = 0.08  // Mouth close speed — slower for natural feel
const RMS_SCALE  = 6.0   // Amplify RMS to get visible mouth movement
const MIN_RMS    = 0.005 // Ignore very quiet audio (noise floor)

export class LipSync {
  constructor(audioManager, avatarRenderer) {
    this._audio = audioManager
    this._avatar = avatarRenderer
    this._currentValue = 0
    this._isActive = false
    this._rafId = null
    this._dataArray = null
  }

  start() {
    this._loop()
  }

  stop() {
    if (this._rafId) cancelAnimationFrame(this._rafId)
  }

  /** Called when audio playback starts (from AudioManager callback). */
  onAudioStart() {
    this._isActive = true
  }

  /** Called when audio playback ends. */
  onAudioEnd() {
    this._isActive = false
  }

  // ── Render loop ─────────────────────────────────────────────────────

  _loop() {
    this._rafId = requestAnimationFrame(() => this._loop())

    const analyser = this._audio.getAnalyser()
    if (!analyser) return

    // Lazy-init data array
    if (!this._dataArray || this._dataArray.length !== analyser.frequencyBinCount) {
      this._dataArray = new Uint8Array(analyser.frequencyBinCount)
    }

    if (this._isActive) {
      analyser.getByteTimeDomainData(this._dataArray)
      const rms = this._computeRMS(this._dataArray)

      if (rms < MIN_RMS) {
        // Treat as silence → close mouth
        this._currentValue += (0 - this._currentValue) * LERP_CLOSE
      } else {
        const target = Math.min(1.0, rms * RMS_SCALE)
        this._currentValue += (target - this._currentValue) * LERP_OPEN
      }
    } else {
      // FR-003-B: Audio ended → Lerp mouth to 0 (~100ms)
      if (this._currentValue > 0.001) {
        this._currentValue += (0 - this._currentValue) * LERP_CLOSE
      } else {
        this._currentValue = 0
      }
    }

    this._avatar.setMouthOpen(this._currentValue)
  }

  _computeRMS(byteData) {
    let sum = 0
    for (let i = 0; i < byteData.length; i++) {
      const normalized = (byteData[i] - 128) / 128  // [-1, 1]
      sum += normalized * normalized
    }
    return Math.sqrt(sum / byteData.length)
  }
}
