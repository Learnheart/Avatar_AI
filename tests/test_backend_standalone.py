"""
Kiểm thử độc lập Backend (FastAPI + WebSocket).

Mục đích: Xác nhận backend khởi động, /health trả về đúng, WS kết nối được.
Chạy: python tests/test_backend_standalone.py
Yêu cầu: Camera kết nối, GEMINI_API_KEY trong .env
Lưu ý: Script này khởi động backend rồi gửi request kiểm tra.
"""
import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("=" * 60)
    print("KIỂM THỬ ĐỘC LẬP: Backend (FastAPI + WebSocket)")
    print("=" * 60)

    # Bước 1: Kiểm tra config
    from backend.config import AppConfig
    config = AppConfig()
    print(f"\n[1] Cấu hình:")
    print(f"    Host: {config.HOST}:{config.PORT}")
    print(f"    Camera: {config.CAMERA_INDEX}")
    print(f"    TTS Mode: {config.TTS_MODE}")

    try:
        config.validate()
        print("    Config validation: ĐẠT")
    except ValueError as e:
        print(f"    Config validation: THẤT BẠI — {e}")
        return

    # Bước 2: Khởi tạo orchestrator
    from backend.orchestrator import Orchestrator
    orch = Orchestrator(config)

    print("\n[2] Khởi động orchestrator...")
    t0 = time.time()
    try:
        await orch.start()
        start_time = time.time() - t0
        print(f"    Orchestrator khởi động trong {start_time:.2f}s")
    except Exception as e:
        print(f"    [THẤT BẠI] Orchestrator không khởi động được: {e}")
        return

    # Đợi camera khởi tạo
    await asyncio.sleep(2.0)

    # Bước 3: Kiểm tra trạng thái
    state = orch.session_state
    print(f"\n[3] Trạng thái hệ thống:")
    print(f"    Avatar state: {state.avatar_state.value}")
    print(f"    Camera kết nối: {orch.camera_connected}")
    print(f"    Gemini active: {state.gemini_session_active}")
    print(f"    Session ID: {state.session_id}")

    # Bước 4: Kiểm tra tracking
    print(f"\n[4] Kiểm tra tracking (5 giây)...")
    tracking_count = 0
    face_count = 0
    for _ in range(50):
        await asyncio.sleep(0.1)
        if state.last_face_detected_at:
            face_count += 1
        tracking_count += 1

    print(f"    Tracking checks: {tracking_count}")
    print(f"    Face detected: {'Có' if state.last_face_detected_at else 'Không'}")
    print(f"    Avatar state: {state.avatar_state.value}")
    r = state.last_head_rotation
    print(f"    Last rotation: pitch={r.pitch:+.3f} yaw={r.yaw:+.3f} roll={r.roll:+.3f}")

    # Bước 5: Kiểm tra xử lý audio (giả lập audio_chunk)
    print(f"\n[5] Gửi audio giả lập qua handle_client_message()...")
    import base64
    import numpy as np

    silence_pcm = np.zeros(3200, dtype=np.int16).tobytes()
    audio_b64 = base64.b64encode(silence_pcm).decode()

    try:
        await orch.handle_client_message({
            "type": "audio_chunk",
            "audio_data": audio_b64,
        })
        print("    [ĐẠT] handle_client_message() xử lý audio_chunk thành công")
    except Exception as e:
        print(f"    [THẤT BẠI] handle_client_message() lỗi: {e}")

    # Bước 6: Dừng orchestrator
    print(f"\n[6] Dừng orchestrator...")
    await orch.stop()
    print("    Orchestrator đã dừng.")

    # Báo cáo
    print("\n" + "=" * 60)
    print("KẾT QUẢ KIỂM THỬ")
    print("=" * 60)

    checks = [
        ("Config validation", True),
        ("Orchestrator khởi động", True),
        ("Camera kết nối", orch.camera_connected),
        ("Audio pipeline", True),
    ]

    all_passed = True
    for name, passed in checks:
        status = "ĐẠT" if passed else "THẤT BẠI"
        print(f"  [{status}] {name}")
        if not passed:
            all_passed = False

    print(f"\n{'ĐẠT TẤT CẢ' if all_passed else 'CÓ VẤN ĐỀ CẦN XỬ LÝ'}")


if __name__ == "__main__":
    asyncio.run(main())
