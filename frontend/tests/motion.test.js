/**
 * Unit tests for avatar/motion.js — quality-gates §1.7 (parameter → motion value).
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  clampHeadRotation,
  headTarget,
  idleHeadRotation,
  mouthTargetFromRms,
  rmsFromTimeDomain,
  splitHeadNeck,
  stepMouth,
  thinkingTilt,
} from '../src/avatar/motion.js'

const LIMITS = { pitch: 0.52, yaw: 0.78, roll: 0.35 }
const MOUTH = { scale: 6.0, noiseFloor: 0.005, lerpOpen: 0.4, lerpClose: 0.08, snapEpsilon: 0.001 }
const EPSILON = 1e-12

/** Square wave of the given amplitude (0 → 1) as AnalyserNode byte data. */
function squareWave(amplitude, length = 128) {
  return Uint8Array.from({ length }, (_, i) => 128 + (i % 2 === 0 ? 1 : -1) * amplitude * 127)
}

// ── clampHeadRotation ───────────────────────────────────────────────────

test('clampHeadRotation keeps in-range values on the same axis with the same sign', () => {
  assert.deepEqual(
    clampHeadRotation({ pitch: 0.1, yaw: -0.2, roll: 0.3 }, LIMITS),
    { pitch: 0.1, yaw: -0.2, roll: 0.3 }
  )
  assert.deepEqual(
    clampHeadRotation({ pitch: -0.1, yaw: 0.2, roll: -0.3 }, LIMITS),
    { pitch: -0.1, yaw: 0.2, roll: -0.3 }
  )
})

test('clampHeadRotation moves only the axis that was given', () => {
  assert.deepEqual(clampHeadRotation({ pitch: 0.2, yaw: 0, roll: 0 }, LIMITS), { pitch: 0.2, yaw: 0, roll: 0 })
  assert.deepEqual(clampHeadRotation({ pitch: 0, yaw: 0.2, roll: 0 }, LIMITS), { pitch: 0, yaw: 0.2, roll: 0 })
  assert.deepEqual(clampHeadRotation({ pitch: 0, yaw: 0, roll: 0.2 }, LIMITS), { pitch: 0, yaw: 0, roll: 0.2 })
})

test('clampHeadRotation clamps beyond the limits, both signs', () => {
  assert.deepEqual(
    clampHeadRotation({ pitch: 2, yaw: 2, roll: 2 }, LIMITS),
    { pitch: 0.52, yaw: 0.78, roll: 0.35 }
  )
  assert.deepEqual(
    clampHeadRotation({ pitch: -2, yaw: -2, roll: -2 }, LIMITS),
    { pitch: -0.52, yaw: -0.78, roll: -0.35 }
  )
})

test('clampHeadRotation keeps values exactly at the limits', () => {
  assert.deepEqual(
    clampHeadRotation({ pitch: 0.52, yaw: -0.78, roll: 0.35 }, LIMITS),
    { pitch: 0.52, yaw: -0.78, roll: 0.35 }
  )
})

test('clampHeadRotation treats non-finite and missing input as 0', () => {
  assert.deepEqual(
    clampHeadRotation({ pitch: NaN, yaw: Infinity, roll: -Infinity }, LIMITS),
    { pitch: 0, yaw: 0, roll: 0 }
  )
  assert.deepEqual(clampHeadRotation({ yaw: 0.3 }, LIMITS), { pitch: 0, yaw: 0.3, roll: 0 })
  assert.deepEqual(clampHeadRotation(null, LIMITS), { pitch: 0, yaw: 0, roll: 0 })
})

// ── splitHeadNeck ───────────────────────────────────────────────────────

test('splitHeadNeck gives the head its share and sums to the input', () => {
  const rotation = { pitch: 0.4, yaw: -0.6, roll: 0.2 }
  const { head, neck } = splitHeadNeck(rotation, 0.7)

  for (const axis of ['pitch', 'yaw', 'roll']) {
    assert.ok(Math.abs(head[axis] - rotation[axis] * 0.7) < EPSILON)
    assert.ok(Math.abs(head[axis] + neck[axis] - rotation[axis]) < EPSILON)
    assert.equal(Math.sign(head[axis]), Math.sign(rotation[axis]))
    assert.equal(Math.sign(neck[axis]), Math.sign(rotation[axis]))
  }
})

