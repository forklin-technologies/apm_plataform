"""A4: the Origin check (every write, login included), the CSRF token bound to the session, and
the writes that must not work from another site."""

import hashlib
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.auth.csrf import csrf_token
from tests.authsupport import PASSWORD, Api, ApiFactory, UserFactory
from tests.helpers import TEST_SECRET

WRITES: list[tuple[str, Any]] = [
    ("/api/v1/auth/login", {"email": "a@b.test", "password": "x"}),
    ("/api/v1/auth/logout", None),
    ("/api/v1/auth/context", {"membership_id": str(uuid.uuid4())}),
    ("/api/v1/auth/password", {"current_password": "x", "new_password": "y"}),
    ("/api/v1/invitations", {"email": "a@b.test", "role": "staff"}),
    ("/api/v1/invitations/accept", {"token": "x"}),
]
ALLOWED = "http://localhost:3000"


# --- Origin ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "body"), WRITES, ids=[w[0].rsplit("/", 1)[-1] for w in WRITES])
@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "https://evil.example",
        "http://evil.example",
        "https://localhost:3000",  # same host, wrong scheme
        "http://localhost:3001",  # same host, wrong port
        "http://localhost",
        "http://localhost:3000.evil.example",
        "http://evil.example/http://localhost:3000",
        "localhost:3000",
        "",
    ],
    ids=[
        "missing",
        "null",
        "foreign-https",
        "foreign-http",
        "wrong-scheme",
        "wrong-port",
        "no-port",
        "suffix-trick",
        "path-trick",
        "no-scheme",
        "empty",
    ],
)
def test_a_write_from_any_other_origin_is_refused_before_anything_else(
    api: Api, path: str, body: Any, origin: str | None
) -> None:
    headers = {} if origin is None else {"Origin": origin}

    response = api.post(path, body, origin=False, csrf=False, headers=headers)

    assert response.status_code == 403
    problem = response.json()
    assert problem["code"] == "origin_not_allowed"
    assert problem["request_id"] == response.headers["x-request-id"]
    assert response.headers["content-type"] == "application/problem+json"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(("path", "body"), WRITES, ids=[w[0].rsplit("/", 1)[-1] for w in WRITES])
def test_the_own_origin_gets_past_the_origin_check(api: Api, path: str, body: Any) -> None:
    response = api.post(path, body, csrf=False)

    refused_for_origin = (
        response.headers.get("content-type") == "application/problem+json"
        and response.json()["code"] == "origin_not_allowed"
    )
    assert not refused_for_origin


def test_the_origin_comparison_ignores_case_and_a_trailing_slash(
    api: Api, users: UserFactory
) -> None:
    user = users.make("staff")

    for origin in ("HTTP://LOCALHOST:3000", "http://localhost:3000/"):
        response = api.login(user, origin=origin)
        assert response.status_code == 200, origin
        api.forget_cookies()


def test_the_referer_is_used_only_when_there_is_no_origin(api: Api, users: UserFactory) -> None:
    user = users.make("staff")
    body = {"email": user.email, "password": PASSWORD}

    own = api.post(
        "/api/v1/auth/login", body, origin=False, headers={"Referer": f"{ALLOWED}/login?x=1"}
    )
    foreign = api.post(
        "/api/v1/auth/login",
        body,
        origin=False,
        headers={"Referer": "https://evil.example/login"},
    )
    mixed = api.post(
        "/api/v1/auth/login",
        body,
        origin="https://evil.example",
        headers={"Referer": f"{ALLOWED}/login"},
    )

    assert own.status_code == 200
    assert foreign.status_code == 403
    assert mixed.status_code == 403  # a foreign Origin is not rescued by a friendly Referer


def test_reading_is_not_subject_to_the_origin_check(api: Api, users: UserFactory) -> None:
    api.login(users.make("staff"))

    response = api.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200


