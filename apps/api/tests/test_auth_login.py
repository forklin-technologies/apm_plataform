"""A2/A4: logging in and out, who am I, and what a failed login gives away (nothing)."""

import hashlib
from typing import Any

import pytest
from argon2 import PasswordHasher, Type
from httpx2 import Response
from sqlalchemy import Engine, text

from app.auth import passwords
from app.auth.sessions import ABSOLUTE_LIFETIME_SECONDS
from tests.authsupport import PASSWORD, Api, ApiFactory, UserFactory

ME = "/api/v1/auth/me"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"


def cookie_attributes(response: Response) -> dict[str, dict[str, Any]]:
    """Set-Cookie headers as {name: {"value": ..., "path": ..., "httponly": True, ...}}."""
    parsed: dict[str, dict[str, Any]] = {}
    for header in response.headers.get_list("set-cookie"):
        first, *attributes = [part.strip() for part in header.split(";")]
        name, _, value = first.partition("=")
        entry: dict[str, Any] = {"value": value}
        for attribute in attributes:
            key, _, attribute_value = attribute.partition("=")
            entry[key.lower()] = attribute_value or True
        parsed[name] = entry
    return parsed


def without_request_id(response: Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    body.pop("request_id", None)
    return body


def session_row(engine: Engine, token: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT user_id, membership_id, revoked_at, revoked_reason, created_at, "
                "expires_at, last_seen_at, host(ip) AS ip FROM sessions WHERE id = :id"
            ),
            {"id": hashlib.sha256(token.encode()).digest()},
        ).one_or_none()


def set_session(engine: Engine, token: str, assignments: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE sessions SET {assignments} WHERE id = :id"),  # noqa: S608
            {"id": hashlib.sha256(token.encode()).digest()},
        )


# --- a good login ------------------------------------------------------------------------------


def test_login_returns_the_session_and_sets_the_cookies(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("staff")

    response = api.login(user)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"user", "active_membership", "memberships", "csrf_token", "session"}
    assert body["user"] == {"id": str(user.id), "email": user.email, "full_name": "Test staff"}
    assert body["active_membership"]["role"] == "staff"
    assert body["active_membership"]["permissions"] == ["expenses:read_own", "expenses:submit"]
    assert [m["membership_id"] for m in body["memberships"]] == [str(user.membership_ids[0])]
    assert body["csrf_token"] == api.csrf
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert "hash" not in response.text.lower()
    assert PASSWORD not in response.text

    token = api.session_token
    assert token is not None and len(token) == 43
    cookies = cookie_attributes(response)
    session = cookies[api.settings.session_cookie_name]
    assert session["httponly"] is True and session["samesite"].lower() == "lax"
    assert session["path"] == "/" and "domain" not in session and "secure" not in session
    csrf = cookies[api.settings.csrf_cookie_name]
    assert "httponly" not in csrf and csrf["samesite"].lower() == "lax" and csrf["path"] == "/"
    assert csrf["value"] == body["csrf_token"] != token

    row = session_row(admin_engine, token)  # the database holds the SHA-256, never the token
    assert row.user_id == user.id and row.membership_id == user.membership_ids[0]
    assert row.revoked_at is None
    assert (row.expires_at - row.created_at).total_seconds() == pytest.approx(
        ABSOLUTE_LIFETIME_SECONDS, abs=5
    )
    assert row.ip == api.ip


def test_the_secure_deployment_uses_host_prefixed_secure_cookies(
    apis: ApiFactory, users: UserFactory
) -> None:
    api = apis.make(secure=True)
    user = users.make("staff")

    response = api.login(user)

    assert response.status_code == 200
    cookies = cookie_attributes(response)
    assert set(cookies) == {"__Host-apm_session", "__Host-apm_csrf"}
    for name, attributes in cookies.items():
        assert attributes["secure"] is True, name  # __Host- requires Secure, Path=/, no Domain
        assert attributes["path"] == "/" and "domain" not in attributes, name
        assert attributes["samesite"].lower() == "lax", name
    assert cookies["__Host-apm_session"]["httponly"] is True
    assert "httponly" not in cookies["__Host-apm_csrf"]
    assert api.get(ME).status_code == 200  # and the browser (jar) sends them back over https


def test_the_development_mode_uses_plain_names_without_the_secure_flag(
    api: Api, users: UserFactory
) -> None:
    response = api.login(users.make("staff"))

    assert set(cookie_attributes(response)) == {"apm_session", "apm_csrf"}
    assert all("secure" not in c for c in cookie_attributes(response).values())


def test_login_with_one_membership_selects_it_and_with_two_asks(
    api: Api, users: UserFactory
) -> None:
    single = users.make("school_admin")
    multi = users.make("staff", extra=(("viewer", "a", "2"),), label="multi")

    first = api.login(single).json()
    api.forget_cookies()
    second = api.login(multi).json()

    assert first["active_membership"]["role"] == "school_admin"
    assert second["active_membership"] is None  # the user must choose (POST /auth/context)
    assert {m["role"] for m in second["memberships"]} == {"staff", "viewer"}


