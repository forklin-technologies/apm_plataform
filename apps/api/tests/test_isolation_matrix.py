"""T2: the isolation matrix.

For every table, every operation and every context, the expected outcome is written down here
as data, and checked against the real database twice: as the application role (apm_app), and as
the table owner (apm_owner, simulated with SET ROLE) to prove FORCE ROW LEVEL SECURITY. The
tests run on the configured database, so dropping a policy, removing FORCE or granting BYPASSRLS
makes them fail.

Contexts (the subject row always belongs to organization A, school A1):
  own_org                 organization A, no school: the whole organization
  own_school              organization A, school A1: the school of the subject row
  same_org_other_school   organization A, school A2: another school of the same organization
  other_org               organization B
  no_context              nothing set: fails closed
"""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.tenant import TenantContext
from tests.dbsupport import Tenants, transaction

CONTEXTS = ["own_org", "own_school", "same_org_other_school", "other_org", "no_context"]
TABLES = ["organizations", "schools", "memberships", "users"]
OPERATIONS = ["select", "insert", "update", "delete"]

ALL_OWN = {"own_org", "own_school", "same_org_other_school"}
SCHOOL_SCOPED = {"own_org", "own_school"}
NOBODY: set[str] = set()

# What takes effect, per role: the set of contexts in which the operation reaches the subject row
# (select sees it, insert/update/delete affect it). Everything else must be denied, either with an
# error or by affecting zero rows.
EXPECTED_APP: dict[tuple[str, str], set[str]] = {
    # The tenant row has no school scope. apm_app cannot create or delete organizations.
    ("organizations", "select"): ALL_OWN,
    ("organizations", "insert"): NOBODY,
    ("organizations", "update"): ALL_OWN,
    ("organizations", "delete"): NOBODY,
    # An org-wide context sees every school; a school context only its own. Creating a school needs
    # an org-wide context. No DELETE privilege.
    ("schools", "select"): SCHOOL_SCOPED,
    ("schools", "insert"): {"own_org"},
    ("schools", "update"): SCHOOL_SCOPED,
    ("schools", "delete"): NOBODY,
    ("memberships", "select"): SCHOOL_SCOPED,
    ("memberships", "insert"): SCHOOL_SCOPED,
    ("memberships", "update"): SCHOOL_SCOPED,
    ("memberships", "delete"): SCHOOL_SCOPED,
    # Visible through a visible membership; no write privilege until authentication exists.
    ("users", "select"): SCHOOL_SCOPED,
    ("users", "insert"): NOBODY,
    ("users", "update"): NOBODY,
    ("users", "delete"): NOBODY,
}

# The owner has every privilege, so only the policies limit it (FORCE ROW LEVEL SECURITY). There is
# no DELETE policy on organizations/schools and no write policy on users, so those stay denied.
EXPECTED_OWNER: dict[tuple[str, str], set[str]] = {
    **EXPECTED_APP,
    ("organizations", "insert"): ALL_OWN,  # with the NEW organization as the context
}


def _context(name: str, tenants: Tenants, own_org: uuid.UUID) -> TenantContext | None:
    return {
        "own_org": TenantContext(own_org),
        "own_school": TenantContext(own_org, tenants.school_a1),
        "same_org_other_school": TenantContext(own_org, tenants.school_a2),
        "other_org": TenantContext(tenants.org_b),
        "no_context": None,
    }[name]


def _fresh() -> str:
    return uuid.uuid4().hex[:10]


