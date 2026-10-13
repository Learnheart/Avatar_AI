/**
 * Avatar Renderer
 *
 * - Loads the VRM model via @pixiv/three-vrm
 * - Head tracking: pitch/yaw/roll → quaternion → slerp → head + neck bones
 * - "Thinking" head tilt added on top of the tracked rotation (covers dead air)
 * - Idle animation: random blink + sine-wave breathing (timings in config.js)
 * - Rotation is clamped to the configured limits — the math lives in motion.js
 * - Missing model / bone / blendshape → warn once at load, skip that feature
 */

import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'
import {
  BACKGROUND_COLOR,
  BLINK_CLOSE_SEC,
  BLINK_HOLD_SEC,
  BLINK_INTERVAL_MIN_SEC,
  BLINK_INTERVAL_RANGE_SEC,
  BREATHING_AMP,
  BREATHING_SPEED,
  CAMERA,
  HEAD_CLAMP,
  HEAD_FOLLOWS_PERSON,
  HEAD_IDLE,
  HEAD_LERP,
  HEAD_SHARE,
  LIGHTS,
  LOADING_HIDE_DELAY_MS,
  MAX_PIXEL_RATIO,
  THINKING_TILT_RAD,
  VRM_PATH,
} from '../config.js'
import {
  clampHeadRotation, headTarget, idleHeadRotation, splitHeadNeck, thinkingTilt,
} from './motion.js'

export class AvatarRenderer {
  constructor(container) {
    this._container = container
    this._renderer = null
    this._scene = null
    this._camera = null
    this._vrm = null
    this._clock = new THREE.Clock()

    // Bones + blendshapes found on the model (resolved once in _loadVRM)
    this._headNode = null
    this._neckNode = null
    this._chestNode = null
    this._hasBlinkLeft = false
    this._hasBlinkRight = false
    this._hasAA = false

    // Head targets: tracked rotation (clamped) + thinking tilt → head / neck quaternions
    this._trackedRotation = clampHeadRotation(null, HEAD_CLAMP)
    this._thinkingTilt = 0
    this._euler = new THREE.Euler(0, 0, 0, 'YXZ')
    this._headTarget = new THREE.Quaternion()
    this._neckTarget = new THREE.Quaternion()

    // Blink state
    this._blinkTimer = this._nextBlinkDelay()
    this._blinkState = 'idle'    // idle | closing | holding | opening
    this._blinkProgress = 0

    // Loading state
    this._loadingEl = document.getElementById('loading')
    this._loadingBar = document.getElementById('loading-bar')
    this._loadingText = document.getElementById('loading-text')
  }

  // ── Init ──────────────────────────────────────────────────────────────

  async init() {
    this._setupScene()
    await this._loadVRM()
    this._hideLoading()
    this._startRenderLoop()
  }