def test_a_user_with_no_membership_logs_in_with_no_context(api: Api, users: UserFactory) -> None:
    body = api.login(users.make(None)).json()

    assert body["active_membership"] is None and body["memberships"] == []


def test_the_email_is_trimmed_and_case_insensitive(api: Api, users: UserFactory) -> None:
    user = users.make("staff")

    response = api.post(LOGIN, {"email": f"  {user.email.upper()} ", "password": PASSWORD})

    assert response.status_code == 200


def test_every_login_issues_a_new_token(api: Api, users: UserFactory) -> None:
    user = users.make("staff")
    api.login(user)
    first = api.session_token

    api.login(user)

    assert api.session_token not in (None, first)


# --- who am I ----------------------------------------------------------------------------------


def test_me_returns_the_session_of_the_cookie(api: Api, users: UserFactory) -> None:
    user = users.make("treasurer")
    logged = api.login(user).json()

    me = api.get(ME)

    assert me.status_code == 200
    assert me.json() == logged
    assert me.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    "cookie",
    [None, "", "x", "A" * 42, "A" * 44, "A" * 43, "../" * 14 + "AA", "a b" * 14 + "a"],
    ids=["none", "empty", "short", "too-short", "too-long", "unknown", "path", "spaces"],
)
def test_me_without_a_valid_session_is_a_401_problem(api: Api, cookie: str | None) -> None:
    if cookie is not None:
        api.set_session_cookie(cookie)

    response = api.get(ME)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "unauthenticated" and body["status"] == 401
    assert body["type"] == "urn:apm-digital:problem:unauthenticated"
    assert body["request_id"] == response.headers["x-request-id"]


def test_a_tampered_token_is_refused(api: Api, users: UserFactory) -> None:
    api.login(users.make("staff"))
    token = api.session_token
    assert token is not None
    flipped = ("A" if token[0] != "A" else "B") + token[1:]
    api.set_session_cookie(flipped)

    assert api.get(ME).status_code == 401


def test_a_rejected_session_clears_the_cookies(api: Api, users: UserFactory) -> None:
    api.login(users.make("staff"))
    api.set_session_cookie("B" * 43)

    response = api.get(ME)

    assert response.status_code == 401
    cleared = cookie_attributes(response)
    assert set(cleared) == {api.settings.session_cookie_name, api.settings.csrf_cookie_name}
    assert all(c["value"] in ("", '""') for c in cleared.values())


@pytest.mark.parametrize(
    ("assignments", "label"),
    [
        ("revoked_at = now()", "revoked"),
        (
            "created_at = now() - interval '13 hours', expires_at = now() - interval '1 hour'",
            "expired",
        ),
        ("last_seen_at = now() - interval '31 minutes'", "idle"),
    ],
)
def test_a_revoked_expired_or_idle_session_is_refused(
    api: Api, users: UserFactory, admin_engine: Engine, assignments: str, label: str
) -> None:
    api.login(users.make("staff"))
    assert api.get(ME).status_code == 200
    assert api.session_token is not None

    set_session(admin_engine, api.session_token, assignments)

    response = api.get(ME)
    assert response.status_code == 401, label
    assert response.json()["code"] == "unauthenticated"


