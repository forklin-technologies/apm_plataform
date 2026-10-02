"""tenancy: no INSERT on memberships and UPDATE only on the mutable columns (M1)

Revision ID: 0005_tenancy_write_grants
Revises: 0004_tenancy_rls
Create Date: 2026-10-01

Why. The foreign key memberships.user_id -> users(id) is checked by the database without row level
security, so it accepts ANY existing user. With INSERT (or UPDATE of user_id) on memberships, an
admin of organization A who knew the UUID of a user that only belongs to organization B could add
that user to A and then read the person through users_select (a visible membership shows the user),
and could probe which UUIDs exist from the foreign key error.

So apm_app can no longer create memberships (the invitation flow of TASK-004 will, through a narrow
SECURITY DEFINER function with its own ADR; the seed uses the admin) and can UPDATE only columns
that change the business data of a row, never the ones that decide which tenant or user it belongs
to (id, organization_id, school_id, user_id) nor created_at.

Mutable columns, and why:
  organizations: name, updated_at.  slug is not mutable by the application: renaming is a platform
                 operation (it changes URLs and uniqueness for everyone).
  schools:       name, updated_at.  slug is not mutable: /apm/{slug} is the public address (ADR-010)
                 printed in links and QR codes; changing it is a deliberate, separate operation.
  memberships:   role, status, updated_at.  These are what an organization admin legitimately
                 changes (promote, suspend, revoke) without touching who the membership is for.
Which role may change them is decided by the permission matrix in code (TASK-004), not here.

Also (N2): no TEMPORARY privilege on the database for PUBLIC or apm_app. A temporary table lives as
long as the pooled connection and shadows a real table for whoever puts pg_temp first in the
search_path, so letting the application role create them is a way to plant state for the next
user of a connection. The pool also runs DISCARD ALL when a connection is returned (app/db/session).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_tenancy_write_grants"
down_revision: str | None = "0004_tenancy_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MUTABLE_COLUMNS = {
    "organizations": ("name", "updated_at"),
    "schools": ("name", "updated_at"),
    "memberships": ("role", "status", "updated_at"),
}


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
            EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM apm_app', current_database());
        END
        $$
        """
    )
    op.execute("SET LOCAL ROLE apm_owner")
    op.execute("REVOKE INSERT ON memberships FROM apm_app")
    for table, columns in MUTABLE_COLUMNS.items():
        # Revoking UPDATE removes the table-level grant and any column-level one.
        op.execute(f"REVOKE UPDATE ON {table} FROM apm_app")
        op.execute(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO apm_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    # Back to the grants of 0004 (so that 0004's own downgrade finds what it expects).
    op.execute("SET LOCAL ROLE apm_owner")
    for table in MUTABLE_COLUMNS:
        op.execute(f"REVOKE UPDATE ON {table} FROM apm_app")
        op.execute(f"GRANT UPDATE ON {table} TO apm_app")
    op.execute("GRANT INSERT ON memberships TO apm_app")
    op.execute("RESET ROLE")
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO PUBLIC', current_database());
        END
        $$
        """
    )
