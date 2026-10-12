import json
import logging

from backend.config import AppConfig
from backend.utils import telemetry
from backend.utils.logging import JsonFormatter
from opentelemetry import trace

from backend.utils.telemetry import SpanReporter, detached_span, otlp_exporters, tracer


class _Lines:
    """Collects what the `trace` logger writes while a test runs."""

    def __init__(self) -> None:
        self.lines: list[dict] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.lines.append(record.msg)

    def __enter__(self) -> list[dict]:
        telemetry.logger.addHandler(self._handler)
        return self.lines

    def __exit__(self, *exc) -> None:
        telemetry.logger.removeHandler(self._handler)


def _format(message: dict) -> dict:
    record = logging.LogRecord("avatar.engine", logging.INFO, "", 0, message, None, None)
    return json.loads(JsonFormatter().format(record))


def test_a_finished_span_is_logged_with_its_duration_and_attributes(spans):
    with _Lines() as lines:
        with tracer.start_as_current_span("stt") as span:
            span.set_attribute("stt.text", "xin chào")
            span.add_event("primary_backend_failed")

    (line,) = [entry for entry in lines if entry["msg"] == "Span"]
    (finished,) = spans.get_finished_spans()
    assert line["span"] == "stt"
    assert line["stt.text"] == "xin chào"
    assert line["events"] == ["primary_backend_failed"]
    assert line["duration_ms"] >= 0
    assert line["trace_id"] == format(finished.context.trace_id, "032x")


def test_a_long_value_is_cut_in_the_log_and_whole_in_the_span(spans):
    prompt = "a" * 500
    with _Lines() as lines:
        with tracer.start_as_current_span("llm") as span:
            span.set_attribute("llm.prompt", prompt)

    limit = AppConfig().TRACE_LOG_VALUE_MAX_CHARS
    assert lines[-1]["llm.prompt"] == "a" * limit + f"… (+{500 - limit} chars)"
    assert spans.get_finished_spans()[0].attributes["llm.prompt"] == prompt


def test_the_span_reporter_cuts_at_its_own_limit():
    assert SpanReporter(3)._cut("abcdef") == "abc… (+3 chars)"
    assert SpanReporter(3)._cut("abc") == "abc"
    assert SpanReporter(3)._cut(12345) == 12345


def test_a_failed_span_is_logged_with_its_error(spans):
    with _Lines() as lines:
        try:
            with detached_span("tts"):
                raise ValueError("no voice")
        except ValueError:
            pass

    assert lines[-1]["error"] == "ValueError: no voice"
    assert not spans.get_finished_spans()[0].status.is_ok


def test_a_detached_span_is_a_child_but_not_the_current_span(spans):
    with tracer.start_as_current_span("turn") as turn:
        with detached_span("llm", **{"llm.prompt": "hỏi"}) as llm:
            assert telemetry.trace.get_current_span() is turn
        assert llm.parent.span_id == turn.get_span_context().span_id
        assert llm.attributes["llm.prompt"] == "hỏi"


def test_a_log_line_inside_a_span_carries_its_trace_id():
    with tracer.start_as_current_span("turn") as span:
        inside = _format({"msg": "Transcript"})
    outside = _format({"msg": "Camera opened"})

    assert inside["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert "trace_id" not in outside


def test_the_duration_of_every_span_is_a_metric(spans, metric_points):
    with tracer.start_as_current_span("unit-test-stage"):
        pass

    stages = {point.attributes["stage"] for point in metric_points("avatar.stage.duration")}
    assert "unit-test-stage" in stages


def test_nothing_is_exported_without_an_endpoint():
    assert otlp_exporters(AppConfig(OTLP_ENDPOINT="")) is None


def test_an_endpoint_gets_a_trace_and_a_metric_exporter_with_the_headers(monkeypatch):
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda **options: ("traces", options))
    monkeypatch.setattr(telemetry, "OTLPMetricExporter", lambda **options: ("metrics", options))
    headers = {"x-api-key": "secret", "team": "kiosk"}

    exporters = otlp_exporters(AppConfig(
        OTLP_ENDPOINT="http://collector.test:4318/", OTLP_HEADERS="x-api-key=secret, team = kiosk"
    ))

    assert exporters == (
        ("traces", {"endpoint": "http://collector.test:4318/v1/traces", "headers": headers}),
        ("metrics", {"endpoint": "http://collector.test:4318/v1/metrics", "headers": headers}),
    )


def test_the_otlp_headers_never_appear_in_repr():
    assert "secret" not in repr(AppConfig(OTLP_HEADERS="x-api-key=secret"))


def test_spans_of_the_web_framework_are_not_logged_as_steps(spans):
    with _Lines() as lines:
        with trace.get_tracer("fastapi").start_as_current_span("GET /health"):
            pass

    assert [span.name for span in spans.get_finished_spans()] == ["GET /health"]  # still exported
    assert lines == []
