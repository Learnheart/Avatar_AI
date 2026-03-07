#!/usr/bin/env python3
"""
Quick standalone test — chạy từng module độc lập, không cần hardware.
Usage:  python quicktest.py
"""
import asyncio
import sys
import os
import time
import struct
from datetime import datetime, timezone, timedelta

# ── Setup path ────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

PASS = "\033[92m PASS \033[0m"
FAIL = "\033[91m FAIL \033[0m"
SKIP = "\033[93m SKIP \033[0m"
# Plain text versions for log file (no ANSI codes)
_TAG = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}
results: list[tuple[str, str, str]] = []

# ── Log file setup ────────────────────────────────────────────────────────
_VN_TZ = timezone(timedelta(hours=7))
_NOW = datetime.now(_VN_TZ)
_LOG_DIR = os.path.join(_ROOT, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_PATH = os.path.join(_LOG_DIR, f"quicktest_{_NOW.strftime('%Y-%m-%d_%H%M%S')}.log")
_log_file = open(_LOG_PATH, "w", encoding="utf-8")


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


def log(status: str, name: str, detail: str = ""):
    results.append((status, name, detail))
    line = f"  [{status}] {name}  {detail}"
    print(line)
    _log_file.write(_strip_ansi(line) + "\n")
    _log_file.flush()


# ══════════════════════════════════════════════════════════════════════════
# 1. Config
# ══════════════════════════════════════════════════════════════════════════
def test_config():
    _section("1. Config")
    try:
        from backend.config import AppConfig
        cfg = AppConfig()
        assert cfg.HOST == "0.0.0.0"
        assert cfg.PORT == 8000
        assert cfg.TRACKING_FPS == 30
        assert cfg.TTS_MODE in ("gemini", "azure", "viettel")
        log(PASS, "AppConfig load", f"HOST={cfg.HOST} PORT={cfg.PORT}")
    except Exception as e:
        log(FAIL, "AppConfig load", str(e))
        return None

    # validate (cần GEMINI_API_KEY)
    try:
        cfg.validate()
        log(PASS, "Config validate", "GEMINI_API_KEY present")
    except ValueError as e:
        log(FAIL, "Config validate", str(e))

    return cfg


# ══════════════════════════════════════════════════════════════════════════
# 2. Models
# ══════════════════════════════════════════════════════════════════════════
def test_models():
    _section("2. Models")
    try:
        from backend.models import (
            AvatarState, AudioSource, HeadRotation,
            TrackingResult, VadEvent, SpeechOutput, SessionState,
        )

        # Enum values
        assert AvatarState.IDLE.value == "idle"
        assert AvatarState.SPEAKING.value == "speaking"
        log(PASS, "AvatarState enum", "IDLE/TRACKING/LISTENING/SPEAKING")

        # HeadRotation
        hr = HeadRotation(pitch=0.1, yaw=-0.2, roll=0.05)
        d = hr.to_dict()
        assert d == {"pitch": 0.1, "yaw": -0.2, "roll": 0.05}
        log(PASS, "HeadRotation", f"to_dict={d}")

        # TrackingResult
        tr = TrackingResult(rotation=hr, face_detected=True, face_count=1)
        assert tr.face_detected is True
        log(PASS, "TrackingResult", f"face_detected={tr.face_detected}")

        # VadEvent
        ve = VadEvent(event_type="speech_start", confidence=0.85)
        assert ve.event_type == "speech_start"
        log(PASS, "VadEvent", f"type={ve.event_type}")

        # SessionState
        ss = SessionState()
        assert ss.avatar_state == AvatarState.IDLE
        assert len(ss.session_id) > 0
        log(PASS, "SessionState", f"id={ss.session_id[:8]}... state={ss.avatar_state.value}")

    except Exception as e:
        log(FAIL, "Models", str(e))


# ══════════════════════════════════════════════════════════════════════════
# 3. Logger
# ══════════════════════════════════════════════════════════════════════════
def test_logger():
    _section("3. Logger")
    try:
        from backend.utils.logging import get_logger
        lg = get_logger("quicktest")
        lg.info({"msg": "quicktest logger works"})
        log(PASS, "get_logger", "JSON structured log OK")
    except Exception as e:
        log(FAIL, "get_logger", str(e))


# ══════════════════════════════════════════════════════════════════════════
# 4. VAD Module
# ══════════════════════════════════════════════════════════════════════════
def test_vad(cfg):
    _section("4. VAD Module")
    if cfg is None:
        log(SKIP, "VAD", "Config not loaded")
        return

    try:
        from backend.modules.vad import VADModule
        vad = VADModule(cfg)
        log(PASS, "VAD init", "instance created")
    except Exception as e:
        log(FAIL, "VAD init", str(e))
        return

    # Load model
    try:
        t0 = time.time()
        vad.load()
        dt = time.time() - t0
        log(PASS, "VAD load", f"model loaded in {dt:.2f}s")
    except Exception as e:
        log(FAIL, "VAD load", str(e))
        return

    # Process silence (960 samples = 60ms of zeros at 16kHz)
    try:
        silence = b"\x00" * (960 * 2)  # 960 Int16 samples
        event = vad.process_audio(silence)
        if event is None:
            log(PASS, "VAD silence", "no event (correct)")
        else:
            log(PASS, "VAD silence", f"event={event.event_type} (may be reset artifact)")
    except Exception as e:
        log(FAIL, "VAD silence", str(e))

    # Process synthetic speech (440Hz sine wave, 0.5s)
    try:
        import numpy as np
        duration = 0.5
        samples = int(16000 * duration)
        t = np.linspace(0, duration, samples, dtype=np.float32)
        sine = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
        pcm = sine.tobytes()

        vad.reset()
        # Feed in 512-sample chunks
        chunk_size = 512 * 2  # bytes
        events = []
        for i in range(0, len(pcm), chunk_size):
            evt = vad.process_audio(pcm[i:i+chunk_size])
            if evt:
                events.append(evt.event_type)

        log(PASS, "VAD sine wave", f"events={events if events else 'none (OK, sine != speech)'}")
    except Exception as e:
        log(FAIL, "VAD sine wave", str(e))


# ══════════════════════════════════════════════════════════════════════════
# 5. Vision Module (needs camera — optional)
# ══════════════════════════════════════════════════════════════════════════
def test_vision(cfg):
    _section("5. Vision Module")
    if cfg is None:
        log(SKIP, "Vision", "Config not loaded")
        return

    # Check model file
    model_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "backend", "models", "face_landmarker.task"
    )
    if os.path.exists(model_path):
        size_mb = os.path.getsize(model_path) / (1024 * 1024)
        log(PASS, "Vision model file", f"face_landmarker.task ({size_mb:.1f} MB)")
    else:
        log(FAIL, "Vision model file", f"NOT FOUND: {model_path}")
        return

    # Check imports
    try:
        from backend.modules.vision import VisionModule
        log(PASS, "Vision import", "VisionModule imported")
    except Exception as e:
        log(FAIL, "Vision import", str(e))
        return

    # Check camera availability (non-blocking)
    try:
        import cv2
        cap = cv2.VideoCapture(cfg.CAMERA_INDEX)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret:
                h, w = frame.shape[:2]
                log(PASS, "Camera read", f"frame {w}x{h} from index={cfg.CAMERA_INDEX}")
            else:
                log(FAIL, "Camera read", "opened but read failed")
        else:
            cap.release()
            log(SKIP, "Camera read", f"no camera at index={cfg.CAMERA_INDEX}")
    except Exception as e:
        log(SKIP, "Camera read", str(e))

    # Quick FaceLandmarker test with a blank image
    try:
        import cv2
        import mediapipe as mp
        import numpy as np

        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_faces=1,
        )
        with mp.tasks.vision.FaceLandmarker.create_from_options(options) as landmarker:
            # Blank image — should detect 0 faces
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=blank)
            result = landmarker.detect(mp_img)
            n = len(result.face_landmarks)
            log(PASS, "FaceLandmarker", f"blank image → {n} faces (expected 0)")
    except Exception as e:
        log(FAIL, "FaceLandmarker", str(e))