def test_login_from_another_site_creates_no_session(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    """Login CSRF: a foreign page cannot sign the victim in as the attacker."""
    user = users.make("staff")

    response = api.login(user, origin="https://evil.example")

    assert response.status_code == 403
    with admin_engine.connect() as connection:
        count: Any = connection.execute(
            text("SELECT count(*) FROM sessions WHERE user_id = :u"), {"u": user.id}
        ).scalar_one()
    assert count == 0
    assert api.session_token is None


def test_a_json_body_sent_as_text_plain_is_not_accepted(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    """The 'simple request' trick (a form posting JSON as text/plain) fails on its own too."""
    user = users.make("staff")

    response = api.client.post(
        "/api/v1/auth/login",
        content=f'{{"email": "{user.email}", "password": "{PASSWORD}"}}',
        headers={"Origin": ALLOWED, "Content-Type": "text/plain"},
    )

    assert response.status_code == 422
    assert api.session_token is None


# --- CSRF token --------------------------------------------------------------------------------


def _someone_else_logged_in(apis: ApiFactory, users: UserFactory) -> str:
    other = apis.make()
    other.login(users.make("staff", label="other"))
    assert other.csrf is not None
    return other.csrf


@pytest.mark.parametrize(
    "variant",
    ["no-header", "no-cookie", "different", "both-planted", "other-sessions-token", "empty-header"],
)
def test_a_logged_in_write_needs_the_csrf_token_of_its_own_session(
    apis: ApiFactory, users: UserFactory, variant: str
) -> None:
    api = apis.make()
    user = users.make("school_admin")
    api.login(user)
    own = api.csrf
    assert own is not None
    foreign = _someone_else_logged_in(apis, users)
    body = {
        "email": users.world.email("x"),
        "role": "staff",
        "school_id": str(users.world.school_a1),
    }

    if variant == "no-header":
        response = api.post("/api/v1/invitations", body, csrf=False)
    elif variant == "empty-header":
        response = api.post("/api/v1/invitations", body, csrf="")
    elif variant == "no-cookie":
        api.client.cookies.delete(api.settings.csrf_cookie_name)
        response = api.post("/api/v1/invitations", body, csrf=own)
    elif variant == "different":
        response = api.post("/api/v1/invitations", body, csrf=own[::-1])
    elif variant == "both-planted":
        # The attacker controls a sibling subdomain: plants a cookie AND sends the same header.
        planted = "P" * 43
        api.client.cookies.set(api.settings.csrf_cookie_name, planted, domain="localhost.local")
        for cookie in api.client.cookies.jar:
            if cookie.name == api.settings.csrf_cookie_name:
                cookie.value = planted
        response = api.post("/api/v1/invitations", body, csrf=planted)
    else:  # a valid token, but of another session
        for cookie in api.client.cookies.jar:
            if cookie.name == api.settings.csrf_cookie_name:
                cookie.value = foreign
        response = api.post("/api/v1/invitations", body, csrf=foreign)

    assert response.status_code == 403, variant
    assert response.json()["code"] == "csrf_failed"


def test_the_matching_csrf_token_is_accepted(apis: ApiFactory, users: UserFactory) -> None:
    api = apis.make()
    api.login(users.make("school_admin"))
    body = {
        "email": users.world.email("ok"),
        "role": "staff",
        "school_id": str(users.world.school_a1),
    }

    assert api.post("/api/v1/invitations", body).status_code == 201


def test_reading_needs_no_csrf_token(api: Api, users: UserFactory) -> None:
    api.login(users.make("staff"))
    api.client.cookies.delete(api.settings.csrf_cookie_name)

    assert api.get("/api/v1/auth/me").status_code == 200


def test_the_csrf_token_is_bound_to_the_session_and_changes_at_every_login(
    api: Api, users: UserFactory
) -> None:
    user = users.make("staff")
    first = api.login(user).json()["csrf_token"]
    second = api.login(user).json()["csrf_token"]

    assert first != second
    assert api.post("/api/v1/auth/logout", csrf=first).status_code == 403  # an old session's token


def test_the_csrf_token_is_derived_from_the_secret_and_the_session_id_only() -> None:
    session_id = hashlib.sha256(b"one").digest()

    token = csrf_token(TEST_SECRET, session_id)

    assert token == csrf_token(TEST_SECRET, session_id)
    assert len(token) == 43 and token.replace("-", "").replace("_", "").isalnum()
    assert token != csrf_token(TEST_SECRET, hashlib.sha256(b"two").digest())
    other_secret = type(TEST_SECRET)("a-completely-different-secret-of-32-chars!!")
    assert token != csrf_token(other_secret, session_id)
    assert token != hashlib.sha256(session_id).hexdigest()[:43]  # not a bare hash: needs the key
