"""
Kiểm thử độc lập Module Thị Giác (Vision).

Mục đích: Xác nhận camera + MediaPipe hoạt động, trả về TrackingResult đúng.
Chạy: python tests/test_vision_standalone.py
Yêu cầu: Camera kết nối (/dev/video0 hoặc DroidCam)
"""
import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import AppConfig
from backend.modules.vision import VisionModule


async def main():
    print("=" * 60)
    print("KIỂM THỬ ĐỘC LẬP: Module Thị Giác (Vision)")
    print("=" * 60)

    # Bước 1: Khởi tạo config
    config = AppConfig()
    print(f"\n[1] Camera index: {config.CAMERA_INDEX}")
    print(f"    Tracking FPS: {config.TRACKING_FPS}")
    print(f"    Snapshot interval: {config.SNAPSHOT_INTERVAL_SEC}s")

    # Bước 2: Khởi tạo và chạy module
    vision = VisionModule(config)
    print("\n[2] Khởi động module thị giác...")
    await vision.start()

    # Đợi 1 giây cho camera khởi động
    await asyncio.sleep(1.0)

    # Bước 3: Thu thập 100 frame tracking
    print("\n[3] Thu thập 100 frame tracking...")
    results = {
        "total_frames": 0,
        "face_detected": 0,
        "no_face": 0,
        "latencies": [],
    }

    for i in range(100):
        t0 = time.monotonic()
        try:
            tracking = await asyncio.wait_for(vision.get_tracking(), timeout=1.0)
            latency_ms = (time.monotonic() - t0) * 1000
            results["total_frames"] += 1
            results["latencies"].append(latency_ms)

            if tracking.face_detected:
                results["face_detected"] += 1
                r = tracking.rotation
                if i % 20 == 0:
                    print(f"    Frame {i:3d}: MẶT PHÁT HIỆN "
                          f"pitch={r.pitch:+.3f} yaw={r.yaw:+.3f} roll={r.roll:+.3f} "
                          f"({latency_ms:.1f}ms)")
            else:
                results["no_face"] += 1
                if i % 20 == 0:
                    print(f"    Frame {i:3d}: KHÔNG CÓ MẶT ({latency_ms:.1f}ms)")

        except asyncio.TimeoutError:
            print(f"    Frame {i:3d}: TIMEOUT")

    # Bước 4: Thu thập snapshot
    print("\n[4] Kiểm tra snapshot JPEG...")
    await asyncio.sleep(config.SNAPSHOT_INTERVAL_SEC + 0.5)
    snapshot = await vision.get_snapshot()
    if snapshot:
        print(f"    Snapshot: {len(snapshot)} bytes (JPEG)")
    else:
        print("    Snapshot: KHÔNG CÓ (có thể chưa đủ thời gian)")

    # Bước 5: Dừng module
    await vision.stop()
    print("\n[5] Module thị giác đã dừng.")

    # Bước 6: Báo cáo
    print("\n" + "=" * 60)
    print("KẾT QUẢ KIỂM THỬ")
    print("=" * 60)
    print(f"  Tổng frame:           {results['total_frames']}")
    print(f"  Phát hiện mặt:        {results['face_detected']}")
    print(f"  Không có mặt:         {results['no_face']}")

    if results["latencies"]:
        avg = sum(results["latencies"]) / len(results["latencies"])
        mx = max(results["latencies"])
        mn = min(results["latencies"])
        print(f"  Độ trễ trung bình:    {avg:.1f}ms")
        print(f"  Độ trễ min/max:       {mn:.1f}ms / {mx:.1f}ms")

    # Đánh giá
    print("\n--- ĐÁNH GIÁ ---")
    passed = True

    if results["total_frames"] < 50:
        print("  [THẤT BẠI] Thu được < 50 frame (camera có vấn đề)")
        passed = False
    else:
        print(f"  [ĐẠT] Thu được {results['total_frames']}/100 frame")

    if results["face_detected"] == 0:
        print("  [CẢNH BÁO] Không phát hiện mặt nào — kiểm tra camera có người trước ống kính")
    else:
        print(f"  [ĐẠT] Phát hiện mặt: {results['face_detected']} frame")

    if results["latencies"]:
        avg = sum(results["latencies"]) / len(results["latencies"])
        if avg > 50:
            print(f"  [CẢNH BÁO] Độ trễ trung bình cao: {avg:.1f}ms (mục tiêu < 33ms)")
        else:
            print(f"  [ĐẠT] Độ trễ trung bình: {avg:.1f}ms")

    print(f"\n{'ĐẠT TẤT CẢ' if passed else 'CÓ VẤN ĐỀ CẦN XỬ LÝ'}")


if __name__ == "__main__":
    asyncio.run(main())
