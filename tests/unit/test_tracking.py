import math
from types import SimpleNamespace

import numpy as np
import pytest

from backend.models import HeadRotation
from backend.perception import tracking
from backend.perception.tracking import (
    _LANDMARK_IDS,
    HeadTracker,
    clamp_rotation,
    pick_largest_face,
)

LIMITS = (0.52, 0.78, 0.35)  # pitch, yaw, roll


def _face(x0: float, y0: float, x1: float, y1: float) -> list[SimpleNamespace]:
    return [SimpleNamespace(x=x0, y=y0), SimpleNamespace(x=x1, y=y1)]


def test_clamp_keeps_values_inside_the_limits():
    rotation = clamp_rotation(0.1, -0.2, 0.3, *LIMITS)

    assert (rotation.pitch, rotation.yaw, rotation.roll) == (0.1, -0.2, 0.3)


@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_clamp_at_the_limits_is_unchanged(sign):
    rotation = clamp_rotation(sign * 0.52, sign * 0.78, sign * 0.35, *LIMITS)

    assert (rotation.pitch, rotation.yaw, rotation.roll) == (
        sign * 0.52, sign * 0.78, sign * 0.35,
    )


@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_clamp_beyond_the_limits_saturates_without_sign_flip(sign):
    rotation = clamp_rotation(sign * 2.0, sign * 3.0, sign * 1.5, *LIMITS)

    assert (rotation.pitch, rotation.yaw, rotation.roll) == (
        sign * 0.52, sign * 0.78, sign * 0.35,
    )


def test_largest_face_is_chosen():
    small = _face(0.1, 0.1, 0.2, 0.2)
    large = _face(0.3, 0.3, 0.9, 0.9)
    medium = _face(0.0, 0.0, 0.3, 0.3)

    assert pick_largest_face([small, large, medium]) is large


def test_no_faces_gives_no_largest_face():
    assert pick_largest_face([]) is None


def test_no_face_means_not_present(config, monkeypatch):
    tracker = HeadTracker(config)
    monkeypatch.setattr(tracker, "_detect", lambda frame: [])

    result = tracker.process(np.zeros((48, 64, 3), dtype=np.uint8))

    assert result.present is False
    assert result.head_rotation is None


# ── Head pose: synthetic face → pinhole camera → solver ───────────────────

IMAGE_W, IMAGE_H = 1280, 720
FACE_DISTANCE_MM = 3000.0
ANGLE_TOLERANCE = math.radians(3)

# An upright face looking into the camera, in camera axes (x → image right, y → image
# down, z → away from the camera), nose tip at the origin. Same order as _LANDMARK_IDS.
UPRIGHT_FACE_MM = np.array(
    [
        [0.0, 0.0, 0.0],          # nose tip
        [0.0, 330.0, 65.0],       # chin: below the nose
        [-225.0, -170.0, 135.0],  # eye corner on the image left: above the nose
        [225.0, -170.0, 135.0],   # eye corner on the image right
        [-150.0, 150.0, 125.0],   # mouth corner on the image left
        [150.0, 150.0, 125.0],    # mouth corner on the image right
    ]
)


def _axis_rotation(axis: str, angle: float) -> np.ndarray:
    """Right-handed rotation about one camera axis."""
    c, s = math.cos(angle), math.sin(angle)
    return {
        "pitch": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
        "yaw": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
        "roll": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]),
    }[axis]


def _projected_face(
    rotation: np.ndarray,
    distance_mm: float = FACE_DISTANCE_MM,
    pixel_noise: np.ndarray | None = None,
) -> list[SimpleNamespace]:
    """Rotate the face about the nose tip and project it with a pinhole camera.
    `pixel_noise` (one x, y offset per landmark) stands in for landmark jitter."""
    points = UPRIGHT_FACE_MM @ rotation.T + [0.0, 0.0, distance_mm]
    pixels = IMAGE_W * points[:, :2] / points[:, 2:] + [IMAGE_W / 2, IMAGE_H / 2]
    if pixel_noise is not None:
        pixels = pixels + pixel_noise
    face = [SimpleNamespace(x=0.0, y=0.0) for _ in range(max(_LANDMARK_IDS) + 1)]
    for index, (x, y) in zip(_LANDMARK_IDS, pixels, strict=True):
        face[index] = SimpleNamespace(x=x / IMAGE_W, y=y / IMAGE_H)
    return face


def _solve(config, rotation: np.ndarray) -> HeadRotation:
    return HeadTracker(config)._solve_head_pose(_projected_face(rotation), IMAGE_W, IMAGE_H)


def test_frontal_face_has_no_rotation(config):
    rotation = _solve(config, np.eye(3))

    assert rotation.pitch == pytest.approx(0.0, abs=ANGLE_TOLERANCE)
    assert rotation.yaw == pytest.approx(0.0, abs=ANGLE_TOLERANCE)
    assert rotation.roll == pytest.approx(0.0, abs=ANGLE_TOLERANCE)


