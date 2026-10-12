"""Structured JSON logger for all backend modules."""
import logging
import json
import sys
from datetime import datetime, timezone

from opentelemetry import trace

# Every backend logger is a child of this one, so the handler and level live in one place.
_ROOT_NAME = "avatar"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name.removeprefix(_ROOT_NAME + "."),
        }
        # Inside a span (a turn, an upload): the id that ties the line to its trace.
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            payload["trace_id"] = trace.format_trace_id(context.trace_id)

        # A dict message is flattened into the payload: logger.info({"msg": ..., "key": val}).
        raw = record.msg
        if isinstance(raw, dict):
            payload.update(raw)
        else:
            payload["msg"] = record.getMessage()

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _root_logger() -> logging.Logger:
    root = logging.getLogger(_ROOT_NAME)
    if not root.handlers:
        root.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        root.propagate = False
    return root


def configure_logging(level: str) -> None:
    """Apply LOG_LEVEL to every backend logger. Called once at startup."""
    _root_logger().setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return _root_logger().getChild(name)
