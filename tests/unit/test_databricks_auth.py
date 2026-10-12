import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from openai import OpenAIError

from backend.config import AppConfig
from backend.providers import databricks
from backend.providers.databricks import DatabricksAuthError, ProfileToken, databricks_api_key


class FakeCli:
    """Stands in for asyncio.create_subprocess_exec: replays scripted CLI results."""

    def __init__(self, *results):
        self.results = list(results)
        self.commands: list[tuple] = []

    async def __call__(self, *command, **kwargs):
        self.commands.append(command)
        returncode, stdout, stderr = self.results.pop(0)

        class Process:
            async def communicate(_):
                return stdout.encode(), stderr.encode()

        Process.returncode = returncode
        return Process()


def _token(value: str, expires_in_sec: float) -> tuple[int, str, str]:
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in_sec)
    payload = {"access_token": value, "token_type": "Bearer", "expiry": expiry.isoformat()}
    return 0, json.dumps(payload), ""


@pytest.fixture
def cli(monkeypatch):
    def install(*results) -> FakeCli:
        fake = FakeCli(*results)
        monkeypatch.setattr(databricks.asyncio, "create_subprocess_exec", fake)
        return fake

    return install


def test_static_token_wins_over_the_profile():
    config = AppConfig(DATABRICKS_TOKEN="dapi-token", DATABRICKS_PROFILE="my-profile")

    assert databricks_api_key(config) == "dapi-token"


def test_profile_is_used_when_there_is_no_static_token():
    key = databricks_api_key(AppConfig(DATABRICKS_PROFILE="my-profile"))

    assert isinstance(key, ProfileToken)


async def test_token_is_fetched_from_the_cli_once_and_reused(cli):
    fake = cli(_token("first", 3600))
    token = ProfileToken("my-profile", refresh_margin_sec=120)

    assert await token() == "first"
    assert await token() == "first"
    assert fake.commands == [("databricks", "auth", "token", "--profile", "my-profile")]


async def test_token_inside_the_refresh_margin_is_renewed(cli):
    fake = cli(_token("first", 60), _token("second", 3600))
    token = ProfileToken("my-profile", refresh_margin_sec=120)

    assert await token() == "first"
    assert await token() == "second"
    assert len(fake.commands) == 2


async def test_concurrent_requests_share_one_cli_call(cli):
    fake = cli(_token("first", 3600))
    token = ProfileToken("my-profile", refresh_margin_sec=120)

    assert await asyncio.gather(token(), token(), token()) == ["first"] * 3
    assert len(fake.commands) == 1


async def test_unknown_expiry_is_not_cached(cli):
    fake = cli((0, json.dumps({"access_token": "a"}), ""), (0, json.dumps({"access_token": "b"}), ""))
    token = ProfileToken("my-profile", refresh_margin_sec=120)

    assert [await token(), await token()] == ["a", "b"]
    assert len(fake.commands) == 2


@pytest.mark.parametrize(
    ("result", "match"),
    [
        ((1, "", "Error: cache: refresh token\n is invalid"), "refresh token is invalid"),
        ((0, "not json", ""), "Unexpected output"),
        ((0, json.dumps({"expiry": "x"}), ""), "Unexpected output"),
    ],
)
async def test_cli_failure_raises_an_auth_error_the_backends_catch(cli, result, match):
    cli(result)

    with pytest.raises(DatabricksAuthError, match=match) as raised:
        await ProfileToken("my-profile", refresh_margin_sec=120)()

    assert isinstance(raised.value, OpenAIError)


async def test_missing_cli_raises_an_auth_error(monkeypatch):
    async def missing(*command, **kwargs):
        raise FileNotFoundError("databricks")

    monkeypatch.setattr(databricks.asyncio, "create_subprocess_exec", missing)

    with pytest.raises(DatabricksAuthError, match="Cannot run"):
        await ProfileToken("my-profile", refresh_margin_sec=120)()
