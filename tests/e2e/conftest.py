"""Shared e2e fixtures. Everything here talks to the real environment (RUN_E2E=1)."""
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

UTTERANCE_TEXT = "Xin chào, cho tôi hỏi lãi suất tiết kiệm kỳ hạn sáu tháng là bao nhiêu?"
NAME_QUESTION_TEXT = "Xin chào, bạn tên là gì?"
_VOICE = "Linh"
_SILENCE_LEVEL = 200  # Int16 amplitude below which the tail counts as silence


def _spoken(text: str, out_dir: Path) -> bytes:
    """`text` said by a real Vietnamese voice, as 16 kHz mono Int16 PCM, generated with
    macOS `say`."""
    if not (shutil.which("say") and shutil.which("afconvert")):
        pytest.skip("macOS `say` and `afconvert` are required to generate the utterance")

    aiff, wav_path = out_dir / "utterance.aiff", out_dir / "utterance.wav"
    spoken = subprocess.run(
        ["say", "-v", _VOICE, "-o", str(aiff), text], capture_output=True, text=True
    )
    if spoken.returncode != 0 or not aiff.exists():
        pytest.skip(f"macOS voice '{_VOICE}' is not available: {spoken.stderr.strip()}")
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav_path)],
        check=True, capture_output=True,
    )

    with wave.open(str(wav_path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")

    # Trim trailing silence so "end of speech" is the real end of the voice.
    voiced = np.flatnonzero(np.abs(samples.astype(np.int32)) > _SILENCE_LEVEL)
    if voiced.size == 0:
        pytest.skip("`say` produced silent audio")
    return samples[: voiced[-1] + 1].tobytes()


@pytest.fixture(scope="session")
def utterance_pcm(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A question about an interest rate."""
    return _spoken(UTTERANCE_TEXT, tmp_path_factory.mktemp("utterance"))


@pytest.fixture(scope="session")
def name_question_pcm(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """The avatar asked for its name."""
    return _spoken(NAME_QUESTION_TEXT, tmp_path_factory.mktemp("name-question"))