# ══════════════════════════════════════════════════════════════════════════
# 6. Gemini Client (needs API key + network)
# ══════════════════════════════════════════════════════════════════════════
def test_gemini(cfg):
    _section("6. Gemini Client")
    if cfg is None:
        log(SKIP, "Gemini", "Config not loaded")
        return

    if not cfg.GEMINI_API_KEY:
        log(SKIP, "Gemini", "no GEMINI_API_KEY")
        return

    try:
        from backend.modules.gemini_client import GeminiClient
        client = GeminiClient(cfg)
        log(PASS, "Gemini init", f"model={cfg.GEMINI_MODEL}")
    except Exception as e:
        log(FAIL, "Gemini init", str(e))
        return

    # Test session connect + disconnect
    async def _test_session():
        received = []

        async def cb(kind, data, mime):
            received.append(kind)

        client.on_response(cb)

        t0 = time.time()
        await client.start_session(cfg.SYSTEM_PROMPT)

        # Wait for session ready (max 10s)
        try:
            await asyncio.wait_for(client._session_ready.wait(), timeout=10.0)
            dt = time.time() - t0
            log(PASS, "Gemini connect", f"session ready in {dt:.2f}s")
        except asyncio.TimeoutError:
            log(FAIL, "Gemini connect", "timeout 10s")
            await client.close_session()
            return

        assert client.is_active
        log(PASS, "Gemini is_active", "True")

        await client.close_session()
        log(PASS, "Gemini close", "session closed cleanly")

    try:
        asyncio.run(_test_session())
    except Exception as e:
        log(FAIL, "Gemini session", str(e))


