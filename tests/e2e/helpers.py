"""Helpers shared by the e2e tests."""
import os

import numpy as np
import pytest

from backend.models import MIC_SAMPLE_RATE

E2E_MARKS = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_E2E") != "1",
        reason="e2e tests hit real Groq + ZeroTTS; set RUN_E2E=1 to run them",
    ),
]

_VIETNAMESE_CHARS = set(
    "ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
)


def looks_vietnamese(text: str) -> bool:
    """True if the text carries Vietnamese-specific letters or tone marks."""
    return any(ch in _VIETNAMESE_CHARS for ch in text.lower())


def resample_to_mic_rate(pcm16: bytes, sample_rate: int) -> bytes:
    """Linear resample of Int16 mono PCM to 16 kHz — good enough to transcribe speech."""
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
    positions = np.arange(0, len(samples), sample_rate / MIC_SAMPLE_RATE)
    return np.interp(positions, np.arange(len(samples)), samples).astype("<i2").tobytes()
