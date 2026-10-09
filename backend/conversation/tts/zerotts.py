"""
ZeroTTS backend — local Vietnamese TTS (ONNX runtime, CPU).

Every call into the `zerotts` package lives in this file. `synthesize_stream` of the
library is a blocking generator, so it runs in a worker thread and its frames are bridged
to asyncio through a queue.
"""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator, Callable, Optional

import numpy as np

from backend.config import AppConfig
from backend.conversation.tts.base import TTS, TTSError
from backend.models import PCM_SAMPLE_WIDTH
from backend.utils.errors import summarize_error
from backend.utils.logging import get_logger

logger = get_logger("tts.zerotts")

_END = object()  # queue sentinel: the worker finished normally


def float_to_pcm16(frame: Any) -> bytes:
    """float32 audio in [-1, 1], shaped (n,) or (1, n) → Int16 LE PCM bytes (clipped)."""
    samples = np.asarray(frame, dtype=np.float32).reshape(-1)
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class ZeroTTSBackend(TTS):
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._model: Any = None
        self._normalize: Optional[Callable[[str], str]] = None
        self._sample_rate = 0
        # One worker: the ONNX session is never used by two sentences at once.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")

    def load(self) -> None:
        """Load ZeroTTS (downloads the weights on first use). Blocking."""
        try:
            from zerotts import ZeroTTS, normalize_vi_text

            model = ZeroTTS.from_pretrained(self.config.ZEROTTS_REPO)
            sample_rate = int(model.sample_rate)
        except Exception as e:
            raise TTSError(
                f"ZeroTTS could not load {self.config.ZEROTTS_REPO!r} (ZEROTTS_REPO). The "
                "weights are downloaded from Hugging Face on the first start, so the app "
                "needs network access to Hugging Face or weights already in its cache: "
                f"{self._summary(e)}"
            ) from e
        try:
            # A wrong voice name would otherwise fail every sentence at synthesis time.
            model.load_voice(self.config.ZEROTTS_VOICE)
        except Exception as e:
            raise TTSError(
                f"ZEROTTS_VOICE {self.config.ZEROTTS_VOICE!r} cannot be used: "
                f"{self._summary(e)}"
            ) from e

        self._model = model
        self._normalize = normalize_vi_text
        self._sample_rate = sample_rate
        logger.info({
            "msg": "ZeroTTS loaded",
            "repo": self.config.ZEROTTS_REPO,
            "voice": self.config.ZEROTTS_VOICE,
            "sample_rate": self._sample_rate,
        })

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _summary(self, error: Exception) -> str:
        return summarize_error(error, self.config.ERROR_SUMMARY_MAX_CHARS)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        if self._model is None:
            raise TTSError("ZeroTTS is not loaded")

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        stop = threading.Event()

        def worker() -> None:
            try:
                frames = self._model.synthesize_stream(
                    self._normalize(text), voice=self.config.ZEROTTS_VOICE
                )
                for frame in frames:
                    if stop.is_set():
                        return
                    loop.call_soon_threadsafe(queue.put_nowait, float_to_pcm16(frame))
                loop.call_soon_threadsafe(queue.put_nowait, _END)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, e)

        self._executor.submit(worker)

        # Aggregate the library's small frames to ~TTS_CHUNK_MS, on a sample boundary.
        chunk_bytes = (
            self._sample_rate * self.config.TTS_CHUNK_MS // 1000 * PCM_SAMPLE_WIDTH
        )
        pending = bytearray()
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    break
                if isinstance(item, Exception):
                    raise TTSError(f"ZeroTTS synthesis failed: {self._summary(item)}") from item
                pending += item
                while len(pending) >= chunk_bytes:
                    yield bytes(pending[:chunk_bytes])
                    del pending[:chunk_bytes]
            if pending:
                yield bytes(pending)
        finally:
            # Consumer finished, failed or was cancelled → stop the worker at its next frame.
            stop.set()
