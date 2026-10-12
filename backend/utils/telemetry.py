"""
Traces and metrics of the backend (OpenTelemetry).

`tracer` and `meter` are used anywhere; `setup()` decides, once per process, where the
data goes: to an OTLP backend when `OTLP_ENDPOINT` is set, and always to the log — each
finished span is one `Span` log line, so the steps of a turn can be read with no backend.

Spans inside an async generator are opened with `detached_span`: a span made "current"
across a `yield` is detached in another context and corrupts the trace.
"""
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, StatusCode

from backend.config import AppConfig
from backend.utils.logging import get_logger

logger = get_logger("trace")

_NAME = "avatar"
_NS_PER_MS = 1_000_000

# Bound to the providers installed by setup(); usable (as no-ops) before it.
tracer = trace.get_tracer(_NAME)
meter = metrics.get_meter(_NAME)

# Duration of every span, by span name (`stage`), plus the points inside a stage that
# matter on their own: llm_first_token, tts_first_chunk, turn_first_audio.
stage_duration = meter.create_histogram(
    "avatar.stage.duration", unit="ms", description="Duration of a step of a turn"
)
# Processing time of the continuous loops, by `stage`: tracking (per frame), vad (per chunk).
perception_duration = meter.create_histogram(
    "avatar.perception.duration", unit="ms", description="Processing time per frame / audio chunk"
)
frames_tracked = meter.create_counter(
    "avatar.perception.frames", description="Camera frames tracked, by `present`"
)
speech_events = meter.create_counter(
    "avatar.perception.speech_events", description="VAD events, by `event`"
)
audio_chunks_dropped = meter.create_counter(
    "avatar.audio.chunks_dropped", description="Malformed microphone chunks dropped"
)
turns = meter.create_counter("avatar.turns", description="Turns, by `outcome`")
capture_dropped = meter.create_counter(
    "avatar.capture.dropped", description="Capture items dropped (queue full), by `kind`"
)

_tracer_provider: Optional[TracerProvider] = None
_meter_provider: Optional[MeterProvider] = None


def is_own_span(span: ReadableSpan) -> bool:
    """True for a span of this backend. FastAPI opens spans of its own for every HTTP
    request and WebSocket (`GET /health`, `fastapi.endpoint`, ...): they are exported
    with the rest, but are not steps of the pipeline."""
    scope = span.instrumentation_scope
    return scope is not None and scope.name == _NAME


class SpanReporter(SpanProcessor):
    """Logs every finished span of the backend and records its duration as a metric."""

    def __init__(self, value_max_chars: int) -> None:
        self._max = value_max_chars

    def on_end(self, span: ReadableSpan) -> None:
        if not is_own_span(span):
            return
        duration_ms = (span.end_time - span.start_time) / _NS_PER_MS
        stage_duration.record(duration_ms, {"stage": span.name})

        line: dict[str, Any] = {
            "msg": "Span",
            "span": span.name,
            "trace_id": trace.format_trace_id(span.context.trace_id),
            "duration_ms": round(duration_ms, 1),
        }
        if span.status.status_code is StatusCode.ERROR:
            line["error"] = self._cut(span.status.description or "error")
        for key, value in (span.attributes or {}).items():
            line[key] = self._cut(value)
        if span.events:
            line["events"] = [event.name for event in span.events]
        logger.info(line)

    def _cut(self, value: Any) -> Any:
        """The log shows the start of a long text; the full value is in the span."""
        if isinstance(value, str) and len(value) > self._max:
            return value[: self._max] + f"… (+{len(value) - self._max} chars)"
        return value


def _otlp_headers(raw: str) -> dict[str, str]:
    """`key=value,key=value` → dict."""
    pairs = (pair.split("=", 1) for pair in raw.split(",") if "=" in pair)
    return {key.strip(): value.strip() for key, value in pairs}


def otlp_exporters(config: AppConfig) -> Optional[tuple[OTLPSpanExporter, OTLPMetricExporter]]:
    """The exporters to the backend at OTLP_ENDPOINT; None when there is none."""
    endpoint = config.OTLP_ENDPOINT.rstrip("/")
    if not endpoint:
        return None
    headers = _otlp_headers(config.OTLP_HEADERS)
    return (
        OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers),
        OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers),
    )


def setup(
    config: AppConfig,
    *,
    span_processors: Sequence[SpanProcessor] = (),
    metric_readers: Sequence[MetricReader] = (),
) -> None:
    """Install the providers. The first call of the process wins: OpenTelemetry does not
    let a provider be replaced, so later calls (another app in the same process) do
    nothing. The extra processors and readers are for tests."""
    global _tracer_provider, _meter_provider
    if _tracer_provider is not None:
        return

    resource = Resource.create({"service.name": config.SERVICE_NAME})
    readers = list(metric_readers)
    _tracer_provider = TracerProvider(resource=resource)
    _tracer_provider.add_span_processor(SpanReporter(config.TRACE_LOG_VALUE_MAX_CHARS))
    for processor in span_processors:
        _tracer_provider.add_span_processor(processor)

    exporters = otlp_exporters(config)
    if exporters:
        span_exporter, metric_exporter = exporters
        # Batched and sent from a background thread: a slow backend never holds a turn.
        _tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        readers.append(PeriodicExportingMetricReader(
            metric_exporter, export_interval_millis=config.METRIC_EXPORT_INTERVAL_SEC * 1000
        ))
    _meter_provider = MeterProvider(resource=resource, metric_readers=readers)

    trace.set_tracer_provider(_tracer_provider)
    metrics.set_meter_provider(_meter_provider)
    logger.info({"msg": "Telemetry ready", "otlp_endpoint": config.OTLP_ENDPOINT or None})


def add_span_processor(processor: SpanProcessor) -> None:
    """Attach one more consumer of finished spans (the capture store). After setup()."""
    if _tracer_provider is not None:
        _tracer_provider.add_span_processor(processor)


def flush() -> None:
    """Send what is still buffered. Called at shutdown."""
    if _tracer_provider is not None:
        _tracer_provider.force_flush()
    if _meter_provider is not None:
        _meter_provider.force_flush()


@contextmanager
def detached_span(name: str, **attributes: Any) -> Iterator[Span]:
    """A child of the current span that is not made current — the form to use inside an
    async generator. An exception marks the span failed; it always ends."""
    span = tracer.start_span(name, attributes=attributes)
    try:
        yield span
    except Exception as e:
        fail(span, e)
        raise
    finally:
        span.end()


def fail(span: Span, error: Exception) -> None:
    """Mark a span failed by an error that is handled and not re-raised."""
    span.record_exception(error)
    span.set_status(StatusCode.ERROR, f"{type(error).__name__}: {error}")
