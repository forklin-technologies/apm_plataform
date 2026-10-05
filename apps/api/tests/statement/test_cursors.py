"""A cursor written by hand is a 422 `invalid_cursor`, never a 500 or a database error."""

import base64
import json
from typing import Any

import pytest

from tests.statement.conftest import Login, Scene
from tests.statement.test_closing_routes import close, closings

GOOD_INSTANT = "2025-03-03T12:00:00+00:00"
GOOD_UUID = "6f0e2b5e-0b57-4a50-9a8f-0f2a8b1c3d4e"


def forge(payload: Any) -> str:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


STATEMENT_FORGERIES: list[Any] = [
    {"settled_at": GOOD_INSTANT, "reference_code": 10**30},  # does not fit a bigint
    {"settled_at": GOOD_INSTANT, "reference_code": 2**63},
    {"settled_at": GOOD_INSTANT, "reference_code": -1},
    {"settled_at": GOOD_INSTANT, "reference_code": True},
    {"settled_at": GOOD_INSTANT, "reference_code": 1.5},
    {"settled_at": GOOD_INSTANT, "reference_code": "1"},
    {"settled_at": GOOD_INSTANT, "reference_code": None},
    {"settled_at": GOOD_INSTANT, "reference_code": [1]},
    {"settled_at": 5, "reference_code": 1},
    {"settled_at": None, "reference_code": 1},
    {"settled_at": "2025-03-03T12:00:00", "reference_code": 1},  # no offset
    {"settled_at": "2025-03-03", "reference_code": 1},
    {"settled_at": "0001-01-01T00:00:00+00:00", "reference_code": 1},  # outside every real date
    {"settled_at": "9999-12-31T23:59:59+00:00", "reference_code": 1},
    {"settled_at": "2025-03-03T12:00:00+23:59", "reference_code": 1},  # offset Postgres refuses
    {"settled_at": "x" * 45, "reference_code": 1},
    {"settled_at": GOOD_INSTANT, "reference_code": 1, "extra": 1},
    {"settled_at": GOOD_INSTANT},
    [],
    [GOOD_INSTANT, 1],
    "just a string",
    5,
    None,
]
PENDING_FORGERIES: list[Any] = [
    {"occurred_at": GOOD_INSTANT, "reference_code": 10**30},
    {"occurred_at": GOOD_INSTANT, "reference_code": True},
    {"occurred_at": "2025-03-03T12:00:00", "reference_code": 1},
    {"occurred_at": "0001-01-01T00:00:00+00:00", "reference_code": 1},
    {"settled_at": GOOD_INSTANT, "reference_code": 1},  # the cursor of another route
]
CLOSINGS_FORGERIES: list[Any] = [
    {"closed_at": GOOD_INSTANT, "id": 5},
    {"closed_at": GOOD_INSTANT, "id": None},
    {"closed_at": GOOD_INSTANT, "id": [GOOD_UUID]},
    {"closed_at": GOOD_INSTANT, "id": "not-a-uuid"},
    {"closed_at": GOOD_INSTANT, "id": GOOD_UUID[:-1]},
    {"closed_at": GOOD_INSTANT, "id": GOOD_UUID.replace("-", "")},
    {"closed_at": "0001-01-01T00:00:00+00:00", "id": GOOD_UUID},
    {"closed_at": "2025-03-03T12:00:00", "id": GOOD_UUID},
    {"closed_at": 5, "id": GOOD_UUID},
    {"closed_at": GOOD_INSTANT},
    {"settled_at": GOOD_INSTANT, "reference_code": 1},  # the cursor of another route
]


def assert_invalid_cursor(response: Any) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"] == [{"field": "cursor", "code": "invalid_cursor"}]


@pytest.mark.parametrize("payload", STATEMENT_FORGERIES, ids=lambda p: json.dumps(p)[:60])
def test_a_forged_cursor_of_the_statement_is_a_422(
    scene: Scene, login: Login, payload: Any
) -> None:
    api = login(scene.users["treasurer"])

    response = api.get(
        f"/api/v1/schools/{scene.fresh.school}/statement",
        params={"period": "2025-03", "cursor": forge(payload)},
    )

    assert_invalid_cursor(response)


@pytest.mark.parametrize("payload", PENDING_FORGERIES, ids=lambda p: json.dumps(p)[:60])
def test_a_forged_cursor_of_the_pending_list_is_a_422(
    scene: Scene, login: Login, payload: Any
) -> None:
    api = login(scene.users["treasurer"])

    response = api.get(
        f"/api/v1/schools/{scene.fresh.school}/statement/pending", params={"cursor": forge(payload)}
    )

    assert_invalid_cursor(response)


@pytest.mark.parametrize("payload", CLOSINGS_FORGERIES, ids=lambda p: json.dumps(p)[:60])
def test_a_forged_cursor_of_the_closings_is_a_422(scene: Scene, login: Login, payload: Any) -> None:
    api = login(scene.users["treasurer"])
    assert close(api, scene).status_code == 201

    response = api.get(closings(scene.fresh.school), params={"cursor": forge(payload)})

    assert_invalid_cursor(response)


def test_a_well_formed_cursor_that_points_nowhere_just_gives_an_empty_page(
    scene: Scene, login: Login
) -> None:
    api = login(scene.users["treasurer"])
    last = {"settled_at": "2099-01-01T00:00:00+00:00", "reference_code": 2**63 - 1}

    response = api.get(
        f"/api/v1/schools/{scene.fresh.school}/statement",
        params={"period": "2025-03", "cursor": forge(last)},
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
