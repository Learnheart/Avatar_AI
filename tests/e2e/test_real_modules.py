"""Module smoke tests against the real Groq API and the real ZeroTTS model."""
import asyncio
import time

import numpy as np
import pytest

from backend.config import AppConfig
from backend.conversation.llm.groq_llm import GroqLLM
from backend.conversation.stt.groq_whisper import GroqWhisperSTT
from backend.conversation.factory import create_tts
from backend.models import MIC_SAMPLE_RATE
from tests.e2e.helpers import E2E_MARKS, looks_vietnamese

pytestmark = E2E_MARKS

LATENCY_BUDGET_SEC = 1.5  # quality-gates §1.3 / §1.4


@pytest.fixture(scope="module")
def config() -> AppConfig:
    app_config = AppConfig()
    app_config.validate()
    return app_config


async def test_stt_transcribes_vietnamese(config, utterance_pcm):
    stt = GroqWhisperSTT(config)
    await stt.transcribe(utterance_pcm, MIC_SAMPLE_RATE)  # warm-up: cold start is excluded

    started = time.perf_counter()
    transcript = await stt.transcribe(utterance_pcm, MIC_SAMPLE_RATE)
    elapsed = time.perf_counter() - started

    print(f"\n[STT] {elapsed:.2f}s  text={transcript.text!r}")
    assert transcript.text
    assert looks_vietnamese(transcript.text)
    assert elapsed < LATENCY_BUDGET_SEC


async def test_llm_streams_a_vietnamese_answer(config):
    llm = GroqLLM(config)
    started = time.perf_counter()
    first_token_at = None
    deltas: list[str] = []

    async for delta in llm.stream_answer("Lãi suất tiết kiệm kỳ hạn sáu tháng là bao nhiêu?"):
        if first_token_at is None:
            first_token_at = time.perf_counter() - started
        deltas.append(delta)

    answer = "".join(deltas)
    print(f"\n[LLM] first token {first_token_at:.2f}s  deltas={len(deltas)}  answer={answer!r}")
    assert looks_vietnamese(answer)
    assert len(deltas) > 1, "the answer must stream incrementally, not arrive as one blob"
    assert first_token_at < LATENCY_BUDGET_SEC


async def test_tts_synthesizes_non_silent_pcm(config):
    tts = create_tts(config)
    await asyncio.to_thread(tts.load)

    started = time.perf_counter()
    first_chunk_at = None
    chunks: list[bytes] = []
    async for chunk in tts.synthesize_stream("Xin chào quý khách, tôi có thể giúp gì ạ?"):
        if first_chunk_at is None:
            first_chunk_at = time.perf_counter() - started
        chunks.append(chunk)

    pcm = b"".join(chunks)
    samples = np.frombuffer(pcm, dtype="<i2")
    duration = len(samples) / tts.sample_rate
    print(
        f"\n[TTS] first chunk {first_chunk_at:.2f}s  total {time.perf_counter() - started:.2f}s"
        f"  audio {duration:.2f}s @ {tts.sample_rate} Hz  chunks={len(chunks)}"
    )
    assert tts.sample_rate > 0
    assert all(len(chunk) % 2 == 0 for chunk in chunks)
    assert duration > 0.5
    assert np.abs(samples.astype(np.int32)).max() > 1000, "audio is silent"
