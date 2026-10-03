"""A6: no password, hash, token or cookie in a response, a log line, a traceback or the output of
alembic.

The test is a canary: it puts distinctive values where secrets enter the system (a password in a
body, a token in a cookie or a header), drives the whole authentication surface (the successes and
every way to fail), and then searches EVERYTHING the system said for those values and for the
password hashes the database ended up holding. SQL logging is switched on for the run (what
`echo=True` does), because that is the setting under which bound values would show.
"""

import hashlib
import logging
import traceback
from pathlib import Path

import pytest
from httpx2 import Response
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth.tokens import new_token
from app.core.config import AdminSettings
from tests.authsupport import PASSWORD, PASSWORD_HASH, ApiFactory, UserFactory, read_outbox
from tests.dbsupport import ScratchDb, run_alembic

# Fake secrets used only as leak canaries (distinctive, so a hit cannot be a coincidence).
WRONG_PASSWORD = "canary-wrong-password-5d1f"  # noqa: S105
WEAK_PASSWORD = "w3akC4nary"  # noqa: S105  (too short on purpose)
INVITEE_PASSWORD = "canary-invitee-password-4b7a"  # noqa: S105
NEW_PASSWORD = "canary-new-password-8c2e"  # noqa: S105
FORGED_SESSION = new_token()
FORGED_CSRF = "canary-forged-csrf-header-77e0"
GUESSED_INVITATION = new_token()
ERROR_VALUE = "canary-bound-value-in-a-failing-statement-31ac"
ERROR_PASSWORD = "canary-password-of-a-request-that-blows-up-6e9d"  # noqa: S105

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
CONTEXT = "/api/v1/auth/context"
PASSWORD_ROUTE = "/api/v1/auth/password"  # noqa: S105  (a path, not a secret)
ACCEPT = "/api/v1/invitations/accept"
UNKNOWN_MEMBERSHIP = "00000000-0000-4000-8000-000000000001"


def found(secrets: dict[str, str], *texts: str) -> list[str]:
    """The names of the secrets that appear in any of the texts."""
    haystack = "\n".join(texts)
    return sorted(name for name, value in secrets.items() if value and value in haystack)


def readable(response: Response) -> str:
    """Everything a client can read of a response except the cookies it is told to store."""
    headers = "\n".join(
        f"{name}: {value}" for name, value in response.headers.items() if name != "set-cookie"
    )
    return f"{response.text}\n{headers}"


class Recorder:
    """Keeps every response so they can all be searched at the end."""

    def __init__(self) -> None:
        self.responses: list[Response] = []

    def __call__(self, response: Response) -> Response:
        self.responses.append(response)
        return response


def _password_hashes(engine: Engine, emails: list[str]) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT email, password_hash FROM users WHERE email = ANY(:emails)"),
            {"emails": emails},
        ).all()
    return {f"hash of {row.email}": row.password_hash for row in rows if row.password_hash}


