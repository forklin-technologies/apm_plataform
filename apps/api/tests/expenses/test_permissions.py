"""Each route, each role: the permission the route declares is the one it enforces. A role without
it gets `403 permission_denied` (before anything about the expense is looked at); a role with it
gets past the gate (here: a 404 for an expense that does not exist, a 201 for a creation)."""

from typing import Any

import pytest

from app.auth.permissions import ROLE_PERMISSIONS, Permission
from tests.expenses.support import Client, Scene, pdf

ROLES = ["organization_admin", "school_admin", "treasurer", "staff", "viewer"]
ANY = "00000000-0000-4000-8000-000000000001"
SUBMIT = {role for role in ROLES if Permission.EXPENSES_SUBMIT in ROLE_PERMISSIONS[role]}
READ = {
    role
    for role in ROLES
    if {Permission.EXPENSES_READ_OWN, Permission.EXPENSES_READ_ALL} & ROLE_PERMISSIONS[role]
}
APPROVE = {role for role in ROLES if Permission.EXPENSES_APPROVE in ROLE_PERMISSIONS[role]}
REIMBURSE = {role for role in ROLES if Permission.REIMBURSEMENTS_REGISTER in ROLE_PERMISSIONS[role]}
CANCEL = SUBMIT | APPROVE
FORM = SUBMIT | READ


def _routes(scene: Scene, school: Any) -> list[tuple[str, str, dict[str, Any], set[str]]]:
    create = {
        "amount_cents": 100,
        "occurred_at": "2026-09-10",
        "category_id": scene.category(school),
        "description": "Cola",
        "paid_by": "APM",
    }
    upload = {"files": {"file": ("a.pdf", pdf(), "application/pdf")}, "data": {"kind": "INVOICE"}}
    return [
        ("GET", "/expense-categories", {}, FORM),
        ("POST", "/expenses", {"json": create}, SUBMIT),
        ("GET", "/expenses", {}, READ),
        ("GET", f"/expenses/{ANY}", {}, READ),
        ("PATCH", f"/expenses/{ANY}", {"json": {"description": "x"}}, SUBMIT),
        ("POST", f"/expenses/{ANY}/attachments", upload, SUBMIT),
        ("GET", f"/expenses/{ANY}/attachments/{ANY}", {}, READ),
        ("POST", f"/expenses/{ANY}/submit", {}, SUBMIT),
        ("POST", f"/expenses/{ANY}/approve", {"json": {}}, APPROVE),
        ("POST", f"/expenses/{ANY}/reject", {"json": {"reason": "nao pode"}}, APPROVE),
        (
            "POST",
            f"/expenses/{ANY}/request-correction",
            {"json": {"reason": "corrija"}},
            APPROVE,
        ),
        (
            "POST",
            f"/expenses/{ANY}/reimburse",
            {"json": {"payment_reference": "TED-1"}},
            REIMBURSE,
        ),
        ("POST", f"/expenses/{ANY}/pay", {}, REIMBURSE),
        ("POST", f"/expenses/{ANY}/cancel", {}, CANCEL),
    ]


def test_the_sets_of_roles_are_what_the_permission_map_says() -> None:
    """If the map of roles changes, this table must be looked at again."""
    admins = {"organization_admin", "school_admin"}
    can_write, can_read, can_decide = (
        admins | {"staff"},
        admins | {"treasurer", "staff"},
        admins | {"treasurer"},
    )
    assert (can_write, can_read, can_decide, can_decide) == (SUBMIT, READ, APPROVE, REIMBURSE)
    assert "viewer" not in FORM | CANCEL


@pytest.mark.parametrize("role", ROLES)
def test_every_route_enforces_its_permission(scene: Scene, role: str) -> None:
    person: Client = scene.person(role, school=None if role == "organization_admin" else "1")
    checked = 0
    for method, tail, kwargs, allowed in _routes(scene, person.school):
        response = person.request(method, tail, **kwargs)
        where = f"{role} {method} {tail}"
        if role in allowed:
            assert response.status_code != 403, (where, response.text)
            assert response.status_code in (200, 201, 404), (where, response.text)
        else:
            assert response.status_code == 403, (where, response.text)
            assert response.json()["code"] == "permission_denied", where
        checked += 1
    assert checked == 14


def test_a_person_with_no_school_chosen_yet_is_asked_to_choose(scene: Scene) -> None:
    both = (("staff", "a", "1"), ("staff", "a", "2"))
    user = scene.users.make("staff", school="1", extra=both[1:])
    api = scene.apis.make(configure=scene._configure)
    assert api.login(user).json()["active_membership"] is None  # two memberships: none chosen

    response = api.get(f"/api/v1/schools/{scene.world.school_a1}/expenses")

    assert response.status_code == 409 and response.json()["code"] == "context_required"
