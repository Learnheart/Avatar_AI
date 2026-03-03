/**
 * Avatar Renderer — TASK-09, 11, 12
 *
 * - Load VRM model via @pixiv/three-vrm
 * - Head tracking: nhận pitch/yaw/roll → Quaternion → Lerp → head + neck bones
 * - Idle animation: blink ngẫu nhiên 3-5s + breathing sin wave
 * - Clamp rotation theo spec (BR-002)
 */

import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'

const VRM_PATH = '/models/avatar.vrm'

// Head rotation clamps (radians) — must match backend config
const CLAMP = {
  pitch: 0.52,   // ±30°
  yaw: 0.78,     // ±45°
  roll: 0.35,    // ±20°
}

const LERP_FACTOR = 0.15         // Smoothing factor per frame
const BREATHING_SPEED = 0.8      // radians/sec ≈ 13 breaths/min
const BREATHING_AMP = 0.003      // Scale amplitude

export class AvatarRenderer {
  constructor(container) {
    this._container = container
    this._renderer = null
    this._scene = null
    this._camera = null
    this._vrm = null
    this._clock = new THREE.Clock()

    // Target rotation (from tracking data)
    this._targetRotation = new THREE.Euler(0, 0, 0, 'YXZ')
    this._currentQuat = new THREE.Quaternion()

    // Blink state
    this._blinkTimer = this._nextBlinkDelay()
    this._blinkState = 'idle'    // idle | closing | holding | opening
    this._blinkProgress = 0

    // Loading state
    this._loaded = false
    this._loadingEl = document.getElementById('loading')
    this._loadingBar = document.getElementById('loading-bar')
    this._loadingText = document.getElementById('loading-text')
  }

  // ── Init ──────────────────────────────────────────────────────────────

  async init() {
    this._setupScene()
    await this._loadVRM()
    this._loaded = true
    this._hideLoading()
    this._startRenderLoop()
  }

  _setupScene() {
    // Renderer
    this._renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    this._renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this._renderer.setSize(window.innerWidth, window.innerHeight)
    this._renderer.shadowMap.enabled = true
    this._container.appendChild(this._renderer.domElement)

    // Scene
    this._scene = new THREE.Scene()
    this._scene.background = new THREE.Color(0x0a0a0f)

    // Camera — portrait framing for kiosk (upper half of body)
    this._camera = new THREE.PerspectiveCamera(
      35, window.innerWidth / window.innerHeight, 0.1, 20
    )
    this._camera.position.set(0, 1.35, 2.2)
    this._camera.lookAt(0, 1.2, 0)

    // Lighting
    const ambient = new THREE.AmbientLight(0xffffff, 0.6)
    this._scene.add(ambient)

    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2)
    dirLight.position.set(1, 2, 2)
    dirLight.castShadow = true
    this._scene.add(dirLight)

    const fillLight = new THREE.DirectionalLight(0x8080ff, 0.3)
    fillLight.position.set(-2, 1, -1)
    this._scene.add(fillLight)

    // Resize handler
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
          this._scene.add(vrm.scene)
          this._vrm = vrm

          // Check available blendshapes
          const em = vrm.expressionManager
          this._hasBlinkLeft = em && em.getExpression('blinkLeft') !== undefined
          this._hasBlinkRight = em && em.getExpression('blinkRight') !== undefined
          this._hasAA = em && em.getExpression('aa') !== undefined

          if (!this._hasBlinkLeft && !this._hasBlinkRight) {
            console.warn('[Avatar] Blink blendshapes not found — EC-008')
          }
          if (!this._hasAA) {
            console.warn('[Avatar] "aa" blendshape not found — lip-sync disabled EC-008')
          }

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

  // ── Public API ────────────────────────────────────────────────────────

