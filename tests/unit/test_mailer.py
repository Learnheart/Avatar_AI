"""Mailer: one plain-text mail to an SMTP server, or to the log."""
import logging
import smtplib

import pytest

from backend.config import AppConfig
from backend.utils import mailer
from backend.utils.mailer import LogMailer, MailError, SmtpMailer, create_mailer


class FakeServer:
    """Stands in for smtplib.SMTP and SMTP_SSL: records what was done to it."""

    made: list["FakeServer"] = []
    fail_on: str = ""

    def __init__(self, host, port, timeout=None, context=None) -> None:
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls: list[tuple] = []
        self.closed = False
        FakeServer.made.append(self)
        self._maybe_fail("connect")

    def _maybe_fail(self, step: str) -> None:
        if FakeServer.fail_on == step:
            raise smtplib.SMTPAuthenticationError(535, b"bad password hunter22")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.closed = True

    def starttls(self, context=None) -> None:
        self.calls.append(("starttls", context is not None))

    def login(self, username, password) -> None:
        self._maybe_fail("login")
        self.calls.append(("login", username, password))

    def send_message(self, message) -> None:
        self._maybe_fail("send")
        self.calls.append(("send", message))


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeServer.made, FakeServer.fail_on = [], ""
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeServer)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeServer)


def _config(**overrides) -> AppConfig:
    return AppConfig(
        GROQ_API_KEY="test-key", MAIL_BACKEND="smtp", SMTP_HOST="smtp.example.com",
        SMTP_FROM="Magic Mirror <avatar@example.com>", **overrides,
    )


def test_a_mail_is_handed_to_the_server_after_starttls_and_login():
    config = _config(SMTP_USERNAME="avatar", SMTP_PASSWORD="hunter22", SMTP_TIMEOUT_SEC=7)

    SmtpMailer(config).send("linh@example.com", "Mã xác nhận", "Mã của bạn là 123456")

    (server,) = FakeServer.made
    assert (server.host, server.port, server.timeout) == ("smtp.example.com", 587, 7)
    assert [call[0] for call in server.calls] == ["starttls", "login", "send"]
    assert server.calls[1] == ("login", "avatar", "hunter22")
    message = server.calls[2][1]
    assert message["To"] == "linh@example.com" and message["Subject"] == "Mã xác nhận"
    assert message["From"] == "Magic Mirror <avatar@example.com>"
    assert message.get_content().strip() == "Mã của bạn là 123456"
    assert server.closed


def test_without_a_user_name_nobody_logs_in():
    SmtpMailer(_config()).send("linh@example.com", "s", "b")

    assert [call[0] for call in FakeServer.made[0].calls] == ["starttls", "send"]


def test_ssl_connects_protected_and_does_not_upgrade():
    SmtpMailer(_config(SMTP_SECURITY="ssl", SMTP_PORT=465)).send("linh@example.com", "s", "b")

    (server,) = FakeServer.made
    assert server.port == 465 and server.context is not None
    assert [call[0] for call in server.calls] == ["send"]


def test_none_sends_in_clear():
    SmtpMailer(_config(SMTP_SECURITY="none", SMTP_PORT=25)).send("linh@example.com", "s", "b")

    assert [call[0] for call in FakeServer.made[0].calls] == ["send"]


@pytest.mark.parametrize("step", ["connect", "login", "send"])
def test_a_failing_server_is_a_mail_error_without_its_text(step):
    FakeServer.fail_on = step

    with pytest.raises(MailError) as failed:
        SmtpMailer(_config(SMTP_USERNAME="avatar", SMTP_PASSWORD="hunter22")).send(
            "linh@example.com", "s", "b"
        )

    assert "hunter22" not in str(failed.value)
    assert "SMTPAuthenticationError" in str(failed.value)


def test_a_server_that_cannot_be_reached_is_a_mail_error(monkeypatch):
    def unreachable(*args, **kwargs):
        raise OSError("Name or service not known")

    monkeypatch.setattr(mailer.smtplib, "SMTP", unreachable)

    with pytest.raises(MailError):
        SmtpMailer(_config()).send("linh@example.com", "s", "b")


def test_the_log_mailer_writes_the_mail_and_sends_nothing(caplog):
    with caplog.at_level(logging.WARNING):
        LogMailer().send("linh@example.com", "Mã xác nhận", "Mã của bạn là 123456")

    (record,) = caplog.records
    assert record.msg["to"] == "linh@example.com" and "123456" in record.msg["body"]
    assert FakeServer.made == []


def test_the_mailer_follows_the_config():
    assert isinstance(create_mailer(AppConfig(GROQ_API_KEY="k")), LogMailer)
    assert isinstance(create_mailer(_config()), SmtpMailer)
    with pytest.raises(ValueError, match="MAIL_BACKEND"):
        create_mailer(AppConfig(GROQ_API_KEY="k", MAIL_BACKEND="pigeon"))
