"""A5: what the three SECURITY DEFINER functions do when called directly as the application role:
exactly their contract, nothing a caller can bend into something else."""

import hashlib
import secrets
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.tenant import TenantContext, apply_tenant_context
from tests.authsupport import TestUser, UserFactory, World
from tests.test_auth_rls import app_tx, refused, scalar

FIND = "SELECT * FROM public.find_login_identity(:email)"
LIST = "SELECT * FROM public.list_memberships_for_user(:user)"
ACCEPT = (
    "SELECT * FROM public.accept_invitation(:token_hash, :full_name, :password_hash, :existing)"
)


def invitation(
    engine: Engine,
    world: World,
    inviter: TestUser,
    email: str,
    *,
    role: str = "staff",
    school: uuid.UUID | None = None,
) -> bytes:
    """A live invitation made by the admin; returns the token hash to accept it with."""
    token_hash = hashlib.sha256(secrets.token_bytes(16)).digest()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO invitations (organization_id, school_id, email, role, token_hash, "
                "invited_by_user_id, expires_at) VALUES (:o, :s, :e, :r, :h, :by, "
                "now() + interval '1 day')"
            ),
            {
                "o": world.org_a,
                "s": school,
                "e": email,
                "r": role,
                "h": token_hash,
                "by": inviter.id,
            },
        )
    return token_hash


# --- find_login_identity -------------------------------------------------------------------------


def test_find_login_identity_returns_the_three_fields_and_nothing_else(
    app_engine: Engine, users: UserFactory
) -> None:
    user = users.make("staff")

    with app_tx(app_engine) as connection:
        result = connection.execute(text(FIND), {"email": user.email})
        row = result.one()

    assert list(result.keys()) == ["user_id", "password_hash", "is_active"]
    assert (row.user_id, row.is_active) == (user.id, True)
    assert row.password_hash.startswith("$argon2id$")


@pytest.mark.parametrize("variant", ["upper", "mixed", "exact"])
def test_find_login_identity_ignores_case(
    app_engine: Engine, users: UserFactory, variant: str
) -> None:
    user = users.make("staff")
    email = {"upper": user.email.upper(), "mixed": user.email.title(), "exact": user.email}[variant]

    with app_tx(app_engine) as connection:
        assert connection.execute(text(FIND), {"email": email}).one().user_id == user.id


@pytest.mark.parametrize(
    "email",
    [
        "nobody@example.test",
        "",
        "%",
        "' OR '1'='1",
        "x'; DROP TABLE users; --",
        "a@b.test' UNION SELECT 1,2,3 --",
        "%@%",
        "_" * 30,
    ],
)
def test_find_login_identity_is_an_exact_match_and_the_argument_is_data(
    app_engine: Engine, email: str
) -> None:
    with app_tx(app_engine) as connection:
        assert connection.execute(text(FIND), {"email": email}).all() == []
        assert scalar(connection, "SELECT to_regclass('public.users') IS NOT NULL") is True


def test_find_login_identity_shows_an_inactive_user_as_inactive_and_a_missing_hash_as_null(
    app_engine: Engine, users: UserFactory
) -> None:
    inactive = users.make("staff", active=False, label="off")
    no_hash = users.make("staff", password=None, label="nohash")

    with app_tx(app_engine) as connection:
        off = connection.execute(text(FIND), {"email": inactive.email}).one()
        empty = connection.execute(text(FIND), {"email": no_hash.email}).one()

    assert off.is_active is False
    assert empty.password_hash is None and empty.is_active is True


def test_a_planted_search_path_cannot_redirect_the_function(
    app_engine: Engine, users: UserFactory
) -> None:
    """The function pins search_path = pg_catalog, and the application role cannot create a
    schema, a temporary table or a function to put in front of it anyway."""
    user = users.make("staff")
    refused(app_engine, "CREATE TEMP TABLE users (id uuid)", "permission denied")
    refused(app_engine, "CREATE SCHEMA evil", "permission denied")
    refused(
        app_engine,
        "CREATE FUNCTION public.lower(text) RETURNS text LANGUAGE sql AS 'SELECT $1'",
        "permission denied",
    )
    with app_tx(app_engine) as connection:
        connection.execute(text("SET LOCAL search_path = pg_temp, public"))
        assert connection.execute(text(FIND), {"email": user.email}).one().user_id == user.id


# --- list_memberships_for_user -------------------------------------------------------------------


