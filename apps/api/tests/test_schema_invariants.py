"""Schema invariants the review showed were only checked indirectly (S1 to S6): each one is asserted
twice, from the catalog (the constraint or privilege exists, exactly as designed) and by behaviour
(the database refuses the write). Run as the admin, which bypasses RLS: only the schema can stop it.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app import models  # noqa: F401  (registers the models)
from app.db.base import Base
from tests.dbsupport import Tenants, transaction
from tests.financial.test_isolation import TABLES as FINANCIAL_TABLES


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

    # The tenancy and authentication tables, and the 13 financial tables of ADR-015: every one of
    # them has it NOT NULL.
    expected = [
        ("invitations", True),
        ("memberships", True),
        ("schools", True),
        *((t, True) for t in FINANCIAL_TABLES),
    ]
    assert [tuple(r) for r in rows] == sorted(expected)


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


def _financial_foreign_keys() -> dict[str, tuple[str, ...]]:
    """The foreign keys of the financial tables as the models declare them (test_models proves the
    names match the catalog; here the whole shape is compared: tables and ordered columns)."""
    expected: dict[str, tuple[str, ...]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in FINANCIAL_TABLES:
            continue
        for constraint in table.foreign_key_constraints:
            expected[str(constraint.name)] = (
                table.name,
                constraint.referred_table.name,
                ",".join(column.name for column in constraint.columns),
                ",".join(element.column.name for element in constraint.elements),
            )
    return expected


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
    financial = _financial_foreign_keys()
    assert len(financial) >= 40  # not an empty comparison: 46 keys across the 13 tables
    assert found == {**EXPECTED_FOREIGN_KEYS, **financial}
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


def _financial_references(table: str) -> set[str]:
    """The foreign keys of the financial tables that point at `table` (every school has its
    settings, for one): they may report the refusal too, ahead of the tenancy key."""
    return {name for name, shape in _financial_foreign_keys().items() if shape[1] == table}


@pytest.mark.parametrize(("sql", "allowed"), REFERENCED_DELETES)
def test_deleting_something_that_is_referenced_is_refused(
    admin_engine: Engine, tenants: Tenants, sql: str, allowed: set[str]
) -> None:
    params = {"user_a1": tenants.user_a1, "school_a1": tenants.school_a1, "org_a": tenants.org_a}
    table = sql.split()[2]  # DELETE FROM <table> WHERE ...
    with transaction(admin_engine) as connection, pytest.raises(IntegrityError) as error:
        connection.execute(text(sql), params)
    assert _constraint(error.value) in allowed | _financial_references(table)
    assert "violates foreign key constraint" in str(error.value.orig)
