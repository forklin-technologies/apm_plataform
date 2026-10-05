"""POST /schools/{school_id}/contributions: the treasury records a contribution in cash."""

import re
import uuid

import pytest
from sqlalchemy import Engine, text

from tests.authsupport import ApiFactory, UserFactory, World


def _login(apis: ApiFactory, users: UserFactory, role: str, school: str = "1"):  # type: ignore[no-untyped-def]
    user = users.make(role, school=school)
    api = apis.make()
    assert api.login(user).status_code == 200
    return api, user


def _url(world: World) -> str:
    return f"/api/v1/schools/{world.school_a1}/contributions"


def test_the_treasury_records_a_cash_contribution(
    apis: ApiFactory, users: UserFactory, world: World, admin_engine: Engine
) -> None:
    api, user = _login(apis, users, "treasurer")
    response = api.post(
        _url(world), {"amount_cents": 5000, "guardian_name": "Maria Exemplo", "class_name": "4B"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert re.fullmatch(r"APM-\d{6}", body["reference_code"])
    assert body["status"] == "PAID" and body["amount_cents"] == 5000 and body["method"] == "CASH"
    with admin_engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT f.status, f.direction, f.settled_at IS NOT NULL, f.created_by_user_id, "
                "f.origin_name, c.method, c.guardian_name, c.class_name, c.receipt_token_hash "
                "FROM financial_transactions f JOIN contributions c ON c.transaction_id = f.id "
                "WHERE f.id = :id"
            ),
            {"id": uuid.UUID(body["id"])},
        ).one()
    assert tuple(stored) == ("PAID", "IN", True, user.id, None, "CASH", "Maria Exemplo", "4B", None)


@pytest.mark.parametrize("role", ["school_admin", "treasurer"])
def test_the_roles_that_may_record_cash(
    apis: ApiFactory, users: UserFactory, world: World, role: str
) -> None:
    api, _ = _login(apis, users, role)
    assert api.post(_url(world), {"amount_cents": 1000, "method": "TRANSFER"}).status_code == 201


@pytest.mark.parametrize("role", ["staff", "viewer"])
def test_the_roles_that_may_not(
    apis: ApiFactory, users: UserFactory, world: World, role: str
) -> None:
    api, _ = _login(apis, users, role)
    response = api.post(_url(world), {"amount_cents": 1000})
    assert response.status_code == 403 and response.json()["code"] == "permission_denied"


def test_another_school_or_no_session(apis: ApiFactory, users: UserFactory, world: World) -> None:
    api, _ = _login(apis, users, "treasurer")
    other = api.post(f"/api/v1/schools/{world.school_a2}/contributions", {"amount_cents": 1000})
    unknown = api.post(f"/api/v1/schools/{uuid.uuid4()}/contributions", {"amount_cents": 1000})
    assert other.status_code == unknown.status_code == 404  # the same answer
    anonymous = apis.make().post(_url(world), {"amount_cents": 1000})
    assert anonymous.status_code in (401, 403)


@pytest.mark.parametrize(
    "body",
    [
        {"amount_cents": 0},
        {"amount_cents": -5},
        {"amount_cents": 1000, "method": "PIX"},
        {"amount_cents": 1000, "category_key": "refund"},
        {"amount_cents": 1000, "contributor_email": "nope"},
        {"amount_cents": 1000, "unknown": "x"},
    ],
)
def test_invalid_bodies_are_refused(
    apis: ApiFactory, users: UserFactory, world: World, body: dict[str, object]
) -> None:
    api, _ = _login(apis, users, "treasurer")
    assert api.post(_url(world), body).status_code == 422


def test_a_donation_goes_to_its_own_category(
    apis: ApiFactory, users: UserFactory, world: World, admin_engine: Engine
) -> None:
    api, _ = _login(apis, users, "treasurer")
    body = api.post(_url(world), {"amount_cents": 2500, "category_key": "donation"}).json()
    with admin_engine.connect() as conn:
        key: str = conn.execute(
            text(
                "SELECT c.key FROM financial_transactions f "
                "JOIN categories c ON c.id = f.category_id WHERE f.id = :id"
            ),
            {"id": uuid.UUID(body["id"])},
        ).scalar_one()
    assert key == "donation"
