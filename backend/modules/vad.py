"""
VAD Module — Silero VAD wrapper for real-time speech detection.

Nhận PCM 16kHz Int16 bytes → trả về VadEvent hoặc None.
Load model 1 lần khi startup, chạy trên CPU.
"""
import time
from typing import Optional

import numpy as np
import torch

from backend.config import AppConfig
from backend.models import VadEvent
from backend.utils.logging import get_logger

logger = get_logger("vad")

# Silero VAD requires exactly 512 samples per chunk at 16kHz (= 32ms)
_CHUNK_SAMPLES = 512
_SAMPLE_RATE = 16000


class VADModule:
    """Stateful Silero VAD wrapper. Not thread-safe — call from single asyncio task."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._model = None
        self._vad_iterator = None
        self._buffer = np.array([], dtype=np.float32)
        self._loaded = False

    def load(self) -> None:
        """Load Silero VAD model. Call once at startup (blocking)."""
        logger.info({"msg": "Loading Silero VAD model..."})
        try:
            # Try the pip package first (silero-vad)
            from silero_vad import load_silero_vad, VADIterator  # type: ignore
            self._model = load_silero_vad()
            self._vad_iterator = VADIterator(
                self._model,
                threshold=self.config.VAD_THRESHOLD,
                sampling_rate=_SAMPLE_RATE,
                min_silence_duration_ms=300,
                speech_pad_ms=100,
            )
        except ImportError:
            # Fallback: torch hub
            logger.info({"msg": "silero-vad package not found, loading via torch hub"})
            self._model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
            )
            _, _, _, VADIterator, _ = utils
            self._vad_iterator = VADIterator(
                self._model,
                threshold=self.config.VAD_THRESHOLD,
                sampling_rate=_SAMPLE_RATE,
                min_silence_duration_ms=300,
                speech_pad_ms=100,
            )

        self._loaded = True
        logger.info({"msg": "Silero VAD loaded", "threshold": self.config.VAD_THRESHOLD})

    def reset(self) -> None:
        """Reset VAD state — call when starting a new conversation session."""
        if self._vad_iterator:
            self._vad_iterator.reset_states()
        self._buffer = np.array([], dtype=np.float32)

    def process_audio(self, pcm_bytes: bytes) -> Optional[VadEvent]:
        """
        Process a chunk of PCM 16kHz Int16 audio bytes.
        Returns a VadEvent if speech start/end detected, else None.
        Internally buffers to ensure 512-sample chunks for Silero.
        """
        if not self._loaded:
            return None

        # Convert Int16 PCM → float32 normalized [-1, 1]
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._buffer = np.concatenate([self._buffer, samples])

        event: Optional[VadEvent] = None

        while len(self._buffer) >= _CHUNK_SAMPLES:
            chunk = self._buffer[:_CHUNK_SAMPLES]
            self._buffer = self._buffer[_CHUNK_SAMPLES:]

            tensor = torch.from_numpy(chunk)
            try:
                result = self._vad_iterator(tensor, return_seconds=False)
            except Exception as e:
                logger.warning({"msg": "VAD processing error", "error": str(e)})
                continue

            if result is None:
                continue

            if "start" in result:
                event = VadEvent(
                    event_type="speech_start",
                    timestamp=time.time(),
                    confidence=float(self.config.VAD_THRESHOLD),
                )
                logger.debug({"msg": "VAD: speech_start"})
            elif "end" in result:
                event = VadEvent(
                    event_type="speech_end",
                    timestamp=time.time(),
                    confidence=float(self.config.VAD_THRESHOLD),
                )
                logger.debug({"msg": "VAD: speech_end"})

        return event
