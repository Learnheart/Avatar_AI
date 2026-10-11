"""Dataclass fields that read their value from the environment.

Each key of `backend/config.py` and `backend/tuning.py` is declared once:
`X: float = env_float("X", 0.5)`. The environment wins over the default; an explicit
constructor argument wins over both.
"""
import os
from dataclasses import field
from typing import Any, Callable


def env_str(
    key: str, default: str = "", *, normalize: Callable[[str], str] = str, repr: bool = True
) -> Any:
    """`normalize` is applied to the value read (e.g. `str.lower`). `repr=False` keeps a
    secret out of repr(config), so out of logs and tracebacks."""
    return field(
        default_factory=lambda: normalize(os.environ.get(key, default)), repr=repr
    )


def env_int(key: str, default: int) -> Any:
    """An unparseable value falls back to the default."""
    return field(default_factory=lambda: _parse(int, key, default))


def env_float(key: str, default: float) -> Any:
    """An unparseable value falls back to the default."""
    return field(default_factory=lambda: _parse(float, key, default))


def _parse(parse: Callable[[Any], Any], key: str, default: Any) -> Any:
    try:
        return parse(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default
