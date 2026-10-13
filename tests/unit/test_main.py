import asyncio
import json
import runpy
import sys
import time
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi.testclient import TestClient

import backend.main as main
from backend.features.accounts.store import UsersStore
from backend.features.memory.store import MemoryStore
from backend.utils.database import Database
from tests.unit import fakes
from tests.unit.memory_fakes import CompletingLLM


def test_python_m_backend_main_serves_on_the_configured_host_and_port(monkeypatch):
    monkeypatch.setenv("groq_api_key", "test-key")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    served: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: served.update(options))

    monkeypatch.delitem(sys.modules, "backend.main", raising=False)  # run it fresh
    runpy.run_module("backend.main", run_name="__main__")

    assert served == {"host": "127.0.0.1", "port": 8123, "log_level": "warning"}


# ── Wiring: what main.py hands to the engine ──────────────────────────────

def test_the_real_engine_is_given_the_knowledge_base_the_memory_and_the_persona(config):
    """The modules are built, not loaded: no model, no camera, no network."""
    knowledge, memory, llm, persona = object(), object(), object(), object()

    orchestrator = main.build_orchestrator(config, knowledge, memory, llm, persona=persona)

    assert orchestrator._engine._knowledge is knowledge
    assert orchestrator._engine._memory is memory
    assert orchestrator._engine._persona is persona
    assert orchestrator._engine._llm is llm


def test_startup_builds_the_orchestrator_on_the_memory_of_the_app(config, monkeypatch):
    built = {}

    def build(config, knowledge, memory, llm, capture=None, persona=None):
        built.update(knowledge=knowledge, memory=memory, llm=llm, persona=persona)
        return fakes.build_orchestrator(config)

    monkeypatch.setattr(main, "build_orchestrator", build)

    with TestClient(main.create_app(config)) as client:
        assert built["memory"] is client.app.state.memory
        assert built["knowledge"] is client.app.state.knowledge
        assert isinstance(built["memory"], main.MemoryService)
        assert built["persona"] is client.app.state.persona
        assert isinstance(built["persona"], main.PersonaService)
        # One LLM for the turns, the imports and the reading of the conversations: they
        # share the state of its backup.
        assert built["llm"] is not None and client.app.state.imports._llm is built["llm"]
        assert client.app.state.memory._gathering._llm is built["llm"]


def test_the_real_orchestrator_is_given_the_memory_to_tell(config):
    knowledge, memory, llm = object(), object(), object()

    assert main.build_orchestrator(config, knowledge, memory, llm)._memory is memory


# ── Startup: the conversations an earlier run left unread ─────────────────

# An hour before the app starts, on the real clock the app reads.
STARTED = datetime.now(timezone.utc) - timedelta(hours=1)
SAID = "Tôi thích câu trả lời thật ngắn gọn."


def _left_by_an_earlier_run(config) -> None:
    """A conversation of a logged-in user whose process was killed before its close."""
    database = Database(config.DATABASE_URL)
    database.start()
    UsersStore(database).touch("linh", "linh", STARTED)
    MemoryStore(database).add_turn("linh", "killed", SAID, "Dạ.", 0.95, False, STARTED)
    database.close()


def _wait(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.01)


def test_startup_reads_the_conversations_left_unread_in_the_background(config, monkeypatch):
    _left_by_an_earlier_run(config)
    llm = CompletingLLM(
        [{"op": "add", "category": "preference", "text": "Tôi thích câu trả lời ngắn gọn.",
          "evidence": SAID}]
    )
    monkeypatch.setattr(main, "create_llm", lambda config: llm)

    with TestClient(main.create_app(config, fakes.build_orchestrator(config))) as client:
        store = client.app.state.memory._store
        _wait(lambda: store.list_items("linh"))

        (item,) = store.list_items("linh")
        assert (item.status, item.source, item.evidence) == ("pending", "conversation", SAID)
        assert store.list_turns("linh", "killed") == []
        assert store.unconsolidated_conversations(datetime.now(timezone.utc)) == []


def test_startup_does_not_wait_for_the_reading_and_shutdown_drops_it(config, monkeypatch):
    _left_by_an_earlier_run(config)
    llm = CompletingLLM(json.dumps([]))
    hanging = []

    async def complete(system, user):  # never answers
        hanging.append(user)
        await asyncio.Event().wait()

    llm.complete = complete
    monkeypatch.setattr(main, "create_llm", lambda config: llm)

    with TestClient(main.create_app(config, fakes.build_orchestrator(config))) as client:
        assert client.get("/health").status_code == 200  # ready while the LLM hangs
        _wait(lambda: hanging)
    # The app has shut down: the reading was cancelled, the conversation stays to be read.

    database = Database(config.DATABASE_URL)
    database.start()
    try:
        store = MemoryStore(database)
        assert len(store.list_turns("linh", "killed")) == 1
        assert store.unconsolidated_conversations(datetime.now(timezone.utc)) == [("linh", "killed")]
    finally:
        database.close()
