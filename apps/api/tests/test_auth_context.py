"""A2/A3: choosing where to act, a session that stays true to the database, and the tenant that
comes from the session and from nothing the client sends."""

import hashlib
import uuid
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.auth.permissions import ROLE_PERMISSIONS, Permission, permissions_for
from app.routers.deps import get_tenant_db
from tests.authsupport import Api, ApiFactory, UserFactory, World

ME = "/api/v1/auth/me"


def session_state(engine: Engine, token: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT membership_id, revoked_at, revoked_reason, expires_at FROM sessions "
                "WHERE id = :id"
            ),
            {"id": hashlib.sha256(token.encode()).digest()},
        ).one()


# --- switching context -------------------------------------------------------------------------


def test_a_user_with_two_memberships_must_choose_before_acting(
    api: Api, users: UserFactory
) -> None:
    user = users.make("school_admin", extra=(("viewer", "a", "2"),), label="two")
    api.login(user)

    response = api.invite(users.world.email("x"), "staff", users.world.school_a1)

    assert response.status_code == 409
    assert response.json()["code"] == "context_required"


def test_switching_rotates_the_token_and_keeps_the_absolute_expiry(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("school_admin", extra=(("viewer", "a", "2"),), label="two")
    api.login(user)
    old_token, old_csrf = api.session_token, api.csrf
    assert old_token is not None
    expiry = session_state(admin_engine, old_token).expires_at

    response = api.switch(user.membership_ids[1])

    assert response.status_code == 200
    body = response.json()
    assert body["active_membership"]["role"] == "viewer"
    assert body["active_membership"]["permissions"] == ["reports:read_aggregate"]
    new_token = api.session_token
    assert new_token not in (None, old_token) and api.csrf != old_csrf
    assert session_state(admin_engine, new_token or "").expires_at == expiry  # no extension
    old = session_state(admin_engine, old_token)
    assert old.revoked_at is not None and old.revoked_reason == "rotated"
    api.set_session_cookie(old_token)
    assert api.get(ME).status_code == 401  # the old token is dead, whoever holds it


def test_switching_to_somebody_elses_membership_or_an_unknown_one_is_the_same_answer(
    api: Api, users: UserFactory
) -> None:
    mine = users.make("school_admin", extra=(("viewer", "a", "2"),), label="two")
    theirs = users.make("staff", org="b", school="1", label="other")
    api.login(mine)

    foreign = api.switch(theirs.membership_ids[0])
    unknown = api.switch(uuid.uuid4())

    for response in (foreign, unknown):
        assert response.status_code == 403
        assert response.json()["code"] == "context_not_allowed"
    assert {k: v for k, v in foreign.json().items() if k != "request_id"} == {
        k: v for k, v in unknown.json().items() if k != "request_id"
    }
    assert api.get(ME).json()["active_membership"] is None  # and nothing changed


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"membership_id": "not-a-uuid"},
        {"membership_id": None},
        {"membership_id": str(uuid.uuid4()), "organization_id": str(uuid.uuid4())},
        {"membership_id": str(uuid.uuid4()), "role": "organization_admin"},
    ],
)
def test_a_malformed_switch_is_a_422(api: Api, users: UserFactory, body: dict[str, Any]) -> None:
    api.login(users.make("staff"))

    response = api.post("/api/v1/auth/context", body)

    assert response.status_code == 422 and response.json()["code"] == "validation_error"


def test_switching_needs_a_session(api: Api) -> None:
    response = api.post("/api/v1/auth/context", {"membership_id": str(uuid.uuid4())}, csrf=False)

    assert response.status_code == 401


# --- the session follows the database ------------------------------------------------------------


def test_a_suspended_membership_ends_the_session_on_the_next_request(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("staff")
    api.login(user)
    token = api.session_token
    assert token is not None
    assert api.get(ME).status_code == 200

    users.set_membership_status(user.membership_ids[0], "suspended")

    response = api.get(ME)
    assert response.status_code == 401 and response.json()["code"] == "session_revoked"
    assert session_state(admin_engine, token).revoked_reason == "membership_inactive"


def test_a_deactivated_user_loses_the_session_on_the_next_request(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("staff")
    api.login(user)
    token = api.session_token
    assert token is not None

    users.set_active(user.id, False)

    response = api.get(ME)
    assert response.status_code == 401 and response.json()["code"] == "session_revoked"
    assert session_state(admin_engine, token).revoked_reason == "user_inactive"


def test_a_change_of_role_takes_effect_on_the_next_request(
    api: Api, users: UserFactory, admin_engine: Engine
) -> None:
    user = users.make("school_admin")
    api.login(user)
    school = users.world.school_a1
    assert api.invite(users.world.email("a"), "staff", school).status_code == 201

    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE memberships SET role = 'viewer' WHERE id = :id"),
            {"id": user.membership_ids[0]},
        )

    assert api.get(ME).json()["active_membership"]["role"] == "viewer"
    assert api.invite(users.world.email("b"), "staff", school).status_code == 403


def test_a_membership_that_was_added_later_shows_up_without_a_new_login(
    api: Api, users: UserFactory, admin_engine: Engine, world: World
) -> None:
    user = users.make("staff")
    api.login(user)
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
                "VALUES (:u, :o, :s, 'viewer', 'active')"
            ),
            {"u": user.id, "o": world.org_a, "s": world.school_a2},
        )

    assert len(api.get(ME).json()["memberships"]) == 2


# --- the tenant comes from the session, from nothing else -------------------------------------