# (table, operation) -> builder(tenants, new_organization_id) -> (sql, params, "count" | "rowcount")
Statement = tuple[str, dict[str, Any], str]
STATEMENTS: dict[tuple[str, str], Callable[[Tenants, uuid.UUID], Statement]] = {
    ("organizations", "select"): lambda t, new: (
        "SELECT count(*) FROM organizations WHERE id = :id", {"id": t.org_a}, "count"),
    ("organizations", "insert"): lambda t, new: (
        "INSERT INTO organizations (id, name, slug) VALUES (:id, 'Fresh', :slug)",
        {"id": new, "slug": f"t3-new-{_fresh()}"}, "rowcount"),
    ("organizations", "update"): lambda t, new: (
        "UPDATE organizations SET name = 'Renamed' WHERE id = :id", {"id": t.org_a}, "rowcount"),
    ("organizations", "delete"): lambda t, new: (
        "DELETE FROM organizations WHERE id = :id", {"id": t.org_a}, "rowcount"),
    ("schools", "select"): lambda t, new: (
        "SELECT count(*) FROM schools WHERE id = :id", {"id": t.school_a1}, "count"),
    ("schools", "insert"): lambda t, new: (
        "INSERT INTO schools (organization_id, name, slug) VALUES (:org, 'Fresh', :slug)",
        {"org": t.org_a, "slug": f"t3-new-{_fresh()}"}, "rowcount"),
    ("schools", "update"): lambda t, new: (
        "UPDATE schools SET name = 'Renamed' WHERE id = :id", {"id": t.school_a1}, "rowcount"),
    ("schools", "delete"): lambda t, new: (
        "DELETE FROM schools WHERE id = :id", {"id": t.school_a1}, "rowcount"),
    ("memberships", "select"): lambda t, new: (
        "SELECT count(*) FROM memberships WHERE id = :id", {"id": t.membership_a1}, "count"),
    ("memberships", "insert"): lambda t, new: (
        "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
        "VALUES (:user, :org, :school, 'staff', 'active')",
        {"user": t.user_free, "org": t.org_a, "school": t.school_a1}, "rowcount"),
    ("memberships", "update"): lambda t, new: (
        "UPDATE memberships SET status = 'suspended' WHERE id = :id",
        {"id": t.membership_a1}, "rowcount"),
    ("memberships", "delete"): lambda t, new: (
        "DELETE FROM memberships WHERE id = :id", {"id": t.membership_a1}, "rowcount"),
    ("users", "select"): lambda t, new: (
        "SELECT count(*) FROM users WHERE id = :id", {"id": t.user_a1}, "count"),
    ("users", "insert"): lambda t, new: (
        "INSERT INTO users (email, full_name) VALUES (:email, 'Fresh')",
        {"email": f"t3-new-{_fresh()}@example.test"}, "rowcount"),
    ("users", "update"): lambda t, new: (
        "UPDATE users SET full_name = 'Renamed' WHERE id = :id", {"id": t.user_a1}, "rowcount"),
    ("users", "delete"): lambda t, new: (
        "DELETE FROM users WHERE id = :id", {"id": t.user_a1}, "rowcount"),
}


def _takes_effect(
    engine: Engine, *, owner: bool, table: str, operation: str, context_name: str, tenants: Tenants
) -> bool:
    new_org = uuid.uuid4()
    # Creating an organization is only possible with that organization as the context.
    own_org = new_org if (table, operation) == ("organizations", "insert") else tenants.org_a
    sql, params, mode = STATEMENTS[(table, operation)](tenants, new_org)
    with transaction(
        engine, owner=owner, context=_context(context_name, tenants, own_org)
    ) as connection:
        try:
            result = connection.execute(text(sql), params)
            return bool(result.scalar_one() == 1) if mode == "count" else result.rowcount == 1
        except DBAPIError:
            return False


CASES = [
    pytest.param(table, operation, context, id=f"{table}-{operation}-{context}")
    for table in TABLES
    for operation in OPERATIONS
    for context in CONTEXTS
]


@pytest.mark.parametrize(("table", "operation", "context_name"), CASES)
def test_application_role_matrix(
    app_engine: Engine, tenants: Tenants, table: str, operation: str, context_name: str
) -> None:
    expected = context_name in EXPECTED_APP[(table, operation)]

    actual = _takes_effect(
        app_engine,
        owner=False,
        table=table,
        operation=operation,
        context_name=context_name,
        tenants=tenants,
    )

    assert actual is expected


