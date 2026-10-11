"""
VAD Module — Silero VAD wrapper for real-time speech detection.

PCM 16 kHz Int16 bytes in → a (possibly empty) list of VADEvent out.
The model is loaded once at startup and runs on CPU. load() and process_chunk() are
blocking → the orchestrator calls them in the VAD executor, never on the event loop.
"""
from typing import Any, Callable, Optional

import numpy as np

from backend.config import AppConfig
from backend.models import MIC_SAMPLE_RATE, PCM_SAMPLE_WIDTH, VADEvent
from backend.utils.logging import get_logger

logger = get_logger("vad")

# Silero VAD requires exactly 512 samples per window at 16kHz (= 32ms)
_WINDOW_SAMPLES = 512


class VADModule:
    """Stateful Silero VAD wrapper. Not thread-safe — call from a single thread."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._iterator: Any = None
        self._to_tensor: Optional[Callable[[np.ndarray], Any]] = None
        self._buffer = np.array([], dtype=np.float32)

    def load(self) -> None:
        """Load Silero VAD model. Call once at startup (blocking)."""
        import torch
        from silero_vad import VADIterator, load_silero_vad

        self._iterator = VADIterator(
            load_silero_vad(),
            threshold=self.config.VAD_THRESHOLD,
            sampling_rate=MIC_SAMPLE_RATE,
            min_silence_duration_ms=self.config.VAD_MIN_SILENCE_MS,
            speech_pad_ms=self.config.VAD_SPEECH_PAD_MS,
        )
        self._to_tensor = torch.from_numpy
        logger.info({
            "msg": "Silero VAD loaded",
            "threshold": self.config.VAD_THRESHOLD,
            "min_silence_ms": self.config.VAD_MIN_SILENCE_MS,
        })

    def reset(self) -> None:
        """Reset VAD state — call when the audio stream restarts (new connection)."""
        if self._iterator is not None:
            self._iterator.reset_states()
        self._buffer = np.array([], dtype=np.float32)

    def process_chunk(self, pcm16: bytes) -> list[VADEvent]:
        """
        Process a chunk of PCM 16kHz Int16 audio bytes.
        Returns every speech_start / speech_end detected in this call, in order.
        Internally buffers the remainder to feed Silero 512-sample windows.
        """
        if self._iterator is None or not pcm16 or len(pcm16) % PCM_SAMPLE_WIDTH:
            return []

        # Silero expects float32 in [-1, 1].
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        self._buffer = np.concatenate([self._buffer, samples])

        events: list[VADEvent] = []
        while len(self._buffer) >= _WINDOW_SAMPLES:
            window = self._buffer[:_WINDOW_SAMPLES]
            self._buffer = self._buffer[_WINDOW_SAMPLES:]

            try:
                result = self._iterator(self._to_tensor(window), return_seconds=False)
            except Exception as e:
                logger.warning({"msg": "VAD processing error", "error": str(e)})
                continue

            if result is None:
                continue
            if "start" in result:
                events.append(VADEvent.SPEECH_START)
                logger.debug({"msg": "VAD: speech_start"})
            elif "end" in result:
                events.append(VADEvent.SPEECH_END)
                logger.debug({"msg": "VAD: speech_end"})

        return events