def _tenant_routes(app: FastAPI) -> None:
    """A route that reads with the tenant-bound session, to see what the session can reach."""

    @app.get("/api/test/tenant")
    def tenant(db: Session = Depends(get_tenant_db)) -> dict[str, list[str]]:  # noqa: B008
        schools = [r[0] for r in db.execute(text("SELECT slug FROM schools ORDER BY slug"))]
        members = [str(r[0]) for r in db.execute(text("SELECT id FROM memberships ORDER BY id"))]
        return {"schools": schools, "memberships": members}


@pytest.fixture
def tenant_api(apis: ApiFactory) -> Api:
    return apis.make(configure=_tenant_routes)


def _slugs(api: Api, world: World, **kwargs: Any) -> set[str]:
    response = api.get("/api/test/tenant", **kwargs)
    assert response.status_code == 200, response.text
    return {s for s in response.json()["schools"] if s.endswith(world.suffix)}


def test_each_role_reaches_exactly_its_own_scope(
    apis: ApiFactory, users: UserFactory, world: World
) -> None:
    seen: dict[str, set[str]] = {}
    for label, user in {
        "school-a1": users.make("staff", org="a", school="1"),
        "school-a2": users.make("viewer", org="a", school="2"),
        "org-a": users.make("organization_admin", org="a", school=None),
        "school-b1": users.make("staff", org="b", school="1"),
        "org-b": users.make("organization_admin", org="b", school=None),
    }.items():
        client = apis.make(configure=_tenant_routes)
        client.login(user)
        seen[label] = _slugs(client, world)

    assert seen == {
        "school-a1": {f"t4-a1-{world.suffix}"},
        "school-a2": {f"t4-a2-{world.suffix}"},
        "org-a": {f"t4-a1-{world.suffix}", f"t4-a2-{world.suffix}"},
        "school-b1": {f"t4-b1-{world.suffix}"},
        "org-b": {f"t4-b1-{world.suffix}"},
    }


def test_the_tenant_is_not_taken_from_a_header_the_query_or_the_body(
    tenant_api: Api, users: UserFactory, world: World
) -> None:
    tenant_api.login(users.make("staff", org="a", school="1"))
    forged = {
        "X-Organization-Id": str(world.org_b),
        "X-School-Id": str(world.school_b1),
        "X-Tenant": str(world.org_b),
    }

    with_headers = _slugs(tenant_api, world, headers=forged)
    with_query = _slugs(
        tenant_api,
        world,
        params={"organization_id": str(world.org_b), "school_id": str(world.school_b1)},
    )

    assert with_headers == with_query == {f"t4-a1-{world.suffix}"}


def test_a_session_without_a_context_reaches_no_tenant_route(
    tenant_api: Api, users: UserFactory
) -> None:
    tenant_api.login(users.make("staff", extra=(("viewer", "a", "2"),), label="two"))

    response = tenant_api.get("/api/test/tenant")

    assert response.status_code == 409 and response.json()["code"] == "context_required"


def test_switching_context_moves_the_tenant_with_it(
    tenant_api: Api, users: UserFactory, world: World
) -> None:
    user = users.make("staff", org="a", school="1", extra=(("viewer", "a", "2"),), label="two")
    tenant_api.login(user)

    tenant_api.switch(user.membership_ids[0])
    first = _slugs(tenant_api, world)
    tenant_api.switch(user.membership_ids[1])
    second = _slugs(tenant_api, world)

    assert first == {f"t4-a1-{world.suffix}"} and second == {f"t4-a2-{world.suffix}"}


def test_a_tenant_route_without_a_session_is_a_401(tenant_api: Api) -> None:
    assert tenant_api.get("/api/test/tenant").status_code == 401


# --- the permission map -------------------------------------------------------------------------


def test_the_permission_map_matches_what_the_roles_of_the_database_allow(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as connection:
        definition: Any = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'memberships'::regclass AND contype = 'c' "
                "AND pg_get_constraintdef(oid) LIKE '%viewer%'"
            )
        ).scalar_one()

    in_database = {
        part.split("'")[1] for part in definition.split("ARRAY[")[1].split("]")[0].split(",")
    }
    assert in_database == set(ROLE_PERMISSIONS)


def test_what_each_role_may_do() -> None:
    everything = set(Permission)
    assert permissions_for("organization_admin") == everything
    assert permissions_for("school_admin") == everything - {Permission.MONTHS_REOPEN}
    assert permissions_for("treasurer") == {
        Permission.CONTRIBUTIONS_RECORD_CASH,
        Permission.EXPENSES_READ_ALL,
        Permission.EXPENSES_APPROVE,
        Permission.REIMBURSEMENTS_REGISTER,
        Permission.REFUNDS_REGISTER,
        Permission.MONTHS_CLOSE,
        Permission.STATEMENT_READ,
        Permission.REPORTS_READ,
    }
    assert permissions_for("staff") == {Permission.EXPENSES_SUBMIT, Permission.EXPENSES_READ_OWN}
    assert permissions_for("viewer") == {Permission.REPORTS_READ_AGGREGATE}
    for unknown in (None, "", "root", "ORGANIZATION_ADMIN", "parent"):
        assert permissions_for(unknown) == frozenset()  # a role that does not exist can do nothing


def test_only_the_organization_admin_can_reopen_a_month_and_nobody_else_manages_access() -> None:
    can_reopen = {r for r, p in ROLE_PERMISSIONS.items() if Permission.MONTHS_REOPEN in p}
    can_invite = {r for r, p in ROLE_PERMISSIONS.items() if Permission.INVITATIONS_CREATE in p}

    assert can_reopen == {"organization_admin"}
    assert can_invite == {"organization_admin", "school_admin"}
