/**
 * AudioWorkletProcessor — runs in dedicated audio thread.
 *
 * Receives Float32 samples at the AudioContext sample rate (CAPTURE_SAMPLE_RATE).
 * Converts to Int16 PCM and posts back to main thread.
 * Accumulates chunks of `processorOptions.chunkSamples` samples before posting
 * (CAPTURE_CHUNK_SAMPLES in src/config.js).
 */

class AudioCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this._chunkSamples = options.processorOptions.chunkSamples
    this._buffer = new Float32Array(0)
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || !input[0]) return true

    const channelData = input[0]   // Float32Array, mono

    const merged = new Float32Array(this._buffer.length + channelData.length)
    merged.set(this._buffer)
    merged.set(channelData, this._buffer.length)
    this._buffer = merged

    // Send complete chunks
    while (this._buffer.length >= this._chunkSamples) {
      const chunk = this._buffer.slice(0, this._chunkSamples)
      this._buffer = this._buffer.slice(this._chunkSamples)

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
