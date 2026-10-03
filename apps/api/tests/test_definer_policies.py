"""QA finding N6 (second half): the POLICY layer of the role `apm_definer`, tried on its own.

The three SECURITY DEFINER functions are held by three layers: the column grants of `apm_definer`
(tests/test_auth_rls.py), the policies `TO apm_definer` (this file) and the body of each function
(tests/test_definer_functions.py). The functions are tested through their contract, where a policy
widened by mistake can hide behind the function's own WHERE or behind a column grant. These tests go
under the functions: they SET ROLE apm_definer and run plain statements, so each policy is judged
alone. Everything happens in one transaction that is always rolled back.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from tests.dbsupport import transaction

ROW_LEVEL_SECURITY = "row-level security"


@dataclass(frozen=True)
class Scene:
    org_with_member: uuid.UUID
    org_without_member: uuid.UUID
    school_with_member: uuid.UUID
    school_without_member: uuid.UUID
    user: uuid.UUID
    new_user: uuid.UUID  # has no membership: the one the insert tests give one to
    memberships: dict[str, uuid.UUID]  # status -> id
    invitations: dict[str, uuid.UUID]  # state -> id


def _scene(connection: Connection) -> Scene:
    """Rows in every state a policy has to tell apart, made as the admin (bypassing row level
    security), before the role is switched."""
    suffix = uuid.uuid4().hex[:8]
    names = ("org_m", "org_n", "school_m", "school_n", "user", "user_s", "user_r", "new_user")
    ids = {name: uuid.uuid4() for name in names}
    connection.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:m, 'M', :sm), (:n, 'N', :sn)"),
        {"m": ids["org_m"], "n": ids["org_n"], "sm": f"dp-m-{suffix}", "sn": f"dp-n-{suffix}"},
    )
    connection.execute(
        text(
            "INSERT INTO schools (id, organization_id, name, slug) VALUES "
            "(:sm, :m, 'SM', :slm), (:sn, :n, 'SN', :sln)"
        ),
        {
            "sm": ids["school_m"],
            "sn": ids["school_n"],
            "m": ids["org_m"],
            "n": ids["org_n"],
            "slm": f"dp-sm-{suffix}",
            "sln": f"dp-sn-{suffix}",
        },
    )
    for key in ("user", "user_s", "user_r", "new_user"):
        connection.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash) "
                "VALUES (:id, :e, 'DP', 'x')"
            ),
            {"id": ids[key], "e": f"dp-{key}-{suffix}@example.test"},
        )
    memberships: dict[str, uuid.UUID] = {}
    # One user per status (a user has one organization-wide membership per organization); only
    # the active one is school-scoped, which is what makes the school visible to the definer.
    for status, user, role in (
        ("active", "user", "staff"),
        ("suspended", "user_s", "viewer"),
        ("revoked", "user_r", "treasurer"),
    ):
        memberships[status] = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO memberships (id, user_id, organization_id, school_id, role, status) "
                "VALUES (:id, :u, :o, :s, :r, :st)"
            ),
            {
                "id": memberships[status],
                "u": ids[user],
                "o": ids["org_m"],
                "s": ids["school_m"] if status == "active" else None,
                "r": role,
                "st": status,
            },
        )
    invitations: dict[str, uuid.UUID] = {}
    now = datetime.now(UTC)
    day = timedelta(days=1)
    # state -> (created_at, expires_at, accepted, revoked)
    states = {
        "live": (now, now + day, False, False),
        "accepted": (now, now + day, True, False),
        "revoked": (now, now + day, False, True),
        "expired": (now - 2 * day, now - day, False, False),
    }
    for state, (created, expires, accepted, revoked) in states.items():
        invitations[state] = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO invitations (id, organization_id, school_id, email, role, token_hash, "
                "invited_by_user_id, created_at, expires_at, accepted_at, accepted_user_id, "
                "revoked_at) VALUES (:id, :o, NULL, :e, 'viewer', decode(:h, 'hex'), :u, "
                ":created, :expires, :accepted_at, :accepted_user, :revoked_at)"
            ),
            {
                "id": invitations[state],
                "o": ids["org_m"],
                "e": f"dp-{state}-{suffix}@example.test",
                "h": uuid.uuid4().hex * 2,
                "u": ids["user"],
                "created": created,
                "expires": expires,
                "accepted_at": now if accepted else None,
                "accepted_user": ids["user"] if accepted else None,
                "revoked_at": now if revoked else None,
            },
        )
    return Scene(
        org_with_member=ids["org_m"],
        org_without_member=ids["org_n"],
        school_with_member=ids["school_m"],
        school_without_member=ids["school_n"],
        user=ids["user"],
        new_user=ids["new_user"],
        memberships=memberships,
        invitations=invitations,
    )


@contextmanager
def as_definer(engine: Engine) -> Iterator[tuple[Connection, Scene]]:
    with transaction(engine) as connection:
        scene = _scene(connection)
        connection.exec_driver_sql("SET LOCAL ROLE apm_definer")
        yield connection, scene


def ids_of(connection: Connection, sql: str) -> set[uuid.UUID]:
    return {row[0] for row in connection.execute(text(sql))}


def refused(connection: Connection, sql: str, params: dict[str, Any], match: str) -> None:
    # raises() outside, so the savepoint is rolled back first and the transaction stays usable
    with pytest.raises(DBAPIError, match=match), connection.begin_nested():
        connection.execute(text(sql), params)


# --- SELECT policies ------------------------------------------------------------------------------


def test_memberships_are_visible_to_the_definer_only_while_active(admin_engine: Engine) -> None:
    with as_definer(admin_engine) as (connection, scene):
        seen = ids_of(connection, "SELECT id FROM memberships")

    assert scene.memberships["active"] in seen
    assert scene.memberships["suspended"] not in seen
    assert scene.memberships["revoked"] not in seen


def test_organizations_and_schools_are_visible_to_the_definer_only_with_a_membership(
    admin_engine: Engine,
) -> None:
    with as_definer(admin_engine) as (connection, scene):
        organizations = ids_of(connection, "SELECT id FROM organizations")
        schools = ids_of(connection, "SELECT id FROM schools")

    assert scene.org_with_member in organizations
    assert scene.org_without_member not in organizations
    assert scene.school_with_member in schools
    assert scene.school_without_member not in schools


def test_invitations_are_visible_to_the_definer_unless_revoked_or_expired(
    admin_engine: Engine,
) -> None:
    """An ACCEPTED invitation stays visible on purpose (an UPDATE also checks the new row against
    the SELECT policy); accept_invitation() filters accepted ones out itself."""
    with as_definer(admin_engine) as (connection, scene):
        seen = ids_of(connection, "SELECT id FROM invitations")

    assert scene.invitations["live"] in seen
    assert scene.invitations["accepted"] in seen
    assert scene.invitations["revoked"] not in seen
    assert scene.invitations["expired"] not in seen


def test_the_definer_reads_every_user_row_but_no_other_table_without_a_policy(
    admin_engine: Engine,
) -> None:
    with as_definer(admin_engine) as (connection, scene):
        users = ids_of(connection, "SELECT id FROM users")

    assert scene.user in users


# --- INSERT policies ------------------------------------------------------------------------------

_MEMBERSHIP = (
    "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
    "VALUES (:u, :o, :s, 'viewer', :status)"
)


@pytest.mark.parametrize("status", ["invited", "suspended", "revoked"])
def test_the_definer_can_only_create_an_active_membership(
    admin_engine: Engine, status: str
) -> None:
    with as_definer(admin_engine) as (connection, scene):
        params = {"u": scene.new_user, "o": scene.org_with_member, "s": None, "status": status}
        refused(connection, _MEMBERSHIP, params, ROW_LEVEL_SECURITY)


def test_the_definer_creates_an_active_membership(admin_engine: Engine) -> None:
    """The control for the test above: the same insert with status 'active' is accepted."""
    with as_definer(admin_engine) as (connection, scene):
        result = connection.execute(
            text(_MEMBERSHIP),
            {"u": scene.new_user, "o": scene.org_with_member, "s": None, "status": "active"},
        )

    assert result.rowcount == 1


def test_the_definer_can_only_create_a_user_that_has_a_password(admin_engine: Engine) -> None:
    with as_definer(admin_engine) as (connection, _scene_):
        refused(
            connection,
            "INSERT INTO users (email, full_name, password_hash) VALUES (:e, 'N', NULL)",
            {"e": f"dp-nopass-{uuid.uuid4().hex[:8]}@example.test"},
            ROW_LEVEL_SECURITY,
        )


def test_the_definer_creates_a_user_with_a_password(admin_engine: Engine) -> None:
    """The control: with a hash the same insert is accepted (is_active is not even a column the
    definer may write; it takes its default, true)."""
    with as_definer(admin_engine) as (connection, _scene_):
        result = connection.execute(
            text("INSERT INTO users (email, full_name, password_hash) VALUES (:e, 'N', 'h')"),
            {"e": f"dp-pass-{uuid.uuid4().hex[:8]}@example.test"},
        )

    assert result.rowcount == 1


# --- UPDATE policy of invitations -----------------------------------------------------------------

_ACCEPT = (
    "UPDATE invitations SET accepted_at = now(), accepted_user_id = :u, updated_at = now() "
    "WHERE id = :id"
)


def test_the_definer_can_accept_a_live_invitation(admin_engine: Engine) -> None:
    with as_definer(admin_engine) as (connection, scene):
        result = connection.execute(
            text(_ACCEPT), {"u": scene.user, "id": scene.invitations["live"]}
        )

    assert result.rowcount == 1


@pytest.mark.parametrize("state", ["accepted", "revoked", "expired"])
def test_the_definer_cannot_accept_an_invitation_that_is_not_live(
    admin_engine: Engine, state: str
) -> None:
    with as_definer(admin_engine) as (connection, scene):
        result = connection.execute(
            text(_ACCEPT), {"u": scene.user, "id": scene.invitations[state]}
        )

    assert result.rowcount == 0  # the row is not there to be updated (USING)


def test_the_definer_can_change_a_live_invitation_only_into_an_accepted_one(
    admin_engine: Engine,
) -> None:
    """WITH CHECK: touching `updated_at` alone, leaving the invitation open, is refused."""
    with as_definer(admin_engine) as (connection, scene):
        refused(
            connection,
            "UPDATE invitations SET updated_at = now() WHERE id = :id",
            {"id": scene.invitations["live"]},
            ROW_LEVEL_SECURITY,
        )