  /**
   * Called by ws-client when tracking_update arrives.
   * Sets TARGET rotation — actual rotation lerps towards it in render loop.
   */
  applyTrackingData(headRotation) {
    if (!headRotation) return
    const { pitch, yaw, roll } = headRotation

    this._targetRotation.set(
      this._clamp(pitch, CLAMP.pitch),
      this._clamp(yaw, CLAMP.yaw),
      this._clamp(roll, CLAMP.roll),
      'YXZ'
    )
  }

  /**
   * Set mouth open blendshape value for lip-sync (0.0 → 1.0).
   * Called by LipSync module.
   */
  setMouthOpen(value) {
    if (!this._vrm || !this._hasAA) return
    this._vrm.expressionManager.setValue('aa', Math.max(0, Math.min(1, value)))
  }

  // ── Render loop ───────────────────────────────────────────────────────

  _startRenderLoop() {
    const animate = () => {
      requestAnimationFrame(animate)
      const delta = this._clock.getDelta()
      const elapsed = this._clock.elapsedTime

      if (this._vrm) {
        this._updateHeadTracking(delta)
        this._updateBlink(delta)
        this._updateBreathing(elapsed)
        this._vrm.update(delta)
      }

      this._renderer.render(this._scene, this._camera)
    }
    animate()
  }

  // ── TASK-11: Head Tracking Animation ─────────────────────────────────

  _updateHeadTracking(delta) {
    const humanoid = this._vrm.humanoid
    const headNode = humanoid.getNormalizedBoneNode('head')
    const neckNode = humanoid.getNormalizedBoneNode('neck')

    const targetQuat = new THREE.Quaternion().setFromEuler(this._targetRotation)

    // Lerp current quaternion towards target
    this._currentQuat.slerp(targetQuat, LERP_FACTOR)

    if (headNode && neckNode) {
      // Distribute: 70% head, 30% neck
      const headQuat = new THREE.Quaternion().slerp(this._currentQuat, 0.7)
      const neckQuat = new THREE.Quaternion().slerp(this._currentQuat, 0.3)
      headNode.quaternion.copy(headQuat)
      neckNode.quaternion.copy(neckQuat)
    } else if (headNode) {
      // Neck bone fallback (EC-008 variant): 100% head
      headNode.quaternion.copy(this._currentQuat)
    }
  }

  // ── TASK-12: Idle Animation ───────────────────────────────────────────

  _updateBlink(delta) {
    if (!this._hasBlinkLeft && !this._hasBlinkRight) return

    const em = this._vrm.expressionManager

    switch (this._blinkState) {
      case 'idle':
        this._blinkTimer -= delta
        if (this._blinkTimer <= 0) {
          this._blinkState = 'closing'
          this._blinkProgress = 0
        }
        break

      case 'closing':
        // 0 → 1 in ~100ms
        this._blinkProgress += delta / 0.1
        if (this._blinkProgress >= 1) {
          this._blinkProgress = 1
          this._blinkState = 'holding'
          this._blinkTimer = 0.05  // Hold 50ms
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
        // 1 → 0 in ~100ms
        this._blinkProgress -= delta / 0.1
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
    // Random interval 3-5 seconds (FR-004-A)
    return 3 + Math.random() * 2
  }

  _updateBreathing(elapsed) {
    const humanoid = this._vrm.humanoid
    const chest = humanoid.getNormalizedBoneNode('chest')
    if (!chest) return

    // FR-004-B: Math.sin(time * speed) * amplitude
    const breathY = Math.sin(elapsed * BREATHING_SPEED) * BREATHING_AMP
    chest.position.y = breathY
  }

  // ── Helpers ───────────────────────────────────────────────────────────

  _clamp(v, max) {
    return Math.max(-max, Math.min(max, v))
  }

  _setLoading(pct, text) {
    if (this._loadingBar) this._loadingBar.style.width = `${pct}%`
    if (this._loadingText) this._loadingText.textContent = text
  }

  _hideLoading() {
    if (this._loadingEl) {
      setTimeout(() => this._loadingEl.classList.add('hidden'), 500)
    }
  }
}
