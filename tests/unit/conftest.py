"""Shared fixtures: unit tests never read the developer's .env values."""
from dataclasses import fields

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.config import AppConfig
from backend.utils import telemetry

_ENV_KEYS = [f.name for f in fields(AppConfig)] + ["groq_api_key"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def config(tmp_path) -> AppConfig:
    # The knowledge base of a test lives in its own temporary directory.
    return AppConfig(GROQ_API_KEY="test-key", KNOWLEDGE_DB_PATH=str(tmp_path / "knowledge.db"))


# Telemetry is installed once per process (OpenTelemetry allows no second provider):
# every test shares these in-memory sinks, and nothing is sent anywhere.
_SPANS = InMemorySpanExporter()
_METRICS = InMemoryMetricReader()
telemetry.setup(
    AppConfig(OTLP_ENDPOINT=""),
    span_processors=[SimpleSpanProcessor(_SPANS)],
    metric_readers=[_METRICS],
)


@pytest.fixture
def spans() -> InMemorySpanExporter:
    """The spans finished during the test: `spans.get_finished_spans()`."""
    _SPANS.clear()
    return _SPANS


@pytest.fixture
def metric_points():
    """Reads the data points recorded so far for a metric: `metric_points(name)`."""
    def read(name: str) -> list:
        data = _METRICS.get_metrics_data()
        return [
            point
            for resource in (data.resource_metrics if data else [])
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == name
            for point in metric.data.data_points
        ]

    return read