@pytest.mark.parametrize(("table", "operation", "context_name"), CASES)
def test_owner_is_also_bound_by_the_policies_force_rls(
    admin_engine: Engine, tenants: Tenants, table: str, operation: str, context_name: str
) -> None:
    expected = context_name in EXPECTED_OWNER[(table, operation)]

    actual = _takes_effect(
        admin_engine,
        owner=True,
        table=table,
        operation=operation,
        context_name=context_name,
        tenants=tenants,
    )

    assert actual is expected


# --- tenant-hop: a write that tries to move a row into somebody else's tenant -------------------

# (description, sql, context name) run as both roles. Each must be refused (an error: WITH CHECK or
# the composite foreign key), and the row must be unchanged afterwards.
HOPS: list[tuple[str, str, str]] = [
    (
        "school moves to another organization",
        "UPDATE schools SET organization_id = :org_b WHERE id = :school_a1",
        "own_org",
    ),
    (
        "membership moves to another organization",
        "UPDATE memberships SET organization_id = :org_b WHERE id = :membership_a1",
        "own_org",
    ),
    (
        "membership points at a school of another organization (composite FK)",
        "UPDATE memberships SET school_id = :school_b1 WHERE id = :membership_a1",
        "own_org",
    ),
    (
        "membership moves to another school of the same organization from a school context",
        "UPDATE memberships SET school_id = :school_a2 WHERE id = :membership_a1",
        "own_school",
    ),
    (
        "organization takes the identity of another organization",
        "UPDATE organizations SET id = :org_b WHERE id = :org_a",
        "own_org",
    ),
    (
        "new membership written into another organization",
        "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
        "VALUES (:user_free, :org_b, NULL, 'viewer', 'active')",
        "own_org",
    ),
    (
        "new school written into another organization",
        "INSERT INTO schools (organization_id, name, slug) VALUES (:org_b, 'Hop', :slug)",
        "own_org",
    ),
    (
        "new membership with a school of another organization (composite FK)",
        "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
        "VALUES (:user_free, :org_a, :school_b1, 'viewer', 'active')",
        "own_org",
    ),
]


@pytest.mark.parametrize("owner", [False, True], ids=["apm_app", "apm_owner"])
@pytest.mark.parametrize(("description", "sql", "context_name"), HOPS, ids=[h[0] for h in HOPS])
def test_tenant_hop_is_refused(
    app_engine: Engine,
    admin_engine: Engine,
    tenants: Tenants,
    owner: bool,
    description: str,
    sql: str,
    context_name: str,
) -> None:
    engine = admin_engine if owner else app_engine
    params = {
        "org_a": tenants.org_a,
        "org_b": tenants.org_b,
        "school_a1": tenants.school_a1,
        "school_a2": tenants.school_a2,
        "school_b1": tenants.school_b1,
        "membership_a1": tenants.membership_a1,
        "user_free": tenants.user_free,
        "slug": f"t3-hop-{_fresh()}",
    }
    with transaction(
        engine, owner=owner, context=_context(context_name, tenants, tenants.org_a)
    ) as connection:
        with pytest.raises(DBAPIError):
            connection.execute(text(sql), params)

    # Nothing moved: the subject rows are exactly where the fixture put them.
    with admin_engine.connect() as connection:
        assert connection.execute(
            text("SELECT organization_id, school_id FROM memberships WHERE id = :m"),
            {"m": tenants.membership_a1},
        ).one() == (tenants.org_a, tenants.school_a1)
        assert connection.execute(
            text("SELECT organization_id FROM schools WHERE id = :s"), {"s": tenants.school_a1}
        ).scalar_one() == tenants.org_a
        assert connection.execute(
            text("SELECT count(*) FROM organizations WHERE id = :o"), {"o": tenants.org_a}
        ).scalar_one() == 1