@pytest.mark.parametrize("axis", ["pitch", "yaw", "roll"])
@pytest.mark.parametrize("degrees", [-20, -10, 10, 20])
def test_single_axis_rotation_is_recovered_with_its_sign(config, axis, degrees):
    angle = math.radians(degrees)

    rotation = _solve(config, _axis_rotation(axis, angle))

    for name in ("pitch", "yaw", "roll"):
        expected = angle if name == axis else 0.0
        assert getattr(rotation, name) == pytest.approx(expected, abs=ANGLE_TOLERANCE)


def test_combined_rotation_matches_the_frontend_euler_order(config):
    """The frontend applies the angles as a YXZ Euler rotation (avatar-renderer.js)."""
    pitch, yaw, roll = math.radians(10), math.radians(-20), math.radians(8)
    combined = (
        _axis_rotation("yaw", yaw) @ _axis_rotation("pitch", pitch) @ _axis_rotation("roll", roll)
    )

    rotation = _solve(config, combined)

    assert rotation.pitch == pytest.approx(pitch, abs=ANGLE_TOLERANCE)
    assert rotation.yaw == pytest.approx(yaw, abs=ANGLE_TOLERANCE)
    assert rotation.roll == pytest.approx(roll, abs=ANGLE_TOLERANCE)


def test_mirror_sign_convention(config):
    """Looking up → pitch < 0; turning to the person's own left (image right) → yaw < 0;
    tilting to the person's own left shoulder (head top to the image right) → roll > 0."""
    def moved(rotation: np.ndarray, point: list[float]) -> np.ndarray:
        return rotation @ np.array(point)

    nose_direction, head_top = [0.0, 0.0, -1.0], [0.0, -1.0, 0.0]
    looks_up = _axis_rotation("pitch", math.radians(-15))
    turns_left = _axis_rotation("yaw", math.radians(-15))
    tilts_left = _axis_rotation("roll", math.radians(15))
    # The rotations really are the movements named above (image y points down).
    assert moved(looks_up, nose_direction)[1] < 0
    assert moved(turns_left, nose_direction)[0] > 0
    assert moved(tilts_left, head_top)[0] > 0

    assert _solve(config, looks_up).pitch < -ANGLE_TOLERANCE
    assert _solve(config, turns_left).yaw < -ANGLE_TOLERANCE
    assert _solve(config, tilts_left).roll > ANGLE_TOLERANCE


# ── Head pose under landmark noise ────────────────────────────────────────

EYE_SPAN_MM = UPRIGHT_FACE_MM[3][0] - UPRIGHT_FACE_MM[2][0]
NOISE_SEED = 7
NOISY_FRAMES_PER_POSE = 40
NOISY_POSES = [  # pitch, yaw, roll — frontal, each clamp limit, and combinations
    (0.0, 0.0, 0.0),
    (0.52, 0.0, 0.0), (-0.52, 0.0, 0.0),
    (0.0, 0.78, 0.0), (0.0, -0.78, 0.0),
    (0.0, 0.0, 0.35), (0.0, 0.0, -0.35),
    (0.26, -0.39, 0.17), (-0.52, 0.78, -0.35), (0.52, -0.78, 0.35),
]


@pytest.mark.parametrize(
    ("face_width_px", "noise_px", "tolerance_deg"),
    # The jitter of a correct solve grows with noise / face width; a flip is off by ~180°.
    [(72, 0.5, 8), (72, 1.0, 16), (144, 0.5, 4), (144, 1.0, 8), (144, 2.0, 16), (384, 2.0, 5)],
)
def test_noisy_landmarks_never_flip_the_pose(config, face_width_px, noise_px, tolerance_deg):
    """The reflected solution (face behind the camera) shows up as roll ≈ ±180° with pitch
    and yaw inverted. The limits are opened so the clamp cannot hide it."""
    config.HEAD_PITCH_MAX = config.HEAD_YAW_MAX = config.HEAD_ROLL_MAX = math.pi
    tracker = HeadTracker(config)
    distance_mm = IMAGE_W * EYE_SPAN_MM / face_width_px
    rng = np.random.default_rng(NOISE_SEED)

    for pitch, yaw, roll in NOISY_POSES:
        truth = (
            _axis_rotation("yaw", yaw) @ _axis_rotation("pitch", pitch)
            @ _axis_rotation("roll", roll)
        )
        for _ in range(NOISY_FRAMES_PER_POSE):
            noise = rng.normal(0.0, noise_px, (len(_LANDMARK_IDS), 2))
            face = _projected_face(truth, distance_mm, noise)

            rotation = tracker._solve_head_pose(face, IMAGE_W, IMAGE_H)

            assert rotation is not None
            error = max(
                abs(rotation.pitch - pitch), abs(rotation.yaw - yaw), abs(rotation.roll - roll)
            )
            assert math.degrees(error) < tolerance_deg, (pitch, yaw, roll)


@pytest.mark.parametrize("depth", [0.0, -3000.0])
def test_pose_behind_the_camera_is_no_pose(config, monkeypatch, depth):
    def solve_behind_camera(*args, **kwargs):
        return True, np.zeros((3, 1)), np.array([[0.0], [0.0], [depth]])

    monkeypatch.setattr(tracking.cv2, "solvePnP", solve_behind_camera)

    assert _solve(config, np.eye(3)) is None