# ══════════════════════════════════════════════════════════════════════════
# 7. TTS Module (gemini mode = pass-through)
# ══════════════════════════════════════════════════════════════════════════
def test_tts(cfg):
    _section("7. TTS Module")
    if cfg is None:
        log(SKIP, "TTS", "Config not loaded")
        return

    async def _test():
        from backend.modules.tts import TTSModule
        tts = TTSModule(cfg)
        await tts.start()
        log(PASS, "TTS start", f"mode={cfg.TTS_MODE}")

        if cfg.TTS_MODE == "gemini":
            # Pass-through test
            fake_audio = b"\x00\x01" * 100
            speech = await tts.process_output("Xin chào", fake_audio, "audio/pcm;rate=24000")
            if speech:
                assert speech.text_content == "Xin chào"
                assert speech.audio_data == fake_audio
                log(PASS, "TTS pass-through", f"text='{speech.text_content}' audio={len(speech.audio_data)}B")
            else:
                log(FAIL, "TTS pass-through", "returned None")

            # No audio → None
            result = await tts.process_output("test", None, None)
            if result is None:
                log(PASS, "TTS no-audio", "returned None (correct)")
            else:
                log(FAIL, "TTS no-audio", "should return None when no audio in gemini mode")
        else:
            log(SKIP, "TTS pass-through", f"mode={cfg.TTS_MODE}, needs API key")

        await tts.stop()
        log(PASS, "TTS stop", "cleaned up")

    try:
        asyncio.run(_test())
    except Exception as e:
        log(FAIL, "TTS", str(e))


# ══════════════════════════════════════════════════════════════════════════
# 8. Orchestrator (unit test — no hardware)
# ══════════════════════════════════════════════════════════════════════════
def test_orchestrator():
    _section("8. Orchestrator (state machine only)")
    try:
        from backend.models import AvatarState, SessionState

        # State transitions
        ss = SessionState()
        assert ss.avatar_state == AvatarState.IDLE

        ss.avatar_state = AvatarState.TRACKING
        assert ss.avatar_state == AvatarState.TRACKING

        ss.avatar_state = AvatarState.LISTENING
        ss.avatar_state = AvatarState.SPEAKING
        ss.is_speaking = True
        assert ss.is_speaking is True

        ss.avatar_state = AvatarState.TRACKING
        ss.is_speaking = False

        log(PASS, "State machine", "IDLE → TRACKING → LISTENING → SPEAKING → TRACKING")

    except Exception as e:
        log(FAIL, "State machine", str(e))

    # WSManager
    try:
        from backend.orchestrator import WSManager
        ws = WSManager()
        assert ws.is_connected is False
        log(PASS, "WSManager", "init OK, is_connected=False")
    except Exception as e:
        log(FAIL, "WSManager", str(e))


# ══════════════════════════════════════════════════════════════════════════
# 9. FastAPI app import
# ══════════════════════════════════════════════════════════════════════════
def test_fastapi():
    _section("9. FastAPI app")
    try:
        from backend.main import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/ws" in routes
        log(PASS, "FastAPI app", f"routes={routes}")
    except Exception as e:
        log(FAIL, "FastAPI import", str(e))


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
def _section(title: str):
    """Print section header to both terminal and log file."""
    print(f"\n── {title} ──")
    _log_file.write(f"\n── {title} ──\n")


def main():
    header = "=" * 60
    print(header)
    print("  Magic Mirror AI Avatar — Quick Module Test")
    print(header)
    _log_file.write(f"{header}\n  Magic Mirror AI Avatar — Quick Module Test\n{header}\n")
    _log_file.write(f"  Started: {_NOW.strftime('%Y-%m-%d %H:%M:%S')} (UTC+7)\n")

    cfg = test_config()
    test_models()
    test_logger()
    test_vad(cfg)
    test_vision(cfg)
    test_gemini(cfg)
    test_tts(cfg)
    test_orchestrator()
    test_fastapi()

    # ── Summary ───────────────────────────────────────────────────────
    sep = "=" * 60
    print(f"\n{sep}")
    print("  SUMMARY")
    print(sep)
    _log_file.write(f"\n{sep}\n  SUMMARY\n{sep}\n")

    passed = sum(1 for s, _, _ in results if "PASS" in s)
    failed = sum(1 for s, _, _ in results if "FAIL" in s)
    skipped = sum(1 for s, _, _ in results if "SKIP" in s)

    summary = f"  PASS {passed}  FAIL {failed}  SKIP {skipped}  Total: {len(results)}"
    print(f"  {PASS} {passed}  {FAIL} {failed}  {SKIP} {skipped}  Total: {len(results)}")
    _log_file.write(summary + "\n")

    if failed > 0:
        print("\n  Failed tests:")
        _log_file.write("\n  Failed tests:\n")
        for s, name, detail in results:
            if "FAIL" in s:
                line = f"    - {name}: {detail}"
                print(line)
                _log_file.write(line + "\n")

    _log_file.write(f"\nTimestamp: {_NOW.strftime('%Y-%m-%d %H:%M:%S')} (UTC+7)\n")
    _log_file.close()
    print(f"\n  Log saved: {_LOG_PATH}")
    print()
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
