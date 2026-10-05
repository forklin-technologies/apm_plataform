"""public flow: the three remaining SECURITY DEFINER functions, the idempotency key, secret_ref (ADR-018)

Revision ID: 0008_public_functions
Revises: 0007_financial_schema

Before there is a session the database does not know which school is asking, and row level security
shows nothing (fail closed). Three requests reach the API in that state: the page of a school (by
slug), the webhook of the Pix provider (by the secret of the payment account) and the receipt of a
contribution (by its token). ADR-016 listed these functions; ADR-018 fixes their signature, their
output and their policies. Like the three of 0006 they belong to apm_definer (no login, no superuser,
no BYPASSRLS), have privileges per COLUMN and explicit policies `TO apm_definer`, each as narrow as it
can be. Nothing else of the public flow needs a function: once the school is resolved the API binds
its context and writes as apm_app under row level security.

Also here:
- `contributions.idempotency_key`: the Idempotency-Key of the public POST, unique PER SCHOOL (never
  global: the M1 lesson), written once with the contribution and never changed;
- `payment_accounts.secret_ref` is no longer writable by apm_app: only the platform (the admin of the
  migrations and the seed, later a platform flow) names the secret an account uses, so a school
  cannot point its account at the secret of another school.
"""

from alembic import op

revision: str = "0008_public_functions"
down_revision: str | None = "0007_financial_schema"
branch_labels = None
depends_on = None

# The columns of contributions that never change (0007) plus the new one.
CONTRIBUTION_IMMUTABLE = (
    "transaction_id",
    "organization_id",
    "school_id",
    "kind",
    "method",
    "external_reference",
    "receipt_token_hash",
    "receipt_expires_at",
    "created_at",
)

RESOLVE_SCHOOL_PUBLIC = """
CREATE FUNCTION public.resolve_school_public(p_slug text)
RETURNS TABLE (
    organization_id uuid, school_id uuid, slug text, name text, accent_color text,
    accent_contrast_color text, suggested_amounts_cents bigint[], allow_custom_amount boolean,
    min_amount_cents bigint, max_amount_cents bigint, required_fields text[], optional_fields text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $fn$
    SELECT s.organization_id, s.id, s.slug, s.name, c.brand_accent, c.brand_accent_contrast,
           c.suggested_amounts_cents, c.allow_custom_amount, c.min_contribution_cents,
           c.max_contribution_cents, c.required_fields, c.optional_fields
    FROM public.schools s
    JOIN public.school_settings c ON c.school_id = s.id
    WHERE s.slug = p_slug
    LIMIT 1
$fn$
"""

RESOLVE_WEBHOOK_TARGET = """
CREATE FUNCTION public.resolve_webhook_target(p_provider text, p_secret_hash text)
RETURNS TABLE (organization_id uuid, school_id uuid, payment_account_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $fn$
    SELECT a.organization_id, a.school_id, a.id
    FROM public.payment_accounts a
    WHERE a.webhook_secret_hash = p_secret_hash AND a.provider = p_provider AND a.status = 'ACTIVE'
    LIMIT 1
$fn$
"""

RESOLVE_RECEIPT = """
CREATE FUNCTION public.resolve_receipt(p_token_hash text)
RETURNS TABLE (organization_id uuid, school_id uuid, transaction_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $fn$
    SELECT c.organization_id, c.school_id, c.transaction_id
    FROM public.contributions c
    WHERE c.receipt_token_hash = p_token_hash
    LIMIT 1
$fn$
"""

FUNCTIONS = (
    (RESOLVE_SCHOOL_PUBLIC, "resolve_school_public(text)"),
    (RESOLVE_WEBHOOK_TARGET, "resolve_webhook_target(text, text)"),
    (RESOLVE_RECEIPT, "resolve_receipt(text)"),
)

# A school that has no ACTIVE payment account cannot receive a Pix contribution, so it has no public
# page: the policies below only let the definer see the schools and settings of such schools.
_ACTIVE_ACCOUNT = (
    "EXISTS (SELECT 1 FROM public.payment_accounts a "
    "WHERE a.school_id = {table}.{column} AND a.status = 'ACTIVE')"
)

POLICIES = (
    ("schools", "schools_definer_select_public"),
    ("school_settings", "school_settings_definer_select"),
    ("payment_accounts", "payment_accounts_definer_select"),
    ("contributions", "contributions_definer_select"),
)