def test_list_memberships_returns_only_active_ones_with_names_and_no_more(
    app_engine: Engine, users: UserFactory, world: World
) -> None:
    user = users.make(
        "staff",
        extra=(("viewer", "a", "2"), ("school_admin", "b", "1")),
        label="three",
    )
    users.set_membership_status(user.membership_ids[1], "suspended")

    with app_tx(app_engine) as connection:
        result = connection.execute(text(LIST), {"user": user.id})
        rows = result.all()

    assert list(result.keys()) == [
        "membership_id",
        "organization_id",
        "organization_name",
        "organization_slug",
        "school_id",
        "school_name",
        "school_slug",
        "role",
        "status",
    ]
    assert {r.membership_id for r in rows} == {user.membership_ids[0], user.membership_ids[2]}
    assert {r.status for r in rows} == {"active"}
    by_role = {r.role: r for r in rows}
    assert by_role["staff"].school_id == world.school_a1
    assert by_role["staff"].organization_name == "Org A"
    assert by_role["school_admin"].organization_id == world.org_b


def test_list_memberships_of_an_organization_wide_role_has_no_school(
    app_engine: Engine, users: UserFactory
) -> None:
    admin = users.make("organization_admin", school=None)

    with app_tx(app_engine) as connection:
        row = connection.execute(text(LIST), {"user": admin.id}).one()

    assert row.school_id is None and row.school_name is None and row.school_slug is None


def test_list_memberships_of_nobody_is_empty(app_engine: Engine, users: UserFactory) -> None:
    with app_tx(app_engine) as connection:
        assert connection.execute(text(LIST), {"user": uuid.uuid4()}).all() == []
        assert connection.execute(text(LIST), {"user": users.make(None).id}).all() == []


# --- accept_invitation ---------------------------------------------------------------------------


def accept(
    connection: Any,
    token_hash: bytes,
    name: str | None,
    password: str | None,
    existing: uuid.UUID | None = None,
) -> Any:
    return connection.execute(
        text(ACCEPT),
        {
            "token_hash": token_hash,
            "full_name": name,
            "password_hash": password,
            "existing": existing,
        },
    ).one()


def test_accepting_creates_a_new_user_and_the_membership_the_invitation_names(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter = users.make("organization_admin", school=None)
    email = world.email("newbie")
    token_hash = invitation(
        admin_engine, world, inviter, email, role="treasurer", school=world.school_a2
    )

    with app_tx(app_engine) as connection:
        result = accept(connection, token_hash, "  New Person ", "$argon2id$fake")
        # The application role can only read what its settings allow: the new user's own row, and
        # the tenant of the invitation.
        connection.execute(
            text("SELECT set_config('app.user_id', :u, true)"), {"u": str(result.user_id)}
        )
        apply_tenant_context(connection, TenantContext(world.org_a))
        created = connection.execute(
            text("SELECT email, full_name, is_active FROM users WHERE id = :id"),
            {"id": result.user_id},
        ).one()
        membership = connection.execute(
            text("SELECT role, status, school_id, organization_id FROM memberships WHERE id = :id"),
            {"id": result.membership_id},
        ).one()

    assert result.outcome == "accepted_new_user" and result.role == "treasurer"
    assert (created.email, created.full_name, created.is_active) == (email, "New Person", True)
    assert tuple(membership) == ("treasurer", "active", world.school_a2, world.org_a)


def test_there_is_no_argument_to_choose_the_role_or_the_tenant() -> None:
    """The role, organization and school come from the invitation row, not from the caller: the
    function takes a token hash, a name, a password hash and an existing user id, and no more."""
    assert ACCEPT.count(":") == 4  # token_hash, full_name, password_hash, existing


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": None, "password": "$argon2id$x"},
        {"name": "   ", "password": "$argon2id$x"},
        {"name": "A Name", "password": None},
    ],
    ids=["no-name", "blank-name", "no-password"],
)
def test_a_new_account_needs_a_name_and_a_password_hash(
    app_engine: Engine,
    admin_engine: Engine,
    users: UserFactory,
    world: World,
    arguments: dict[str, Any],
) -> None:
    inviter = users.make("organization_admin", school=None)
    email = world.email("incomplete")
    token_hash = invitation(admin_engine, world, inviter, email)

    with app_tx(app_engine) as connection:
        result = accept(connection, token_hash, arguments["name"], arguments["password"])
        created = scalar(connection, "SELECT count(*) FROM users WHERE email = :e", e=email)

    assert result.outcome == "invalid" and created == 0


