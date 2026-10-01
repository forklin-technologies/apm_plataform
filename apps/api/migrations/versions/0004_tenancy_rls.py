"""tenancy: row level security (ENABLE + FORCE, fail closed) and minimal grants

Revision ID: 0004_tenancy_rls
Revises: 0003_tenancy_tables
Create Date: 2026-10-01

Scope predicate P(org, school): the row belongs to the context organization and, when the context
also names a school, to that school. With no context app_org() is NULL, the comparison is not
true and no row is visible or writable. There is no "system" switch and no USING (true) policy.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_tenancy_rls"
down_revision: str | None = "0003_tenancy_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("organizations", "schools", "users", "memberships")


def _scope(org_column: str, school_column: str) -> str:
    # The subselects make the planner evaluate each function once per statement, not per row.
    return (
        f"({org_column} = (SELECT public.app_org()) AND "
        f"((SELECT public.app_school()) IS NULL OR {school_column} = (SELECT public.app_school())))"
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE apm_owner")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # organizations: the tenant itself. Only its own row, whatever the school scope.
    own_org = "id = (SELECT public.app_org())"
    op.execute(f"CREATE POLICY organizations_select ON organizations FOR SELECT USING ({own_org})")
    op.execute(
        f"CREATE POLICY organizations_update ON organizations FOR UPDATE "
        f"USING ({own_org}) WITH CHECK ({own_org})"
    )
    # No privilege for apm_app: only the owner (migrations, seed) inserts organizations, and
    # only with the new organization as the context. Creating tenants as a platform flow needs
    # its own ADR.
    op.execute(
        f"CREATE POLICY organizations_insert ON organizations FOR INSERT WITH CHECK ({own_org})"
    )

    # schools: an org-wide context sees every school of the organization; a school context, only it.
    schools_scope = _scope("organization_id", "id")
    op.execute(f"CREATE POLICY schools_select ON schools FOR SELECT USING ({schools_scope})")
    op.execute(
        f"CREATE POLICY schools_update ON schools FOR UPDATE "
        f"USING ({schools_scope}) WITH CHECK ({schools_scope})"
    )
    op.execute(
        "CREATE POLICY schools_insert ON schools FOR INSERT WITH CHECK "
        "(organization_id = (SELECT public.app_org()) AND (SELECT public.app_school()) IS NULL)"
    )

    # memberships: an organization-wide row (school_id NULL) only appears in an org-wide context.
    memberships_scope = _scope("organization_id", "school_id")
    for command, clauses in (
        ("SELECT", f"USING ({memberships_scope})"),
        ("INSERT", f"WITH CHECK ({memberships_scope})"),
        ("UPDATE", f"USING ({memberships_scope}) WITH CHECK ({memberships_scope})"),
        ("DELETE", f"USING ({memberships_scope})"),
    ):
        op.execute(
            f"CREATE POLICY memberships_{command.lower()} ON memberships FOR {command} {clauses}"
        )

    # users: global identity. Visible only through a membership that is itself visible in the
    # context. No write policy: creating or changing users comes with authentication (TASK-004).
    op.execute(
        f"""
        CREATE POLICY users_select ON users FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = users.id AND {_scope("m.organization_id", "m.school_id")}
            )
        )
        """
    )

    # Minimal, explicit grants. A table created later starts with no access for apm_app.
    op.execute("GRANT SELECT, UPDATE ON organizations TO apm_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON schools TO apm_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO apm_app")
    # password_hash is left out on purpose: nothing in the application role may read it yet.
    op.execute(
        "GRANT SELECT (id, email, full_name, is_active, created_at, updated_at) ON users TO apm_app"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE apm_owner")
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM apm_app")
    for table, policies in (
        ("organizations", ("select", "update", "insert")),
        ("schools", ("select", "update", "insert")),
        ("memberships", ("select", "insert", "update", "delete")),
        ("users", ("select",)),
    ):
        for policy in policies:
            op.execute(f"DROP POLICY {table}_{policy} ON {table}")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("RESET ROLE")
