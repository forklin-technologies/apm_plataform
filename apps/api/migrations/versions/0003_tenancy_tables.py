"""tenancy: organizations, schools, users and memberships

Revision ID: 0003_tenancy_tables
Revises: 0002_tenancy_roles
Create Date: 2026-10-01

The tables are created as apm_owner (not as the admin), so apm_owner owns them. Row level
security comes in the next revision; until then apm_app has no privilege on them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_tenancy_tables"
down_revision: str | None = "0002_tenancy_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SLUG_PATTERN = "^[a-z0-9]+(-[a-z0-9]+)*$"
TIMESTAMPS = """
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE apm_owner")
    op.execute(
        f"""
        CREATE TABLE organizations (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            name text NOT NULL,
            slug text NOT NULL,
            {TIMESTAMPS},
            CONSTRAINT pk_organizations PRIMARY KEY (id),
            CONSTRAINT uq_organizations_slug UNIQUE (slug),
            CONSTRAINT ck_organizations_slug_format CHECK (slug ~ '{SLUG_PATTERN}'),
            CONSTRAINT ck_organizations_name_not_blank CHECK (length(btrim(name)) > 0)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE schools (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            name text NOT NULL,
            slug text NOT NULL,
            {TIMESTAMPS},
            CONSTRAINT pk_schools PRIMARY KEY (id),
            CONSTRAINT fk_schools_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
            CONSTRAINT uq_schools_slug UNIQUE (slug),
            CONSTRAINT uq_schools_id_organization_id UNIQUE (id, organization_id),
            CONSTRAINT ck_schools_slug_format CHECK (slug ~ '{SLUG_PATTERN}'),
            CONSTRAINT ck_schools_name_not_blank CHECK (length(btrim(name)) > 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_schools_organization_id ON schools (organization_id)")
    op.execute(
        f"""
        CREATE TABLE users (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            email text NOT NULL,
            full_name text NOT NULL,
            password_hash text,
            is_active boolean NOT NULL DEFAULT true,
            {TIMESTAMPS},
            CONSTRAINT pk_users PRIMARY KEY (id),
            CONSTRAINT ck_users_email_format CHECK (email ~ '^[^@\\s]+@[^@\\s]+$'),
            CONSTRAINT ck_users_full_name_not_blank CHECK (length(btrim(full_name)) > 0)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")
    op.execute(
        f"""
        CREATE TABLE memberships (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            school_id uuid,
            role text NOT NULL,
            status text NOT NULL DEFAULT 'invited',
            {TIMESTAMPS},
            CONSTRAINT pk_memberships PRIMARY KEY (id),
            CONSTRAINT fk_memberships_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_memberships_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
            -- Never mixes an organization with a school of another one. school_id NULL skips the
            -- check (MATCH SIMPLE): that is how organization-wide access is stored.
            CONSTRAINT fk_memberships_school_id_organization_id_schools
                FOREIGN KEY (school_id, organization_id)
                REFERENCES schools (id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT ck_memberships_role_valid CHECK (
                role IN ('organization_admin', 'school_admin', 'treasurer', 'staff', 'viewer')),
            CONSTRAINT ck_memberships_status_valid CHECK (
                status IN ('invited', 'active', 'suspended', 'revoked')),
            CONSTRAINT ck_memberships_organization_admin_is_org_wide CHECK (
                role <> 'organization_admin' OR school_id IS NULL)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_memberships_user_org_school "
        "ON memberships (user_id, organization_id, school_id) WHERE school_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_memberships_user_org_wide "
        "ON memberships (user_id, organization_id) WHERE school_id IS NULL"
    )
    op.execute("CREATE INDEX ix_memberships_user_id ON memberships (user_id)")
    op.execute("CREATE INDEX ix_memberships_organization_id ON memberships (organization_id)")
    op.execute("CREATE INDEX ix_memberships_school_id ON memberships (school_id)")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE apm_owner")
    op.execute("DROP TABLE IF EXISTS memberships")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS schools")
    op.execute("DROP TABLE IF EXISTS organizations")
    op.execute("RESET ROLE")
