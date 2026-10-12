import runpy
import sys

import uvicorn
from fastapi.testclient import TestClient

import backend.main as main
from tests.unit import fakes


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

def test_the_real_engine_is_given_the_knowledge_base_and_the_memory(config):
    """The modules are built, not loaded: no model, no camera, no network."""
    knowledge, memory, llm = object(), object(), object()

    orchestrator = main.build_orchestrator(config, knowledge, memory, llm)

    assert orchestrator._engine._knowledge is knowledge
    assert orchestrator._engine._memory is memory
    assert orchestrator._engine._llm is llm


def test_startup_builds_the_orchestrator_on_the_memory_of_the_app(config, monkeypatch):
    built = {}

    def build(config, knowledge, memory, llm, capture=None):
        built.update(knowledge=knowledge, memory=memory, llm=llm)
        return fakes.build_orchestrator(config)

    monkeypatch.setattr(main, "build_orchestrator", build)

    with TestClient(main.create_app(config)) as client:
        assert built["memory"] is client.app.state.memory
        assert built["knowledge"] is client.app.state.knowledge
        assert isinstance(built["memory"], main.MemoryService)
        # One LLM for the turns and the imports: they share the state of its backup.
        assert built["llm"] is not None and client.app.state.imports._llm is built["llm"]
