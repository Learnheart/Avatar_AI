"""
Vision Module — Camera capture + MediaPipe Face Landmarker → HeadRotation.

Chạy trong dedicated thread (asyncio.to_thread) vì cv2.VideoCapture là blocking I/O.
Output HeadRotation được đẩy vào asyncio.Queue(maxsize=2) để orchestrator consume.

API: mediapipe.tasks (0.10.x+) — dùng FaceLandmarker thay cho legacy solutions.
Model: backend/models/face_landmarker.task (download từ Google MediaPipe).
"""
import asyncio
import base64
import math
import os
import threading
import time
from typing import Optional

import cv2
import mediapipe as mp
import numpy as np

from backend.config import AppConfig
from backend.models import HeadRotation
from backend.utils.logging import get_logger

logger = get_logger("vision")

# ── MediaPipe Tasks API (mediapipe >= 0.10) ───────────────────────────────
_BaseOptions = mp.tasks.BaseOptions
_FaceLandmarker = mp.tasks.vision.FaceLandmarker
_FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
_VisionRunningMode = mp.tasks.vision.RunningMode

_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "face_landmarker.task"
)

# ── MediaPipe landmark indices dùng cho solvePnP ──────────────────────────
# Nose tip, Chin, Left eye outer, Right eye outer, Left mouth, Right mouth
_LANDMARK_IDS = [4, 152, 33, 263, 61, 291]

# 3D reference face model (mm) — canonical face geometry
_FACE_3D = np.array(
    [
        [0.0, 0.0, 0.0],          # Nose tip
        [0.0, -330.0, -65.0],     # Chin
        [-225.0, 170.0, -135.0],  # Left eye outer
        [225.0, 170.0, -135.0],   # Right eye outer
        [-150.0, -150.0, -125.0], # Left mouth
        [150.0, -150.0, -125.0],  # Right mouth
    ],
    dtype=np.float64,
)

_DIST_COEFFS = np.zeros((4, 1), dtype=np.float64)