def test_nothing_secret_is_in_a_response_a_log_line_or_the_console(
    apis: ApiFactory,
    users: UserFactory,
    admin_engine: Engine,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = Recorder()
    world = users.world
    # Made before the logging is turned up: the test's own admin engine does not hide parameters,
    # and what is under test is what the APPLICATION says.
    admin_user = users.make("organization_admin", school=None, label="leak-admin")
    # SQLAlchemy keeps its own logger at WARNING: INFO on `sqlalchemy.engine` is what `echo=True`
    # does, and it prints every statement with its parameters (hidden here by hide_parameters).
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")

    # --- the whole surface, successes and failures ------------------------------------------
    admin = apis.make()
    record(admin.login(admin_user))
    admin_session, admin_csrf = admin.session_token, admin.csrf
    invitee_email = world.email("leak-invitee")
    record(admin.invite(invitee_email, "staff", world.school_a1))
    invitation = read_outbox(tmp_path)[-1].token

    newcomer = apis.make()
    record(
        newcomer.post(ACCEPT, {"token": invitation, "full_name": "Leak", "password": WEAK_PASSWORD})
    )
    record(
        newcomer.post(
            ACCEPT, {"token": GUESSED_INVITATION, "full_name": "Leak", "password": INVITEE_PASSWORD}
        )
    )
    record(
        newcomer.post(
            ACCEPT, {"token": invitation, "full_name": "Leak", "password": INVITEE_PASSWORD}
        )
    )
    record(
        newcomer.post(
            ACCEPT, {"token": invitation, "full_name": "Leak", "password": INVITEE_PASSWORD}
        )
    )
    record(newcomer.login(invitee_email, WRONG_PASSWORD))
    record(newcomer.login("nobody-leak@example.test", WRONG_PASSWORD))
    record(newcomer.login(invitee_email, INVITEE_PASSWORD))
    first_session, first_csrf = newcomer.session_token, newcomer.csrf
    record(newcomer.get(ME))
    record(newcomer.switch(UNKNOWN_MEMBERSHIP))
    record(
        newcomer.post(
            PASSWORD_ROUTE, {"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD}
        )
    )
    record(
        newcomer.post(
            PASSWORD_ROUTE, {"current_password": INVITEE_PASSWORD, "new_password": WEAK_PASSWORD}
        )
    )
    record(
        newcomer.post(
            PASSWORD_ROUTE, {"current_password": INVITEE_PASSWORD, "new_password": NEW_PASSWORD}
        )
    )
    second_session, second_csrf = newcomer.session_token, newcomer.csrf
    record(newcomer.post(LOGIN, {"email": invitee_email, "password": 5}))  # a malformed body
    record(newcomer.post(PASSWORD_ROUTE, {"current_password": NEW_PASSWORD}, csrf=FORGED_CSRF))
    record(
        newcomer.post(
            PASSWORD_ROUTE, {"current_password": NEW_PASSWORD}, origin="https://evil.example"
        )
    )
    record(newcomer.post(LOGOUT))
    # A revoked token, a forged one and a forged CSRF header, presented as a stolen cookie would be.
    assert first_session is not None
    newcomer.set_session_cookie(first_session)
    record(newcomer.get(ME))
    newcomer.set_session_cookie(FORGED_SESSION)
    record(newcomer.get(ME))
    record(newcomer.post(CONTEXT, {"membership_id": UNKNOWN_MEMBERSHIP}, csrf=FORGED_CSRF))
    # Brute force until blocked (the answer with Retry-After is a response too).
    brute = apis.make()
    for _ in range(7):
        record(brute.login(invitee_email, WRONG_PASSWORD))
    assert record.responses[-1].status_code == 429

    # --- a request that blows up on a failing statement that carries a secret ---------------
    def explode(db: Session, email: str) -> None:
        db.execute(
            text("SELECT 1 FROM a_table_that_does_not_exist WHERE some_column = :v"),
            {"v": ERROR_VALUE},
        )

    monkeypatch.setattr("app.routers.v1.auth.find_login_identity", explode)
    blown = apis.make()
    record(blown.login("someone-leak@example.test", ERROR_PASSWORD))
    assert record.responses[-1].status_code == 500
    assert record.responses[-1].json()["code"] == "internal_error"

    monkeypatch.undo()

    # ...and one whose own message quotes what the client sent (a library's error might).
    def refuse_to_check(stored_hash: str | None, password: str) -> bool:
        raise ValueError("cannot check " + password)

    monkeypatch.setattr("app.auth.passwords.check_password", refuse_to_check)
    record(blown.login("someone-leak@example.test", ERROR_PASSWORD))
    assert record.responses[-1].status_code == 500
    monkeypatch.undo()
    # What would reach a log or a traceback if the error escaped: the exception and its chain.
    factory = blown.client.app.state.session_factory  # type: ignore[attr-defined]
    failing = text("SELECT 1 FROM a_table_that_does_not_exist WHERE some_column = :v")
    with factory() as session, pytest.raises(Exception) as raised:  # noqa: PT011  (any driver error)
        session.execute(failing, {"v": ERROR_VALUE})
    exception_text = "".join(traceback.format_exception(raised.value)) + repr(raised.value)

    # What the application said, collected before the test reads the database as admin again.
    console = capsys.readouterr()
    everything_said = "\n".join([caplog.text, console.out, console.err, exception_text])
    assert "SELECT" in caplog.text and "unhandled error on request" in caplog.text

    # --- what must not be anywhere ---------------------------------------------------------
    hashes = _password_hashes(admin_engine, [admin_user.email, invitee_email])
    assert len(hashes) == 2 and all(value.startswith("$argon2id$") for value in hashes.values())
    credentials = {
        "password": PASSWORD,
        "wrong password": WRONG_PASSWORD,
        "weak password": WEAK_PASSWORD,
        "invitee password": INVITEE_PASSWORD,
        "new password": NEW_PASSWORD,
        "error password": ERROR_PASSWORD,
        "bound value": ERROR_VALUE,
        "forged session": FORGED_SESSION,
        "forged csrf": FORGED_CSRF,
        "invitation": invitation,
        "guessed invitation": GUESSED_INVITATION,
        "hash of the test password": PASSWORD_HASH,
        **hashes,
    }
    session_ids = {
        f"id of session {n}": hashlib.sha256(token.encode()).hexdigest()
        for n, token in enumerate((admin_session, first_session, second_session), 1)
        if token
    }
    session_tokens = {
        f"session token {n}": token
        for n, token in enumerate((admin_session, first_session, second_session), 1)
        if token
    }
    csrf_tokens = {
        f"csrf token {n}": token
        for n, token in enumerate((admin_csrf, first_csrf, second_csrf), 1)
        if token
    }
    assert len(session_tokens) == 3 and len(csrf_tokens) == 3

    for response in record.responses:
        where = f"{response.request.method} {response.request.url.path} {response.status_code}"
        # The CSRF token is meant to be in the answer of a login, of "me" and of a context switch.
        forbidden = {**credentials, **session_ids, **session_tokens}
        if response.status_code >= 400:
            forbidden.update(csrf_tokens)
        assert found(forbidden, readable(response)) == [], where
        # A cookie may carry the session token and the CSRF token, never anything else.
        cookies = "\n".join(response.headers.get_list("set-cookie"))
        assert found({**credentials, **session_ids}, cookies) == [], where

    never_said = {**credentials, **session_ids, **session_tokens, **csrf_tokens}
    assert found(never_said, everything_said) == []
    assert "$argon2" not in everything_said


def test_the_output_of_alembic_never_carries_a_password_or_a_hash(
    scratch_db: ScratchDb,
) -> None:
    passwords = {
        "admin password": make_url(scratch_db.admin_url).password or "",
        "application password": make_url(scratch_db.app_url).password or "",
    }
    assert all(len(value) >= 12 for value in passwords.values())
    secrets = {
        **passwords,
        **{f"start of the {name}": value[:12] for name, value in passwords.items()},
        **{f"end of the {name}": value[-12:] for name, value in passwords.items()},
        "hash of the test password": PASSWORD_HASH,
    }

    outputs: list[str] = []
    for arguments in (
        ("upgrade", "head"),
        ("downgrade", "0005_tenancy_write_grants"),
        ("upgrade", "head"),
    ):
        result = run_alembic(scratch_db, *arguments)
        assert result.returncode == 0, result.stderr[-2000:]
        outputs.append(result.stdout)
        outputs.append(result.stderr)

    saying = "\n".join(outputs)
    assert "0006_auth_sessions" in saying  # it did run the migration this test is about
    assert found(secrets, saying) == []
    assert "$argon2" not in saying


def test_a_migration_that_cannot_run_fails_without_echoing_the_password(
    scratch_db: ScratchDb, admin_settings: AdminSettings
) -> None:
    """The failure path prints the most (a traceback); it must still hold no password."""
    wrong = make_url(scratch_db.admin_url).set(
        password="canary-not-the-real-password-93be"  # noqa: S106
    )
    broken = ScratchDb(
        scratch_db.name, wrong.render_as_string(hide_password=False), scratch_db.app_url
    )

    result = run_alembic(broken, "upgrade", "head")

    saying = result.stdout + result.stderr
    assert result.returncode != 0
    real = make_url(scratch_db.admin_url).password or ""
    assert "canary-not-the-real-password-93be" not in saying
    assert real not in saying and real[:12] not in saying and real[-12:] not in saying
    assert admin_settings.database_url.get_secret_value() not in saying
    assert (make_url(scratch_db.app_url).password or "") not in saying


# QA finding N10e: SQLAlchemy at DEBUG prints the ROWS a query returns (password hashes, session
# ids). The app raises the engine loggers to INFO when it builds its engine.


def test_starting_the_app_keeps_the_engine_logger_from_printing_result_rows(
    apis: ApiFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # What an operator does to "see the SQL"; caplog puts the levels back at the end of the test.
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine.Engine")

    api = apis.make()
    with api.client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.execute(text("SELECT upper(CAST(:v AS text))"), {"v": "canary-result-row"})

    assert logging.getLogger("sqlalchemy.engine").getEffectiveLevel() >= logging.INFO
    assert logging.getLogger("sqlalchemy.engine.Engine").getEffectiveLevel() >= logging.INFO
    assert "CANARY-RESULT-ROW" not in caplog.text
    assert "canary-result-row" not in caplog.text
