"""Quick test: Camera + MediaPipe Face Detection pipeline.

Chạy: cd /mnt/hdd/projects/avatar_AI && python tests/quicktest_camera.py
Tuỳ chỉnh: python tests/quicktest_camera.py [camera_url_or_index] [rotate]

Ví dụ:
  python tests/quicktest_camera.py                                    # dùng .env
  python tests/quicktest_camera.py http://admin:admin@ip:8081/video 90
  python tests/quicktest_camera.py 0
"""
import cv2
import math
import sys
import os
import time

import numpy as np
import mediapipe as mp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.models import HeadRotation, TrackingResult
from backend.config import AppConfig

_cfg = AppConfig()
DEFAULT_URL = _cfg.CAMERA_URL or "http://admin:admin@192.168.1.15:8081/video"
DEFAULT_ROTATE = _cfg.CAMERA_ROTATE
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "models", "face_landmarker.task")
LANDMARK_IDS = [4, 152, 33, 263, 61, 291]
FACE_3D = np.array([
    [0, 0, 0], [0, -330, -65], [-225, 170, -135],
    [225, 170, -135], [-150, -150, -125], [150, -150, -125],
], dtype=np.float64)
N_FRAMES = 50


def rotate_frame(frame, rotate):
    if rotate == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotate in (-90, 270):
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    elif rotate == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    return frame


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    rotate = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_ROTATE
    if isinstance(source, str) and source.isdigit():
        source = int(source)

    print(f"Source: {source}")
    print(f"Rotate: {rotate}")
    print(f"Model:  {MODEL_PATH}")
    print()

    # 1. Save 1 frame
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print("FAIL: Khong mo duoc camera")
        return 1
    ret, frame = cap.read()
    if ret:
        frame = rotate_frame(frame, rotate)
        cv2.imwrite("/tmp/cam.jpg", frame)
        print(f"Frame saved: /tmp/cam.jpg ({frame.shape[1]}x{frame.shape[0]})")
    else:
        print("FAIL: Khong doc duoc frame")
        cap.release()
        return 1
    cap.release()

    # 2. Face detection
    opts = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.4,
        min_face_presence_confidence=0.4,
    )

    cap = cv2.VideoCapture(source)
    faces = 0
    latencies = []

    with mp.tasks.vision.FaceLandmarker.create_from_options(opts) as lm:
        for i in range(N_FRAMES):
            t0 = time.time()
            ret, frame = cap.read()
            if not ret:
                continue
            frame = rotate_frame(frame, rotate)
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            elapsed = (time.time() - t0) * 1000
            latencies.append(elapsed)

            if result.face_landmarks:
                faces += 1
                f = result.face_landmarks[0]
                pts = np.array([[f[j].x * w, f[j].y * h] for j in LANDMARK_IDS], dtype=np.float64)
                cm = np.array([[w, 0, w / 2], [0, w, h / 2], [0, 0, 1]], dtype=np.float64)
                ok, rv, _ = cv2.solvePnP(FACE_3D, pts, cm, np.zeros((4, 1), dtype=np.float64))
                if ok:
                    rm, _ = cv2.Rodrigues(rv)
                    pitch = math.degrees(math.asin(-rm[2][1]))
                    yaw = math.degrees(math.atan2(rm[2][0], rm[2][2]))
                    roll = math.degrees(math.atan2(rm[0][1], rm[1][1]))
                    if faces <= 5 or i == N_FRAMES - 1:
                        print(f"  [{i:2d}] FACE  pitch={pitch:+6.1f}  yaw={yaw:+6.1f}  roll={roll:+6.1f}  {elapsed:.0f}ms")
            else:
                if i < 3:
                    print(f"  [{i:2d}] ---   {elapsed:.0f}ms")

    cap.release()

    print()
    pct = faces / N_FRAMES * 100
    avg = sum(latencies) / len(latencies) if latencies else 0
    print(f"Ket qua: {faces}/{N_FRAMES} frames co mat ({pct:.0f}%)")
    print(f"Latency: avg={avg:.0f}ms")
    if faces >= N_FRAMES // 2:
        print("PASS")
        return 0
    else:
        print("CHUA DETECT - huong front camera vao mat roi thu lai")
        return 1


if __name__ == "__main__":
    sys.exit(main())
