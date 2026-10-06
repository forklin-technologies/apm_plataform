"""expiry job: the function that says WHERE to look (ADR-019)

Revision ID: 0009_expiry_job
Revises: 0008_public_functions

Expiring old Pix charges and contributions is a routine with no school in hand: it has to find the
schools that have something to expire, and row level security (fail closed) shows nothing without a
context. ADR-019 adds ONE narrow function to the closed list: it returns only the ids of the schools
with pending expiry work, never a row of money. Like the six before it, it belongs to apm_definer (no
login, no superuser, no BYPASSRLS), has privileges per COLUMN and one explicit policy `TO apm_definer`
per table, each as narrow as it can be.

The writing does NOT happen in the function: the job opens one session per school, binds that school
and expires as apm_app under row level security, with the audit of the triggers (actor SYSTEM).

The two rules are written here too (120 seconds of grace after a charge's `expires_at`, 24 hours for
a contribution) and in `app/contributions/expiry.py`; a test keeps both in agreement.
"""

from alembic import op

revision: str = "0009_expiry_job"
down_revision: str | None = "0008_public_functions"
branch_labels = None
depends_on = None

FIND_SCHOOLS = """
CREATE FUNCTION public.find_schools_with_stale_contributions(p_after uuid, p_limit integer)
RETURNS TABLE (organization_id uuid, school_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $fn$
    SELECT s.organization_id, s.school_id
    FROM (
        SELECT c.organization_id, c.school_id
        FROM public.pix_charges c
        WHERE c.status = 'PENDING' AND c.expires_at + interval '120 seconds' <= now()
        UNION
        SELECT f.organization_id, f.school_id
        FROM public.financial_transactions f
        WHERE f.kind = 'CONTRIBUTION' AND f.status = 'PENDING_PAYMENT'
          AND f.created_at + interval '24 hours' <= now()
          AND NOT EXISTS (
              SELECT 1 FROM public.pix_charges c
              WHERE c.transaction_id = f.id AND c.status = 'PENDING')
    ) s
    WHERE p_after IS NULL OR s.school_id > p_after
    ORDER BY s.school_id
    LIMIT least(greatest(coalesce(p_limit, 1), 1), 500)
$fn$
"""

SIGNATURE = "find_schools_with_stale_contributions(uuid, integer)"

POLICIES = (
    ("pix_charges", "pix_charges_definer_stale_select"),
    ("financial_transactions", "financial_transactions_definer_stale_select"),
)


def upgrade() -> None:
    op.execute("SET LOCAL ROLE apm_owner")
    op.execute(
        "GRANT SELECT (transaction_id, organization_id, school_id, status, expires_at) "
        "ON pix_charges TO apm_definer"
    )
    op.execute(
        "GRANT SELECT (id, organization_id, school_id, kind, status, created_at) "
        "ON financial_transactions TO apm_definer"
    )
    op.execute(
        "CREATE POLICY pix_charges_definer_stale_select ON pix_charges FOR SELECT TO apm_definer "
        "USING (status = 'PENDING')"
    )
    op.execute(
        "CREATE POLICY financial_transactions_definer_stale_select ON financial_transactions "
        "FOR SELECT TO apm_definer USING (kind = 'CONTRIBUTION' AND status = 'PENDING_PAYMENT')"
    )
    op.execute("RESET ROLE")

    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO apm_definer")
    op.execute("SET LOCAL ROLE apm_definer")
    op.execute(FIND_SCHOOLS)
    op.execute(f"REVOKE ALL ON FUNCTION public.{SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{SIGNATURE} TO apm_app")
    op.execute("RESET ROLE")
    op.execute("REVOKE CREATE ON SCHEMA public FROM apm_definer")


def downgrade() -> None:
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO apm_definer")
    op.execute("SET LOCAL ROLE apm_definer")
    op.execute(f"DROP FUNCTION IF EXISTS public.{SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute("REVOKE CREATE ON SCHEMA public FROM apm_definer")

    op.execute("SET LOCAL ROLE apm_owner")
    for table, policy in reversed(POLICIES):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute("REVOKE ALL ON pix_charges FROM apm_definer")
    op.execute("REVOKE ALL ON financial_transactions FROM apm_definer")
    op.execute("RESET ROLE")
