/**
 * Frontend config — the single place for env-driven values and tunable constants.
 *
 * Env (Vite, see frontend/.env.example): VITE_WS_URL, VITE_API_URL, VITE_VRM_PATH.
 */

// ── Env-driven ────────────────────────────────────────────────────────────

export const WS_URL = import.meta.env.VITE_WS_URL || `ws://${window.location.hostname}:8000/ws`
// Backend HTTP API (knowledge base, memory, persona), without a trailing slash.
export const API_URL = (
  import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8000`
).replace(/\/+$/, '')
export const VRM_PATH = import.meta.env.VITE_VRM_PATH || '/models/avatar.vrm'

// ── WebSocket reconnect (exponential backoff) ─────────────────────────────

export const WS_BACKOFF_INITIAL_MS = 3000
export const WS_BACKOFF_MAX_MS = 30000
export const WS_BACKOFF_FACTOR = 2

// ── Audio ─────────────────────────────────────────────────────────────────

export const CAPTURE_SAMPLE_RATE = 16000    // Hz — must match backend VAD/STT input
export const CAPTURE_CHUNK_SAMPLES = 320    // 20ms at 16000 Hz
export const CAPTURE_WORKLET_URL = '/worklet/audio-processor.js'
export const ANALYSER_FFT_SIZE = 256        // Time-domain window read by lip-sync

// ── Knowledge base ────────────────────────────────────────────────────────

// Checked before uploading; the backend has the last word (its loaders and
// KNOWLEDGE_MAX_FILE_MB).
export const KNOWLEDGE_EXTENSIONS = ['.txt', '.md', '.csv', '.pdf', '.docx']
export const KNOWLEDGE_MAX_FILE_MB = 10

// ── Profile page (memory) ─────────────────────────────────────────────────

// The text fields stop at this length; the backend has the last word (its
// MEMORY_ITEM_MAX_CHARS).
export const MEMORY_ITEM_MAX_CHARS = 200
// A description of the person, checked before it is sent; the backend has the last
// word (its loaders and MEMORY_IMPORT_MAX_FILE_MB).
export const MEMORY_IMPORT_EXTENSIONS = ['.txt', '.md', '.csv', '.pdf', '.docx']
export const MEMORY_IMPORT_MAX_FILE_MB = 5

// ── Persona block of the profile page ─────────────────────────────────────

// The fields and the description stop at these lengths; the backend has the last word
// (its PERSONA_FIELD_MAX_CHARS and PERSONA_IMPORT_MAX_CHARS).
export const PERSONA_FIELD_MAX_CHARS = 300
export const PERSONA_IMPORT_MAX_CHARS = 8000

// ── Transcript window ─────────────────────────────────────────────────────

export const TRANSCRIPT_MAX_LINES = 40          // Lines kept on screen; the oldest are dropped
export const TRANSCRIPT_SCROLL_SLACK_PX = 24    // This close to the end, a new line scrolls the list

// ── Head motion ───────────────────────────────────────────────────────────

// Head rotation clamps (radians) — gentle motion: ~35% of the backend limits.
// The backend still sends the full range; the frontend softens it for display.
export const HEAD_CLAMP = {
  pitch: 0.18,   // ~±10°  (was ±30°)
  yaw: 0.27,     // ~±16°  (was ±45°)
  roll: 0.12,    // ~±7°   (was ±20°)
}
export const HEAD_LERP = 0.15           // Smoothing factor per frame
export const HEAD_SHARE = 0.7           // Head takes 70%, neck the remaining 30%
export const THINKING_TILT_RAD = 0.12   // Extra roll (~7°) while state is "thinking"

// ── Lip-sync ──────────────────────────────────────────────────────────────

export const MOUTH = {
  scale: 6.0,           // Amplify RMS to get visible mouth movement
  noiseFloor: 0.005,    // Ignore very quiet audio
  lerpOpen: 0.4,        // Mouth open speed (per frame, ~16ms)
  lerpClose: 0.08,      // Mouth close speed — slower for natural feel
  snapEpsilon: 0.001,   // Below this a closing mouth snaps to 0
}

// ── Idle animation ────────────────────────────────────────────────────────

export const BLINK_INTERVAL_MIN_SEC = 3      // Random interval 3-5 seconds
export const BLINK_INTERVAL_RANGE_SEC = 2
export const BLINK_CLOSE_SEC = 0.1           // Close / open duration
export const BLINK_HOLD_SEC = 0.05
export const BREATHING_SPEED = 0.8           // radians/sec ≈ 13 breaths/min
export const BREATHING_AMP = 0.003           // Scale amplitude

// ── Scene ─────────────────────────────────────────────────────────────────

export const MAX_PIXEL_RATIO = 2
export const BACKGROUND_COLOR = 0x0a0a0f

// Camera — portrait framing for kiosk (head + shoulders only)
export const CAMERA = {
  fov: 24,
  near: 0.1,
  far: 20,
  position: [0, 1.42, 1.15],
  lookAt: [0, 1.4, 0],
}

export const LIGHTS = {
  ambient: { color: 0xffffff, intensity: 0.6 },
  key: { color: 0xffffff, intensity: 1.2, position: [1, 2, 2] },
  fill: { color: 0x8080ff, intensity: 0.3, position: [-2, 1, -1] },
}

export const LOADING_HIDE_DELAY_MS = 500
