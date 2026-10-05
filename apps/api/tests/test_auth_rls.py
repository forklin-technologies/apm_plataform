"""A3/A4: what the application role can reach in the tables TASK-004 added, one promise at a time:
sessions (only the one it was handed the token of), login attempts, invitations (never the token
hash), a user's own row, and the narrow reach of apm_definer."""

import hashlib
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.tenant import TenantContext, apply_tenant_context
from tests.authsupport import TestUser, UserFactory, World


@contextmanager
def app_tx(
    engine: Engine, context: TenantContext | None = None, **settings: Any
) -> Iterator[Connection]:
    """A transaction as apm_app, always rolled back, with the given transaction-local settings
    (`app_user_id=...` becomes `app.user_id`) and optionally a tenant context."""
    connection = engine.connect()
    transaction = connection.begin()
    try:
        for name, value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name.replace("_", ".", 1), "value": str(value)},
            )
        if context is not None:
            apply_tenant_context(connection, context)
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def refused(
    engine: Engine,
    sql: str,
    match: str,
    params: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> None:
    with (
        app_tx(engine, **(settings or {})) as connection,
        pytest.raises(DBAPIError, match=match),
    ):
        connection.execute(text(sql), params or {})


def scalar(connection: Connection, sql: str, **params: Any) -> Any:
    return connection.execute(text(sql), params).scalar_one()


def make_session(
    engine: Engine, user: TestUser, membership: uuid.UUID | None = None
) -> tuple[bytes, str]:
    """A session row made by the admin; returns (session id = SHA-256, the token it stands for)."""
    token = secrets.token_urlsafe(32)
    session_id = hashlib.sha256(token.encode()).digest()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, membership_id, expires_at) "
                "VALUES (:id, :u, :m, now() + interval '1 hour')"
            ),
            {"id": session_id, "u": user.id, "m": membership},
        )
    return session_id, token


@pytest.fixture
def two_users(users: UserFactory) -> tuple[TestUser, TestUser]:
    return users.make("staff", label="one"), users.make("staff", org="b", label="two")


# --- sessions ------------------------------------------------------------------------------------


