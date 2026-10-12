"""
Credential of the Databricks AI Gateway backends (LLM and STT).

Either the static `DATABRICKS_TOKEN`, or the OAuth token of a Databricks CLI profile
(`DATABRICKS_PROFILE`, after `databricks auth login --profile <name>`). A profile token
lives about an hour, so it is fetched from the CLI and renewed before it expires.
"""
import asyncio
import json
import time
from datetime import datetime
from typing import Awaitable, Callable, Union

from openai import OpenAIError

from backend.config import AppConfig

_CLI = "databricks"


class DatabricksAuthError(OpenAIError):
    """No token could be obtained from the CLI profile. An OpenAIError, so the backends
    report it like any other failed request (LLMError / STTError)."""


class ProfileToken:
    """Async callable returning the profile's current token, as the OpenAI client expects."""

    def __init__(self, profile: str, refresh_margin_sec: float) -> None:
        self._profile = profile
        self._margin = refresh_margin_sec
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            if not self._token or time.time() >= self._expires_at - self._margin:
                self._token, self._expires_at = await self._fetch()
            return self._token

    async def _fetch(self) -> tuple[str, float]:
        try:
            process = await asyncio.create_subprocess_exec(
                _CLI, "auth", "token", "--profile", self._profile,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            raise DatabricksAuthError(f"Cannot run the `{_CLI}` CLI: {e}") from e
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            reason = " ".join(stderr.decode(errors="replace").split())
            raise DatabricksAuthError(
                f"`{_CLI} auth token --profile {self._profile}` failed: {reason}"
            )
        try:
            payload = json.loads(stdout)
            token = payload["access_token"]
        except (ValueError, KeyError, TypeError) as e:
            raise DatabricksAuthError(f"Unexpected output of `{_CLI} auth token`") from e
        try:
            expires_at = datetime.fromisoformat(payload["expiry"]).timestamp()
        except (ValueError, KeyError, TypeError):
            expires_at = 0.0  # unknown expiry: ask the CLI again on the next request
        return token, expires_at


def databricks_api_key(config: AppConfig) -> Union[str, Callable[[], Awaitable[str]]]:
    """`DATABRICKS_TOKEN` if set, else the token of `DATABRICKS_PROFILE`."""
    if config.DATABRICKS_TOKEN:
        return config.DATABRICKS_TOKEN
    return ProfileToken(config.DATABRICKS_PROFILE, config.DATABRICKS_TOKEN_REFRESH_MARGIN_SEC)