def _immutable_trigger(columns: tuple[str, ...]) -> str:
    args = ", ".join(f"'{column}'" for column in columns)
    return (
        "CREATE TRIGGER contributions_05_immutable BEFORE UPDATE ON public.contributions "
        f"FOR EACH ROW EXECUTE FUNCTION public.assert_immutable_columns({args})"
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE apm_owner")

    # --- the idempotency key of the public POST
    op.execute("ALTER TABLE contributions ADD COLUMN idempotency_key uuid")
    op.execute(
        "ALTER TABLE contributions ADD CONSTRAINT uq_contributions_school_id_idempotency_key "
        "UNIQUE (school_id, idempotency_key)"
    )
    op.execute("DROP TRIGGER contributions_05_immutable ON public.contributions")
    op.execute(_immutable_trigger((*CONTRIBUTION_IMMUTABLE[:-1], "idempotency_key", "created_at")))
    op.execute(
        "GRANT SELECT (idempotency_key), INSERT (idempotency_key) ON contributions TO apm_app"
    )

    # --- only the platform names the secret of a payment account
    op.execute("REVOKE INSERT (secret_ref), UPDATE (secret_ref) ON payment_accounts FROM apm_app")

    # --- what apm_definer may read for the three functions: columns and policies
    op.execute(
        "GRANT SELECT (school_id, organization_id, suggested_amounts_cents, allow_custom_amount, "
        "min_contribution_cents, max_contribution_cents, required_fields, optional_fields, "
        "brand_accent, brand_accent_contrast) ON school_settings TO apm_definer"
    )
    op.execute(
        "GRANT SELECT (id, organization_id, school_id, provider, status, webhook_secret_hash) "
        "ON payment_accounts TO apm_definer"
    )
    op.execute(
        "GRANT SELECT (transaction_id, organization_id, school_id, receipt_token_hash, "
        "receipt_expires_at) ON contributions TO apm_definer"
    )
    op.execute(
        "CREATE POLICY schools_definer_select_public ON schools FOR SELECT TO apm_definer "
        f"USING ({_ACTIVE_ACCOUNT.format(table='schools', column='id')})"
    )
    op.execute(
        "CREATE POLICY school_settings_definer_select ON school_settings FOR SELECT TO apm_definer "
        f"USING ({_ACTIVE_ACCOUNT.format(table='school_settings', column='school_id')})"
    )
    op.execute(
        "CREATE POLICY payment_accounts_definer_select ON payment_accounts FOR SELECT "
        "TO apm_definer USING (status = 'ACTIVE')"
    )
    op.execute(
        "CREATE POLICY contributions_definer_select ON contributions FOR SELECT TO apm_definer "
        "USING (receipt_token_hash IS NOT NULL "
        "AND (receipt_expires_at IS NULL OR receipt_expires_at > now()))"
    )
    op.execute("RESET ROLE")

    # --- the functions, created by apm_definer so that it owns them
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO apm_definer")
    op.execute("SET LOCAL ROLE apm_definer")
    for ddl, signature in FUNCTIONS:
        op.execute(ddl)
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO apm_app")
    op.execute("RESET ROLE")
    op.execute("REVOKE CREATE ON SCHEMA public FROM apm_definer")


def downgrade() -> None:
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO apm_definer")
    op.execute("SET LOCAL ROLE apm_definer")
    for _ddl, signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    op.execute("RESET ROLE")
    op.execute("REVOKE CREATE ON SCHEMA public FROM apm_definer")

    op.execute("SET LOCAL ROLE apm_owner")
    for table, policy in reversed(POLICIES):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute("REVOKE ALL ON school_settings FROM apm_definer")
    op.execute("REVOKE ALL ON payment_accounts FROM apm_definer")
    op.execute("REVOKE ALL ON contributions FROM apm_definer")
    op.execute("GRANT INSERT (secret_ref), UPDATE (secret_ref) ON payment_accounts TO apm_app")
    op.execute("DROP TRIGGER contributions_05_immutable ON public.contributions")
    op.execute(_immutable_trigger(CONTRIBUTION_IMMUTABLE))
    op.execute(
        "ALTER TABLE contributions DROP CONSTRAINT IF EXISTS uq_contributions_school_id_idempotency_key"
    )
    op.execute("ALTER TABLE contributions DROP COLUMN IF EXISTS idempotency_key")
    op.execute("RESET ROLE")