def test_sessions_are_invisible_without_the_token_or_the_user(
    app_engine: Engine, admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    make_session(admin_engine, two_users[0])
    make_session(admin_engine, two_users[1])

    with app_tx(app_engine) as connection:
        assert scalar(connection, "SELECT count(*) FROM sessions") == 0


def test_the_session_id_setting_shows_exactly_that_session(
    app_engine: Engine, admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    first, _ = make_session(admin_engine, two_users[0])
    second, _ = make_session(admin_engine, two_users[1])

    with app_tx(app_engine, app_session_id=first.hex()) as connection:
        rows = connection.execute(text("SELECT id, user_id FROM sessions")).all()

    assert [(bytes(r.id), r.user_id) for r in rows] == [(first, two_users[0].id)]
    assert second not in [bytes(r.id) for r in rows]


def test_the_user_id_setting_shows_that_users_sessions_only(
    app_engine: Engine, admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    mine = {make_session(admin_engine, two_users[0])[0] for _ in range(2)}
    make_session(admin_engine, two_users[1])

    with app_tx(app_engine, app_user_id=two_users[0].id) as connection:
        seen = {bytes(r.id) for r in connection.execute(text("SELECT id FROM sessions"))}

    assert seen == mine


@pytest.mark.parametrize(
    ("setting", "value", "match"),
    [
        ("app_session_id", "zz-not-hex", "invalid hexadecimal"),
        ("app_user_id", "not-a-uuid", "invalid input syntax for type uuid"),
    ],
)
def test_a_malformed_setting_is_an_error_never_data(
    app_engine: Engine, setting: str, value: str, match: str
) -> None:
    refused(app_engine, "SELECT count(*) FROM sessions", match, settings={setting: value})


def test_a_session_can_only_be_created_for_the_user_in_the_context(
    app_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    insert = (
        "INSERT INTO sessions (id, user_id, expires_at) "
        "VALUES (:id, :user, now() + interval '1 hour')"
    )
    params = {"id": secrets.token_bytes(32), "user": two_users[1].id}

    refused(app_engine, insert, "row-level security", params, {"app_user_id": two_users[0].id})
    refused(app_engine, insert, "row-level security", params)  # no user in the context at all
    with app_tx(app_engine, app_user_id=two_users[1].id) as connection:
        connection.execute(text(insert), params)  # the matching user: allowed


def test_a_session_cannot_point_at_the_membership_of_another_user(
    admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    one, two = two_users
    with pytest.raises(IntegrityError) as error, admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, membership_id, expires_at) "
                "VALUES (:id, :u, :m, now() + interval '1 hour')"
            ),
            {"id": secrets.token_bytes(32), "u": one.id, "m": two.membership_ids[0]},
        )

    diagnostics: Any = error.value.orig.diag  # type: ignore[union-attr]
    assert diagnostics.constraint_name == "fk_sessions_membership_id_user_id_memberships"


def test_a_session_can_point_at_a_membership_of_its_own_user(
    admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    make_session(admin_engine, two_users[0], two_users[0].membership_ids[0])


def test_only_the_bookkeeping_columns_of_a_session_can_change(
    app_engine: Engine, admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    mine, _ = make_session(admin_engine, two_users[0])
    theirs, _ = make_session(admin_engine, two_users[1])

    with app_tx(app_engine, app_session_id=mine.hex()) as connection:
        revoke = "UPDATE sessions SET revoked_at = now(), revoked_reason = 'logout' WHERE id = :id"
        assert connection.execute(text(revoke), {"id": mine}).rowcount == 1
        assert connection.execute(text(revoke), {"id": theirs}).rowcount == 0  # not visible

    for column, value in (
        ("user_id", f"'{two_users[1].id}'"),
        ("expires_at", "now() + interval '30 days'"),
        ("membership_id", f"'{two_users[1].membership_ids[0]}'"),
        ("id", "'\\x00'"),
        ("created_at", "now()"),
    ):
        refused(
            app_engine,
            f"UPDATE sessions SET {column} = {value} WHERE id = :id",  # noqa: S608
            "permission denied",
            {"id": mine},
            {"app_session_id": mine.hex()},
        )


def test_a_revoked_reason_must_be_a_known_one(
    app_engine: Engine, admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    mine, _ = make_session(admin_engine, two_users[0])

    with (
        app_tx(app_engine, app_session_id=mine.hex()) as connection,
        pytest.raises(IntegrityError, match="ck_sessions_revoked_reason_valid"),
    ):
        connection.execute(
            text("UPDATE sessions SET revoked_reason = 'because' WHERE id = :id"), {"id": mine}
        )


def test_sessions_cannot_be_deleted_or_truncated_by_the_application(
    app_engine: Engine, admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    mine, _ = make_session(admin_engine, two_users[0])

    refused(
        app_engine,
        "DELETE FROM sessions WHERE id = :id",
        "permission denied",
        {"id": mine},
        {"app_session_id": mine.hex()},
    )
    refused(app_engine, "TRUNCATE sessions", "permission denied")


def test_force_row_level_security_binds_the_owner_of_sessions_too(
    admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    make_session(admin_engine, two_users[0])
    connection = admin_engine.connect()
    transaction = connection.begin()
    try:
        connection.exec_driver_sql("SET LOCAL ROLE apm_owner")
        assert scalar(connection, "SELECT count(*) FROM sessions") == 0
    finally:
        transaction.rollback()
        connection.close()


def test_accepted_risk_a_connection_that_runs_arbitrary_sql_can_name_a_user(
    app_engine: Engine, admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    """DOCUMENTED, NOT FIXED (docs/auth.md): the settings are not authenticated, so code that can
    execute arbitrary SQL as apm_app can set app.user_id itself and read that user's session rows
    (the id is only a SHA-256 of the token, which authenticates nobody) or change their password.
    The defence is that the application never builds SQL from input (see
    test_no_sql_is_built_from_strings) and that the settings are set from the validated session
    only. This test exists so that the risk stays visible and the claim stays honest."""
    victim = two_users[1]
    make_session(admin_engine, victim)

    with app_tx(app_engine, app_user_id=victim.id) as connection:
        assert scalar(connection, "SELECT count(*) FROM sessions") == 1
        updated = connection.execute(
            text("UPDATE users SET password_hash = '$argon2id$forged' WHERE id = :id"),
            {"id": victim.id},
        ).rowcount
    assert updated == 1  # (rolled back here; in production this is exactly what the risk means)


# --- login attempts ------------------------------------------------------------------------------


def _attempt(
    subject: bytes, ip: bytes, when: str = "now()", kind: str = "login"
) -> tuple[str, dict[str, Any]]:
    return (
        f"INSERT INTO login_attempts (kind, subject_hmac, ip_hmac, succeeded, attempted_at) "  # noqa: S608
        f"VALUES (:k, :s, :i, false, {when})",
        {"k": kind, "s": subject, "i": ip},
    )


def test_login_attempts_can_only_be_written_as_of_now(app_engine: Engine) -> None:
    subject, ip = secrets.token_bytes(32), secrets.token_bytes(32)

    with app_tx(app_engine) as connection:
        connection.execute(text(_attempt(subject, ip)[0]), _attempt(subject, ip)[1])
    for when in ("now() - interval '1 hour'", "now() + interval '1 hour'"):
        sql, params = _attempt(subject, ip, when)
        refused(app_engine, sql, "row-level security", params)  # nobody can forge or erase history


def test_login_attempts_show_a_week_and_only_what_is_a_day_old_can_be_removed(
    app_engine: Engine, admin_engine: Engine
) -> None:
    """A DELETE with a WHERE must also pass the SELECT policy, so the week the application sees has
    to be wider than the day after which it may delete (otherwise nothing could ever be purged)."""
    subject, ip = secrets.token_bytes(32), secrets.token_bytes(32)
    ages = {"recent": "1 hour", "day-old": "30 hours", "week-old": "8 days"}
    with admin_engine.begin() as connection:
        for age in ages.values():
            sql, params = _attempt(subject, ip, f"now() - interval '{age}'")
            connection.execute(text(sql), params)
    try:
        with app_tx(app_engine) as connection:
            seen = scalar(
                connection, "SELECT count(*) FROM login_attempts WHERE subject_hmac = :s", s=subject
            )
            removed = connection.execute(
                text("DELETE FROM login_attempts WHERE subject_hmac = :s"), {"s": subject}
            ).rowcount
            kept_recent = connection.execute(
                text(
                    "DELETE FROM login_attempts WHERE subject_hmac = :s "
                    "AND attempted_at > now() - interval '24 hours'"
                ),
                {"s": subject},
            ).rowcount
        assert seen == 2  # recent and day-old; the week-old row is out of sight
        assert removed == 1  # only the day-old one may go
        assert kept_recent == 0  # the recent one cannot be deleted by the application
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM login_attempts WHERE subject_hmac = :s"), {"s": subject}
            )


def test_login_attempts_cannot_be_updated_and_must_hold_digests(app_engine: Engine) -> None:
    subject, ip = secrets.token_bytes(32), secrets.token_bytes(32)
    refused(app_engine, "UPDATE login_attempts SET succeeded = true", "permission denied")
    with app_tx(app_engine) as connection, pytest.raises(IntegrityError, match="hmacs_are_sha256"):
        connection.execute(
            text(_attempt(b"a-plain-email@example.test", ip)[0]),
            _attempt(b"a-plain-email@example.test", ip)[1],
        )
    sql, params = _attempt(subject, ip, kind="something-else")
    with app_tx(app_engine) as connection, pytest.raises(IntegrityError, match="kind_valid"):
        connection.execute(text(sql), params)


# --- invitations ---------------------------------------------------------------------------------


def make_invitation(
    engine: Engine,
    world: World,
    inviter: TestUser,
    *,
    org: str = "a",
    school: uuid.UUID | None = None,
) -> uuid.UUID:
    invitation_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO invitations (id, organization_id, school_id, email, role, token_hash, "
                "invited_by_user_id, expires_at) VALUES (:id, :o, :s, :e, 'staff', :h, :by, "
                "now() + interval '1 day')"
            ),
            {
                "id": invitation_id,
                "o": world.org_a if org == "a" else world.org_b,
                "s": school,
                "e": world.email("inv"),
                "h": secrets.token_bytes(32),
                "by": inviter.id,
            },
        )
    return invitation_id


def test_invitations_are_scoped_to_the_tenant(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter_a = users.make("organization_admin", school=None)
    inviter_b = users.make("organization_admin", org="b", school=None)
    in_a1 = make_invitation(admin_engine, world, inviter_a, school=world.school_a1)
    in_a2 = make_invitation(admin_engine, world, inviter_a, school=world.school_a2)
    in_b = make_invitation(admin_engine, world, inviter_b, org="b", school=world.school_b1)

    def seen(context: TenantContext | None) -> set[uuid.UUID]:
        with app_tx(app_engine, context) as connection:
            return {r[0] for r in connection.execute(text("SELECT id FROM invitations"))}

    mine = {in_a1, in_a2, in_b}
    assert seen(None) & mine == set()
    assert seen(TenantContext(world.org_a)) & mine == {in_a1, in_a2}
    assert seen(TenantContext(world.org_a, world.school_a1)) & mine == {in_a1}
    assert seen(TenantContext(world.org_b)) & mine == {in_b}


def test_the_token_hash_of_an_invitation_cannot_be_read_by_the_application(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    make_invitation(admin_engine, world, users.make("organization_admin", school=None))
    context = TenantContext(world.org_a)

    for projection in ("token_hash", "*", "i.token_hash", "length(token_hash)"):
        with (
            app_tx(app_engine, context) as connection,
            pytest.raises(DBAPIError, match="permission denied"),
        ):
            connection.execute(text(f"SELECT {projection} FROM invitations i"))  # noqa: S608
    with app_tx(app_engine, context) as connection:  # everything else is readable
        connection.execute(text("SELECT id, email, role, expires_at, accepted_at FROM invitations"))


def test_an_invitation_is_inserted_for_the_active_tenant_by_the_user_in_the_context(
    app_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter = users.make("organization_admin", school=None)
    other = users.make("organization_admin", org="b", school=None)
    insert = (
        "INSERT INTO invitations (organization_id, school_id, email, role, token_hash, "
        "invited_by_user_id, expires_at{extra}) VALUES (:org, NULL, :e, 'staff', :h, :by, "
        "now() + interval '1 day'{value})"
    )
    params = {
        "org": world.org_a,
        "e": world.email("x"),
        "h": secrets.token_bytes(32),
        "by": inviter.id,
    }
    context = TenantContext(world.org_a)

    with app_tx(app_engine, context, app_user_id=inviter.id) as connection:
        connection.execute(text(insert.format(extra="", value="")), params)  # allowed
    for label, sql, changes, settings in (
        ("another organization", insert, {"org": world.org_b}, {"app_user_id": inviter.id}),
        ("another inviter", insert, {"by": other.id}, {"app_user_id": inviter.id}),
        ("no user in the context", insert, {}, {}),
        (
            "already accepted",
            insert.format(extra=", accepted_at, accepted_user_id", value=", now(), :by"),
            {},
            {"app_user_id": inviter.id},
        ),
    ):
        sql = sql.format(extra="", value="") if label != "already accepted" else sql
        with (
            app_tx(app_engine, context, **settings) as connection,
            pytest.raises(DBAPIError, match="row-level security|permission denied"),
        ):
            connection.execute(text(sql), {**params, **changes})


def test_only_revoking_an_invitation_can_be_done_and_only_in_the_own_tenant(
    app_engine: Engine, admin_engine: Engine, users: UserFactory, world: World
) -> None:
    inviter_a = users.make("organization_admin", school=None)
    inviter_b = users.make("organization_admin", org="b", school=None)
    mine = make_invitation(admin_engine, world, inviter_a)
    theirs = make_invitation(admin_engine, world, inviter_b, org="b")
    revoke = "UPDATE invitations SET revoked_at = now(), updated_at = now() WHERE id = :id"

    with app_tx(app_engine, TenantContext(world.org_a)) as connection:
        assert connection.execute(text(revoke), {"id": mine}).rowcount == 1
        assert connection.execute(text(revoke), {"id": theirs}).rowcount == 0
    for column, value in (
        ("role", "'organization_admin'"),
        ("accepted_at", "now()"),
        ("accepted_user_id", f"'{inviter_a.id}'"),
        ("token_hash", "'\\x00'"),
        ("expires_at", "now() + interval '9 years'"),
        ("email", "'someone@else.test'"),
        ("organization_id", f"'{world.org_b}'"),
    ):
        with (
            app_tx(app_engine, TenantContext(world.org_a)) as connection,
            pytest.raises(DBAPIError, match="permission denied"),
        ):
            connection.execute(
                text(f"UPDATE invitations SET {column} = {value} WHERE id = :id"),  # noqa: S608
                {"id": mine},
            )
    refused(app_engine, "DELETE FROM invitations", "permission denied")


# --- a user's own row -----------------------------------------------------------------------------


def test_a_user_sees_and_changes_only_their_own_row_even_with_no_tenant(
    app_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    one, two = two_users

    with app_tx(app_engine, app_user_id=one.id) as connection:
        rows = connection.execute(text("SELECT id, email FROM users")).all()
        own = connection.execute(
            text("UPDATE users SET password_hash = :h, updated_at = now() WHERE id = :id"),
            {"h": "$argon2id$x", "id": one.id},
        ).rowcount
        other = connection.execute(
            text("UPDATE users SET password_hash = :h WHERE id = :id"),
            {"h": "$argon2id$x", "id": two.id},
        ).rowcount
    with app_tx(app_engine) as connection:
        nobody = scalar(connection, "SELECT count(*) FROM users")

    assert [(r.id, r.email) for r in rows] == [(one.id, one.email)]
    assert (own, other, nobody) == (1, 0, 0)


@pytest.mark.parametrize("column", ["email", "is_active", "full_name", "id", "created_at"])
def test_a_user_cannot_change_anything_but_the_password(
    app_engine: Engine, two_users: tuple[TestUser, TestUser], column: str
) -> None:
    value = {
        "email": "'x@y.test'",
        "is_active": "false",
        "full_name": "'x'",
        "id": "gen_random_uuid()",
        "created_at": "now()",
    }[column]
    refused(
        app_engine,
        f"UPDATE users SET {column} = {value} WHERE id = :id",  # noqa: S608
        "permission denied",
        {"id": two_users[0].id},
        {"app_user_id": two_users[0].id},
    )


def test_the_application_cannot_read_a_password_hash_or_create_a_user(
    app_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    settings = {"app_user_id": two_users[0].id}
    refused(app_engine, "SELECT password_hash FROM users", "permission denied", settings=settings)
    refused(app_engine, "SELECT * FROM users", "permission denied", settings=settings)
    refused(
        app_engine,
        "INSERT INTO users (email, full_name) VALUES ('a@b.test', 'A')",
        "permission denied",
        settings=settings,
    )


# --- apm_definer ----------------------------------------------------------------------------------

DEFINER_COLUMNS = {
    ("users", "SELECT"): {"id", "email", "password_hash", "is_active"},
    ("users", "INSERT"): {"email", "full_name", "password_hash"},
    ("memberships", "SELECT"): {"id", "user_id", "organization_id", "school_id", "role", "status"},
    ("memberships", "INSERT"): {"user_id", "organization_id", "school_id", "role", "status"},
    ("organizations", "SELECT"): {"id", "name", "slug"},
    ("schools", "SELECT"): {"id", "organization_id", "name", "slug"},
    (
        "invitations",
        "SELECT",
    ): {
        "id",
        "organization_id",
        "school_id",
        "email",
        "role",
        "token_hash",
        "expires_at",
        "accepted_at",
        "revoked_at",
    },
    ("invitations", "UPDATE"): {"accepted_at", "accepted_user_id", "updated_at"},
    # 0008 (ADR-018): what the three functions of the public flow read
    ("school_settings", "SELECT"): {
        "school_id", "organization_id", "suggested_amounts_cents", "allow_custom_amount",
        "min_contribution_cents", "max_contribution_cents", "required_fields", "optional_fields",
        "brand_accent", "brand_accent_contrast",
    },
    ("payment_accounts", "SELECT"): {
        "id", "organization_id", "school_id", "provider", "status", "webhook_secret_hash",
    },
    ("contributions", "SELECT"): {
        "transaction_id", "organization_id", "school_id", "receipt_token_hash",
        "receipt_expires_at",
    },
}  # fmt: skip


def test_apm_definer_has_exactly_these_column_privileges_and_no_others(
    admin_engine: Engine,
) -> None:
    found: dict[tuple[str, str], set[str]] = {}
    with admin_engine.connect() as connection:
        columns = connection.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
            )
        ).all()
        for table, column in columns:
            for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
                allowed = scalar(
                    connection,
                    "SELECT has_column_privilege('apm_definer', :t, :c, :p)",
                    t=table,
                    c=column,
                    p=privilege,
                )
                if allowed:
                    found.setdefault((table, privilege), set()).add(column)
        table_level = connection.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = 'apm_definer' AND table_schema = 'public'"
            )
        ).all()

    assert found == DEFINER_COLUMNS
    assert table_level == []  # nothing at table level: every grant is by column
    for untouched in ("sessions", "login_attempts"):
        assert all(table != untouched for table, _ in found)


def test_apm_definer_has_no_other_reach_in_the_database(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        checks = connection.execute(
            text(
                "SELECT has_schema_privilege('apm_definer', 'public', 'USAGE'), "
                "has_schema_privilege('apm_definer', 'public', 'CREATE'), "
                "has_database_privilege('apm_definer', current_database(), 'TEMPORARY'), "
                "has_database_privilege('apm_definer', current_database(), 'CREATE'), "
                "pg_has_role('apm_app', 'apm_definer', 'MEMBER'), "
                "pg_has_role('apm_app', 'apm_definer', 'SET')"
            )
        ).one()

    assert tuple(checks) == (True, False, False, False, False, False)


def test_the_application_role_cannot_become_apm_definer(app_engine: Engine) -> None:
    refused(app_engine, "SET ROLE apm_definer", "permission denied|must be member")


def test_apm_definer_alone_sees_users_and_only_through_its_four_columns(
    admin_engine: Engine, two_users: tuple[TestUser, TestUser]
) -> None:
    connection = admin_engine.connect()
    transaction = connection.begin()
    try:
        connection.exec_driver_sql("SET LOCAL ROLE apm_definer")
        ids = {r[0] for r in connection.execute(text("SELECT id FROM users"))}
        assert {two_users[0].id, two_users[1].id} <= ids  # the lookup needs to find anybody
    finally:
        transaction.rollback()
        connection.close()
    for sql in (
        "SELECT full_name FROM users",
        "SELECT * FROM users",
        "SELECT count(*) FROM sessions",
        "SELECT count(*) FROM login_attempts",
        "UPDATE users SET is_active = false",
        "DELETE FROM users",
        "DELETE FROM memberships",
        "UPDATE memberships SET role = 'organization_admin'",
        "SELECT created_at FROM invitations",
        "UPDATE invitations SET role = 'organization_admin'",
    ):
        connection = admin_engine.connect()
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("SET LOCAL ROLE apm_definer")
            with pytest.raises(DBAPIError, match="permission denied"):
                connection.execute(text(sql))
        finally:
            transaction.rollback()
            connection.close()
