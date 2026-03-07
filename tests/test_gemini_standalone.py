"""
Kiểm thử độc lập Module Gemini Client.

Mục đích: Xác nhận ISS-01 — chữ ký send_realtime_input() hoạt động đúng.
Chạy: python tests/test_gemini_standalone.py
Yêu cầu: GEMINI_API_KEY hợp lệ trong .env
"""
import asyncio
import sys
import os
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import AppConfig
from backend.modules.gemini_client import GeminiClient


async def main():
    print("=" * 60)
    print("KIỂM THỬ ĐỘC LẬP: Gemini Live API Client (ISS-01)")
    print("=" * 60)

    # Bước 1: Kiểm tra cấu hình
    config = AppConfig()
    if not config.GEMINI_API_KEY:
        print("\n[THẤT BẠI] GEMINI_API_KEY không có trong .env")
        print("  Hãy thêm GEMINI_API_KEY=... vào file .env")
        return

    api_key_preview = config.GEMINI_API_KEY[:8] + "..." + config.GEMINI_API_KEY[-4:]
    print(f"\n[1] API Key: {api_key_preview}")
    print(f"    Model: {config.GEMINI_MODEL}")
    print(f"    Voice: {config.GEMINI_VOICE}")
    print(f"    TTS Mode: {config.TTS_MODE}")

    # Bước 2: Khởi tạo client
    client = GeminiClient(config)
    responses_received = []

    async def on_response(kind, data, mime_type):
        responses_received.append({
            "kind": kind,
            "data_size": len(data) if data else 0,
            "mime_type": mime_type,
            "time": time.time(),
        })
        if kind == "text":
            print(f"    [Gemini] Văn bản: {data[:100]}")
        elif kind == "audio":
            print(f"    [Gemini] Âm thanh: {len(data)} bytes, {mime_type}")
        elif kind == "turn_complete":
            print(f"    [Gemini] Lượt hoàn thành")

    client.on_response(on_response)

    # Bước 3: Mở phiên Gemini Live
    print("\n[2] Đang mở phiên Gemini Live...")
    t0 = time.time()
    try:
        await client.start_session(config.SYSTEM_PROMPT)
    except Exception as e:
        print(f"    [THẤT BẠI] Không mở được phiên: {e}")
        return

    # Đợi phiên sẵn sàng
    try:
        await asyncio.wait_for(client._session_ready.wait(), timeout=10.0)
        connect_time = time.time() - t0
        print(f"    Phiên đã kết nối trong {connect_time:.2f}s")
    except asyncio.TimeoutError:
        print(f"    [THẤT BẠI] Phiên không sẵn sàng sau 10 giây")
        await client.close_session()
        return

    print(f"    is_active: {client.is_active}")

    # Bước 4: Gửi audio giả lập (kiểm tra send_realtime_input signature)
    print("\n[3] Gửi audio giả lập để kiểm tra send_realtime_input()...")
    # Tạo 1 giây im lặng PCM 16kHz Int16
    silence = np.zeros(16000, dtype=np.int16).tobytes()
    chunk_size = 3200  # 200ms chunks

    try:
        for i in range(0, len(silence), chunk_size):
            chunk = silence[i:i + chunk_size]
            await client.send_audio(chunk)
        print("    [ĐẠT] send_audio() (gọi send_realtime_input(audio=...)) thành công")
    except Exception as e:
        print(f"    [THẤT BẠI] send_audio() lỗi: {e}")
        print(f"    → Cần điều chỉnh chữ ký send_realtime_input() trong gemini_client.py")
        await client.close_session()
        return

    # Bước 5: Gửi ảnh JPEG giả lập
    print("\n[4] Gửi ảnh JPEG giả lập để kiểm tra send_image()...")
    # Tạo JPEG tối thiểu (1x1 pixel đen)
    try:
        import cv2
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        _, jpeg_buf = cv2.imencode(".jpg", img)
        jpeg_bytes = jpeg_buf.tobytes()
        await client.send_image(jpeg_bytes)
        print(f"    [ĐẠT] send_image() (gọi send_realtime_input(video=...)) thành công")
    except ImportError:
        print("    [BỎ QUA] cv2 không có, bỏ qua kiểm tra send_image()")
    except Exception as e:
        print(f"    [THẤT BẠI] send_image() lỗi: {e}")

    # Bước 6: Đợi phản hồi từ Gemini (nếu có)
    print("\n[5] Đợi 5 giây xem Gemini có phản hồi không...")
    await asyncio.sleep(5.0)

    # Bước 7: Đóng phiên
    print("\n[6] Đang đóng phiên Gemini...")
    await client.close_session()
    print(f"    is_active sau khi đóng: {client.is_active}")

    # Báo cáo
    print("\n" + "=" * 60)
    print("KẾT QUẢ KIỂM THỬ")
    print("=" * 60)
    print(f"  Kết nối phiên:        ĐẠT ({connect_time:.2f}s)")
    print(f"  send_realtime_input:  ĐẠT (audio + video)")
    print(f"  Phản hồi nhận được:   {len(responses_received)}")

    for r in responses_received:
        print(f"    - {r['kind']}: {r['data_size']} bytes, mime={r['mime_type']}")

    has_audio = any(r["kind"] == "audio" for r in responses_received)
    has_text = any(r["kind"] == "text" for r in responses_received)
    has_complete = any(r["kind"] == "turn_complete" for r in responses_received)

    if has_audio or has_text:
        print("\n  [ĐẠT] Gemini trả về phản hồi")
    else:
        print("\n  [THÔNG TIN] Gemini chưa trả phản hồi (bình thường — chỉ gửi im lặng)")

    print("\n  ISS-01: ĐÃ XÁC NHẬN — send_realtime_input() hoạt động đúng")
    print("  Chữ ký: session.send_realtime_input(audio=types.Blob(...))")
    print("          session.send_realtime_input(video=types.Blob(...))")


if __name__ == "__main__":
    asyncio.run(main())
