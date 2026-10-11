import time

import pytest

from backend.config import AppConfig
from backend.core.perception import camera as camera_module
from backend.core.perception.camera import Camera


class FakeCapture:
    """Stands in for cv2.VideoCapture: opens fine, every read fails."""

    opened = 0
    reads = 0

    def __init__(self, source) -> None:
        type(self).opened += 1

    def isOpened(self) -> bool:
        return True

    def set(self, prop, value) -> None:
        pass

    def read(self):
        type(self).reads += 1
        return False, None

    def release(self) -> None:
        pass


@pytest.fixture
def fake_capture(monkeypatch) -> type[FakeCapture]:
    FakeCapture.opened = 0
    FakeCapture.reads = 0
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", FakeCapture)
    return FakeCapture


def test_failed_read_waits_camera_retry_sec_before_reopening(fake_capture):
    camera = Camera(AppConfig(GROQ_API_KEY="test-key", CAMERA_RETRY_SEC=30))
    camera.start()
    try:
        deadline = time.monotonic() + 2
        while fake_capture.reads == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)

        assert fake_capture.opened == 1
        assert not camera.connected
    finally:
        stopping = time.monotonic()
        camera.stop()
    # The retry wait ends as soon as the camera is stopped.
    assert time.monotonic() - stopping < 2
