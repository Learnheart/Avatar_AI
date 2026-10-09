import runpy
import sys

import uvicorn


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
