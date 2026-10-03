"""QA findings N1 and N2a: when the Origin header is present it decides alone (a friendly Referer
never rescues `Origin: null` or a broken value), and an Origin or Referer that cannot even be parsed
is a refusal, not a server error."""

import pytest

from app.auth.tokens import new_token
from tests.authsupport import PASSWORD, Api, ApiFactory, UserFactory, no_unhandled_error

LOGIN = "/api/v1/auth/login"
ACCEPT = "/api/v1/invitations/accept"
CONTEXT = "/api/v1/auth/context"
OWN = "http://localhost:3000"
UNKNOWN_MEMBERSHIP = "00000000-0000-4000-8000-000000000001"

# --- N1: when Origin is there it decides alone --------------------------------------------------

BAD_ORIGINS = ["null", "garbage", "", "://", "http://[", "localhost:3000", "ftp://localhost:3000"]


@pytest.mark.parametrize("origin", BAD_ORIGINS, ids=lambda o: o or "empty")
@pytest.mark.parametrize("route", ["login", "accept", "session"])
def test_an_origin_header_that_is_not_the_site_is_not_rescued_by_a_friendly_referer(
    apis: ApiFactory,
    users: UserFactory,
    caplog: pytest.LogCaptureFixture,
    route: str,
    origin: str,
) -> None:
    api = apis.make()
    path, body = {
        "login": (LOGIN, {"email": "someone@example.test", "password": PASSWORD}),
        "accept": (ACCEPT, {"token": new_token(), "full_name": "N One", "password": PASSWORD}),
        "session": (CONTEXT, {"membership_id": UNKNOWN_MEMBERSHIP}),
    }[route]
    if route == "session":
        assert api.login(users.make("staff")).status_code == 200

    response = api.post(path, body, origin=origin, headers={"Referer": f"{OWN}/page"})

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "origin_not_allowed"
    no_unhandled_error(caplog)


@pytest.mark.parametrize("route", ["login", "accept", "session"])
def test_without_an_origin_header_the_own_referer_is_still_enough(
    apis: ApiFactory, users: UserFactory, route: str
) -> None:
    api = apis.make()
    path, body = {
        "login": (LOGIN, {"email": "someone@example.test", "password": PASSWORD}),
        "accept": (ACCEPT, {"token": new_token(), "full_name": "N One", "password": PASSWORD}),
        "session": (CONTEXT, {"membership_id": UNKNOWN_MEMBERSHIP}),
    }[route]
    if route == "session":
        assert api.login(users.make("staff")).status_code == 200

    response = api.post(path, body, origin=False, headers={"Referer": f"{OWN}/page"})

    assert response.json().get("code") != "origin_not_allowed"


# --- N2a: an Origin or Referer that cannot be parsed is a 403, not a 500 -------------------------


@pytest.mark.parametrize(
    "value", ["http://[", "http://[::1", "http://::1]", "http://[]]", "[", "]"]
)
@pytest.mark.parametrize("header", ["Origin", "Referer"])
def test_an_origin_or_referer_that_cannot_be_parsed_is_refused_not_a_server_error(
    api: Api, caplog: pytest.LogCaptureFixture, header: str, value: str
) -> None:
    response = api.post(
        LOGIN,
        {"email": "someone@example.test", "password": PASSWORD},
        origin=False,
        headers={header: value},
    )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "origin_not_allowed"
    no_unhandled_error(caplog)