  _setupScene() {
    this._renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    this._renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_PIXEL_RATIO))
    this._renderer.setSize(window.innerWidth, window.innerHeight)
    this._renderer.shadowMap.enabled = true
    this._container.appendChild(this._renderer.domElement)

    this._scene = new THREE.Scene()
    this._scene.background = new THREE.Color(BACKGROUND_COLOR)

    // Camera — portrait framing for kiosk (upper half of body)
    this._camera = new THREE.PerspectiveCamera(
      CAMERA.fov, window.innerWidth / window.innerHeight, CAMERA.near, CAMERA.far
    )
    this._camera.position.set(...CAMERA.position)
    this._camera.lookAt(...CAMERA.lookAt)

    const ambient = new THREE.AmbientLight(LIGHTS.ambient.color, LIGHTS.ambient.intensity)
    this._scene.add(ambient)

    const dirLight = new THREE.DirectionalLight(LIGHTS.key.color, LIGHTS.key.intensity)
    dirLight.position.set(...LIGHTS.key.position)
    dirLight.castShadow = true
    this._scene.add(dirLight)

    const fillLight = new THREE.DirectionalLight(LIGHTS.fill.color, LIGHTS.fill.intensity)
    fillLight.position.set(...LIGHTS.fill.position)
    this._scene.add(fillLight)

    window.addEventListener('resize', () => {
      this._camera.aspect = window.innerWidth / window.innerHeight
      this._camera.updateProjectionMatrix()
      this._renderer.setSize(window.innerWidth, window.innerHeight)
    })
  }

  async _loadVRM() {
    this._setLoading(20, 'Đang tải mô hình avatar...')

    return new Promise((resolve, reject) => {
      const loader = new GLTFLoader()
      loader.register((parser) => new VRMLoaderPlugin(parser))

      loader.load(
        VRM_PATH,
        (gltf) => {
          const vrm = gltf.userData.vrm
          if (!vrm) {
            reject(new Error('VRM data not found in gltf.userData'))
            return
          }

          VRMUtils.removeUnnecessaryJoints(gltf.scene)

          // VRM 0.x models face +Z (towards the camera's back); VRM 1.0 faces -Z.
          // three-vrm does not auto-rotate, so turn a 0.x model 180° to face the camera.
          if (vrm.meta?.metaVersion === '0') {
            VRMUtils.rotateVRM0(vrm)
          }

          this._scene.add(vrm.scene)
          this._vrm = vrm
          this._probeCapabilities(vrm)
          this._setRestPose(vrm)
          this._updateHeadTargets()

          this._setLoading(100, 'Sẵn sàng!')
          console.log('[Avatar] VRM loaded')
          resolve(vrm)
        },
        (progress) => {
          const pct = Math.round((progress.loaded / (progress.total || 1)) * 70) + 20
          this._setLoading(pct, 'Đang tải mô hình avatar...')
        },
        (error) => {
          console.error('[Avatar] VRM load error:', error)
          this._setLoading(100, 'Lỗi tải avatar — chạy tiếp không có model')
          // Don't reject — allow system to run without avatar for debugging
          resolve(null)
        }
      )
    })
  }

  /**
   * Resolve which expressions and bones the model has. Each missing one is
   * warned about once here and its feature is skipped from then on.
   */
  _probeCapabilities(vrm) {
    // getExpression() returns null when absent
    const em = vrm.expressionManager
    this._hasBlinkLeft = em?.getExpression('blinkLeft') != null
    this._hasBlinkRight = em?.getExpression('blinkRight') != null
    this._hasAA = em?.getExpression('aa') != null

    if (!this._hasBlinkLeft && !this._hasBlinkRight) {
      console.warn('[Avatar] Blink blendshapes not found — blinking disabled')
    }
    if (!this._hasAA) {
      console.warn('[Avatar] "aa" blendshape not found — lip-sync disabled')
    }

    // getNormalizedBoneNode() returns null when absent
    const humanoid = vrm.humanoid
    this._headNode = humanoid?.getNormalizedBoneNode('head') ?? null
    this._neckNode = humanoid?.getNormalizedBoneNode('neck') ?? null
    this._chestNode = humanoid?.getNormalizedBoneNode('chest') ?? null

    if (!this._headNode) {
      console.warn('[Avatar] "head" bone not found — head tracking disabled')
    } else if (!this._neckNode) {
      console.warn('[Avatar] "neck" bone not found — head takes full rotation')
    }
    if (!this._chestNode) {
      console.warn('[Avatar] "chest" bone not found — breathing disabled')
    }
  }

  /**
   * Lower the arms from the default T-pose to a relaxed pose along the body.
   * VRM humanoid bones are normalized (A/T-pose aligned), so rotating the upper
   * arms around Z brings them down; a small elbow bend keeps it natural.
   * Portrait framing hides most of this, but it removes the spread-arm silhouette.
   */
  _setRestPose(vrm) {
    const humanoid = vrm.humanoid
    if (!humanoid) return

    const DOWN = 1.25        // ~72° shoulder drop
    const ELBOW = 0.15       // slight forearm bend
    const pose = [
      ['leftUpperArm', 'z', DOWN],
      ['rightUpperArm', 'z', -DOWN],
      ['leftLowerArm', 'z', ELBOW],
      ['rightLowerArm', 'z', -ELBOW],
    ]
    for (const [bone, axis, angle] of pose) {
      const node = humanoid.getNormalizedBoneNode(bone)
      if (node) node.rotation[axis] += angle
    }
  }

  // ── Public API ────────────────────────────────────────────────────────

  /**
   * Called when a tracking_update arrives, and with null when the connection drops.
   * The rotation of the person is only followed with HEAD_FOLLOWS_PERSON; otherwise
   * the head keeps its own motion. A null rotation is nobody to follow.
   */
  applyTrackingData(headRotation) {
    // The tracker's pitch sign is inverted relative to the VRM head bone's X axis
    // (looking up tilted the head down and vice versa), so flip pitch here.
    const adjusted = headRotation
      ? { ...headRotation, pitch: -headRotation.pitch }
      : headRotation
    this._trackedRotation = clampHeadRotation(adjusted, HEAD_CLAMP)
  }

  /**
   * Called when a server `state` message arrives (idle | listening | thinking | speaking).
   * "thinking" adds a small head tilt on top of the head's motion.
   */
  setState(state) {
    this._thinkingTilt = thinkingTilt(state, THINKING_TILT_RAD)
  }

  /**
   * Set mouth open blendshape value for lip-sync (0.0 → 1.0).
   * Called by LipSync module.
   */
  setMouthOpen(value) {
    if (!this._hasAA) return
    this._vrm.expressionManager.setValue('aa', value)
  }

  // ── Render loop ───────────────────────────────────────────────────────

  _startRenderLoop() {
    const animate = () => {
      requestAnimationFrame(animate)
      const delta = this._clock.getDelta()
      const elapsed = this._clock.elapsedTime

      if (this._vrm) {
        this._updateHeadTargets(elapsed)
        this._updateHeadTracking()
        this._updateBlink(delta)
        this._updateBreathing(elapsed)
        this._vrm.update(delta)
      }

      this._renderer.render(this._scene, this._camera)
    }
    animate()
  }

  // ── Head tracking animation ──────────────────────────────────────────

  /**
   * Recompute head / neck target quaternions for this frame: the head's own motion,
   * the person's rotation when it is followed, the thinking tilt on roll, clamped,
   * then distributed (pitch → X, yaw → Y, roll → Z).
   */
  _updateHeadTargets(elapsed) {
    const target = headTarget(
      idleHeadRotation(elapsed, HEAD_IDLE),
      HEAD_FOLLOWS_PERSON ? this._trackedRotation : null,
      this._thinkingTilt,
      HEAD_CLAMP,
    )
    // Without a neck bone the head takes the full rotation
    const { head, neck } = splitHeadNeck(target, this._neckNode ? HEAD_SHARE : 1)

    this._headTarget.setFromEuler(this._euler.set(head.pitch, head.yaw, head.roll))
    this._neckTarget.setFromEuler(this._euler.set(neck.pitch, neck.yaw, neck.roll))
  }

  _updateHeadTracking() {
    if (!this._headNode) return

    this._headNode.quaternion.slerp(this._headTarget, HEAD_LERP)
    if (this._neckNode) this._neckNode.quaternion.slerp(this._neckTarget, HEAD_LERP)
  }

  // ── Idle animation ───────────────────────────────────────────────────

  _updateBlink(delta) {
    if (!this._hasBlinkLeft && !this._hasBlinkRight) return

    switch (this._blinkState) {
      case 'idle':
        this._blinkTimer -= delta
        if (this._blinkTimer <= 0) {
          this._blinkState = 'closing'
          this._blinkProgress = 0
        }
        break

      case 'closing':
        // 0 → 1 over BLINK_CLOSE_SEC
        this._blinkProgress += delta / BLINK_CLOSE_SEC
        if (this._blinkProgress >= 1) {
          this._blinkProgress = 1
          this._blinkState = 'holding'
          this._blinkTimer = BLINK_HOLD_SEC
        }
        this._setBlinkValue(this._blinkProgress)
        break

      case 'holding':
        this._blinkTimer -= delta
        if (this._blinkTimer <= 0) {
          this._blinkState = 'opening'
          this._blinkProgress = 1
        }
        break

      case 'opening':
        // 1 → 0 over BLINK_CLOSE_SEC
        this._blinkProgress -= delta / BLINK_CLOSE_SEC
        if (this._blinkProgress <= 0) {
          this._blinkProgress = 0
          this._blinkState = 'idle'
          this._blinkTimer = this._nextBlinkDelay()
        }
        this._setBlinkValue(this._blinkProgress)
        break
    }
  }

  _setBlinkValue(v) {
    const em = this._vrm.expressionManager
    if (this._hasBlinkLeft) em.setValue('blinkLeft', v)
    if (this._hasBlinkRight) em.setValue('blinkRight', v)
  }

  _nextBlinkDelay() {
    return BLINK_INTERVAL_MIN_SEC + Math.random() * BLINK_INTERVAL_RANGE_SEC
  }

  _updateBreathing(elapsed) {
    if (!this._chestNode) return

    this._chestNode.position.y = Math.sin(elapsed * BREATHING_SPEED) * BREATHING_AMP
  }

  // ── Helpers ───────────────────────────────────────────────────────────

  _setLoading(pct, text) {
    if (this._loadingBar) this._loadingBar.style.width = `${pct}%`
    if (this._loadingText) this._loadingText.textContent = text
  }

  _hideLoading() {
    if (this._loadingEl) {
      setTimeout(() => this._loadingEl.classList.add('hidden'), LOADING_HIDE_DELAY_MS)
    }
  }
}
