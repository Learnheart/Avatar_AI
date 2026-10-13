"""
Mailer — sending one plain-text mail, behind a boundary chosen by `MAIL_BACKEND`.

  smtp   an SMTP server (`SMTP_*` keys), with the standard library
  log    nothing is sent: the mail is written to the backend log. For a machine without
         a mail server; whoever reads the log reads the mail, so it proves nothing about
         who owns an address

Every method blocks; call them off the event loop.
"""
import smtplib
import ssl
from abc import ABC, abstractmethod
from email.message import EmailMessage

from backend.config import MAIL_LOG, MAIL_SMTP, SMTP_SSL, SMTP_STARTTLS, AppConfig
from backend.utils.logging import get_logger

logger = get_logger("mailer")


class MailError(Exception):
    """The mail could not be handed to the server."""


class Mailer(ABC):
    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> None:
        """Send one plain-text mail. Raises MailError."""


class LogMailer(Mailer):
    """Writes the mail to the log instead of sending it."""

    def send(self, to: str, subject: str, body: str) -> None:
        logger.warning({
            "msg": "Mail written to the log, not sent (MAIL_BACKEND=log)",
            "to": to,
            "subject": subject,
            "body": body,
        })


class SmtpMailer(Mailer):
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.config.SMTP_FROM
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        host, port = self.config.SMTP_HOST, self.config.SMTP_PORT
        timeout = self.config.SMTP_TIMEOUT_SEC
        try:
            if self.config.SMTP_SECURITY == SMTP_SSL:
                server = smtplib.SMTP_SSL(
                    host, port, timeout=timeout, context=ssl.create_default_context()
                )
            else:
                server = smtplib.SMTP(host, port, timeout=timeout)
            with server:
                if self.config.SMTP_SECURITY == SMTP_STARTTLS:
                    server.starttls(context=ssl.create_default_context())
                if self.config.SMTP_USERNAME:
                    server.login(self.config.SMTP_USERNAME, self.config.SMTP_PASSWORD)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as e:
            # The server's answer may quote the credentials: only its kind is kept.
            raise MailError(f"The mail was not sent: {type(e).__name__}") from e
        logger.info({"msg": "Mail sent", "subject": subject})


def create_mailer(config: AppConfig) -> Mailer:
    """The mailer of the configured backend."""
    if config.MAIL_BACKEND == MAIL_SMTP:
        return SmtpMailer(config)
    if config.MAIL_BACKEND == MAIL_LOG:
        return LogMailer()
    raise ValueError(f"Invalid MAIL_BACKEND: {config.MAIL_BACKEND}")
