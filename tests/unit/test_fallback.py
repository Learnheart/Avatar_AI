import asyncio

import pytest

from backend.config import AppConfig
from backend.core.contracts import LLM, STT, Embedder, EmbeddingError, LLMError, STTError
from backend.models import ChatTurn, Transcript
from backend.providers import catalog, fallback
from backend.providers.catalog import create_llm, create_stt
from backend.providers.fallback import FallbackEmbedder, FallbackLLM, FallbackSTT


class ScriptedSTT(STT):
    def __init__(self, text="", error=None, delay=0.0):
        self.text, self.error, self.delay = text, error, delay
        self.calls = 0

    async def transcribe(self, pcm16, sample_rate):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return Transcript(text=self.text, language="vi")


class ScriptedLLM(LLM):
    def __init__(self, deltas=(), error=None, delay=0.0):
        self.deltas, self.error, self.delay = list(deltas), error, delay
        self.calls = 0
        self.requests: list[tuple] = []
        self.closed = False

    async def stream_answer(self, user_text, history=(), profile=""):
        self.calls += 1
        self.requests.append((user_text, history, profile))
        try:
            await asyncio.sleep(self.delay)
            for delta in self.deltas:
                yield delta
            if self.error:
                raise self.error
        finally:
            self.closed = True


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(FALLBACK_PRIMARY_TIMEOUT_SEC=0.05, FALLBACK_COOLDOWN_SEC=300)


@pytest.fixture
def clock(monkeypatch):
    """Controls the cooldown clock without touching the event loop's."""
    now = [1000.0]
    monkeypatch.setattr(fallback.time, "monotonic", lambda: now[0])
    return now


async def _answer(llm: LLM) -> list[str]:
    return [delta async for delta in llm.stream_answer("Xin chào")]


# ── STT ───────────────────────────────────────────────────────────────────

async def test_stt_healthy_primary_answers_and_the_backup_is_not_called(config):
    primary, backup = ScriptedSTT("từ primary"), ScriptedSTT("từ backup")

    transcript = await FallbackSTT(config, primary, backup).transcribe(b"\x01\x00", 16000)

    assert transcript.text == "từ primary"
    assert backup.calls == 0


@pytest.mark.parametrize(
    "primary", [ScriptedSTT(error=STTError("503")), ScriptedSTT("late", delay=5)]
)
async def test_stt_backup_takes_over_on_error_or_timeout(config, primary):
    backup = ScriptedSTT("từ backup")

    transcript = await FallbackSTT(config, primary, backup).transcribe(b"\x01\x00", 16000)

    assert transcript.text == "từ backup"


async def test_stt_primary_is_skipped_during_the_cooldown_then_retried(config, clock):
    primary, backup = ScriptedSTT(error=STTError("503")), ScriptedSTT("từ backup")
    stt = FallbackSTT(config, primary, backup)

    await stt.transcribe(b"\x01\x00", 16000)
    await stt.transcribe(b"\x01\x00", 16000)
    assert (primary.calls, backup.calls) == (1, 2)

    clock[0] += config.FALLBACK_COOLDOWN_SEC + 1
    primary.error, primary.text = None, "từ primary"
    assert (await stt.transcribe(b"\x01\x00", 16000)).text == "từ primary"
    assert (primary.calls, backup.calls) == (2, 2)


async def test_stt_bad_input_is_not_a_reason_to_fall_back(config):
    primary, backup = ScriptedSTT(error=ValueError("44100")), ScriptedSTT("từ backup")
    stt = FallbackSTT(config, primary, backup)

    with pytest.raises(ValueError, match="44100"):
        await stt.transcribe(b"\x01\x00", 44100)

    assert backup.calls == 0
    assert stt._state.available()


async def test_stt_error_of_the_backup_reaches_the_caller(config):
    stt = FallbackSTT(
        config, ScriptedSTT(error=STTError("503")), ScriptedSTT(error=STTError("backup down"))
    )

    with pytest.raises(STTError, match="backup down"):
        await stt.transcribe(b"\x01\x00", 16000)