def test_activity_extends_an_idle_session_but_writes_are_throttled(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    api.login(users.make("staff"))
    token = api.session_token
    assert token is not None
    set_session(admin_engine, token, "last_seen_at = now() - interval '10 minutes'")

    api.get(ME)
    after_first = session_row(admin_engine, token).last_seen_at
    api.get(ME)
    after_second = session_row(admin_engine, token).last_seen_at

    assert (session_row(admin_engine, token).expires_at - after_first).total_seconds() > 0
    with admin_engine.connect() as connection:
        age: Any = connection.execute(
            text("SELECT EXTRACT(EPOCH FROM now() - :t)"), {"t": after_first}
        ).scalar_one()
    assert float(age) < 60  # it was refreshed ...
    assert after_second == after_first  # ... and the next request inside a minute wrote nothing


# --- logging out -------------------------------------------------------------------------------


def test_logout_revokes_the_session_and_clears_the_cookies(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    api.login(users.make("staff"))
    token = api.session_token
    assert token is not None

    response = api.post(LOGOUT)

    assert response.status_code == 204 and response.content == b""
    cleared = cookie_attributes(response)
    assert set(cleared) == {api.settings.session_cookie_name, api.settings.csrf_cookie_name}
    row = session_row(admin_engine, token)
    assert row.revoked_at is not None and row.revoked_reason == "logout"
    api.set_session_cookie(token)  # the browser forgot it; an attacker who kept it gets nothing
    assert api.get(ME).status_code == 401


def test_logout_is_idempotent_and_needs_no_session(api: Api) -> None:
    assert api.post(LOGOUT).status_code == 204
    assert api.post(LOGOUT).status_code == 204


def test_logout_with_a_session_but_without_the_csrf_token_is_refused(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    """A foreign page must not be able to log the user out."""
    api.login(users.make("staff"))
    token = api.session_token
    assert token is not None

    response = api.post(LOGOUT, csrf=False)

    assert response.status_code == 403 and response.json()["code"] == "csrf_failed"
    assert session_row(admin_engine, token).revoked_at is None
    assert api.get(ME).status_code == 200


# --- a failed login says nothing ----------------------------------------------------------------


def _count_verifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = passwords.verify_hash

    def spy(stored_hash: str, password: str) -> bool:
        calls.append(stored_hash)
        return real(stored_hash, password)

    monkeypatch.setattr(passwords, "verify_hash", spy)
    return calls


def test_every_failure_looks_the_same_and_always_verifies_one_password(
    apis: ApiFactory, users: UserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown e-mail, wrong password, inactive account and an account with no password all give
    the same answer, and in ALL of them exactly one Argon2 verification runs. Counting the calls is
    the proof (a clock would be flaky): the same work is done whatever the account is."""
    real_user = users.make("staff")
    inactive = users.make("staff", active=False, label="inactive")
    no_password = users.make("staff", password=None, label="nopass")
    attempts = {
        "unknown": (users.world.email("nobody"), PASSWORD),
        "wrong": (real_user.email, "not the password at all"),
        "inactive": (inactive.email, PASSWORD),
        "no-password": (no_password.email, PASSWORD),
    }
    calls = _count_verifications(monkeypatch)
    answers: dict[str, Response] = {}
    verified: dict[str, list[str]] = {}
    for label, (email, password) in attempts.items():
        calls.clear()
        answers[label] = apis.make().post(LOGIN, {"email": email, "password": password})
        verified[label] = list(calls)

    reference = answers["unknown"]
    for label, response in answers.items():
        assert response.status_code == 401, label
        assert response.headers["content-type"] == "application/problem+json", label
        assert without_request_id(response) == without_request_id(reference), label
        assert "set-cookie" not in response.headers, label
        assert len(verified[label]) == 1, label  # one verification, whatever the account
    dummy = passwords._DUMMY_HASH  # noqa: SLF001
    assert verified["unknown"] == verified["inactive"] == verified["no-password"] == [dummy]
    assert verified["wrong"] != [dummy] and verified["wrong"][0].startswith("$argon2id$")
    body = without_request_id(reference)
    assert body["code"] == "invalid_credentials" and body["title"] == "Invalid e-mail or password"


def test_a_successful_login_verifies_exactly_one_password_too(
    api: Api, users: UserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = users.make("staff")
    calls = _count_verifications(monkeypatch)

    assert api.login(user).status_code == 200

    assert len(calls) == 1 and calls[0] != passwords._DUMMY_HASH  # noqa: SLF001


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "a@b.test"},
        {"password": "x"},
        {"email": "not-an-email", "password": "S3cretPw!!xx"},
        {"email": "a@b.test", "password": "S3cretPw!!xx", "role": "organization_admin"},
        {"email": "a@b.test", "password": "S3cretPw!!xx", "organization_id": "x"},
        {"email": "a@b.test", "password": "p" * 257},
        {"email": 5, "password": "x"},
        {"email": "a@b.test", "password": ["x"]},
        [],
        "text",
    ],
)
def test_a_malformed_login_is_a_422_that_never_echoes_the_input(
    api: Api, body: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_verifications(monkeypatch)

    response = api.post(LOGIN, body)

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "validation_error"
    assert all(set(error) == {"field", "code"} for error in problem["errors"])
    assert "S3cretPw!!xx" not in response.text and "p" * 20 not in response.text
    assert calls == []  # nothing was hashed for a body that is not even valid


def test_an_sql_looking_email_is_just_an_unknown_email(api: Api) -> None:
    response = api.post(LOGIN, {"email": "x'--@y.test", "password": "S3cretPw!!xx"})

    assert response.status_code == 401


def test_a_hash_made_with_old_parameters_is_upgraded_at_login(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("staff")
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID).hash(PASSWORD)
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET password_hash = :h WHERE id = :id"), {"h": weak, "id": user.id}
        )

    assert api.login(user).status_code == 200

    with admin_engine.connect() as connection:
        stored: Any = connection.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {"id": user.id}
        ).scalar_one()
    assert stored != weak and "m=19456,t=2,p=1" in stored
    api.forget_cookies()
    assert api.login(user).status_code == 200  # and the new hash verifies


# --- session fixation ----------------------------------------------------------------------------


def test_a_token_the_client_brings_to_login_is_never_adopted(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("staff")
    api.set_session_cookie("F" * 43)  # chosen by an attacker (planted on the victim's browser)

    api.login(user)

    assert api.session_token not in (None, "F" * 43)
    assert session_row(admin_engine, "F" * 43) is None


@pytest.mark.parametrize("same_user", [True, False])
def test_login_ends_the_session_the_browser_already_had(
    api: Api, users: UserFactory, admin_engine: Engine, same_user: bool
) -> None:
    first = users.make("staff")
    api.login(first)
    old = api.session_token
    assert old is not None

    api.login(first if same_user else users.make("viewer"))

    row = session_row(admin_engine, old)
    assert row.revoked_at is not None and row.revoked_reason == "rotated"
