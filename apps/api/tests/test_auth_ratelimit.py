"""A4: progressive blocking of guesses, kept in Postgres as HMACs, and what the client address is
(and is not) when a proxy is, or is not, in front."""

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx2 import Client
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth import passwords
from app.auth.ratelimit import (
    IP_FAILURES,
    PAIR_BACKOFF_CAP_SECONDS,
    SUBJECT_FAILURES,
    attempt_keys,
    blocked_for,
    client_ip,
    record_attempt,
)
from app.auth.tokens import keyed_digest
from app.core.config import AdminSettings
from tests.authsupport import PASSWORD, Api, ApiFactory, UserFactory
from tests.dbsupport import API_DIR
from tests.helpers import TEST_AUTH_SECRET, TEST_SECRET

LOGIN = "/api/v1/auth/login"
WRONG = "this is not the password"
SESSION_IP_BY_EMAIL = (
    "SELECT host(ip) FROM sessions WHERE user_id = (SELECT id FROM users WHERE email = :e)"
)


def wrong_login(api: Api, email: str) -> Any:
    return api.post(LOGIN, {"email": email, "password": WRONG})


# --- through the API ---------------------------------------------------------------------------


def test_five_wrong_passwords_are_free_then_the_attempts_wait(
    apis: ApiFactory, users: UserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = users.make("staff")
    api = apis.make()
    assert [wrong_login(api, user.email).status_code for _ in range(5)] == [401] * 5
    verified: list[str] = []
    real = passwords.verify_hash

    def spy(stored_hash: str, password: str) -> bool:
        verified.append(stored_hash)
        return real(stored_hash, password)

    monkeypatch.setattr(passwords, "verify_hash", spy)

    wrong = wrong_login(api, user.email)
    right = api.login(user)

    for response in (wrong, right):
        assert response.status_code == 429
        assert response.json()["code"] == "rate_limited"
        assert 0 < int(response.headers["retry-after"]) <= 30
        assert "set-cookie" not in response.headers
    assert verified == []  # a blocked attempt costs the server nothing: no password is hashed


def test_an_unknown_email_is_blocked_exactly_like_a_real_one(
    apis: ApiFactory, users: UserFactory
) -> None:
    real = users.make("staff")
    ghost = users.world.email("ghost")
    on_real, on_ghost = apis.make(), apis.make()

    timeline = {
        label: [wrong_login(client, email).status_code for _ in range(7)]
        for label, client, email in (("real", on_real, real.email), ("ghost", on_ghost, ghost))
    }
    retry = {
        "real": wrong_login(on_real, real.email).headers["retry-after"],
        "ghost": wrong_login(on_ghost, ghost).headers["retry-after"],
    }

    assert timeline["real"] == timeline["ghost"] == [401] * 5 + [429] * 2
    assert abs(int(retry["real"]) - int(retry["ghost"])) <= 2  # and the same wait, give or take


def test_the_block_belongs_to_the_pair_so_another_address_can_still_log_in(
    apis: ApiFactory, users: UserFactory
) -> None:
    user = users.make("staff")
    attacker = apis.make()
    for _ in range(6):
        wrong_login(attacker, user.email)

    owner_elsewhere = apis.make().login(user)

    assert attacker.login(user).status_code == 429
    assert owner_elsewhere.status_code == 200  # the attacker cannot lock the owner out of their own


def test_a_successful_login_ends_the_streak(apis: ApiFactory, users: UserFactory) -> None:
    user = users.make("staff")
    api = apis.make()
    for _ in range(4):
        wrong_login(api, user.email)
    assert api.login(user).status_code == 200

    after = [wrong_login(api, user.email).status_code for _ in range(4)]

    assert after == [401] * 4  # eight failures in all, but never five in a row


def test_one_address_trying_many_emails_is_blocked_as_a_whole(
    apis: ApiFactory, users: UserFactory
) -> None:
    sprayer = apis.make()
    for index in range(IP_FAILURES):
        # a different e-mail each time, so no (e-mail, address) pair ever reaches its own limit
        assert wrong_login(sprayer, users.world.email(f"spray{index}")).status_code == 401

    response = sprayer.login(users.make("staff"))

    assert response.status_code == 429 and response.json()["code"] == "rate_limited"


def test_the_counters_hold_only_keyed_digests_of_the_email_and_the_address(
    apis: ApiFactory, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("staff")
    api = apis.make()
    wrong_login(api, user.email)

    ip_digest = keyed_digest(TEST_SECRET, "attempt-ip", api.ip)
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT kind, subject_hmac, ip_hmac, succeeded FROM login_attempts "
                "WHERE ip_hmac = :ip"
            ),
            {"ip": ip_digest},
        ).all()
        columns: Any = (
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'login_attempts' ORDER BY ordinal_position"
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1 and rows[0].kind == "login" and rows[0].succeeded is False
    assert bytes(rows[0].subject_hmac) == keyed_digest(TEST_SECRET, "attempt-subject", user.email)
    assert columns == ["id", "kind", "subject_hmac", "ip_hmac", "succeeded", "attempted_at"]
    dump = repr(rows).encode()
    assert user.email.encode() not in dump and api.ip.encode() not in dump


def test_the_digests_depend_on_the_secret_and_on_their_purpose() -> None:
    other = SecretStr("a-different-secret-with-at-least-32-chars!!")

    assert keyed_digest(TEST_SECRET, "attempt-ip", "10.0.0.1") != keyed_digest(
        other, "attempt-ip", "10.0.0.1"
    )
    assert keyed_digest(TEST_SECRET, "attempt-ip", "x") != keyed_digest(
        TEST_SECRET, "attempt-subject", "x"
    )
    assert keyed_digest(TEST_SECRET, "csrf", "x") != keyed_digest(TEST_SECRET, "attempt-ip", "x")
    assert len(keyed_digest(TEST_SECRET, "attempt-ip", "x")) == 32


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("10.1.2.3", "10.1.2.3"),
        ("::1", "::1"),
        ("2001:DB8:0:0:0:0:0:1", "2001:db8::1"),
        ("::ffff:10.0.0.1", "::ffff:10.0.0.1"),
        (None, "unknown"),
        ("", "unknown"),
        ("testclient", "unknown"),
        ("10.1.2.3, 10.9.9.9", "unknown"),
        ("a" * 500, "unknown"),
    ],
)
def test_the_client_address_is_normalised_or_a_fixed_word(host: str | None, expected: str) -> None:
    assert client_ip(host) == expected