# ── LLM ───────────────────────────────────────────────────────────────────

async def test_llm_healthy_primary_streams_and_the_backup_is_not_called(config):
    primary, backup = ScriptedLLM(["Xin ", "chào."]), ScriptedLLM(["backup"])

    assert await _answer(FallbackLLM(config, primary, backup)) == ["Xin ", "chào."]
    assert backup.calls == 0
    assert primary.closed


@pytest.mark.parametrize(
    "primary", [ScriptedLLM(error=LLMError("503")), ScriptedLLM(["late"], delay=5)]
)
async def test_llm_backup_takes_over_before_the_first_delta(config, primary):
    backup = ScriptedLLM(["Từ ", "backup."])

    assert await _answer(FallbackLLM(config, primary, backup)) == ["Từ ", "backup."]
    assert primary.closed


async def test_llm_history_and_profile_reach_the_backup_unchanged(config):
    history = (ChatTurn("Lãi suất?", ("Năm phần trăm.",)),)
    primary, backup = ScriptedLLM(error=LLMError("down")), ScriptedLLM(["backup"])
    llm = FallbackLLM(config, primary, backup)

    assert [d async for d in llm.stream_answer("còn gì nữa?", history, "Tên: Linh")] == ["backup"]

    assert primary.requests == backup.requests == [("còn gì nữa?", history, "Tên: Linh")]


async def test_llm_failure_after_the_first_delta_is_not_replayed_by_the_backup(config):
    primary = ScriptedLLM(["Xin "], error=LLMError("reset"))
    backup = ScriptedLLM(["backup"])
    llm = FallbackLLM(config, primary, backup)

    received: list[str] = []
    with pytest.raises(LLMError, match="reset"):
        async for delta in llm.stream_answer("Xin chào"):
            received.append(delta)

    assert received == ["Xin "]
    assert backup.calls == 0


async def test_llm_only_the_first_delta_is_bounded(config):
    class SlowSecondDelta(LLM):
        async def stream_answer(self, user_text, history=(), profile=""):
            yield "Xin "
            await asyncio.sleep(config.FALLBACK_PRIMARY_TIMEOUT_SEC * 3)
            yield "chào."

    llm = FallbackLLM(config, SlowSecondDelta(), ScriptedLLM(["backup"]))

    assert await _answer(llm) == ["Xin ", "chào."]


async def test_llm_empty_primary_answer_is_not_a_failure(config):
    primary, backup = ScriptedLLM([]), ScriptedLLM(["backup"])
    llm = FallbackLLM(config, primary, backup)

    assert await _answer(llm) == []
    assert backup.calls == 0
    assert llm._state.available()


async def test_llm_primary_is_skipped_during_the_cooldown_then_retried(config, clock):
    primary, backup = ScriptedLLM(error=LLMError("503")), ScriptedLLM(["backup"])
    llm = FallbackLLM(config, primary, backup)

    await _answer(llm)
    await _answer(llm)
    assert (primary.calls, backup.calls) == (1, 2)

    clock[0] += config.FALLBACK_COOLDOWN_SEC + 1
    primary.error, primary.deltas = None, ["primary"]
    assert await _answer(llm) == ["primary"]


async def test_llm_closing_the_answer_closes_the_backend_stream(config):
    primary = ScriptedLLM(["Xin ", "chào."])
    stream = FallbackLLM(config, primary, ScriptedLLM(["backup"])).stream_answer("Xin chào")

    assert await anext(stream) == "Xin "
    await stream.aclose()

    assert primary.closed


# ── Catalog ───────────────────────────────────────────────────────────────

@pytest.fixture
def backends(monkeypatch):
    for table in (catalog._LLM_BACKENDS, catalog._STT_BACKENDS):
        monkeypatch.setitem(table, "one", lambda config: "one")
        monkeypatch.setitem(table, "two", lambda config: "two")


