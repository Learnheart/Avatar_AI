"""What the account tests share: the stores on a real SQLite file, a clock, a mailer
that keeps the mails, and the two steps that create an account over HTTP."""
import re
from datetime import datetime, timedelta, timezone

from backend.features.accounts.store import AccountsStore, UsersStore
from backend.utils.database import Database
from backend.utils.mailer import MailError, Mailer

START = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
PASSWORD = "mật khẩu đủ dài"


class Clock:
    """Stands still until it is moved."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


def open_stores(config) -> tuple[Database, UsersStore, AccountsStore]:
    database = Database(config.DATABASE_URL)
    database.start()
    return database, UsersStore(database), AccountsStore(database)


class CapturingMailer(Mailer):
    """Keeps the mails instead of sending them: `sent` holds (to, subject, body).
    `down` makes every send fail."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.down = False

    def send(self, to: str, subject: str, body: str) -> None:
        if self.down:
            raise MailError("The mail was not sent: SMTPServerDisconnected")
        self.sent.append((to, subject, body))

    def code(self, to: str | None = None) -> str:
        """The code of the last mail (sent to `to`, when it is given)."""
        # A mail goes to the address as it is stored: in lower case.
        mails = [mail for mail in self.sent if to is None or mail[0] == to.strip().lower()]
        return re.search(r"\b(\d{6})\b", mails[-1][2]).group(1)


def capture_mail(client) -> CapturingMailer:
    """Have the app of the test client keep its mails; the mailer that keeps them."""
    mailer = client.app.state.accounts._mailer
    if not isinstance(mailer, CapturingMailer):
        mailer = client.app.state.accounts._mailer = CapturingMailer()
    return mailer


def create_account(client, email: str = "linh@example.com", **fields) -> dict:
    """Create an account over HTTP, code included; the session of the new user."""
    mailer = capture_mail(client)
    asked = client.post("/auth/register", json={"email": email, "password": PASSWORD, **fields})
    assert asked.status_code == 202, asked.text
    created = client.post("/auth/register/verify", json={"email": email, "code": mailer.code(email)})
    assert created.status_code == 201, created.text
    return created.json()
