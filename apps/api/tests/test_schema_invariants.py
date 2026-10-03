"""Schema invariants the review showed were only checked indirectly (S1 to S6): each one is asserted
twice, from the catalog (the constraint or privilege exists, exactly as designed) and by behaviour
(the database refuses the write). Run as the admin, which bypasses RLS: only the schema can stop it.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.dbsupport import Tenants, transaction


def _constraint(error: IntegrityError) -> str:
    diag = getattr(error.orig, "diag", None)
    return str(diag.constraint_name) if diag is not None else ""


# --- S1: nobody but apm_owner can create objects in schema public -------------------------------


def test_schema_public_grants_are_exactly_these(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END, "
                "a.privilege_type FROM pg_namespace n, aclexplode(n.nspacl) a "
                "WHERE n.nspname = 'public'"
            )
        ).all()

    visible = {tuple(r) for r in rows if r[0] in ("PUBLIC", "apm_app", "apm_owner")}
    assert visible == {
        ("PUBLIC", "USAGE"),
        ("apm_app", "USAGE"),
        ("apm_owner", "USAGE"),
        ("apm_owner", "CREATE"),
    }


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE s1_intruder (id int)",
        "CREATE VIEW s1_intruder AS SELECT 1",
        "CREATE SEQUENCE s1_intruder",
        "CREATE TYPE s1_intruder AS ENUM ('a')",
        "CREATE FUNCTION s1_intruder() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "CREATE TABLE public.s1_intruder (id int)",
    ],
)
def test_application_role_cannot_create_anything_in_public(
    app_engine: Engine, statement: str
) -> None:
    with transaction(app_engine) as connection, pytest.raises(ProgrammingError) as error:
        connection.execute(text(statement))
    assert "permission denied" in str(error.value.orig)


def test_application_role_has_no_create_on_the_schema(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        values = connection.execute(
            text(
                "SELECT has_schema_privilege('apm_app', 'public', 'CREATE'), "
                "has_schema_privilege('public', 'public', 'CREATE')"
            )
        ).one()
    assert tuple(values) == (False, False)


# --- S2: every tenant-owned row has an organization --------------------------------------------


def test_every_organization_id_column_is_not_null(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname, a.attnotnull FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attname = 'organization_id' "
                "AND NOT a.attisdropped ORDER BY c.relname"
            )
        ).all()

    assert [tuple(r) for r in rows] == [
        ("invitations", True),
        ("memberships", True),
        ("schools", True),
    ]


def test_schools_and_memberships_refuse_a_missing_organization(
    admin_engine: Engine, tenants: Tenants
) -> None:
    with transaction(admin_engine) as connection, pytest.raises(IntegrityError) as school_error:
        connection.execute(
            text("INSERT INTO schools (organization_id, name, slug) VALUES (NULL, 'x', 's2-null')")
        )
    with transaction(admin_engine) as connection, pytest.raises(IntegrityError) as membership_error:
        connection.execute(
            text(
                "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
                "VALUES (:u, NULL, NULL, 'viewer', 'active')"
            ),
            {"u": tenants.user_free},
        )

    for error in (school_error.value, membership_error.value):
        assert "null value in column" in str(error.orig)
        assert getattr(error.orig.diag, "column_name", None) == "organization_id"  # type: ignore[union-attr]


# --- S3: schools.slug has a format, like organizations.slug ------------------------------------

BAD_SLUGS = [
    "Bad Slug",
    "UPPER",
    "-leading",
    "trailing-",
    "double--hyphen",
    "under_score",
    "",
    "a b",
    "é",
]
GOOD_SLUGS = ["a", "demo-escola-1", "x9", "0-1-2"]


@pytest.mark.parametrize("slug", BAD_SLUGS)
@pytest.mark.parametrize("table", ["schools", "organizations"])
def test_a_slug_with_a_bad_format_is_refused(
    admin_engine: Engine, tenants: Tenants, table: str, slug: str
) -> None:
    sql = (
        "INSERT INTO schools (organization_id, name, slug) VALUES (:org, 'x', :slug)"
        if table == "schools"
        else "INSERT INTO organizations (name, slug) VALUES ('x', :slug)"
    )
    with transaction(admin_engine) as connection, pytest.raises(IntegrityError) as error:
        connection.execute(text(sql), {"org": tenants.org_a, "slug": slug})
    assert _constraint(error.value) == f"ck_{table}_slug_format"


@pytest.mark.parametrize("slug", GOOD_SLUGS)
@pytest.mark.parametrize("table", ["schools", "organizations"])
def test_a_slug_with_a_good_format_is_accepted(
    admin_engine: Engine, tenants: Tenants, table: str, slug: str
) -> None:
    suffix = uuid.uuid4().hex[:6]
    unique = f"{slug}-{suffix}"
    sql = (
        "INSERT INTO schools (organization_id, name, slug) VALUES (:org, 'x', :slug)"
        if table == "schools"
        else "INSERT INTO organizations (name, slug) VALUES ('x', :slug)"
    )
    with transaction(admin_engine) as connection:
        connection.execute(text(sql), {"org": tenants.org_a, "slug": unique})


# --- S4 to S6: the foreign keys exist, with the exact shape and RESTRICT -------------------------

EXPECTED_FOREIGN_KEYS = {
    "fk_invitations_accepted_user_id_users": (
        "invitations",
        "users",
        "accepted_user_id",
        "id",
    ),
    "fk_invitations_invited_by_user_id_users": (
        "invitations",
        "users",
        "invited_by_user_id",
        "id",
    ),
    "fk_invitations_organization_id_organizations": (
        "invitations",
        "organizations",
        "organization_id",
        "id",
    ),
    "fk_invitations_school_id_organization_id_schools": (
        "invitations",
        "schools",
        "school_id,organization_id",
        "id,organization_id",
    ),
    # A session can only point at a membership of ITS OWN user (composite key, 0006).
    "fk_sessions_membership_id_user_id_memberships": (
        "sessions",
        "memberships",
        "membership_id,user_id",
        "id,user_id",
    ),
    "fk_sessions_user_id_users": ("sessions", "users", "user_id", "id"),
    "fk_memberships_organization_id_organizations": (
        "memberships",
        "organizations",
        "organization_id",
        "id",
    ),
    "fk_memberships_school_id_organization_id_schools": (
        "memberships",
        "schools",
        "school_id,organization_id",
        "id,organization_id",
    ),
    "fk_memberships_user_id_users": ("memberships", "users", "user_id", "id"),
    "fk_schools_organization_id_organizations": (
        "schools",
        "organizations",
        "organization_id",
        "id",
    ),
}


def test_the_foreign_keys_are_exactly_these_and_all_restrict(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT con.conname, rel.relname, ref.relname, "
                "(SELECT string_agg(att.attname, ',' ORDER BY k.ord) FROM unnest(con.conkey) "
                "   WITH ORDINALITY k(attnum, ord) JOIN pg_attribute att "
                "   ON att.attrelid = con.conrelid AND att.attnum = k.attnum), "
                "(SELECT string_agg(att.attname, ',' ORDER BY k.ord) FROM unnest(con.confkey) "
                "   WITH ORDINALITY k(attnum, ord) JOIN pg_attribute att "
                "   ON att.attrelid = con.confrelid AND att.attnum = k.attnum), "
                "con.confdeltype, con.confupdtype, con.confmatchtype "
                "FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_class ref ON ref.oid = con.confrelid "
                "JOIN pg_namespace n ON n.oid = rel.relnamespace "
                "WHERE con.contype = 'f' AND n.nspname = 'public'"
            )
        ).all()

    found: dict[str, tuple[Any, ...]] = {r[0]: tuple(r[1:5]) for r in rows}
    assert found == EXPECTED_FOREIGN_KEYS
    # ON DELETE RESTRICT ('r'), ON UPDATE NO ACTION ('a'), MATCH SIMPLE ('s') for every one.
    assert {tuple(r[5:]) for r in rows} == {("r", "a", "s")}


def test_a_membership_needs_an_existing_user(admin_engine: Engine, tenants: Tenants) -> None:
    with transaction(admin_engine) as connection, pytest.raises(IntegrityError) as error:
        connection.execute(
            text(
                "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
                "VALUES (:u, :o, NULL, 'viewer', 'active')"
            ),
            {"u": uuid.uuid4(), "o": tenants.org_a},
        )
    assert _constraint(error.value) == "fk_memberships_user_id_users"


def test_a_school_needs_an_existing_organization(admin_engine: Engine) -> None:
    with transaction(admin_engine) as connection, pytest.raises(IntegrityError) as error:
        connection.execute(
            text("INSERT INTO schools (organization_id, name, slug) VALUES (:o, 'x', 's5-orphan')"),
            {"o": uuid.uuid4()},
        )
    assert _constraint(error.value) == "fk_schools_organization_id_organizations"


# Which foreign key may refuse the delete. Deleting an organization is referenced by schools AND by
# memberships: PostgreSQL fires the referential triggers in a name-dependent order, so the test must
# not assume which one reports first. It asserts the set of legitimate refusals instead.
REFERENCED_DELETES = [
    ("DELETE FROM users WHERE id = :user_a1", {"fk_memberships_user_id_users"}),
    (
        "DELETE FROM schools WHERE id = :school_a1",
        {"fk_memberships_school_id_organization_id_schools"},
    ),
    (
        "DELETE FROM organizations WHERE id = :org_a",
        {
            "fk_schools_organization_id_organizations",
            "fk_memberships_organization_id_organizations",
        },
    ),
]


@pytest.mark.parametrize(("sql", "allowed"), REFERENCED_DELETES)
def test_deleting_something_that_is_referenced_is_refused(
    admin_engine: Engine, tenants: Tenants, sql: str, allowed: set[str]
) -> None:
    params = {"user_a1": tenants.user_a1, "school_a1": tenants.school_a1, "org_a": tenants.org_a}
    with transaction(admin_engine) as connection, pytest.raises(IntegrityError) as error:
        connection.execute(text(sql), params)
    assert _constraint(error.value) in allowed
    assert "violates foreign key constraint" in str(error.value.orig)


# --- the CHECK constraints of the authentication tables (0006) -----------------------------------
# Asserted like the foreign keys, twice: from the catalog (the constraint exists, with exactly this
# definition) and by behaviour (the database refuses the write, naming THAT constraint). Removing or
# weakening any one of them fails both.

AUTH_TABLES = ("sessions", "login_attempts", "invitations")
EXPECTED_AUTH_CHECKS = {
    "ck_sessions_id_is_a_sha256": ("sessions", "CHECK ((octet_length(id) = 32))"),
    "ck_sessions_expires_after_creation": ("sessions", "CHECK ((expires_at > created_at))"),
    "ck_sessions_revoked_reason_valid": (
        "sessions",
        "CHECK (((revoked_reason IS NULL) OR (revoked_reason = ANY (ARRAY['logout'::text, "
        "'rotated'::text, 'password_changed'::text, 'membership_inactive'::text, "
        "'user_inactive'::text]))))",
    ),
    "ck_login_attempts_kind_valid": (
        "login_attempts",
        "CHECK ((kind = ANY (ARRAY['login'::text, 'password'::text, 'invitation'::text])))",
    ),
    "ck_login_attempts_hmacs_are_sha256": (
        "login_attempts",
        "CHECK (((octet_length(subject_hmac) = 32) AND (octet_length(ip_hmac) = 32)))",
    ),
    "ck_invitations_token_hash_is_a_sha256": (
        "invitations",
        "CHECK ((octet_length(token_hash) = 32))",
    ),
    "ck_invitations_email_format": (
        "invitations",
        r"CHECK (((email ~ '^[^@\s]+@[^@\s]+$'::text) AND (email = lower(email))))",
    ),
    "ck_invitations_role_valid": (
        "invitations",
        "CHECK ((role = ANY (ARRAY['organization_admin'::text, 'school_admin'::text, "
        "'treasurer'::text, 'staff'::text, 'viewer'::text])))",
    ),
    "ck_invitations_organization_admin_is_org_wide": (
        "invitations",
        "CHECK (((role <> 'organization_admin'::text) OR (school_id IS NULL)))",
    ),
    "ck_invitations_expires_after_creation": (
        "invitations",
        "CHECK ((expires_at > created_at))",
    ),
    "ck_invitations_accepted_has_a_user": (
        "invitations",
        "CHECK (((accepted_at IS NULL) = (accepted_user_id IS NULL)))",
    ),
}


def test_the_check_constraints_of_the_auth_tables_are_exactly_these(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT con.conname, rel.relname, pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace n ON n.oid = rel.relnamespace "
                "WHERE con.contype = 'c' AND n.nspname = 'public' AND rel.relname = ANY(:tables)"
            ),
            {"tables": list(AUTH_TABLES)},
        ).all()

    assert {r[0]: (r[1], r[2]) for r in rows} == EXPECTED_AUTH_CHECKS


_SESSION = (
    "INSERT INTO sessions (id, user_id, expires_at) "
    "VALUES (CAST(:id AS bytea), :user, now() + interval '1 hour')"
)
_ATTEMPT = (
    "INSERT INTO login_attempts (kind, subject_hmac, ip_hmac, succeeded) "
    "VALUES (:kind, CAST(:subject AS bytea), CAST(:ip AS bytea), true)"
)
_INVITATION = (
    "INSERT INTO invitations (organization_id, school_id, email, role, token_hash, "
    "invited_by_user_id, expires_at, accepted_at, accepted_user_id) "
    "VALUES (:org, :school, :email, :role, CAST(:hash AS bytea), :user, "
    "now() + interval '1 hour', :accepted_at, :accepted_user)"
)
_SHA = "\\x" + "ab" * 32  # a 32 byte value, as a bytea literal
_SHORT = "\\x01"


def _invitation_values(tenants: Tenants, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "org": tenants.org_a,
        "school": tenants.school_a1,
        "email": "check@example.test",
        "role": "staff",
        "hash": _SHA,
        "user": tenants.user_admin_a,
        "accepted_at": None,
        "accepted_user": None,
    }
    return values | overrides


def _auth_check_cases(tenants: Tenants) -> list[tuple[str, str, dict[str, Any]]]:
    sess = {"id": _SHA, "user": tenants.user_a1}
    att = {"kind": "login", "subject": _SHA, "ip": _SHA}
    inv = _invitation_values
    return [
        ("ck_sessions_id_is_a_sha256", _SESSION, sess | {"id": _SHORT}),
        (
            "ck_sessions_expires_after_creation",
            "INSERT INTO sessions (id, user_id, expires_at) "
            "VALUES (CAST(:id AS bytea), :user, now() - interval '1 hour')",
            sess,
        ),
        (
            "ck_sessions_revoked_reason_valid",
            "INSERT INTO sessions (id, user_id, expires_at, revoked_reason) "
            "VALUES (CAST(:id AS bytea), :user, now() + interval '1 hour', 'because')",
            sess,
        ),
        ("ck_login_attempts_kind_valid", _ATTEMPT, att | {"kind": "reset"}),
        ("ck_login_attempts_hmacs_are_sha256", _ATTEMPT, att | {"subject": _SHORT}),
        ("ck_login_attempts_hmacs_are_sha256", _ATTEMPT, att | {"ip": _SHORT}),
        ("ck_invitations_token_hash_is_a_sha256", _INVITATION, inv(tenants, hash=_SHORT)),
        ("ck_invitations_email_format", _INVITATION, inv(tenants, email="nobody")),
        ("ck_invitations_email_format", _INVITATION, inv(tenants, email="Mixed@Example.test")),
        ("ck_invitations_role_valid", _INVITATION, inv(tenants, role="owner")),
        (
            "ck_invitations_organization_admin_is_org_wide",
            _INVITATION,
            inv(tenants, role="organization_admin"),
        ),
        (
            "ck_invitations_expires_after_creation",
            _INVITATION.replace("now() + interval '1 hour'", "now() - interval '1 hour'"),
            inv(tenants),
        ),
        (
            "ck_invitations_accepted_has_a_user",
            _INVITATION,
            inv(tenants, accepted_at=datetime.now(UTC)),
        ),
        (
            "ck_invitations_accepted_has_a_user",
            _INVITATION,
            inv(tenants, accepted_user=tenants.user_a1),
        ),
    ]


def test_every_check_of_the_auth_tables_refuses_a_write_that_breaks_it(
    admin_engine: Engine, tenants: Tenants
) -> None:
    cases = _auth_check_cases(tenants)
    assert {name for name, _sql, _params in cases} == set(EXPECTED_AUTH_CHECKS)  # none forgotten

    for name, sql, params in cases:
        with transaction(admin_engine) as connection, pytest.raises(IntegrityError) as error:
            connection.execute(text(sql), params)
        assert _constraint(error.value) == name, (name, params)


def test_the_rows_the_checks_are_tried_with_are_good_ones(
    admin_engine: Engine, tenants: Tenants
) -> None:
    """The control: with nothing broken every one of those inserts is accepted (they are rolled
    back), so a refusal above is really the constraint and not some other column."""
    with transaction(admin_engine) as connection:
        connection.execute(text(_SESSION), {"id": _SHA, "user": tenants.user_a1})
        connection.execute(text(_ATTEMPT), {"kind": "login", "subject": _SHA, "ip": _SHA})
        connection.execute(text(_INVITATION), _invitation_values(tenants))