def test_an_existing_account_must_present_itself_and_is_never_modified(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter = users.make("organization_admin", school=None)
    owner = users.make("staff", label="owner")
    stranger = users.make("staff", label="stranger")
    token_hash = invitation(
        admin_engine, world, inviter, owner.email, role="viewer", school=world.school_a2
    )

    with app_tx(app_engine) as connection:
        before = scalar(connection, "SELECT 1")  # (the settings below are not needed here)
        anonymous = accept(connection, token_hash, "Mallory", "$argon2id$attacker")
        wrong = accept(connection, token_hash, "Mallory", "$argon2id$attacker", stranger.id)
        right = accept(connection, token_hash, None, None, owner.id)
        names = connection.execute(
            text("SELECT full_name FROM users WHERE email = :e"), {"e": owner.email}
        ).all()

    assert before == 1
    assert anonymous.outcome == "login_required" and wrong.outcome == "login_required"
    assert anonymous.user_id is None and wrong.membership_id is None
    assert right.outcome == "accepted_existing_user" and right.user_id == owner.id
    assert names == []  # (the application role cannot even read users outside its context)


def test_accepting_twice_or_with_a_wrong_hash_is_the_same_nothing(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter = users.make("organization_admin", school=None)
    token_hash = invitation(admin_engine, world, inviter, world.email("once"))

    with app_tx(app_engine) as connection:
        first = accept(connection, token_hash, "Once", "$argon2id$x")
        again = accept(connection, token_hash, "Twice", "$argon2id$x")
        unknown = accept(connection, hashlib.sha256(b"unknown").digest(), "Who", "$argon2id$x")

    assert first.outcome == "accepted_new_user"
    assert tuple(again) == tuple(unknown) == ("invalid", None, None, None, None, None)


def test_an_expired_or_revoked_invitation_cannot_be_accepted(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter = users.make("organization_admin", school=None)
    expired = invitation(admin_engine, world, inviter, world.email("old"))
    revoked = invitation(admin_engine, world, inviter, world.email("gone"))
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE invitations SET created_at = now() - interval '5 days', "
                "expires_at = now() - interval '1 day' WHERE token_hash = :h"
            ),
            {"h": expired},
        )
        connection.execute(
            text("UPDATE invitations SET revoked_at = now() WHERE token_hash = :h"), {"h": revoked}
        )

    with app_tx(app_engine) as connection:
        results = [accept(connection, h, "A", "$argon2id$x").outcome for h in (expired, revoked)]

    assert results == ["invalid", "invalid"]


def test_someone_who_is_already_a_member_of_that_scope_gets_nothing_created(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter = users.make("organization_admin", school=None)
    member = users.make("staff", school="1", label="member")
    token_hash = invitation(
        admin_engine, world, inviter, member.email, role="viewer", school=world.school_a1
    )

    with app_tx(app_engine) as connection:
        result = accept(connection, token_hash, None, None, member.id)

    assert result.outcome == "invalid"  # the unique membership per (user, scope) stops it


def test_an_inactive_account_cannot_accept(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter = users.make("organization_admin", school=None)
    dormant = users.make("staff", active=False, label="dormant")
    token_hash = invitation(admin_engine, world, inviter, dormant.email)

    with app_tx(app_engine) as connection:
        assert accept(connection, token_hash, None, None, dormant.id).outcome == "invalid"


def test_the_functions_cannot_be_called_by_a_role_that_was_not_granted_execute(
    admin_engine: Engine,
) -> None:
    """EXECUTE belongs to apm_app (and the definer): a login role with no grant is refused."""
    role = f"apm_t_noexec_{secrets.token_hex(4)}"
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"CREATE ROLE {role} NOLOGIN"))
    try:
        connection = admin_engine.connect()
        transaction = connection.begin()
        try:
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            for call in (
                "SELECT * FROM public.find_login_identity('a@b.test')",
                "SELECT * FROM public.list_memberships_for_user(gen_random_uuid())",
                "SELECT * FROM public.accept_invitation('\\x00', 'a', 'b', NULL)",
            ):
                with pytest.raises(DBAPIError, match="permission denied"):
                    connection.execute(text(call))
                connection.rollback()
                transaction = connection.begin()
                connection.execute(text(f"SET LOCAL ROLE {role}"))
        finally:
            transaction.rollback()
            connection.close()
    finally:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
