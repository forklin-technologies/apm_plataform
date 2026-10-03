"""QA finding N2b and N2c: nothing a client can put in a text field, a header or a cookie makes the
API answer 500. Text the database or the driver cannot take (a NUL byte, a lone surrogate) is a 422
in EVERY text field of an authentication request, and a CSRF token that is not ASCII is a failed
check, not a TypeError."""

from typing import Any

import pytest

from app.auth.tokens import new_token
from tests.authsupport import PASSWORD, Api, UserFactory, no_unhandled_error, raw_post

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ACCEPT = "/api/v1/invitations/accept"
CONTEXT = "/api/v1/auth/context"
CHANGE = "/api/v1/auth/password"
INVITE = "/api/v1/invitations"
UNKNOWN_MEMBERSHIP = "00000000-0000-4000-8000-000000000001"

# --- N2b: text a database or a driver cannot take is a 422 ---------------------------------------

NUL = "\x00"
SURROGATE = "\ud800"
TOKEN = new_token()
BAD_TEXT_CASES: list[tuple[str, dict[str, Any]]] = [
    (LOGIN, {"email": f"a{NUL}b@example.test", "password": PASSWORD}),
    (LOGIN, {"email": f"a@example.test{NUL}", "password": PASSWORD}),
    (LOGIN, {"email": f"{SURROGATE}@example.test", "password": PASSWORD}),
    (LOGIN, {"email": "a@example.test", "password": f"a long {NUL} password"}),
    (LOGIN, {"email": "a@example.test", "password": f"a long {SURROGATE} password"}),
    (ACCEPT, {"token": TOKEN, "full_name": f"A{NUL}", "password": PASSWORD}),
    (ACCEPT, {"token": TOKEN, "full_name": SURROGATE, "password": PASSWORD}),
    (ACCEPT, {"token": TOKEN, "full_name": "A", "password": f"a long {NUL} password"}),
    (ACCEPT, {"token": f"{TOKEN[:-1]}{NUL}", "full_name": "A", "password": PASSWORD}),
    (ACCEPT, {"token": SURROGATE, "full_name": "A", "password": PASSWORD}),
]


@pytest.mark.parametrize(("path", "body"), BAD_TEXT_CASES)
def test_a_nul_byte_or_a_lone_surrogate_in_an_unauthenticated_body_is_a_422(
    api: Api, caplog: pytest.LogCaptureFixture, path: str, body: dict[str, Any]
) -> None:
    response = raw_post(api, path, body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert NUL not in response.text and SURROGATE not in response.text
    no_unhandled_error(caplog)


@pytest.mark.parametrize("bad", [NUL, SURROGATE], ids=["nul", "surrogate"])
def test_the_same_text_is_refused_in_the_bodies_of_a_logged_in_user(
    api: Api, users: UserFactory, caplog: pytest.LogCaptureFixture, bad: str
) -> None:
    assert api.login(users.make("organization_admin", school=None)).status_code == 200
    cases = [
        (CHANGE, {"current_password": f"x{bad}y", "new_password": "a long enough password"}),
        (CHANGE, {"current_password": PASSWORD, "new_password": f"a long {bad} password"}),
        (INVITE, {"email": f"a{bad}@example.test", "role": "staff"}),
        (INVITE, {"email": "a@example.test", "role": f"staff{bad}"}),
    ]

    for path, body in cases:
        response = raw_post(api, path, body, csrf=True)
        assert response.status_code == 422, (path, response.text)
    no_unhandled_error(caplog)


# --- N2c: a CSRF header or cookie that is not ASCII is a failed check, not a TypeError -----------


@pytest.mark.parametrize("where", ["header", "cookie", "both"])
@pytest.mark.parametrize("path", [CONTEXT, LOGOUT])
def test_a_csrf_token_with_a_byte_that_is_not_ascii_fails_the_check(
    api: Api,
    users: UserFactory,
    caplog: pytest.LogCaptureFixture,
    path: str,
    where: str,
) -> None:
    assert api.login(users.make("staff")).status_code == 200
    real_csrf = (api.csrf or "").encode()
    session = f"{api.settings.session_cookie_name}={api.session_token}".encode()
    csrf_name = api.settings.csrf_cookie_name.encode()
    headers: dict[str, Any] = {"Origin": api.origin}
    headers["X-CSRF-Token"] = b"caf\xe9" if where in ("header", "both") else real_csrf
    cookie_value = b"caf\xe9" if where in ("cookie", "both") else real_csrf
    headers["Cookie"] = session + b"; " + csrf_name + b"=" + cookie_value

    response = api.client.post(path, json={"membership_id": UNKNOWN_MEMBERSHIP}, headers=headers)

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "csrf_failed"
    no_unhandled_error(caplog)
