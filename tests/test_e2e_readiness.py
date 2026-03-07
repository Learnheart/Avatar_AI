"""
Phase 4C — Kiểm tra sẵn sàng E2E.

Script kiểm tra tất cả điều kiện tiên quyết trước khi chạy E2E test.
Không phải test tự động — chạy thủ công để xác nhận môi trường.

Chạy: cd /mnt/hdd/projects/avatar_AI && source .venv/bin/activate && python tests/test_e2e_readiness.py
"""
import os
import sys
import time
import struct

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    icon = "  [+]" if condition else "  [!]"
    msg = f"{icon} {name}: {status}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return condition


def main():
    print("=" * 60)
    print("  Magic Mirror AI Avatar — E2E Readiness Check")
    print("=" * 60)
    results = []

    # ── 1. Python Environment ────────────────────────────────────────
    print("\n1. MÔI TRƯỜNG PYTHON")

    results.append(check(
        "Python >= 3.10",
        sys.version_info >= (3, 10),
        f"Python {sys.version_info.major}.{sys.version_info.minor}"
    ))

    try:
        import fastapi
        results.append(check("FastAPI", True, fastapi.__version__))
    except ImportError:
        results.append(check("FastAPI", False, "Chưa cài"))

    try:
        import cv2
        results.append(check("OpenCV", True, cv2.__version__))
    except ImportError:
        results.append(check("OpenCV", False, "Chưa cài"))

    try:
        import mediapipe as mp
        results.append(check("MediaPipe", True, mp.__version__))
    except ImportError:
        results.append(check("MediaPipe", False, "Chưa cài"))

    try:
        import torch
        results.append(check("PyTorch", True, f"{torch.__version__}, CUDA: {torch.cuda.is_available()}"))
    except ImportError:
        results.append(check("PyTorch", False, "Chưa cài"))

    try:
        from google import genai
        results.append(check("google-genai", True))
    except ImportError:
        results.append(check("google-genai", False, "Chưa cài"))

    # ── 2. Config & API Keys ────────────────────────────────────────
    print("\n2. CẤU HÌNH & API KEY")

    from backend.config import AppConfig
    config = AppConfig()

    has_key = bool(config.GEMINI_API_KEY) and config.GEMINI_API_KEY != "your_gemini_api_key_here"
    results.append(check("Gemini API Key", has_key, "Đã cấu hình" if has_key else "Thiếu hoặc placeholder"))
    results.append(check("Gemini Model", True, config.GEMINI_MODEL))
    results.append(check("TTS Mode", True, config.TTS_MODE))

    # ── 3. Models & Assets ──────────────────────────────────────────
    print("\n3. MÔ HÌNH & TÀI NGUYÊN")

    face_model_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "backend", "models", "face_landmarker.task"
    )
    results.append(check(
        "Face Landmarker model",
        os.path.exists(face_model_path),
        f"{os.path.getsize(face_model_path) / 1024 / 1024:.1f} MB" if os.path.exists(face_model_path) else "Thiếu"
    ))

    vrm_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend", "public", "models", "avatar.vrm"
    )
    results.append(check(
        "VRM Avatar model",
        os.path.exists(vrm_path),
        f"{os.path.getsize(vrm_path) / 1024 / 1024:.1f} MB" if os.path.exists(vrm_path) else "Thiếu — tải từ VRoid Hub"
    ))

    # Verify VRM is valid glTF
    if os.path.exists(vrm_path):
        with open(vrm_path, "rb") as f:
            magic = f.read(4)
        results.append(check("VRM format hợp lệ (glTF)", magic == b"glTF"))

    # Silero VAD model
    silero_path = os.path.expanduser("~/.cache/torch/hub/snakers4_silero-vad_master")
    results.append(check(
        "Silero VAD cache",
        os.path.exists(silero_path),
        "Đã tải trước" if os.path.exists(silero_path) else "Sẽ tải lần đầu (cần internet)"
    ))

    # ── 4. Camera ───────────────────────────────────────────────────
    print("\n4. CAMERA")

    camera_device = os.path.exists(f"/dev/video{config.CAMERA_INDEX}")
    results.append(check(
        f"Camera device /dev/video{config.CAMERA_INDEX}",
        camera_device,
        "Tồn tại" if camera_device else "Không tìm thấy"
    ))

    if camera_device:
        try:
            import cv2
            cap = cv2.VideoCapture(config.CAMERA_INDEX)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ret, frame = cap.read()
            if ret:
                h, w = frame.shape[:2]
                results.append(check("Camera đang stream", True, f"{w}x{h}"))
            else:
                results.append(check("Camera đang stream", False, "Device tồn tại nhưng không đọc được frame — DroidCam đang chạy?"))
            cap.release()
        except Exception as e:
            results.append(check("Camera đang stream", False, str(e)))

    # ── 5. Frontend Build ───────────────────────────────────────────
    print("\n5. FRONTEND")

    dist_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend", "dist", "index.html"
    )
    results.append(check("Frontend build (dist/)", os.path.exists(dist_path)))

    node_modules = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend", "node_modules"
    )
    results.append(check("node_modules", os.path.exists(node_modules)))

    # ── 6. Gemini API Connectivity ──────────────────────────────────
    print("\n6. GEMINI API")

    if has_key:
        try:
            from google import genai
            client = genai.Client(api_key=config.GEMINI_API_KEY)
            t0 = time.time()
            response = client.models.get(model=config.GEMINI_MODEL)
            elapsed = time.time() - t0
            results.append(check(
                "Gemini API kết nối",
                True,
                f"Model: {response.name}, latency: {elapsed:.2f}s"
            ))
        except Exception as e:
            results.append(check("Gemini API kết nối", False, str(e)))
    else:
        results.append(check("Gemini API kết nối", False, "Bỏ qua — thiếu API key"))

    # ── 7. VAD Module ───────────────────────────────────────────────
    print("\n7. VAD MODULE")

    try:
        from backend.modules.vad import VADModule
        vad = VADModule(config)
        t0 = time.time()
        vad.load()
        elapsed = time.time() - t0

        # Test with silence
        silence = struct.pack("<512h", *([0] * 512))
        event = vad.process_audio(silence)
        results.append(check(
            "Silero VAD",
            True,
            f"Tải: {elapsed:.2f}s, im lặng → event={event}"
        ))
    except Exception as e:
        results.append(check("Silero VAD", False, str(e)))

    # ── Summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"  KẾT QUẢ: {passed}/{total} kiểm tra ĐẠT")

    failed = [i for i, r in enumerate(results) if not r]
    if failed:
        print(f"  THẤT BẠI: {len(failed)} kiểm tra")
        print("\n  Hành động cần thiết trước E2E:")
        # Categorize failures
        for idx in failed:
            print(f"    - Sửa kiểm tra #{idx + 1}")
    else:
        print("  TẤT CẢ ĐẠT — Sẵn sàng chạy E2E test!")

    print("=" * 60)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
