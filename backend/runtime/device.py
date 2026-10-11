"""Inference device of this machine, detected once at startup."""
import platform
import shutil


def detect_device() -> str:
    """Return "metal" (Apple Silicon), "cuda" (NVIDIA driver present) or "cpu"."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "metal"
    if shutil.which("nvidia-smi"):
        return "cuda"
    return "cpu"
