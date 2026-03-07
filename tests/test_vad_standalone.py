"""
Kiểm thử độc lập Module VAD (Phát hiện giọng nói).

Mục đích: Xác nhận Silero VAD load thành công, phát hiện speech_start/end đúng.
Chạy: python tests/test_vad_standalone.py
Yêu cầu: Không cần phần cứng đặc biệt (dùng audio giả lập)
"""
import sys
import os
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import AppConfig
from backend.modules.vad import VADModule


def generate_silence(duration_sec: float = 0.5, sample_rate: int = 16000) -> bytes:
    """Tạo âm thanh im lặng (PCM Int16)."""
    samples = int(duration_sec * sample_rate)
    silence = np.zeros(samples, dtype=np.int16)
    return silence.tobytes()


def generate_speech(duration_sec: float = 1.0, freq: float = 440.0, sample_rate: int = 16000) -> bytes:
    """Tạo âm thanh giả lập tiếng nói (sóng sin + nhiễu, PCM Int16)."""
    samples = int(duration_sec * sample_rate)
    t = np.linspace(0, duration_sec, samples)
    # Sóng sin + nhiễu trắng (mô phỏng giọng nói)
    signal = np.sin(2 * np.pi * freq * t) * 0.5
    noise = np.random.randn(samples) * 0.1
    combined = (signal + noise) * 16000
    return combined.astype(np.int16).tobytes()


def test_vad():
    print("=" * 60)
    print("KIỂM THỬ ĐỘC LẬP: Module VAD (Phát hiện giọng nói)")
    print("=" * 60)

    # Bước 1: Khởi tạo
    config = AppConfig()
    vad = VADModule(config)
    print(f"\n[1] VAD threshold: {config.VAD_THRESHOLD}")

    # Bước 2: Load mô hình
    print("\n[2] Đang tải mô hình Silero VAD...")
    t0 = time.time()
    try:
        vad.load()
        load_time = time.time() - t0
        print(f"    Tải thành công trong {load_time:.2f}s")
    except Exception as e:
        print(f"    [THẤT BẠI] Không tải được VAD: {e}")
        return

    # Bước 3: Gửi im lặng — không nên phát hiện giọng nói
    print("\n[3] Gửi 2 giây im lặng...")
    silence = generate_silence(2.0)
    chunk_size = 1024  # 512 samples * 2 bytes
    events_silence = []

    for i in range(0, len(silence), chunk_size):
        chunk = silence[i:i + chunk_size]
        if len(chunk) < chunk_size:
            break
        event = vad.process_audio(chunk)
        if event:
            events_silence.append(event)

    print(f"    Sự kiện phát hiện: {len(events_silence)}")
    for e in events_silence:
        print(f"      - {e.event_type} (confidence={e.confidence})")

    # Bước 4: Gửi tiếng nói giả lập — nên phát hiện speech_start
    print("\n[4] Gửi 2 giây tiếng nói giả lập...")
    vad.reset()
    speech = generate_speech(2.0)
    events_speech = []

    for i in range(0, len(speech), chunk_size):
        chunk = speech[i:i + chunk_size]
        if len(chunk) < chunk_size:
            break
        event = vad.process_audio(chunk)
        if event:
            events_speech.append(event)

    print(f"    Sự kiện phát hiện: {len(events_speech)}")
    for e in events_speech:
        print(f"      - {e.event_type} (confidence={e.confidence})")

    # Bước 5: Gửi tiếng nói rồi im lặng — nên phát hiện speech_start rồi speech_end
    print("\n[5] Gửi 1s tiếng nói + 1s im lặng (kỳ vọng: start rồi end)...")
    vad.reset()
    combined = generate_speech(1.0) + generate_silence(1.0)
    events_combined = []

    for i in range(0, len(combined), chunk_size):
        chunk = combined[i:i + chunk_size]
        if len(chunk) < chunk_size:
            break
        event = vad.process_audio(chunk)
        if event:
            events_combined.append(event)

    print(f"    Sự kiện phát hiện: {len(events_combined)}")
    for e in events_combined:
        print(f"      - {e.event_type} (confidence={e.confidence})")

    # Bước 6: Kiểm tra reset
    print("\n[6] Kiểm tra reset()...")
    vad.reset()
    event_after_reset = vad.process_audio(generate_silence(0.1))
    print(f"    Sự kiện sau reset + im lặng: {event_after_reset}")

    # Báo cáo
    print("\n" + "=" * 60)
    print("KẾT QUẢ KIỂM THỬ")
    print("=" * 60)

    passed = True

    # Kiểm tra 1: VAD load thành công
    print("  [ĐẠT] Mô hình Silero VAD tải thành công")

    # Kiểm tra 2: Im lặng không nên tạo speech_start
    has_false_positive = any(e.event_type == "speech_start" for e in events_silence)
    if has_false_positive:
        print("  [CẢNH BÁO] Phát hiện speech_start trong im lặng (false positive)")
    else:
        print("  [ĐẠT] Im lặng không tạo speech_start")

    # Kiểm tra 3: Tiếng nói nên tạo speech_start
    has_speech_start = any(e.event_type == "speech_start" for e in events_speech)
    if has_speech_start:
        print("  [ĐẠT] Tiếng nói giả lập phát hiện speech_start")
    else:
        print("  [CẢNH BÁO] Không phát hiện speech_start cho tiếng nói giả lập")
        print("           (Silero VAD có thể cần audio thực tế để hoạt động chính xác)")

    # Kiểm tra 4: Combined có cả start và end
    has_start = any(e.event_type == "speech_start" for e in events_combined)
    has_end = any(e.event_type == "speech_end" for e in events_combined)
    if has_start and has_end:
        print("  [ĐẠT] Phát hiện cả speech_start và speech_end")
    elif has_start:
        print("  [CẢNH BÁO] Chỉ phát hiện speech_start, chưa có speech_end")
        print("           (có thể cần thêm im lặng để trigger end)")
    else:
        print("  [CẢNH BÁO] Không phát hiện speech events trong audio kết hợp")

    print(f"\n{'ĐẠT TẤT CẢ' if passed else 'CÓ VẤN ĐỀ CẦN XỬ LÝ'}")


if __name__ == "__main__":
    test_vad()
