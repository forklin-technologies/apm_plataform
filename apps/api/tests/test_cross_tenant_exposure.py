"""T9 (M1): the application role cannot read or probe a user of ANOTHER tenant through any write.

The attack: an admin of organization A knows the UUID of a user that only belongs to organization B.
The foreign key memberships.user_id -> users(id) is checked by the database without row level
security, so it accepts any existing user. If apm_app could INSERT a membership (or UPDATE its
user_id), the user would become "a member of A", users_select would show the person (e-mail, name,
is_active) to A, and the foreign key error would tell which UUIDs exist.

The fix is in privileges, not in a policy: apm_app has no INSERT on memberships and no UPDATE on
user_id (nor organization_id, school_id, id). These tests run on the configured database, so giving
a grant back makes them fail.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from app.db.tenant import TenantContext, tenant_session
from app.models import Membership
from tests.dbsupport import Tenants, transaction

CONTEXTS = ["own_org", "own_school"]


def _context(name: str, tenants: Tenants) -> TenantContext:
    if name == "own_org":
        return TenantContext(tenants.org_a)
    return TenantContext(tenants.org_a, tenants.school_a1)


def _insert_membership_of(user_id: uuid.UUID, tenants: Tenants, school: uuid.UUID | None) -> Any:
    return (
        "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
        "VALUES (:user, :org, :school, 'viewer', 'active')",
        {"user": user_id, "org": tenants.org_a, "school": school},
    )


def _b_only_user_rows(app_engine: Engine, tenants: Tenants, context: TenantContext) -> list[int]:
    """How many rows of the B-only user org A can see, by id, by e-mail and through memberships."""
    with transaction(app_engine, context=context) as connection:
        return [
            connection.execute(
                text("SELECT count(*) FROM users WHERE id = :u"), {"u": tenants.user_b1}
            ).scalar_one(),
            connection.execute(
                text("SELECT count(*) FROM users WHERE email = :e"), {"e": tenants.emails[3]}
            ).scalar_one(),
            connection.execute(
                text("SELECT count(*) FROM memberships WHERE user_id = :u"), {"u": tenants.user_b1}
            ).scalar_one(),
        ]


@pytest.mark.parametrize("context_name", CONTEXTS)
def test_the_user_of_another_tenant_is_invisible_to_begin_with(
    app_engine: Engine, tenants: Tenants, context_name: str
) -> None:
    assert _b_only_user_rows(app_engine, tenants, _context(context_name, tenants)) == [0, 0, 0]


@pytest.mark.parametrize("school", ["org-wide", "school-a1"])
@pytest.mark.parametrize("context_name", CONTEXTS)
def test_inserting_a_membership_of_a_user_of_another_tenant_is_denied(
    app_engine: Engine, tenants: Tenants, context_name: str, school: str
) -> None:
    sql, params = _insert_membership_of(
        tenants.user_b1, tenants, None if school == "org-wide" else tenants.school_a1
    )

    with (
        transaction(app_engine, context=_context(context_name, tenants)) as connection,
        pytest.raises(ProgrammingError) as error,
    ):
        connection.execute(text(sql), params)

    assert "permission denied" in str(error.value.orig)
    # Still nothing about that person is readable, by any route.
    assert _b_only_user_rows(app_engine, tenants, _context(context_name, tenants)) == [0, 0, 0]


@pytest.mark.parametrize("column", ["user_id", "organization_id", "school_id", "id"])
@pytest.mark.parametrize("context_name", CONTEXTS)
def test_updating_who_a_membership_belongs_to_is_denied(
    app_engine: Engine, tenants: Tenants, context_name: str, column: str
) -> None:
    value = {
        "user_id": tenants.user_b1,
        "organization_id": tenants.org_b,
        "school_id": tenants.school_a2,
        "id": uuid.uuid4(),
    }[column]

    with (
        transaction(app_engine, context=_context(context_name, tenants)) as connection,
        pytest.raises(ProgrammingError) as error,
    ):
        connection.execute(
            text(f"UPDATE memberships SET {column} = :v WHERE id = :m"),  # noqa: S608
            {"v": value, "m": tenants.membership_a1},
        )

    assert "permission denied" in str(error.value.orig)
    assert _b_only_user_rows(app_engine, tenants, _context(context_name, tenants)) == [0, 0, 0]


def test_the_membership_is_still_attached_to_the_same_user_afterwards(
    app_engine: Engine, admin_engine: Engine, tenants: Tenants
) -> None:
    with (
        transaction(app_engine, context=TenantContext(tenants.org_a)) as connection,
        pytest.raises(ProgrammingError),
    ):
        connection.execute(
            text("UPDATE memberships SET user_id = :u WHERE id = :m"),
            {"u": tenants.user_b1, "m": tenants.membership_a1},
        )

    with admin_engine.connect() as connection:
        owner: Any = connection.execute(
            text("SELECT user_id FROM memberships WHERE id = :m"), {"m": tenants.membership_a1}
        ).scalar_one()
    assert owner == tenants.user_a1


def test_probing_uuids_through_the_foreign_key_does_not_work(
    app_engine: Engine, tenants: Tenants
) -> None:
    """An existing user and a made-up UUID are refused identically, before the foreign key runs."""
    errors = []
    for candidate in (tenants.user_b1, uuid.uuid4()):
        sql, params = _insert_membership_of(candidate, tenants, None)
        with (
            transaction(app_engine, context=TenantContext(tenants.org_a)) as connection,
            pytest.raises(DBAPIError) as error,
        ):
            connection.execute(text(sql), params)
        errors.append((error.value.orig.sqlstate, str(error.value.orig)))  # type: ignore[union-attr]

    assert errors[0] == errors[1]
    assert errors[0][0] == "42501"  # insufficient_privilege, not 23503 foreign_key_violation


def test_orm_cannot_attach_a_user_of_another_tenant_either(
    app_engine: Engine, tenants: Tenants
) -> None:
    factory = sessionmaker(app_engine)
    with tenant_session(factory, TenantContext(tenants.org_a)) as session:
        session.add(
            Membership(
                user_id=tenants.user_b1,
                organization_id=tenants.org_a,
                school_id=None,
                role="viewer",
                status="active",
            )
        )
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()


def test_what_apm_app_may_still_update_in_memberships(app_engine: Engine, tenants: Tenants) -> None:
    """The legitimate changes keep working: role and status (and updated_at)."""
    with transaction(app_engine, context=TenantContext(tenants.org_a)) as connection:
        result = connection.execute(
            text(
                "UPDATE memberships SET role = 'treasurer', status = 'suspended', "
                "updated_at = now() WHERE id = :m"
            ),
            {"m": tenants.membership_a1},
        )
        assert result.rowcount == 1