class VisionModule:
    """Thread-safe camera module. Pushes HeadRotation into an asyncio.Queue."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._rotation_queue: asyncio.Queue[HeadRotation] = asyncio.Queue(maxsize=2)
        self._snapshot_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_rotation = HeadRotation()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Public API ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, name="vision-capture", daemon=True
        )
        self._thread.start()
        logger.info({"msg": "Vision module started", "camera": self.config.CAMERA_INDEX})

    async def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info({"msg": "Vision module stopped"})

    async def get_rotation(self) -> HeadRotation:
        """Get the latest head rotation (blocks until available)."""
        return await self._rotation_queue.get()

    async def get_snapshot(self) -> Optional[bytes]:
        """Get the latest JPEG snapshot (non-blocking)."""
        try:
            return self._snapshot_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    # ── Internal: runs in dedicated thread ───────────────────────────────

    def _capture_loop(self) -> None:
        options = _FaceLandmarkerOptions(
            base_options=_BaseOptions(model_asset_path=_MODEL_PATH),
            running_mode=_VisionRunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        cap = self._open_camera()
        last_snapshot_time = 0.0
        frame_interval = 1.0 / self.config.TRACKING_FPS

        with _FaceLandmarker.create_from_options(options) as landmarker:
            while not self._stop_event.is_set():
                t0 = time.monotonic()

                if cap is None or not cap.isOpened():
                    cap = self._retry_camera(cap)
                    time.sleep(0.1)
                    continue

                ret, frame = cap.read()
                if not ret:
                    logger.warning({"msg": "Camera read failed, retrying"})
                    cap = self._retry_camera(cap)
                    continue

                h, w = frame.shape[:2]
                rotation = self._process_frame(landmarker, frame, w, h)
                self._push_rotation(rotation)

                # Periodic JPEG snapshot for Gemini visual context
                now = time.time()
                if now - last_snapshot_time >= self.config.SNAPSHOT_INTERVAL_SEC:
                    jpeg = self._encode_jpeg(frame, w)
                    self._push_snapshot(jpeg)
                    last_snapshot_time = now

                # Throttle to target FPS
                elapsed = time.monotonic() - t0
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        if cap:
            cap.release()

    def _open_camera(self, retries: int = 5) -> Optional[cv2.VideoCapture]:
        for attempt in range(retries):
            cap = cv2.VideoCapture(self.config.CAMERA_INDEX)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_FPS, self.config.TRACKING_FPS)
                logger.info({"msg": "Camera opened", "attempt": attempt + 1})
                return cap
            cap.release()
            logger.warning({"msg": f"Camera open failed, retry {attempt+1}/{retries}"})
            time.sleep(2.0)
        logger.error({"msg": "Could not open camera after retries"})
        return None

    def _retry_camera(self, old_cap: Optional[cv2.VideoCapture]) -> Optional[cv2.VideoCapture]:
        if old_cap:
            old_cap.release()
        return self._open_camera(retries=1)

    def _process_frame(
        self, landmarker, frame: np.ndarray, w: int, h: int
    ) -> HeadRotation:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = landmarker.detect(mp_image)

        if not result.face_landmarks:
            return self._last_rotation  # Keep last known rotation

        # Use largest face (closest to camera) — EC-004
        landmarks_2d = self._get_largest_face_landmarks(
            result.face_landmarks, w, h
        )
        rotation = self._solve_head_pose(landmarks_2d, w, h)
        self._last_rotation = rotation
        return rotation

    def _get_largest_face_landmarks(
        self, all_faces, w: int, h: int
    ) -> np.ndarray:
        """If multiple faces, pick the one with the largest bounding box."""
        best = None
        best_area = -1.0
        for face in all_faces:
            xs = [lm.x for lm in face]
            ys = [lm.y for lm in face]
            area = (max(xs) - min(xs)) * (max(ys) - min(ys))
            if area > best_area:
                best_area = area
                best = face

        points = []
        for idx in _LANDMARK_IDS:
            lm = best[idx]
            points.append([lm.x * w, lm.y * h])
        return np.array(points, dtype=np.float64)

    def _solve_head_pose(
        self, landmarks_2d: np.ndarray, w: int, h: int
    ) -> HeadRotation:
        focal_length = w
        cam_matrix = np.array(
            [[focal_length, 0, w / 2], [0, focal_length, h / 2], [0, 0, 1]],
            dtype=np.float64,
        )

        success, rot_vec, _ = cv2.solvePnP(
            _FACE_3D,
            landmarks_2d,
            cam_matrix,
            _DIST_COEFFS,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return self._last_rotation

        rot_mat, _ = cv2.Rodrigues(rot_vec)
        # Extract Euler angles (pitch=X, yaw=Y, roll=Z) in radians
        pitch = math.asin(-rot_mat[2][1])
        yaw = math.atan2(rot_mat[2][0], rot_mat[2][2])
        roll = math.atan2(rot_mat[0][1], rot_mat[1][1])

        # Clamp to safe limits (BR-002)
        pitch = max(-self.config.HEAD_PITCH_MAX, min(self.config.HEAD_PITCH_MAX, pitch))
        yaw = max(-self.config.HEAD_YAW_MAX, min(self.config.HEAD_YAW_MAX, yaw))
        roll = max(-self.config.HEAD_ROLL_MAX, min(self.config.HEAD_ROLL_MAX, roll))

        return HeadRotation(pitch=pitch, yaw=yaw, roll=roll)

    def _encode_jpeg(self, frame: np.ndarray, w: int) -> bytes:
        max_w = self.config.SNAPSHOT_MAX_WIDTH
        if w > max_w:
            scale = max_w / w
            frame = cv2.resize(frame, None, fx=scale, fy=scale)
        _, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.config.SNAPSHOT_QUALITY]
        )
        return buf.tobytes()

    def _push_rotation(self, rotation: HeadRotation) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._put_rotation(rotation), self._loop
        )

    async def _put_rotation(self, rotation: HeadRotation) -> None:
        try:
            self._rotation_queue.put_nowait(rotation)
        except asyncio.QueueFull:
            try:
                self._rotation_queue.get_nowait()  # Drop oldest
            except asyncio.QueueEmpty:
                pass
            self._rotation_queue.put_nowait(rotation)

    def _push_snapshot(self, jpeg: bytes) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._put_snapshot(jpeg), self._loop
        )

    async def _put_snapshot(self, jpeg: bytes) -> None:
        try:
            self._snapshot_queue.put_nowait(jpeg)
        except asyncio.QueueFull:
            try:
                self._snapshot_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._snapshot_queue.put_nowait(jpeg)