# --- the progression itself, with controlled history ----------------------------------------------


@pytest.fixture
def app_session(admin_settings: AdminSettings, app_engine: Engine) -> Iterator[Session]:
    with sessionmaker(app_engine)() as session:
        yield session
        session.rollback()


def _history(admin_engine: Engine, kind: str, subject: bytes, ip: bytes, rows: list[Any]) -> None:
    """Insert past attempts as the admin: (seconds ago, succeeded) each."""
    with admin_engine.begin() as connection:
        for seconds_ago, succeeded in rows:
            connection.execute(
                text(
                    "INSERT INTO login_attempts (kind, subject_hmac, ip_hmac, succeeded, "
                    "attempted_at) VALUES (:k, :s, :i, :ok, now() - make_interval(secs => :ago))"
                ),
                {"k": kind, "s": subject, "i": ip, "ok": succeeded, "ago": seconds_ago},
            )


@pytest.fixture
def keys() -> Any:
    return attempt_keys(
        TEST_SECRET, f"subject-{uuid.uuid4()}", f"10.250.{uuid.uuid4().int % 250}.1"
    )


@pytest.fixture
def cleanup(admin_engine: Engine, keys: Any) -> Iterator[None]:
    yield
    with admin_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM login_attempts WHERE subject_hmac = :s OR ip_hmac = :i"),
            {"s": keys.subject, "i": keys.ip},
        )


@pytest.mark.usefixtures("cleanup")
@pytest.mark.parametrize(
    ("failures", "expected"),
    [(0, 0), (4, 0), (5, 30), (6, 60), (7, 120), (8, 240), (9, 480), (10, 900), (15, 900)],
)
def test_the_wait_doubles_with_each_failure_up_to_a_cap(
    admin_engine: Engine, app_session: Session, keys: Any, failures: int, expected: int
) -> None:
    _history(admin_engine, "login", keys.subject, keys.ip, [(1, False)] * failures)

    wait = blocked_for(app_session, "login", keys)

    assert wait == pytest.approx(expected, abs=2)
    assert wait <= PAIR_BACKOFF_CAP_SECONDS