test('splitHeadNeck with full head share leaves the neck neutral', () => {
  const { head, neck } = splitHeadNeck({ pitch: 0.4, yaw: -0.6, roll: 0.2 }, 1)
  assert.deepEqual(head, { pitch: 0.4, yaw: -0.6, roll: 0.2 })
  assert.deepEqual(neck, { pitch: 0, yaw: 0, roll: 0 })
})

test('splitHeadNeck treats non-finite input as 0', () => {
  const { head, neck } = splitHeadNeck({ pitch: NaN, yaw: Infinity, roll: 0.2 }, 0.7)
  assert.equal(head.pitch, 0)
  assert.equal(neck.pitch, 0)
  assert.equal(head.yaw, 0)
  assert.equal(neck.yaw, 0)
  assert.ok(Math.abs(head.roll + neck.roll - 0.2) < EPSILON)

  assert.deepEqual(splitHeadNeck({ pitch: 0.4, yaw: 0, roll: 0 }, NaN).head, { pitch: 0, yaw: 0, roll: 0 })
})

// ── thinkingTilt ────────────────────────────────────────────────────────

test('thinkingTilt is applied only while thinking', () => {
  assert.equal(thinkingTilt('thinking', 0.12), 0.12)
  for (const state of ['idle', 'listening', 'speaking', undefined]) {
    assert.equal(thinkingTilt(state, 0.12), 0)
  }
  assert.equal(thinkingTilt('thinking', NaN), 0)
})

test('thinkingTilt added to a tracked roll stays within the clamp', () => {
  const tilted = clampHeadRotation({ pitch: 0, yaw: 0, roll: 0.3 + thinkingTilt('thinking', 0.12) }, LIMITS)
  assert.equal(tilted.roll, LIMITS.roll)
})

// ── rmsFromTimeDomain ───────────────────────────────────────────────────

test('rmsFromTimeDomain is 0 for silence and empty input', () => {
  assert.equal(rmsFromTimeDomain(new Uint8Array(128).fill(128)), 0)
  assert.equal(rmsFromTimeDomain(new Uint8Array(0)), 0)
  assert.equal(rmsFromTimeDomain(null), 0)
})

test('rmsFromTimeDomain measures amplitude over (b - 128) / 128', () => {
  assert.ok(Math.abs(rmsFromTimeDomain(Uint8Array.from([0, 0, 0, 0])) - 1) < EPSILON)
  assert.ok(Math.abs(rmsFromTimeDomain(Uint8Array.from([192, 64, 192, 64])) - 0.5) < EPSILON)
})

test('rmsFromTimeDomain treats non-finite data as 0', () => {
  assert.equal(rmsFromTimeDomain([NaN, 128, 128]), 0)
  assert.equal(rmsFromTimeDomain([Infinity, 128]), 0)
})

// ── mouthTargetFromRms ──────────────────────────────────────────────────

test('louder audio gives a larger mouth target', () => {
  const quiet = mouthTargetFromRms(rmsFromTimeDomain(squareWave(0.02)), MOUTH)
  const medium = mouthTargetFromRms(rmsFromTimeDomain(squareWave(0.06)), MOUTH)
  const loud = mouthTargetFromRms(rmsFromTimeDomain(squareWave(0.12)), MOUTH)

  assert.ok(quiet > 0)
  assert.ok(medium > quiet)
  assert.ok(loud > medium)
})

test('mouthTargetFromRms scales the RMS and saturates at 1', () => {
  assert.ok(Math.abs(mouthTargetFromRms(0.05, MOUTH) - 0.3) < EPSILON)
  assert.equal(mouthTargetFromRms(0.5, MOUTH), 1)
  assert.equal(mouthTargetFromRms(1, MOUTH), 1)
})

test('mouthTargetFromRms is 0 below the noise floor and open at it', () => {
  assert.equal(mouthTargetFromRms(0, MOUTH), 0)
  assert.equal(mouthTargetFromRms(0.0049, MOUTH), 0)
  assert.ok(mouthTargetFromRms(0.005, MOUTH) > 0)
})

test('mouthTargetFromRms treats non-finite RMS as 0', () => {
  assert.equal(mouthTargetFromRms(NaN, MOUTH), 0)
  assert.equal(mouthTargetFromRms(Infinity, MOUTH), 0)
  assert.equal(mouthTargetFromRms(undefined, MOUTH), 0)
})

// ── stepMouth ───────────────────────────────────────────────────────────

