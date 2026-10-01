"""T3: integrity enforced by the schema itself (run as the admin, which bypasses RLS: only the
constraints can stop these writes)."""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from tests.dbsupport import Tenants, transaction


def _violated(error: IntegrityError) -> str:
    return str(error.orig.diag.constraint_name) if error.orig is not None else ""


def _insert_membership(connection, tenants: Tenants, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "user": tenants.user_free,
        "org": tenants.org_a,
        "school": tenants.school_a1,
        "role": "viewer",
        "status": "active",
    }
    values.update(overrides)
    connection.execute(
        text(
            "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
            "VALUES (:user, :org, :school, :role, :status)"
        ),
        values,
    )


def test_composite_fk_rejects_a_school_of_another_organization(
    admin_engine: Engine, tenants: Tenants
) -> None:
    with transaction(admin_engine) as connection:
        with pytest.raises(IntegrityError) as error:
            _insert_membership(connection, tenants, school=tenants.school_b1)

    assert _violated(error.value) == "fk_memberships_school_id_organization_id_schools"


def test_composite_fk_accepts_a_school_of_the_same_organization(
    admin_engine: Engine, tenants: Tenants
) -> None:
    with transaction(admin_engine) as connection:
        _insert_membership(connection, tenants, school=tenants.school_a2)


def test_membership_without_school_means_the_whole_organization(
    admin_engine: Engine, tenants: Tenants
) -> None:
    with transaction(admin_engine) as connection:
        _insert_membership(connection, tenants, school=None, role="treasurer")


def test_membership_still_needs_an_existing_organization(
    admin_engine: Engine, tenants: Tenants
) -> None:
    with transaction(admin_engine) as connection:
        with pytest.raises(IntegrityError) as error:
            _insert_membership(connection, tenants, school=None, org=uuid.uuid4())

    assert _violated(error.value) == "fk_memberships_organization_id_organizations"


def test_school_slug_is_unique_across_organizations(admin_engine: Engine, tenants: Tenants) -> None:
    with transaction(admin_engine) as connection:
        with pytest.raises(IntegrityError) as error:
            connection.execute(
                text("INSERT INTO schools (organization_id, name, slug) VALUES (:org, 'Dup', :slug)"),
                {"org": tenants.org_b, "slug": tenants.slugs[2]},  # the slug of school A1
            )

    assert _violated(error.value) == "uq_schools_slug"


def test_organization_slug_is_unique(admin_engine: Engine, tenants: Tenants) -> None:
    with transaction(admin_engine) as connection:
        with pytest.raises(IntegrityError) as error:
            connection.execute(
                text("INSERT INTO organizations (name, slug) VALUES ('Dup', :slug)"),
                {"slug": tenants.slugs[0]},
            )

    assert _violated(error.value) == "uq_organizations_slug"


@pytest.mark.parametrize("transform", [str.upper, str.title, lambda e: e.swapcase()])
def test_user_email_is_unique_ignoring_case(
    admin_engine: Engine, tenants: Tenants, transform: object
) -> None:
    clash = transform(tenants.emails[0])  # type: ignore[operator]
    assert clash != tenants.emails[0]

    with transaction(admin_engine) as connection:
        with pytest.raises(IntegrityError) as error:
            connection.execute(
                text("INSERT INTO users (email, full_name) VALUES (:email, 'Dup')"), {"email": clash}
            )

    assert _violated(error.value) == "uq_users_email_lower"


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("INSERT INTO organizations (name, slug) VALUES ('X', 'Bad Slug')", "ck_organizations_slug_format"),
        ("INSERT INTO organizations (name, slug) VALUES ('X', '-edge')", "ck_organizations_slug_format"),
        ("INSERT INTO organizations (name, slug) VALUES ('  ', 'blank-name')", "ck_organizations_name_not_blank"),
        ("INSERT INTO users (email, full_name) VALUES ('not-an-email', 'X')", "ck_users_email_format"),
        ("INSERT INTO users (email, full_name) VALUES ('a b@example.test', 'X')", "ck_users_email_format"),
    ],
)
def test_check_constraints_reject_bad_values(
    admin_engine: Engine, statement: str, constraint: str
) -> None:
    with transaction(admin_engine) as connection:
        with pytest.raises(IntegrityError) as error:
            connection.execute(text(statement))

    assert _violated(error.value) == constraint


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        # platform_admin is deliberately not a membership role (ADR-014).
        ({"role": "platform_admin", "school": None}, "ck_memberships_role_valid"),
        ({"role": "owner"}, "ck_memberships_role_valid"),
        ({"status": "deleted"}, "ck_memberships_status_valid"),
        # An organization admin is organization-wide: it cannot be pinned to one school.
        ({"role": "organization_admin"}, "ck_memberships_organization_admin_is_org_wide"),
    ],
)
def test_membership_checks(
    admin_engine: Engine, tenants: Tenants, overrides: dict[str, object], constraint: str
) -> None:
    with transaction(admin_engine) as connection:
        with pytest.raises(IntegrityError) as error:
            _insert_membership(connection, tenants, **overrides)

    assert _violated(error.value) == constraint


def test_every_membership_role_in_the_spec_is_accepted(
    admin_engine: Engine, tenants: Tenants
) -> None:
    with transaction(admin_engine) as connection:
        _insert_membership(connection, tenants, school=None, role="organization_admin")
        _insert_membership(connection, tenants, school=tenants.school_a1, role="school_admin")
        _insert_membership(connection, tenants, school=tenants.school_a2, role="treasurer")
        _insert_membership(connection, tenants, school=tenants.school_a2, role="staff", user=tenants.user_a1)


def test_one_membership_per_user_and_scope(admin_engine: Engine, tenants: Tenants) -> None:
    with transaction(admin_engine) as connection:
        _insert_membership(connection, tenants, school=tenants.school_a1)
        with pytest.raises(IntegrityError) as school_error:
            _insert_membership(connection, tenants, school=tenants.school_a1, role="staff")
    with transaction(admin_engine) as connection:
        _insert_membership(connection, tenants, school=None)
        with pytest.raises(IntegrityError) as org_error:
            _insert_membership(connection, tenants, school=None, role="staff")

    assert _violated(school_error.value) == "uq_memberships_user_org_school"
    assert _violated(org_error.value) == "uq_memberships_user_org_wide"