@pytest.mark.usefixtures("cleanup")
def test_the_wait_is_counted_from_the_last_failure(
    admin_engine: Engine, app_session: Session, keys: Any
) -> None:
    _history(admin_engine, "login", keys.subject, keys.ip, [(20, False)] * 5)

    assert blocked_for(app_session, "login", keys) == pytest.approx(10, abs=2)  # 30 s - 20 s


@pytest.mark.usefixtures("cleanup")
def test_old_failures_leave_the_window(
    admin_engine: Engine, app_session: Session, keys: Any
) -> None:
    _history(admin_engine, "login", keys.subject, keys.ip, [(16 * 60, False)] * 12)

    assert blocked_for(app_session, "login", keys) == 0


@pytest.mark.usefixtures("cleanup")
def test_failures_before_a_success_do_not_count(
    admin_engine: Engine, app_session: Session, keys: Any
) -> None:
    _history(
        admin_engine,
        "login",
        keys.subject,
        keys.ip,
        [(300, False)] * 8 + [(120, True)] + [(60, False)] * 3,
    )

    assert blocked_for(app_session, "login", keys) == 0  # three since the success: not five


@pytest.mark.usefixtures("cleanup")
def test_the_kinds_of_attempt_do_not_share_counters(
    admin_engine: Engine, app_session: Session, keys: Any
) -> None:
    _history(admin_engine, "password", keys.subject, keys.ip, [(1, False)] * 9)

    assert blocked_for(app_session, "login", keys) == 0
    assert blocked_for(app_session, "password", keys) > 0


@pytest.mark.usefixtures("cleanup")
def test_a_subject_attacked_from_many_addresses_is_blocked_as_a_whole(
    admin_engine: Engine, app_session: Session, keys: Any
) -> None:
    """30 failures in an hour, each from a different address: no pair and no address reaches its
    limit, the account does."""
    for index in range(SUBJECT_FAILURES):
        other_ip = attempt_keys(TEST_SECRET, "unused", f"10.251.0.{index}").ip
        _history(admin_engine, "login", keys.subject, other_ip, [(60, False)])
    try:
        assert blocked_for(app_session, "login", keys) > 0
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM login_attempts WHERE subject_hmac = :s"), {"s": keys.subject}
            )


def test_recording_an_attempt_stores_the_two_digests_and_nothing_else(
    app_session: Session, admin_engine: Engine, keys: Any, cleanup: None
) -> None:
    record_attempt(app_session, "login", keys, succeeded=False)
    app_session.commit()

    with admin_engine.connect() as connection:
        count: Any = connection.execute(
            text("SELECT count(*) FROM login_attempts WHERE subject_hmac = :s AND ip_hmac = :i"),
            {"s": keys.subject, "i": keys.ip},
        ).scalar_one()
    assert count == 1


# --- the client address, with and without a proxy -------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_server(
    admin_settings: AdminSettings, tmp_path: Path, forwarded_allow_ips: str
) -> Iterator[str]:
    """The API under uvicorn, started like the Dockerfile does (`--proxy-headers`)."""
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(API_DIR),
        "ENV": "test",
        "DATABASE_URL": admin_settings.database_url.get_secret_value(),
        "AUTH_SECRET": TEST_AUTH_SECRET,
        "COOKIE_SECURE": "false",
        "PUBLIC_ORIGINS": "http://localhost:3000",
        "OUTBOX_DIR": str(tmp_path),
        "FORWARDED_ALLOW_IPS": forwarded_allow_ips,
    }
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "--factory",
            "app.main:create_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--proxy-headers",
            "--log-level",
            "warning",
        ],
        cwd=API_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        with Client() as probe:
            for _ in range(100):
                try:
                    if probe.get(f"{base}/api/health", timeout=1).status_code == 200:
                        break
                except Exception:  # noqa: BLE001, S110  (not up yet)
                    time.sleep(0.1)
            else:
                pytest.fail("the API under uvicorn did not start")
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)


def _login(base: str, email: str, password: str, forwarded_for: str | None) -> Any:
    headers = {"Origin": "http://localhost:3000"}
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    with Client() as client:
        return client.post(
            f"{base}{LOGIN}", json={"email": email, "password": password}, headers=headers
        )