test('stepMouth lerps towards an open target with lerpOpen', () => {
  assert.ok(Math.abs(stepMouth(0, 1, MOUTH) - 0.4) < EPSILON)
  assert.ok(Math.abs(stepMouth(0.5, 1, MOUTH) - 0.7) < EPSILON)
  assert.ok(Math.abs(stepMouth(1, 0.5, MOUTH) - 0.8) < EPSILON)
})

test('stepMouth closes with lerpClose when the target is 0', () => {
  assert.ok(Math.abs(stepMouth(1, 0, MOUTH) - 0.92) < EPSILON)
})

test('stepMouth converges to 0 and snaps', () => {
  let value = 1
  let frames = 0
  while (value > 0 && frames < 1000) {
    const next = stepMouth(value, 0, MOUTH)
    assert.ok(next < value)
    value = next
    frames++
  }
  assert.equal(value, 0)
  assert.ok(frames < 1000)
  assert.equal(stepMouth(0.001, 0, MOUTH), 0)
  assert.equal(stepMouth(0, 0, MOUTH), 0)
})

test('stepMouth output stays within 0..1 and treats non-finite input as 0', () => {
  assert.equal(stepMouth(NaN, 0, MOUTH), 0)
  assert.equal(stepMouth(0.5, NaN, MOUTH), stepMouth(0.5, 0, MOUTH))
  assert.ok(Math.abs(stepMouth(Infinity, 1, MOUTH) - 0.4) < EPSILON)
  assert.equal(stepMouth(5, 1, MOUTH), 1)
  assert.equal(stepMouth(-5, 0, MOUTH), 0)
})


// ── The head's own motion ───────────────────────────────────────────────────

const IDLE = {
  pitch: { amplitude: 0.03, periods: [9.1, 5.3], phase: 0.7 },
  yaw: { amplitude: 0.07, periods: [13.7, 7.9], phase: 0 },
  roll: { amplitude: 0.02, periods: [11.3, 6.1], phase: 2.1 },
}

test('idleHeadRotation stays within the amplitude of each axis', () => {
  for (let t = 0; t < 600; t += 0.37) {
    const rotation = idleHeadRotation(t, IDLE)
    for (const axis of ['pitch', 'yaw', 'roll']) {
      assert.ok(Math.abs(rotation[axis]) <= IDLE[axis].amplitude + 1e-12, `${axis} at ${t}`)
    }
  }
})

test('idleHeadRotation moves, slowly, and depends on time alone', () => {
  const at = (t) => idleHeadRotation(t, IDLE).yaw

  assert.deepEqual(idleHeadRotation(12.5, IDLE), idleHeadRotation(12.5, IDLE))
  assert.notEqual(at(0), at(3))
  // A sixtieth of a second apart, the head has barely moved: no jump between frames.
  for (let t = 0; t < 60; t += 1.3) {
    assert.ok(Math.abs(at(t + 1 / 60) - at(t)) < 0.001, `jump at ${t}`)
  }
})

test('idleHeadRotation uses both periods: it does not repeat after the slow one', () => {
  const [slow] = IDLE.yaw.periods

  assert.ok(Math.abs(idleHeadRotation(2, IDLE).yaw - idleHeadRotation(2 + slow, IDLE).yaw) > 1e-4)
})

test('idleHeadRotation treats a time that is no number as the start', () => {
  assert.deepEqual(idleHeadRotation(NaN, IDLE), idleHeadRotation(0, IDLE))
})

test('headTarget without anybody to follow is the head\'s own motion plus the extra roll', () => {
  const idle = { pitch: 0.01, yaw: -0.02, roll: 0.005 }

  assert.deepEqual(headTarget(idle, null, 0, LIMITS), idle)
  assert.ok(Math.abs(headTarget(idle, null, 0.1, LIMITS).roll - 0.105) < 1e-12)
})

test('headTarget adds the person it follows and clamps the sum', () => {
  const idle = { pitch: 0.01, yaw: 0.02, roll: 0 }

  const target = headTarget(idle, { pitch: 0.1, yaw: 5, roll: -0.05 }, 0.02, LIMITS)

  assert.ok(Math.abs(target.pitch - 0.11) < 1e-12)
  assert.equal(target.yaw, LIMITS.yaw)
  assert.ok(Math.abs(target.roll - -0.03) < 1e-12)
})
