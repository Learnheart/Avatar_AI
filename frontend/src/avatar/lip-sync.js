/**
 * Lip-sync Module
 *
 * Reads the RMS amplitude from the AnalyserNode on every requestAnimationFrame.
 * Maps RMS → blendshape "aa" value (0.0 → 1.0) → avatarRenderer.setMouthOpen().
 * The math lives in motion.js (pure, unit tested).
 *
 * The mouth follows the audio while the avatar speaks and lerps back to 0
 * when the audio ends.
 */

import { MOUTH } from '../config.js'
import { mouthTargetFromRms, rmsFromTimeDomain, stepMouth } from './motion.js'

export class LipSync {
  constructor(audioManager, avatarRenderer) {
    this._audio = audioManager
    this._avatar = avatarRenderer
    this._currentValue = 0
    this._dataArray = null
  }

  start() {
    this._loop()
  }

  // ── Render loop ─────────────────────────────────────────────────────

  _loop() {
    requestAnimationFrame(() => this._loop())

    const analyser = this._audio.getAnalyser()
    if (!analyser) return

    if (!this._dataArray || this._dataArray.length !== analyser.frequencyBinCount) {
      this._dataArray = new Uint8Array(analyser.frequencyBinCount)
    }

    // Only audio actually playing opens the mouth; otherwise it lerps to 0
    let target = 0
    if (this._audio.isPlaying()) {
      analyser.getByteTimeDomainData(this._dataArray)
      target = mouthTargetFromRms(rmsFromTimeDomain(this._dataArray), MOUTH)
    }

    this._currentValue = stepMouth(this._currentValue, target, MOUTH)
    this._avatar.setMouthOpen(this._currentValue)
  }
}