def _clean_addresses(admin_engine: Engine, addresses: list[str]) -> None:
    digests = [keyed_digest(TEST_SECRET, "attempt-ip", address) for address in addresses]
    with admin_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM login_attempts WHERE ip_hmac = ANY(:d)"), {"d": digests}
        )


def test_without_a_trusted_proxy_a_forged_forwarded_for_changes_nothing(
    admin_settings: AdminSettings, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    """No proxy in front (or one that is not in FORWARDED_ALLOW_IPS): the address is the peer's,
    whatever X-Forwarded-For says, so rotating it does not escape the block."""
    user = users.make("staff")
    forged = [f"198.51.100.{n}" for n in range(1, 8)]
    try:
        with running_server(admin_settings, tmp_path, "203.0.113.9") as base:
            statuses = [
                _login(base, user.email, WRONG, forwarded_for=address).status_code
                for address in forged
            ]
            logged = _login(base, users.make("staff", label="two").email, PASSWORD, "198.51.100.99")
    finally:
        _clean_addresses(admin_engine, ["127.0.0.1", *forged, "198.51.100.99"])

    assert statuses == [401] * 5 + [429] * 2
    assert logged.status_code == 200
    with admin_engine.connect() as connection:
        recorded: Any = connection.execute(
            text(SESSION_IP_BY_EMAIL),
            {"e": logged.json()["user"]["email"]},
        ).scalar_one()
    assert recorded == "127.0.0.1"  # the peer, not the address the client claimed


def test_behind_a_trusted_proxy_the_forwarded_address_is_the_clients(
    admin_settings: AdminSettings, users: UserFactory, tmp_path: Path, admin_engine: Engine
) -> None:
    """The proxy is the peer (127.0.0.1 here) and is listed in FORWARDED_ALLOW_IPS: clients are
    told apart by the address it forwards, so one client's failures do not block another's."""
    user = users.make("staff")
    spoofer = "198.51.100.10"
    other = "198.51.100.20"
    try:
        with running_server(admin_settings, tmp_path, "127.0.0.1") as base:
            first = [
                _login(base, user.email, WRONG, forwarded_for=spoofer).status_code for _ in range(6)
            ]
            second = _login(base, user.email, PASSWORD, forwarded_for=other)
    finally:
        _clean_addresses(admin_engine, [spoofer, other, "127.0.0.1"])

    assert first == [401] * 5 + [429]
    assert second.status_code == 200  # another client behind the same proxy is not blocked
    with admin_engine.connect() as connection:
        recorded: Any = connection.execute(
            text("SELECT host(ip) FROM sessions WHERE user_id = :u"), {"u": user.id}
        ).scalar_one()
    assert recorded == other


@pytest.mark.usefixtures("cleanup")
def test_recording_an_attempt_now_and_then_purges_the_attempts_older_than_a_day(
    admin_engine: Engine, app_session: Session, keys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _history(admin_engine, "login", keys.subject, keys.ip, [(30 * 3600, False), (3600, False)])
    monkeypatch.setattr("app.auth.ratelimit.secrets.randbelow", lambda _n: 0)  # always purge

    record_attempt(app_session, "login", keys, succeeded=False)
    app_session.commit()

    with admin_engine.connect() as connection:
        left: Any = connection.execute(
            text("SELECT count(*) FROM login_attempts WHERE subject_hmac = :s"),
            {"s": keys.subject},
        ).scalar_one()
    assert left == 2  # the day-old one is gone; the hour-old one and the new one stay


@pytest.mark.usefixtures("cleanup")
def test_most_recordings_do_not_purge(
    admin_engine: Engine, app_session: Session, keys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _history(admin_engine, "login", keys.subject, keys.ip, [(30 * 3600, False)])
    monkeypatch.setattr("app.auth.ratelimit.secrets.randbelow", lambda _n: 99)  # never purge

    record_attempt(app_session, "login", keys, succeeded=False)
    app_session.commit()

    with admin_engine.connect() as connection:
        left: Any = connection.execute(
            text("SELECT count(*) FROM login_attempts WHERE subject_hmac = :s"),
            {"s": keys.subject},
        ).scalar_one()
    assert left == 2
