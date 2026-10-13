/**
 * Motion mapping — pure functions (docs/quality-gates.md §1.7)
 *
 * Parameter in → numeric motion value out. Imports nothing (no three, no DOM)
 * so it runs under `node --test`. Tunables are passed in as arguments.
 * Non-finite inputs are treated as 0.
 */

function finiteOrZero(v) {
  return Number.isFinite(v) ? v : 0
}

function clampSymmetric(v, max) {
  const limit = finiteOrZero(max)
  return Math.max(-limit, Math.min(limit, finiteOrZero(v)))
}

function clamp01(v) {
  return Math.max(0, Math.min(1, finiteOrZero(v)))
}

/**
 * Clamp head rotation (radians) to ±limits per axis.
 * pitch → X, yaw → Y, roll → Z, no sign flip. A null rotation gives neutral.
 */
export function clampHeadRotation(rotation, limits) {
  return {
    pitch: clampSymmetric(rotation?.pitch, limits.pitch),
    yaw: clampSymmetric(rotation?.yaw, limits.yaw),
    roll: clampSymmetric(rotation?.roll, limits.roll),
  }
}

/**
 * Distribute a rotation between head and neck bones.
 * head gets headShare, neck gets the rest — the two always sum to the input.
 */
export function splitHeadNeck(rotation, headShare) {
  const share = finiteOrZero(headShare)
  const head = {
    pitch: finiteOrZero(rotation.pitch) * share,
    yaw: finiteOrZero(rotation.yaw) * share,
    roll: finiteOrZero(rotation.roll) * share,
  }
  const neck = {
    pitch: finiteOrZero(rotation.pitch) - head.pitch,
    yaw: finiteOrZero(rotation.yaw) - head.yaw,
    roll: finiteOrZero(rotation.roll) - head.roll,
  }
  return { head, neck }
}

/**
 * One axis of the head's own motion at `seconds`: two sine waves of different periods
 * (seconds) added, so the sway does not repeat visibly. Always within ±amplitude.
 */
function sway(seconds, { amplitude, periods, phase }) {
  const t = finiteOrZero(seconds)
  const [slow, fast] = periods
  const wave = 0.65 * Math.sin((2 * Math.PI * t) / slow + finiteOrZero(phase))
    + 0.35 * Math.sin((2 * Math.PI * t) / fast + 2 * finiteOrZero(phase))
  return finiteOrZero(amplitude) * wave
}

/**
 * The head's own motion at `seconds`, as someone standing and waiting: a slow sway on
 * each axis, each described by { amplitude (radians), periods: [slow, fast] (seconds),
 * phase }. It depends on time alone, not on the person in front of the avatar.
 */
export function idleHeadRotation(seconds, axes) {
  return {
    pitch: sway(seconds, axes.pitch),
    yaw: sway(seconds, axes.yaw),
    roll: sway(seconds, axes.roll),
  }
}

/**
 * The rotation the head aims at: its own motion, the rotation of the person it follows
 * (null when it follows nobody) and an extra roll, added and clamped to the limits.
 */
export function headTarget(idle, followed, extraRoll, limits) {
  return clampHeadRotation({
    pitch: finiteOrZero(idle?.pitch) + finiteOrZero(followed?.pitch),
    yaw: finiteOrZero(idle?.yaw) + finiteOrZero(followed?.yaw),
    roll: finiteOrZero(idle?.roll) + finiteOrZero(followed?.roll) + finiteOrZero(extraRoll),
  }, limits)
}

/**
 * Extra head roll (radians) for a turn state: `tilt` while thinking, else 0.
 */
export function thinkingTilt(state, tilt) {
  return state === 'thinking' ? finiteOrZero(tilt) : 0
}

/**
 * RMS amplitude of AnalyserNode byte time-domain data (128 = silence) → [0, 1].
 */
export function rmsFromTimeDomain(bytes) {
  if (!bytes?.length) return 0
  let sum = 0
  for (let i = 0; i < bytes.length; i++) {
    const normalized = (bytes[i] - 128) / 128  // [-1, 1]
    sum += normalized * normalized
  }
  return finiteOrZero(Math.sqrt(sum / bytes.length))
}

/**
 * Map RMS → mouth open target (0.0 → 1.0). Below the noise floor → 0.
 */
export function mouthTargetFromRms(rms, { scale, noiseFloor }) {
  const level = finiteOrZero(rms)
  if (level < noiseFloor) return 0
  return Math.min(1, level * scale)
}

/**
 * One frame of mouth smoothing: lerp fast towards an open target, slowly back
 * to 0, and snap to 0 once below snapEpsilon.
 */
export function stepMouth(current, target, { lerpOpen, lerpClose, snapEpsilon }) {
  const value = clamp01(current)
  const goal = clamp01(target)
  if (goal > 0) return value + (goal - value) * lerpOpen
  return value > snapEpsilon ? value - value * lerpClose : 0
}
