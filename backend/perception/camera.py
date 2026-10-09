"""
Camera — OpenCV frame capture.

Runs in a dedicated thread because cv2.VideoCapture is blocking I/O. The thread keeps the
newest BGR frame; the orchestrator fetches it with take_frame(). A lost camera is retried
every CAMERA_RETRY_SEC, never a crash.
"""
import threading
from typing import Optional

import cv2
import numpy as np

from backend.config import AppConfig
from backend.utils.logging import get_logger

logger = get_logger("camera")

_ROTATIONS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    -90: cv2.ROTATE_90_COUNTERCLOCKWISE,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    180: cv2.ROTATE_180,
}


class Camera:
    """Thread-safe camera reader. Keeps only the newest frame."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._connected = False

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, name="camera-capture", daemon=True
        )
        self._thread.start()
        logger.info({"msg": "Camera started", "source": str(self._source())})

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.config.CAMERA_RETRY_SEC)
            self._thread = None
        logger.info({"msg": "Camera stopped"})

    def take_frame(self) -> Optional[np.ndarray]:
        """Return the newest BGR frame not yet taken, or None if there is none."""
        with self._lock:
            frame, self._frame = self._frame, None
        return frame

    # ── Internal: runs in dedicated thread ───────────────────────────────

    def _source(self) -> "str | int":
        return self.config.CAMERA_URL if self.config.CAMERA_URL else self.config.CAMERA_INDEX

    def _capture_loop(self) -> None:
        cap: Optional[cv2.VideoCapture] = None
        rotation = _ROTATIONS.get(self.config.CAMERA_ROTATE)

        while not self._stop_event.is_set():
            if cap is None:
                cap = self._open()
                if cap is None:
                    self._stop_event.wait(self.config.CAMERA_RETRY_SEC)
                    continue

            ret, frame = cap.read()
            if not ret:
                logger.warning({"msg": "Camera read failed, reconnecting"})
                cap.release()
                cap = None
                self._connected = False
                self._stop_event.wait(self.config.CAMERA_RETRY_SEC)
                continue

            # e.g. a phone mounted sideways
            if rotation is not None:
                frame = cv2.rotate(frame, rotation)
            with self._lock:
                self._frame = frame

        if cap is not None:
            cap.release()
        self._connected = False

    def _open(self) -> Optional[cv2.VideoCapture]:
        cap = cv2.VideoCapture(self._source())
        if not cap.isOpened():
            cap.release()
            logger.warning({"msg": "Camera open failed, will retry", "source": str(self._source())})
            return None

        if not self.config.CAMERA_URL:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.CAMERA_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.CAMERA_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, self.config.TRACKING_FPS)
        self._connected = True
        logger.info({"msg": "Camera opened", "source": str(self._source())})
        return cap