def test_backup_backends_wrap_the_primary(backends):
    config = AppConfig(
        LLM_BACKEND="one", LLM_BACKEND_BACKUP="two", STT_BACKEND="one", STT_BACKEND_BACKUP="two"
    )

    llm, stt = create_llm(config), create_stt(config)

    assert isinstance(llm, FallbackLLM) and (llm._primary, llm._backup) == ("one", "two")
    assert isinstance(stt, FallbackSTT) and (stt._primary, stt._backup) == ("one", "two")


@pytest.mark.parametrize("backup", ["", "one"])
def test_no_backup_or_the_same_backend_builds_the_primary_alone(backends, backup):
    config = AppConfig(
        LLM_BACKEND="one", LLM_BACKEND_BACKUP=backup, STT_BACKEND="one", STT_BACKEND_BACKUP=backup
    )

    assert (create_llm(config), create_stt(config)) == ("one", "one")


def test_unknown_backup_raises_value_error(backends):
    with pytest.raises(ValueError, match="nope"):
        create_llm(AppConfig(LLM_BACKEND="one", LLM_BACKEND_BACKUP="nope"))
    with pytest.raises(ValueError, match="nope"):
        create_stt(AppConfig(STT_BACKEND="one", STT_BACKEND_BACKUP="nope"))


# ── Embedding ─────────────────────────────────────────────────────────────

class ScriptedEmbedder(Embedder):
    def __init__(self, value=0.0, error=None, hang=False, signature="model"):
        self.value, self.error, self.hang = value, error, hang
        self._signature = signature
        self.calls = 0

    @property
    def signature(self):
        return self._signature

    async def embed(self, texts):
        self.calls += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return [[self.value] for _ in texts]


async def test_a_healthy_primary_embeds_and_the_backup_is_not_called(config, clock):
    primary, backup = ScriptedEmbedder(1.0, signature="primary"), ScriptedEmbedder(2.0)
    embedder = FallbackEmbedder(config, primary, backup)

    assert await embedder.embed(["a", "b"]) == [[1.0], [1.0]]
    assert backup.calls == 0
    assert embedder.signature == "primary"


async def test_the_backup_embeds_when_the_primary_fails(config, clock):
    primary = ScriptedEmbedder(error=EmbeddingError("token expired"), signature="primary")
    backup = ScriptedEmbedder(2.0, signature="backup")
    embedder = FallbackEmbedder(config, primary, backup)

    assert await embedder.embed(["a"]) == [[2.0]]
    # Same index: the signature does not follow the backend that answered.
    assert embedder.signature == "primary"


async def test_a_failed_embedding_primary_is_skipped_then_tried_again(config, clock):
    primary, backup = ScriptedEmbedder(error=EmbeddingError("down")), ScriptedEmbedder(2.0)
    embedder = FallbackEmbedder(config, primary, backup)

    await embedder.embed(["a"])
    await embedder.embed(["a"])
    assert (primary.calls, backup.calls) == (1, 2)

    clock[0] += config.FALLBACK_COOLDOWN_SEC + 1
    primary.error, primary.value = None, 1.0
    assert await embedder.embed(["a"]) == [[1.0]]
    assert (primary.calls, backup.calls) == (2, 2)


async def test_a_caller_giving_up_on_a_hung_primary_sends_the_next_requests_to_the_backup(
    config,
):
    # No patched clock here: the event loop needs the real one for the caller's timeout.
    primary, backup = ScriptedEmbedder(hang=True), ScriptedEmbedder(2.0)
    embedder = FallbackEmbedder(config, primary, backup)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(embedder.embed(["a"]), timeout=0.05)
    assert backup.calls == 0

    assert await embedder.embed(["a"]) == [[2.0]]
    assert primary.calls == 1


async def test_the_error_of_the_embedding_backup_reaches_the_caller(config, clock):
    embedder = FallbackEmbedder(
        config,
        ScriptedEmbedder(error=EmbeddingError("primary down")),
        ScriptedEmbedder(error=EmbeddingError("backup down")),
    )

    with pytest.raises(EmbeddingError, match="backup down"):
        await embedder.embed(["a"])
