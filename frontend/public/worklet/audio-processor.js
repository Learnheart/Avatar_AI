/**
 * AudioWorkletProcessor — runs in dedicated audio thread.
 *
 * Receives Float32 samples at AudioContext sample rate (forced to 16kHz).
 * Converts to Int16 PCM and posts back to main thread.
 * Accumulates 20ms chunks (320 samples @ 16kHz) before posting.
 */

const CHUNK_SAMPLES = 320   // 20ms at 16000 Hz

class AudioCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._buffer = new Float32Array(0)
  }

  process(inputs, _outputs, _parameters) {
    const input = inputs[0]
    if (!input || !input[0]) return true

    const channelData = input[0]   // Float32Array, mono

    // Append to internal buffer
    const merged = new Float32Array(this._buffer.length + channelData.length)
    merged.set(this._buffer)
    merged.set(channelData, this._buffer.length)
    this._buffer = merged

    // Send complete chunks
    while (this._buffer.length >= CHUNK_SAMPLES) {
      const chunk = this._buffer.slice(0, CHUNK_SAMPLES)
      this._buffer = this._buffer.slice(CHUNK_SAMPLES)

      // Convert Float32 → Int16
      const int16 = new Int16Array(chunk.length)
      for (let i = 0; i < chunk.length; i++) {
        const s = Math.max(-1, Math.min(1, chunk[i]))
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF
      }

      // Transfer ownership (zero-copy)
      this.port.postMessage({ type: 'audio', buffer: int16.buffer }, [int16.buffer])
    }

    return true  // Keep processor alive
  }
}

registerProcessor('audio-capture-processor', AudioCaptureProcessor)
