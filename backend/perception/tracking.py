"""
Head tracking — MediaPipe Face Landmarker → presence + HeadRotation.

API: mediapipe.tasks (0.10.x+). Model: backend/assets/face_landmarker.task.
load() and process() are blocking → the orchestrator calls them in the vision executor,
never on the event loop.
"""
import math
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from backend.config import AppConfig
from backend.models import HeadRotation, TrackingResult
from backend.utils.logging import get_logger

logger = get_logger("tracking")

_MODEL_PATH = Path(__file__).resolve().parent.parent / "assets" / "face_landmarker.task"

# ── MediaPipe landmark indices used for solvePnP ──────────────────────────
# Nose tip, chin, eye outer corners (image left, image right), mouth corners (same).
_LANDMARK_IDS = [4, 152, 33, 263, 61, 291]

# 3D reference face (mm), nose tip at the origin, in OpenCV camera axes: x → image right,
# y → image DOWN, z → away from the camera. An upright face looking into the camera is
# therefore the identity rotation, and solvePnP returns the head rotation itself.
_FACE_3D = np.array(
    [
        [0.0, 0.0, 0.0],          # Nose tip
        [0.0, 330.0, 65.0],       # Chin
        [-225.0, -170.0, 135.0],  # Eye outer corner, image left
        [225.0, -170.0, 135.0],   # Eye outer corner, image right
        [-150.0, 150.0, 125.0],   # Mouth corner, image left
        [150.0, 150.0, 125.0],    # Mouth corner, image right
    ],
    dtype=np.float64,
)

_DIST_COEFFS = np.zeros((4, 1), dtype=np.float64)

# A face is a sequence of normalized landmarks, each with `.x` and `.y` in [0, 1].
Face = Sequence[Any]


def clamp_rotation(
    pitch: float, yaw: float, roll: float,
    pitch_max: float, yaw_max: float, roll_max: float,
) -> HeadRotation:
    """Clamp each axis to ±its limit."""
    return HeadRotation(
        pitch=max(-pitch_max, min(pitch_max, pitch)),
        yaw=max(-yaw_max, min(yaw_max, yaw)),
        roll=max(-roll_max, min(roll_max, roll)),
    )


def pick_largest_face(faces: Sequence[Face]) -> Optional[Face]:
    """Pick the face with the largest bounding box (closest to camera)."""
    best: Optional[Face] = None
    best_area = -1.0
    for face in faces:
        xs = [lm.x for lm in face]
        ys = [lm.y for lm in face]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area > best_area:
            best_area = area
            best = face
    return best


class HeadTracker:
    """Stateless per frame. Not thread-safe — call from one thread."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._mp: Any = None
        self._landmarker: Any = None

    def load(self) -> None:
        """Create the Face Landmarker. Call once at startup (blocking)."""
        import mediapipe as mp

        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(_MODEL_PATH)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_faces=self.config.TRACKING_MAX_FACES,
            min_face_detection_confidence=self.config.TRACKING_MIN_CONFIDENCE,
            min_face_presence_confidence=self.config.TRACKING_MIN_CONFIDENCE,
            min_tracking_confidence=self.config.TRACKING_MIN_CONFIDENCE,
        )
        self._mp = mp
        self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        logger.info({"msg": "Face Landmarker loaded", "max_faces": self.config.TRACKING_MAX_FACES})

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None

    def process(self, frame: np.ndarray) -> TrackingResult:
        """BGR frame in → presence + clamped rotation of the largest face."""
        face = pick_largest_face(self._detect(frame))
        if face is None:
            return TrackingResult(present=False)

        h, w = frame.shape[:2]
        return TrackingResult(present=True, head_rotation=self._solve_head_pose(face, w, h))

    # ── Internal ──────────────────────────────────────────────────────────

    def _detect(self, frame: np.ndarray) -> Sequence[Face]:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        return self._landmarker.detect(mp_image).face_landmarks

    def _solve_head_pose(self, face: Face, w: int, h: int) -> Optional[HeadRotation]:
        landmarks_2d = np.array(
            [[face[idx].x * w, face[idx].y * h] for idx in _LANDMARK_IDS],
            dtype=np.float64,
        )
        focal_length = w
        cam_matrix = np.array(
            [[focal_length, 0, w / 2], [0, focal_length, h / 2], [0, 0, 1]],
            dtype=np.float64,
        )

        # SQPNP searches for the global optimum. ITERATIVE without an initial guess lands on
        # the reflected pose (face behind the camera, roll ≈ ±180°) once landmarks carry
        # sub-pixel noise.
        success, rot_vec, trans_vec = cv2.solvePnP(
            _FACE_3D,
            landmarks_2d,
            cam_matrix,
            _DIST_COEFFS,
            flags=cv2.SOLVEPNP_SQPNP,
        )
        # A face behind the camera is not a pose: the caller keeps the last-known rotation.
        if not success or trans_vec[2, 0] <= 0:
            return None

        # Wire contract: radians, applied by the frontend to the head bone as a YXZ Euler
        # rotation (pitch → X, yaw → Y, roll → Z) with no sign flip, so the avatar mirrors
        # the person. Camera axes and the avatar's axes differ by a point reflection, which
        # leaves a rotation matrix unchanged — hence the same YXZ decomposition here:
        #   pitch > 0: nods down          (looking up is negative)
        #   yaw   > 0: turns to the person's own right (own left is negative)
        #   roll  > 0: tilts toward the person's own left shoulder
        rot_mat, _ = cv2.Rodrigues(rot_vec)
        pitch = math.asin(max(-1.0, min(1.0, -rot_mat[1][2])))
        yaw = math.atan2(rot_mat[0][2], rot_mat[2][2])
        roll = math.atan2(rot_mat[1][0], rot_mat[1][1])

        return clamp_rotation(
            pitch, yaw, roll,
            self.config.HEAD_PITCH_MAX, self.config.HEAD_YAW_MAX, self.config.HEAD_ROLL_MAX,
        )
