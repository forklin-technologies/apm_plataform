"""financial schema: ledger, details, support tables, audit, closings, statement functions

Revision ID: 0007_financial_schema
Revises: 0006_auth_sessions
Create Date: 2026-10-02

ADR-015 (and its revision). Everything is created as apm_owner, with ENABLE + FORCE row level
security and the scope predicate of ADR-014. docs/financial-model.md explains the model, the
invariants and the trigger inventory.

Rules this migration follows (the lesson of M1, ADR-014):
  * No single-column foreign key to a tenant table: every reference to a financial row, a category
    or a detail carries organization_id and school_id, so a row can only point inside its school and
    pointing at another school fails with the same error as a missing id (no existence oracle).
    The only single-column foreign keys are *_user_id to users; each is guarded by a trigger that
    fails first, with a uniform error, unless the user holds an ACTIVE membership visible here.
  * INSERT and UPDATE are granted per column; there is no DELETE anywhere (no grant, no policy, and
    a trigger that refuses it for every role); audit_logs only grows.
  * No SECURITY DEFINER: every function is SECURITY INVOKER with a fixed search_path.
"""

# ruff: noqa: E501  (SQL text: the statements are read as SQL, not wrapped like Python)

from collections.abc import Sequence

from alembic import op

revision: str = "0007_financial_schema"
down_revision: str | None = "0006_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEX64 = "^[0-9a-f]{64}$"
TIMESTAMPS = """
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
"""

# Advisory lock namespaces (first key of pg_advisory_xact_lock(int, int)); the second key is the
# hash of the school id. Separate namespaces so a settlement and a new reference code never wait
# for each other.
LOCK_REFERENCE = 7001
LOCK_PERIOD = 7002
LOCK_PIX_REFERENCE = 7003


def _scope(org_column: str, school_column: str) -> str:
    return (
        f"({org_column} = (SELECT public.app_org()) AND "
        f"((SELECT public.app_school()) IS NULL OR {school_column} = (SELECT public.app_school())))"
    )


def _tenant_fks(table: str, *, school_nullable: bool = False) -> str:
    return f"""
        CONSTRAINT fk_{table}_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
        CONSTRAINT fk_{table}_school_id_organization_id_schools
            FOREIGN KEY (school_id, organization_id)
            REFERENCES schools (id, organization_id) ON DELETE RESTRICT"""


def _detail_fk(table: str, kind: str) -> str:
    return f"""
        CONSTRAINT fk_{table}_transaction_id_financial_transactions
            FOREIGN KEY (transaction_id, organization_id, school_id, kind)
            REFERENCES financial_transactions (id, organization_id, school_id, kind)
            ON DELETE RESTRICT,
        CONSTRAINT ck_{table}_kind CHECK (kind = '{kind}')"""


# --------------------------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------------------------

IDENTIFICATION_FIELDS = (
    "ARRAY['guardian_name', 'contributor_email', 'contributor_phone', 'student_name', "
    "'class_name']::text[]"
)
ORIGIN_TYPES = "'GUARDIAN', 'TEACHER', 'DIRECTOR', 'EMPLOYEE', 'MANAGEMENT', 'APM', 'OTHER'"


def _create_support_tables() -> None:
    op.execute(
        f"""
        CREATE TABLE categories (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            key text NOT NULL,
            name text NOT NULL,
            applies_to text NOT NULL,
            -- Where the category goes in the monthly report (section 12 of the product document).
            report_group text NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            {TIMESTAMPS},
            CONSTRAINT pk_categories PRIMARY KEY (id),
            {_tenant_fks("categories")},
            CONSTRAINT uq_categories_school_id_key UNIQUE (school_id, key),
            -- Target of financial_transactions (category_id, direction, school_id, organization_id).
            CONSTRAINT uq_categories_id_applies_to_school_id_organization_id
                UNIQUE (id, applies_to, school_id, organization_id),
            CONSTRAINT ck_categories_key_format CHECK (key ~ '^[a-z0-9_]{{1,40}}$'),
            CONSTRAINT ck_categories_name_length CHECK (length(btrim(name)) BETWEEN 1 AND 80),
            CONSTRAINT ck_categories_applies_to_valid CHECK (applies_to IN ('IN', 'OUT')),
            -- The only valid groups, per direction (a report_group CHECK of its own would be implied).
            CONSTRAINT ck_categories_group_matches_direction CHECK (
                (applies_to = 'IN' AND report_group IN ('CONTRIBUTIONS', 'OTHER_INCOME', 'REFUNDS'))
                OR (applies_to = 'OUT' AND report_group = 'EXPENSES_REIMBURSEMENTS'))
        )
        """
    )
    op.execute("CREATE INDEX ix_categories_school_id ON categories (school_id)")
    op.execute("CREATE INDEX ix_categories_organization_id ON categories (organization_id)")

    op.execute(
        f"""
        CREATE TABLE school_settings (
            school_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            timezone text NOT NULL DEFAULT 'America/Sao_Paulo',
            min_contribution_cents bigint NOT NULL DEFAULT 1000,
            max_contribution_cents bigint NOT NULL DEFAULT 500000,
            suggested_amounts_cents bigint[] NOT NULL DEFAULT '{{2000,3000,4000}}',
            allow_custom_amount boolean NOT NULL DEFAULT true,
            pix_expiration_minutes integer NOT NULL DEFAULT 30,
            -- Which identification fields the public form asks, as two lists (anonymous = both empty).
            required_fields text[] NOT NULL DEFAULT '{{}}',
            optional_fields text[] NOT NULL DEFAULT '{{guardian_name,contributor_email,contributor_phone}}',
            brand_accent text NOT NULL DEFAULT '#0a84ff',
            brand_accent_contrast text NOT NULL DEFAULT '#ffffff',
            approval_limit_cents bigint,
            {TIMESTAMPS},
            CONSTRAINT pk_school_settings PRIMARY KEY (school_id),
            {_tenant_fks("school_settings")},
            -- An unknown zone makes now() AT TIME ZONE fail, which refuses the row.
            CONSTRAINT ck_school_settings_timezone_valid
                CHECK ((now() AT TIME ZONE timezone) IS NOT NULL),
            CONSTRAINT ck_school_settings_contribution_range
                CHECK (min_contribution_cents > 0 AND min_contribution_cents <= max_contribution_cents
                       AND max_contribution_cents <= 1000000000),
            -- One to six suggested amounts, every one inside the minimum and the maximum.
            CONSTRAINT ck_school_settings_suggested_amounts_valid CHECK (
                cardinality(suggested_amounts_cents) BETWEEN 1 AND 6
                AND array_position(suggested_amounts_cents, NULL) IS NULL
                AND min_contribution_cents <= ALL (suggested_amounts_cents)
                AND max_contribution_cents >= ALL (suggested_amounts_cents)),
            CONSTRAINT ck_school_settings_pix_expiration_range
                CHECK (pix_expiration_minutes BETWEEN 5 AND 1440),
            CONSTRAINT ck_school_settings_identification_fields_valid CHECK (
                required_fields <@ {IDENTIFICATION_FIELDS}
                AND optional_fields <@ {IDENTIFICATION_FIELDS}
                AND NOT (required_fields && optional_fields)),
            CONSTRAINT ck_school_settings_brand_accent_format
                CHECK (brand_accent ~ '^#[0-9a-fA-F]{{6}}$'),
            CONSTRAINT ck_school_settings_brand_accent_contrast_format
                CHECK (brand_accent_contrast ~ '^#[0-9a-fA-F]{{6}}$'),
            CONSTRAINT ck_school_settings_approval_limit_positive
                CHECK (approval_limit_cents IS NULL OR approval_limit_cents > 0),
            -- Target of nothing today; keeps (school_id, organization_id) usable by future FKs.
            CONSTRAINT uq_school_settings_school_id_organization_id UNIQUE (school_id, organization_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_school_settings_organization_id ON school_settings (organization_id)"
    )


def _create_ledger() -> None:
    op.execute(
        f"""
        CREATE TABLE financial_transactions (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            kind text NOT NULL,
            direction text NOT NULL,
            -- The amount REQUESTED (an expense) or expected (a contribution). It is editable only
            -- in the states ft_07_change allows; the approved and the reimbursed amounts live in
            -- the detail rows.
            amount_cents bigint NOT NULL,
            status text NOT NULL,
            -- The PURPOSE of the movement (every movement has one).
            category_id uuid NOT NULL,
            -- The ORIGIN: who the money comes from or who spent it. origin_name is for people with
            -- no account; the name of a contributor is NOT here (the ledger freezes when settled,
            -- and personal data must stay erasable): it lives in contributions.guardian_name.
            origin_type text NOT NULL,
            origin_name text,
            origin_user_id uuid,
            -- When it happened (the real date of the fact).
            occurred_at timestamptz NOT NULL DEFAULT now(),
            -- When it was BOOKED in the cash ledger (NULL while pending). A settlement dated in a
            -- closed month is booked now and flagged late_adjustment (ADR-015, trigger ft_30_settle).
            settled_at timestamptz,
            late_adjustment boolean NOT NULL DEFAULT false,
            parent_transaction_id uuid,
            parent_kind text,
            created_by_user_id uuid,
            reference_code bigint NOT NULL,
            {TIMESTAMPS},
            CONSTRAINT pk_financial_transactions PRIMARY KEY (id),
            {_tenant_fks("financial_transactions")},
            CONSTRAINT fk_financial_transactions_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_financial_transactions_origin_user_id_users
                FOREIGN KEY (origin_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            -- Target of the detail tables and of the parent link: same school, declared kind.
            CONSTRAINT uq_financial_transactions_id_organization_id_school_id_kind
                UNIQUE (id, organization_id, school_id, kind),
            CONSTRAINT uq_financial_transactions_school_id_reference_code
                UNIQUE (school_id, reference_code),
            CONSTRAINT fk_financial_transactions_parent
                FOREIGN KEY (parent_transaction_id, organization_id, school_id, parent_kind)
                REFERENCES financial_transactions (id, organization_id, school_id, kind)
                ON DELETE RESTRICT,
            CONSTRAINT fk_financial_transactions_category
                FOREIGN KEY (category_id, direction, school_id, organization_id)
                REFERENCES categories (id, applies_to, school_id, organization_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_financial_transactions_kind_valid
                CHECK (kind IN ('CONTRIBUTION', 'EXPENSE', 'REIMBURSEMENT', 'REFUND')),
            CONSTRAINT ck_financial_transactions_direction_valid
                CHECK (direction IN ('IN', 'OUT')),
            CONSTRAINT ck_financial_transactions_amount_range
                CHECK (amount_cents > 0 AND amount_cents <= 1000000000000),
            CONSTRAINT ck_financial_transactions_status_for_kind CHECK (
                (kind = 'CONTRIBUTION'
                    AND status IN ('PENDING_PAYMENT', 'PAID', 'EXPIRED', 'CANCELLED', 'REVIEW_REQUIRED'))
                OR (kind = 'EXPENSE'
                    AND status IN ('DRAFT', 'SUBMITTED', 'CORRECTION_REQUESTED', 'APPROVED',
                                   'REJECTED', 'PAID', 'CANCELLED'))
                OR (kind = 'REIMBURSEMENT' AND status IN ('PENDING', 'PAID', 'CANCELLED'))
                OR (kind = 'REFUND'
                    AND status IN ('REQUESTED', 'AWAITING_CONFIRMATION', 'CONFIRMED', 'REJECTED'))),
            -- Direction follows from the kind. A REFUND is money coming BACK to the APM (always IN);
            -- its parent is optional (an expense, a reimbursement, or none for a wrong payment).
            CONSTRAINT ck_financial_transactions_shape CHECK (
                (kind = 'CONTRIBUTION' AND direction = 'IN'
                    AND parent_transaction_id IS NULL AND parent_kind IS NULL)
                OR (kind = 'EXPENSE' AND direction = 'OUT'
                    AND parent_transaction_id IS NULL AND parent_kind IS NULL)
                OR (kind = 'REIMBURSEMENT' AND direction = 'OUT'
                    AND parent_transaction_id IS NOT NULL AND parent_kind = 'EXPENSE')
                OR (kind = 'REFUND' AND direction = 'IN'
                    AND ((parent_transaction_id IS NULL AND parent_kind IS NULL)
                         OR (parent_transaction_id IS NOT NULL
                             AND parent_kind IN ('EXPENSE', 'REIMBURSEMENT'))))),
            CONSTRAINT ck_financial_transactions_not_own_parent
                CHECK (parent_transaction_id IS NULL OR parent_transaction_id <> id),
            -- Only PAID (and, for a refund, CONFIRMED) is settled, and settled means PAID or CONFIRMED.
            CONSTRAINT ck_financial_transactions_paid_iff_settled
                CHECK ((status IN ('PAID', 'CONFIRMED')) = (settled_at IS NOT NULL)),
            CONSTRAINT ck_financial_transactions_late_needs_settled
                CHECK (NOT late_adjustment OR settled_at IS NOT NULL),
            -- Only a public Pix contribution (ADR-010) has no author.
            CONSTRAINT ck_financial_transactions_author_required
                CHECK (kind = 'CONTRIBUTION' OR created_by_user_id IS NOT NULL),
            CONSTRAINT ck_financial_transactions_origin_type_valid
                CHECK (origin_type IN ({ORIGIN_TYPES})),
            CONSTRAINT ck_financial_transactions_origin_name_length
                CHECK (origin_name IS NULL OR length(btrim(origin_name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_financial_transactions_no_contributor_name
                CHECK (kind <> 'CONTRIBUTION' OR origin_name IS NULL),
            CONSTRAINT ck_financial_transactions_reference_positive CHECK (reference_code > 0)
        )
        """
    )
    for statement in (
        "CREATE INDEX ix_financial_transactions_school_settled "
        "ON financial_transactions (school_id, settled_at, reference_code) "
        "WHERE settled_at IS NOT NULL",
        "CREATE INDEX ix_financial_transactions_school_status "
        "ON financial_transactions (school_id, status)",
        "CREATE INDEX ix_financial_transactions_school_occurred "
        "ON financial_transactions (school_id, occurred_at)",
        "CREATE INDEX ix_financial_transactions_parent "
        "ON financial_transactions (parent_transaction_id) WHERE parent_transaction_id IS NOT NULL",
        "CREATE INDEX ix_financial_transactions_category ON financial_transactions (category_id)",
        "CREATE INDEX ix_financial_transactions_origin_user "
        "ON financial_transactions (origin_user_id) WHERE origin_user_id IS NOT NULL",
        "CREATE INDEX ix_financial_transactions_organization_id "
        "ON financial_transactions (organization_id)",
        "CREATE UNIQUE INDEX uq_financial_transactions_one_active_reimbursement "
        "ON financial_transactions (school_id, parent_transaction_id) "
        "WHERE kind = 'REIMBURSEMENT' AND status IN ('PENDING', 'PAID')",
    ):
        op.execute(statement)


def _create_details() -> None:
    # The primary key of a detail is (transaction_id, organization_id, school_id), not the
    # transaction id alone. The composite foreign key to the ledger already ties the three
    # together, so there is still one detail per transaction; but a primary key on the id alone
    # would answer "this id has a detail" (duplicate key) BEFORE the foreign key refuses an id of
    # another tenant, which is the existence oracle of M1. The same rule holds for every unique
    # index the application can feed: it carries the school (tests/financial/test_references.py).
    op.execute(
        f"""
        CREATE TABLE contributions (
            transaction_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            kind text NOT NULL DEFAULT 'CONTRIBUTION',
            -- PIX through the provider's charge; PIX_DIRECT is a Pix paid straight to the key of the
            -- APM, outside the platform's charge, seen on the bank statement and registered or
            -- reconciled by the management; CASH, TRANSFER and OTHER are manual entries of the
            -- treasury (donations, other income and revenue of the APM are contributions with their
            -- own category).
            method text NOT NULL,
            -- The end-to-end id of a PIX_DIRECT Pix (and only of it): what makes the same Pix
            -- impossible to register twice. It is also kept distinct from every pix_charges
            -- end_to_end_id of the school by the triggers contributions_20_insert and
            -- pix_charges_20_end_to_end_id (a Pix is never two contributions).
            external_reference text,
            -- Identification of the contributor: only what the school enables, never public, and it
            -- can only be erased (non-null to null), never rewritten.
            guardian_name text,
            student_name text,
            class_name text,
            contributor_email text,
            contributor_phone text,
            receipt_token_hash text,
            receipt_expires_at timestamptz,
            -- Why the management settled a REVIEW_REQUIRED contribution (written once).
            review_decision_reason text,
            {TIMESTAMPS},
            CONSTRAINT pk_contributions PRIMARY KEY (transaction_id, organization_id, school_id),
            {_tenant_fks("contributions")},
            {_detail_fk("contributions", "CONTRIBUTION")},
            -- Global on purpose: the token is a 128-bit secret stored only as a hash, so a clash
            -- reveals nothing, and resolve_receipt (ADR-016) looks it up without a tenant.
            CONSTRAINT uq_contributions_receipt_token_hash UNIQUE (receipt_token_hash),
            -- Per school, never global (the M1 lesson: a global UNIQUE would answer for other tenants).
            CONSTRAINT uq_contributions_school_id_external_reference
                UNIQUE (school_id, external_reference),
            CONSTRAINT ck_contributions_method_valid
                CHECK (method IN ('PIX', 'PIX_DIRECT', 'CASH', 'TRANSFER', 'OTHER')),
            CONSTRAINT ck_contributions_external_reference_format
                CHECK (external_reference IS NULL OR external_reference ~ '^E[A-Za-z0-9]{{31}}$'),
            CONSTRAINT ck_contributions_external_reference_iff_pix_direct
                CHECK ((method = 'PIX_DIRECT') = (external_reference IS NOT NULL)),
            CONSTRAINT ck_contributions_guardian_name_length
                CHECK (guardian_name IS NULL OR length(btrim(guardian_name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_contributions_student_name_length
                CHECK (student_name IS NULL OR length(btrim(student_name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_contributions_class_name_length
                CHECK (class_name IS NULL OR length(btrim(class_name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_contributions_contributor_email_format
                CHECK (contributor_email IS NULL
                       OR (length(contributor_email) <= 254 AND contributor_email ~ '^[^@\\s]+@[^@\\s]+$')),
            CONSTRAINT ck_contributions_contributor_phone_length
                CHECK (contributor_phone IS NULL OR length(btrim(contributor_phone)) BETWEEN 1 AND 30),
            CONSTRAINT ck_contributions_receipt_token_hash_format
                CHECK (receipt_token_hash IS NULL OR receipt_token_hash ~ '{HEX64}'),
            CONSTRAINT ck_contributions_pix_has_receipt
                CHECK (method <> 'PIX'
                       OR (receipt_token_hash IS NOT NULL AND receipt_expires_at IS NOT NULL)),
            CONSTRAINT ck_contributions_review_decision_reason_length
                CHECK (review_decision_reason IS NULL
                       OR length(btrim(review_decision_reason)) BETWEEN 3 AND 500)
        )
        """
    )
    op.execute("CREATE INDEX ix_contributions_school_id ON contributions (school_id)")

    op.execute(
        f"""
        CREATE TABLE expenses (
            transaction_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            kind text NOT NULL DEFAULT 'EXPENSE',
            description text NOT NULL,
            vendor text,
            -- Why it was bought, and how the collaborator (or the APM) paid.
            purchase_reason text,
            payment_method text,
            paid_by text NOT NULL,
            submitted_by_user_id uuid NOT NULL,
            -- Who decided (approved or rejected). approved_at is set by the database.
            approved_by_user_id uuid,
            approved_at timestamptz,
            -- The amount approved (the one that is reimbursed): at most the amount requested, and
            -- lower than it only when a collaborator paid (partial approval).
            approved_amount_cents bigint,
            decision_reason text,
            -- What the management asked to be corrected (rewritable; the history is in the audit log).
            correction_reason text,
            {TIMESTAMPS},
            CONSTRAINT pk_expenses PRIMARY KEY (transaction_id, organization_id, school_id),
            {_tenant_fks("expenses")},
            {_detail_fk("expenses", "EXPENSE")},
            CONSTRAINT fk_expenses_submitted_by_user_id_users
                FOREIGN KEY (submitted_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_expenses_approved_by_user_id_users
                FOREIGN KEY (approved_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_expenses_description_length
                CHECK (length(btrim(description)) BETWEEN 1 AND 500),
            CONSTRAINT ck_expenses_vendor_length
                CHECK (vendor IS NULL OR length(btrim(vendor)) BETWEEN 1 AND 200),
            CONSTRAINT ck_expenses_purchase_reason_length
                CHECK (purchase_reason IS NULL OR length(btrim(purchase_reason)) BETWEEN 3 AND 500),
            CONSTRAINT ck_expenses_payment_method_valid
                CHECK (payment_method IS NULL OR payment_method IN ('PIX', 'CARD', 'CASH', 'OTHER')),
            CONSTRAINT ck_expenses_paid_by_valid CHECK (paid_by IN ('APM', 'COLLABORATOR')),
            -- Separation of duties, in the database: nobody decides their own expense.
            CONSTRAINT ck_expenses_decider_is_not_submitter
                CHECK (approved_by_user_id IS NULL OR approved_by_user_id <> submitted_by_user_id),
            CONSTRAINT ck_expenses_decision_complete
                CHECK ((approved_by_user_id IS NULL) = (approved_at IS NULL)),
            CONSTRAINT ck_expenses_approved_amount_positive
                CHECK (approved_amount_cents IS NULL
                       OR (approved_amount_cents > 0 AND approved_amount_cents <= 1000000000000)),
            CONSTRAINT ck_expenses_decision_reason_length
                CHECK (decision_reason IS NULL OR length(btrim(decision_reason)) BETWEEN 1 AND 500),
            CONSTRAINT ck_expenses_correction_reason_length
                CHECK (correction_reason IS NULL OR length(btrim(correction_reason)) BETWEEN 3 AND 500)
        )
        """
    )
    op.execute("CREATE INDEX ix_expenses_school_id ON expenses (school_id)")
    op.execute("CREATE INDEX ix_expenses_submitted_by_user_id ON expenses (submitted_by_user_id)")

    op.execute(
        f"""
        CREATE TABLE reimbursements (
            transaction_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            kind text NOT NULL DEFAULT 'REIMBURSEMENT',
            beneficiary_user_id uuid NOT NULL,
            -- Who paid it (the treasury pays through the bank and records it) and the bank reference.
            paid_by_user_id uuid,
            payment_reference text,
            {TIMESTAMPS},
            CONSTRAINT pk_reimbursements PRIMARY KEY (transaction_id, organization_id, school_id),
            {_tenant_fks("reimbursements")},
            {_detail_fk("reimbursements", "REIMBURSEMENT")},
            CONSTRAINT fk_reimbursements_beneficiary_user_id_users
                FOREIGN KEY (beneficiary_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_reimbursements_paid_by_user_id_users
                FOREIGN KEY (paid_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_reimbursements_payment_reference_length
                CHECK (payment_reference IS NULL OR length(btrim(payment_reference)) BETWEEN 1 AND 200)
        )
        """
    )
    op.execute("CREATE INDEX ix_reimbursements_school_id ON reimbursements (school_id)")
    op.execute(
        "CREATE INDEX ix_reimbursements_beneficiary_user_id ON reimbursements (beneficiary_user_id)"
    )

    op.execute(
        f"""
        CREATE TABLE refunds (
            transaction_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            kind text NOT NULL DEFAULT 'REFUND',
            reason text NOT NULL,
            payment_reference text,
            -- Who confirmed that the money came back (required when CONFIRMED).
            confirmed_by_user_id uuid,
            {TIMESTAMPS},
            CONSTRAINT pk_refunds PRIMARY KEY (transaction_id, organization_id, school_id),
            {_tenant_fks("refunds")},
            {_detail_fk("refunds", "REFUND")},
            CONSTRAINT fk_refunds_confirmed_by_user_id_users
                FOREIGN KEY (confirmed_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_refunds_reason_length CHECK (length(btrim(reason)) BETWEEN 3 AND 500),
            CONSTRAINT ck_refunds_payment_reference_length
                CHECK (payment_reference IS NULL OR length(btrim(payment_reference)) BETWEEN 1 AND 200)
        )
        """
    )
    op.execute("CREATE INDEX ix_refunds_school_id ON refunds (school_id)")

    op.execute(
        f"""
        CREATE TABLE expense_attachments (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            transaction_id uuid NOT NULL,
            -- INVOICE (nota fiscal), PAYMENT_PROOF (comprovante do pagamento) or OTHER.
            kind text NOT NULL,
            storage_key text NOT NULL,
            file_name text NOT NULL,
            content_type text NOT NULL,
            size_bytes bigint NOT NULL,
            sha256 text NOT NULL,
            uploaded_by_user_id uuid NOT NULL,
            {TIMESTAMPS},
            CONSTRAINT pk_expense_attachments PRIMARY KEY (id),
            {_tenant_fks("expense_attachments")},
            CONSTRAINT fk_expense_attachments_transaction_id_expenses
                FOREIGN KEY (transaction_id, organization_id, school_id)
                REFERENCES expenses (transaction_id, organization_id, school_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_expense_attachments_uploaded_by_user_id_users
                FOREIGN KEY (uploaded_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT uq_expense_attachments_school_id_transaction_id_sha256
                UNIQUE (school_id, transaction_id, sha256),
            CONSTRAINT ck_expense_attachments_kind_valid
                CHECK (kind IN ('INVOICE', 'PAYMENT_PROOF', 'OTHER')),
            CONSTRAINT ck_expense_attachments_storage_key_length
                CHECK (length(storage_key) BETWEEN 1 AND 500 AND storage_key !~ '^/'),
            CONSTRAINT ck_expense_attachments_file_name_length
                CHECK (length(btrim(file_name)) BETWEEN 1 AND 255),
            CONSTRAINT ck_expense_attachments_content_type_format
                CHECK (content_type ~ '^[a-z0-9.+-]+/[a-z0-9.+-]+$'),
            CONSTRAINT ck_expense_attachments_size_range
                CHECK (size_bytes BETWEEN 1 AND 20971520),
            CONSTRAINT ck_expense_attachments_sha256_format CHECK (sha256 ~ '{HEX64}')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_expense_attachments_transaction_id ON expense_attachments (transaction_id)"
    )
    op.execute("CREATE INDEX ix_expense_attachments_school_id ON expense_attachments (school_id)")


def _create_pix_and_webhooks() -> None:
    # One receiving account per school (ACTIVE). NO credential, certificate or key is ever stored
    # here: secret_ref is only a REFERENCE to a secrets manager ("env:NAME", "file:path", "vault:path").
    # webhook_secret_hash is the SHA-256 of the 128-bit secret of the webhook (a hash, like a session
    # token, not a credential): the application role cannot SELECT it; only the narrow function of
    # ADR-016 (resolve_webhook_target, TASK-006) will read it.
    op.execute(
        f"""
        CREATE TABLE payment_accounts (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            provider text NOT NULL,
            external_account_id text NOT NULL,
            status text NOT NULL DEFAULT 'PENDING',
            secret_ref text NOT NULL,
            webhook_secret_hash text,
            {TIMESTAMPS},
            CONSTRAINT pk_payment_accounts PRIMARY KEY (id),
            {_tenant_fks("payment_accounts")},
            -- Target of pix_charges (payment_account_id, organization_id, school_id).
            CONSTRAINT uq_payment_accounts_id_organization_id_school_id
                UNIQUE (id, organization_id, school_id),
            CONSTRAINT uq_payment_accounts_school_id_provider_external_account_id
                UNIQUE (school_id, provider, external_account_id),
            -- Global for the lookup without a tenant; the hash of a 128-bit secret reveals nothing.
            CONSTRAINT uq_payment_accounts_webhook_secret_hash UNIQUE (webhook_secret_hash),
            CONSTRAINT ck_payment_accounts_provider_valid CHECK (provider IN ('BB', 'SANDBOX')),
            CONSTRAINT ck_payment_accounts_external_account_id_length
                CHECK (length(btrim(external_account_id)) BETWEEN 1 AND 100),
            CONSTRAINT ck_payment_accounts_status_valid
                CHECK (status IN ('ACTIVE', 'INACTIVE', 'PENDING')),
            -- scheme:path, so that a raw secret does not fit.
            CONSTRAINT ck_payment_accounts_secret_ref_format
                CHECK (secret_ref ~ '^[a-z][a-z0-9_]{{1,19}}:[A-Za-z0-9_./-]{{1,200}}$'),
            CONSTRAINT ck_payment_accounts_webhook_secret_hash_format
                CHECK (webhook_secret_hash IS NULL OR webhook_secret_hash ~ '{HEX64}')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_payment_accounts_one_active_per_school "
        "ON payment_accounts (school_id) WHERE status = 'ACTIVE'"
    )
    op.execute("CREATE INDEX ix_payment_accounts_school_id ON payment_accounts (school_id)")

    op.execute(
        f"""
        CREATE TABLE pix_charges (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            transaction_id uuid NOT NULL,
            payment_account_id uuid NOT NULL,
            provider text NOT NULL,
            txid text NOT NULL,
            status text NOT NULL DEFAULT 'PENDING',
            -- What was charged, and what the provider says it received (they differ only in REVIEW_REQUIRED).
            amount_cents bigint NOT NULL,
            received_amount_cents bigint,
            divergence_reason text,
            expires_at timestamptz NOT NULL,
            emv_payload text,
            end_to_end_id text,
            paid_at timestamptz,
            {TIMESTAMPS},
            CONSTRAINT pk_pix_charges PRIMARY KEY (id),
            {_tenant_fks("pix_charges")},
            CONSTRAINT fk_pix_charges_transaction_id_contributions
                FOREIGN KEY (transaction_id, organization_id, school_id)
                REFERENCES contributions (transaction_id, organization_id, school_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_pix_charges_payment_account_id_payment_accounts
                FOREIGN KEY (payment_account_id, organization_id, school_id)
                REFERENCES payment_accounts (id, organization_id, school_id)
                ON DELETE RESTRICT,
            -- Per school, never global: a global UNIQUE would be an oracle across tenants, and each
            -- APM has its own bank account (ADR-011).
            CONSTRAINT uq_pix_charges_school_id_provider_txid UNIQUE (school_id, provider, txid),
            CONSTRAINT ck_pix_charges_provider_valid CHECK (provider IN ('BB', 'SANDBOX')),
            CONSTRAINT ck_pix_charges_txid_format CHECK (txid ~ '^[A-Za-z0-9]{{26,35}}$'),
            CONSTRAINT ck_pix_charges_status_valid
                CHECK (status IN ('PENDING', 'PAID', 'REVIEW_REQUIRED', 'EXPIRED', 'CANCELLED')),
            CONSTRAINT ck_pix_charges_amount_range
                CHECK (amount_cents > 0 AND amount_cents <= 1000000000000),
            CONSTRAINT ck_pix_charges_received_amount_range
                CHECK (received_amount_cents IS NULL
                       OR (received_amount_cents > 0 AND received_amount_cents <= 1000000000000)),
            CONSTRAINT ck_pix_charges_expires_after_creation CHECK (expires_at > created_at),
            CONSTRAINT ck_pix_charges_end_to_end_id_format
                CHECK (end_to_end_id IS NULL OR end_to_end_id ~ '^E[A-Za-z0-9]{{31}}$'),
            -- Money was received (PAID, or REVIEW_REQUIRED when the amount differs): the provider's
            -- identifiers and the amount received are all there, and only then.
            CONSTRAINT ck_pix_charges_paid_iff_confirmed CHECK (
                (status IN ('PAID', 'REVIEW_REQUIRED'))
                = (end_to_end_id IS NOT NULL AND paid_at IS NOT NULL AND received_amount_cents IS NOT NULL)),
            CONSTRAINT ck_pix_charges_received_matches_status CHECK (
                (status <> 'PAID' OR received_amount_cents = amount_cents)
                AND (status <> 'REVIEW_REQUIRED'
                     OR (received_amount_cents <> amount_cents
                         AND length(btrim(coalesce(divergence_reason, ''))) >= 3))),
            CONSTRAINT ck_pix_charges_divergence_reason_length
                CHECK (divergence_reason IS NULL OR length(btrim(divergence_reason)) BETWEEN 3 AND 500),
            CONSTRAINT ck_pix_charges_emv_payload_length
                CHECK (emv_payload IS NULL OR length(emv_payload) BETWEEN 1 AND 4096)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_pix_charges_school_id_provider_end_to_end_id "
        "ON pix_charges (school_id, provider, end_to_end_id) WHERE end_to_end_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_pix_charges_one_pending_per_contribution "
        "ON pix_charges (school_id, transaction_id) WHERE status = 'PENDING'"
    )
    op.execute("CREATE INDEX ix_pix_charges_transaction_id ON pix_charges (transaction_id)")
    op.execute("CREATE INDEX ix_pix_charges_payment_account_id ON pix_charges (payment_account_id)")
    op.execute("CREATE INDEX ix_pix_charges_school_id ON pix_charges (school_id)")
    op.execute(
        "CREATE INDEX ix_pix_charges_pending_expiry ON pix_charges (expires_at) "
        "WHERE status = 'PENDING'"
    )

    op.execute(
        """
        CREATE TABLE webhook_events (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            provider text NOT NULL,
            -- The provider's event id, or the end-to-end id when it sends none: what makes the
            -- webhook idempotent (a repeated delivery hits the UNIQUE and is dropped).
            idempotency_key text NOT NULL,
            end_to_end_id text,
            raw_payload jsonb NOT NULL,
            signature_valid boolean NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            processed_at timestamptz,
            attempts integer NOT NULL DEFAULT 0,
            processing_error text,
            CONSTRAINT pk_webhook_events PRIMARY KEY (id),
            CONSTRAINT fk_webhook_events_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
            CONSTRAINT fk_webhook_events_school_id_organization_id_schools
                FOREIGN KEY (school_id, organization_id)
                REFERENCES schools (id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT uq_webhook_events_school_id_provider_idempotency_key
                UNIQUE (school_id, provider, idempotency_key),
            CONSTRAINT ck_webhook_events_provider_valid CHECK (provider IN ('BB', 'SANDBOX')),
            CONSTRAINT ck_webhook_events_idempotency_key_length
                CHECK (length(idempotency_key) BETWEEN 1 AND 200),
            CONSTRAINT ck_webhook_events_end_to_end_id_format
                CHECK (end_to_end_id IS NULL OR end_to_end_id ~ '^E[A-Za-z0-9]{31}$'),
            CONSTRAINT ck_webhook_events_raw_payload_object
                CHECK (jsonb_typeof(raw_payload) = 'object' AND pg_column_size(raw_payload) < 65536),
            CONSTRAINT ck_webhook_events_attempts_non_negative CHECK (attempts >= 0),
            CONSTRAINT ck_webhook_events_processing_error_length
                CHECK (processing_error IS NULL OR length(processing_error) <= 1000)
        )
        """
    )
    op.execute("CREATE INDEX ix_webhook_events_school_id ON webhook_events (school_id)")
    op.execute(
        "CREATE INDEX ix_webhook_events_unprocessed ON webhook_events (received_at) "
        "WHERE processed_at IS NULL"
    )


def _create_audit_and_closings() -> None:
    op.execute(
        """
        CREATE TABLE audit_logs (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            -- NULL for an event of the organization itself (only an organization-wide context
            -- can write it).
            school_id uuid,
            actor_user_id uuid,
            actor_type text NOT NULL,
            action text NOT NULL,
            entity_type text NOT NULL,
            entity_id uuid,
            -- The readable reference of the movement (its reference_code), for sentences like
            -- "Maria Silva approved expense APM-20261002-000042". The display format is not stored.
            entity_reference bigint,
            before_data jsonb,
            after_data jsonb,
            request_id text,
            ip inet,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_audit_logs PRIMARY KEY (id),
            CONSTRAINT fk_audit_logs_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
            CONSTRAINT fk_audit_logs_school_id_organization_id_schools
                FOREIGN KEY (school_id, organization_id)
                REFERENCES schools (id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT fk_audit_logs_actor_user_id_users
                FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_audit_logs_actor_type_valid
                CHECK (actor_type IN ('USER', 'SYSTEM', 'PUBLIC')),
            CONSTRAINT ck_audit_logs_actor_matches_type
                CHECK ((actor_type = 'USER') = (actor_user_id IS NOT NULL)),
            CONSTRAINT ck_audit_logs_action_format CHECK (action ~ '^[a-z0-9_]+(\\.[a-z0-9_]+)+$'),
            CONSTRAINT ck_audit_logs_entity_type_format CHECK (entity_type ~ '^[a-z0-9_]+$'),
            CONSTRAINT ck_audit_logs_entity_reference_positive
                CHECK (entity_reference IS NULL OR entity_reference > 0),
            CONSTRAINT ck_audit_logs_before_data_object
                CHECK (before_data IS NULL OR jsonb_typeof(before_data) = 'object'),
            CONSTRAINT ck_audit_logs_after_data_object
                CHECK (after_data IS NULL OR jsonb_typeof(after_data) = 'object'),
            CONSTRAINT ck_audit_logs_request_id_length
                CHECK (request_id IS NULL OR length(request_id) BETWEEN 1 AND 200)
        )
        """
    )
    op.execute("CREATE INDEX ix_audit_logs_school_occurred ON audit_logs (school_id, occurred_at)")
    op.execute("CREATE INDEX ix_audit_logs_entity ON audit_logs (entity_type, entity_id)")
    op.execute(
        "CREATE INDEX ix_audit_logs_school_reference ON audit_logs (school_id, entity_reference) "
        "WHERE entity_reference IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_audit_logs_organization_id ON audit_logs (organization_id)")
    op.execute(
        "CREATE INDEX ix_audit_logs_actor_user_id ON audit_logs (actor_user_id) "
        "WHERE actor_user_id IS NOT NULL"
    )

    op.execute(
        f"""
        CREATE TABLE monthly_closings (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            period_start date NOT NULL,
            period_end date NOT NULL,
            timezone text NOT NULL,
            -- The cash figures. All of them are computed by the database (statement_summary).
            opening_balance_cents bigint NOT NULL,
            contributions_in_cents bigint NOT NULL,
            other_in_cents bigint NOT NULL,
            refunds_in_cents bigint NOT NULL,
            total_in_cents bigint NOT NULL,
            expenses_out_cents bigint NOT NULL,
            reimbursements_out_cents bigint NOT NULL,
            total_out_cents bigint NOT NULL,
            closing_balance_cents bigint NOT NULL,
            -- A photograph of the moment of the closing (the status history is not stored): they are
            -- NOT covered by the hash and verify_closing does not recompute them.
            pending_reimbursements_cents bigint NOT NULL,
            closing_after_pending_cents bigint NOT NULL,
            entries_count integer NOT NULL,
            entries_hash text NOT NULL,
            -- The breakdown by report group and category, computed by the database.
            breakdown jsonb NOT NULL,
            closed_by_user_id uuid NOT NULL,
            closed_at timestamptz NOT NULL DEFAULT now(),
            report_ref text,
            reopened_at timestamptz,
            reopened_by_user_id uuid,
            reopen_reason text,
            CONSTRAINT pk_monthly_closings PRIMARY KEY (id),
            {_tenant_fks("monthly_closings")},
            CONSTRAINT fk_monthly_closings_closed_by_user_id_users
                FOREIGN KEY (closed_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_monthly_closings_reopened_by_user_id_users
                FOREIGN KEY (reopened_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_monthly_closings_calendar_month CHECK (
                extract(day FROM period_start) = 1
                AND period_end = (period_start + interval '1 month')::date - 1),
            CONSTRAINT ck_monthly_closings_totals_add_up CHECK (
                total_in_cents = contributions_in_cents + other_in_cents + refunds_in_cents
                AND total_out_cents = expenses_out_cents + reimbursements_out_cents),
            CONSTRAINT ck_monthly_closings_balance_arithmetic
                CHECK (closing_balance_cents = opening_balance_cents + total_in_cents - total_out_cents),
            CONSTRAINT ck_monthly_closings_committed_arithmetic
                CHECK (closing_after_pending_cents = closing_balance_cents - pending_reimbursements_cents),
            CONSTRAINT ck_monthly_closings_totals_non_negative CHECK (
                contributions_in_cents >= 0 AND other_in_cents >= 0 AND refunds_in_cents >= 0
                AND expenses_out_cents >= 0 AND reimbursements_out_cents >= 0
                AND pending_reimbursements_cents >= 0 AND entries_count >= 0),
            CONSTRAINT ck_monthly_closings_entries_hash_format CHECK (entries_hash ~ '{HEX64}'),
            CONSTRAINT ck_monthly_closings_breakdown_object CHECK (jsonb_typeof(breakdown) = 'object'),
            CONSTRAINT ck_monthly_closings_report_ref_length
                CHECK (report_ref IS NULL OR length(report_ref) BETWEEN 1 AND 500),
            CONSTRAINT ck_monthly_closings_reopen_complete CHECK (
                (reopened_at IS NULL) = (reopened_by_user_id IS NULL)
                AND (reopened_at IS NULL) = (reopen_reason IS NULL)),
            CONSTRAINT ck_monthly_closings_reopen_reason_length
                CHECK (reopen_reason IS NULL OR length(btrim(reopen_reason)) BETWEEN 10 AND 500)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_monthly_closings_active_period "
        "ON monthly_closings (school_id, period_start) WHERE reopened_at IS NULL"
    )
    op.execute("CREATE INDEX ix_monthly_closings_school_id ON monthly_closings (school_id)")
    op.execute(
        "CREATE INDEX ix_monthly_closings_organization_id ON monthly_closings (organization_id)"
    )


# --------------------------------------------------------------------------------------------
# Functions and triggers (all SECURITY INVOKER, search_path fixed to pg_catalog)
# --------------------------------------------------------------------------------------------

HEADER = "LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog"

FUNCTIONS: dict[str, str] = {}

# Generic guards, parameterised by trigger arguments (column names / statuses).
FUNCTIONS["forbid_delete"] = f"""
CREATE FUNCTION public.forbid_delete() RETURNS trigger {HEADER} AS $body$
BEGIN
    RAISE EXCEPTION '% rows can never be deleted (compensate instead)', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END
$body$
"""

FUNCTIONS["forbid_update"] = f"""
CREATE FUNCTION public.forbid_update() RETURNS trigger {HEADER} AS $body$
BEGIN
    RAISE EXCEPTION '% rows can never be updated', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END
$body$
"""

FUNCTIONS["forbid_truncate"] = f"""
CREATE FUNCTION public.forbid_truncate() RETURNS trigger {HEADER} AS $body$
BEGIN
    RAISE EXCEPTION '% can never be truncated', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END
$body$
"""

FUNCTIONS["assert_immutable_columns"] = f"""
CREATE FUNCTION public.assert_immutable_columns() RETURNS trigger {HEADER} AS $body$
DECLARE
    col text;
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
BEGIN
    FOREACH col IN ARRAY TG_ARGV LOOP
        IF (new_row -> col) <> (old_row -> col) THEN
            RAISE EXCEPTION '%.% can never change', TG_TABLE_NAME, col
                USING ERRCODE = 'restrict_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["assert_set_once_columns"] = f"""
CREATE FUNCTION public.assert_set_once_columns() RETURNS trigger {HEADER} AS $body$
DECLARE
    col text;
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
BEGIN
    FOREACH col IN ARRAY TG_ARGV LOOP
        IF (old_row -> col) <> 'null'::jsonb AND (new_row -> col) <> (old_row -> col) THEN
            RAISE EXCEPTION '%.% was already set and can never change', TG_TABLE_NAME, col
                USING ERRCODE = 'restrict_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["assert_anonymize_only"] = f"""
CREATE FUNCTION public.assert_anonymize_only() RETURNS trigger {HEADER} AS $body$
DECLARE
    col text;
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
BEGIN
    FOREACH col IN ARRAY TG_ARGV LOOP
        IF (new_row -> col) <> 'null'::jsonb AND (new_row -> col) <> (old_row -> col) THEN
            RAISE EXCEPTION '%.% can only be erased, never rewritten', TG_TABLE_NAME, col
                USING ERRCODE = 'restrict_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["freeze_when_final"] = f"""
CREATE FUNCTION public.freeze_when_final() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF OLD.status = ANY (TG_ARGV) AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION '% % is final (%) and can never change', TG_TABLE_NAME, OLD.id, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

# A *_user_id must be a user with an ACTIVE membership of the row's organization (and school) that
# is VISIBLE in the current context. Raised BEFORE the foreign key is checked and identical for a
# missing user and for a user of another tenant, so it is no existence oracle (the M1 class).
FUNCTIONS["assert_active_member"] = f"""
CREATE FUNCTION public.assert_active_member() RETURNS trigger {HEADER} AS $body$
DECLARE
    col text;
    new_row jsonb := to_jsonb(NEW);
    old_row jsonb;
    ref text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        old_row := to_jsonb(OLD);
    END IF;
    FOREACH col IN ARRAY TG_ARGV LOOP
        ref := new_row ->> col;
        IF ref IS NULL OR (old_row IS NOT NULL AND ref IS NOT DISTINCT FROM (old_row ->> col)) THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.memberships m
            WHERE m.user_id = ref::uuid AND m.status = 'active'
              AND m.organization_id = NEW.organization_id
              AND (m.school_id IS NULL OR NEW.school_id IS NULL OR m.school_id = NEW.school_id)
        ) THEN
            RAISE EXCEPTION 'invalid user reference in %', col USING ERRCODE = 'foreign_key_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END
$body$
"""

# --- financial_transactions ------------------------------------------------------------------

# Gapless, race-free reference per school: a transaction-scoped advisory lock serialises the writers
# of one school, so max()+1 never repeats and a rollback gives the number back. UNIQUE
# (school_id, reference_code) is the backstop. Needs READ COMMITTED (the default): under a stricter
# level a second writer fails with a unique violation, it never duplicates.
FUNCTIONS["ft_assign_reference_code"] = f"""
CREATE FUNCTION public.ft_assign_reference_code() RETURNS trigger {HEADER} AS $body$
BEGIN
    PERFORM pg_advisory_xact_lock({LOCK_REFERENCE}, hashtext(NEW.school_id::text));
    SELECT coalesce(max(f.reference_code), 0) + 1 INTO NEW.reference_code
    FROM public.financial_transactions f WHERE f.school_id = NEW.school_id;
    RETURN NEW;
END
$body$
"""

# A contribution may be born REVIEW_REQUIRED, but only a PIX_DIRECT one (a credit seen on the bank
# statement and not yet confirmed by the management): the method lives in the detail row, which
# does not exist yet, so contributions_20_insert makes that rule when the detail row arrives.
FUNCTIONS["ft_check_initial"] = f"""
CREATE FUNCTION public.ft_check_initial() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF NOT (NEW.kind || ':' || NEW.status) = ANY (ARRAY[
        'CONTRIBUTION:PENDING_PAYMENT', 'CONTRIBUTION:PAID', 'CONTRIBUTION:REVIEW_REQUIRED',
        'EXPENSE:DRAFT', 'EXPENSE:SUBMITTED',
        'REIMBURSEMENT:PENDING', 'REFUND:REQUESTED'
    ]) THEN
        RAISE EXCEPTION 'a new % cannot start as %', NEW.kind, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

# The state machines (ADR-015, revision 2) and what may be edited. A final row never reaches this
# trigger (ft_06_freeze refuses it first). The request of an expense (amount, purpose, date) is
# editable only while it is a draft or being corrected; the one other change of an amount is the
# acceptance of a REVIEW_REQUIRED contribution, which sets it to the amount the provider received
# and needs the reason of the management, written first.
FUNCTIONS["ft_check_change"] = f"""
CREATE FUNCTION public.ft_check_change() RETURNS trigger {HEADER} AS $body$
DECLARE
    edges text[] := ARRAY[
        'CONTRIBUTION:PENDING_PAYMENT>PAID', 'CONTRIBUTION:PENDING_PAYMENT>EXPIRED',
        'CONTRIBUTION:PENDING_PAYMENT>CANCELLED', 'CONTRIBUTION:PENDING_PAYMENT>REVIEW_REQUIRED',
        'CONTRIBUTION:REVIEW_REQUIRED>PAID', 'CONTRIBUTION:REVIEW_REQUIRED>CANCELLED',
        'EXPENSE:DRAFT>SUBMITTED', 'EXPENSE:DRAFT>CANCELLED',
        'EXPENSE:SUBMITTED>APPROVED', 'EXPENSE:SUBMITTED>REJECTED',
        'EXPENSE:SUBMITTED>CORRECTION_REQUESTED',
        'EXPENSE:CORRECTION_REQUESTED>SUBMITTED', 'EXPENSE:CORRECTION_REQUESTED>CANCELLED',
        'EXPENSE:APPROVED>PAID', 'EXPENSE:APPROVED>CANCELLED',
        'REIMBURSEMENT:PENDING>PAID', 'REIMBURSEMENT:PENDING>CANCELLED',
        'REFUND:REQUESTED>AWAITING_CONFIRMATION', 'REFUND:REQUESTED>REJECTED',
        'REFUND:AWAITING_CONFIRMATION>CONFIRMED', 'REFUND:AWAITING_CONFIRMATION>REJECTED'
    ];
    editable boolean := OLD.kind = 'EXPENSE' AND OLD.status IN ('DRAFT', 'CORRECTION_REQUESTED');
    reason text;
    via text;
    received bigint;
BEGIN
    IF NEW.status <> OLD.status
       AND NOT ((OLD.kind || ':' || OLD.status || '>' || NEW.status) = ANY (edges)) THEN
        RAISE EXCEPTION '% cannot go from % to %', OLD.kind, OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.category_id <> OLD.category_id OR NEW.occurred_at <> OLD.occurred_at) AND NOT editable THEN
        RAISE EXCEPTION 'the purpose and the date change only while an expense is a draft or being corrected'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.kind = 'CONTRIBUTION' AND OLD.status = 'REVIEW_REQUIRED'
       AND NEW.status IN ('PAID', 'CANCELLED') THEN
        SELECT c.review_decision_reason, c.method INTO reason, via
        FROM public.contributions c WHERE c.transaction_id = NEW.id;
        IF length(btrim(coalesce(reason, ''))) < 3 THEN
            RAISE EXCEPTION 'a review decision records its reason (written first)'
                USING ERRCODE = 'check_violation';
        END IF;
        IF via = 'PIX_DIRECT' THEN
            -- A direct Pix has no charge to compare with: the amount is the one registered.
            IF NEW.amount_cents <> OLD.amount_cents THEN
                RAISE EXCEPTION 'deciding a direct Pix review does not change the amount'
                    USING ERRCODE = 'restrict_violation';
            END IF;
        ELSIF NEW.status = 'PAID' THEN
            SELECT p.received_amount_cents INTO received
            FROM public.pix_charges p WHERE p.transaction_id = NEW.id AND p.status = 'REVIEW_REQUIRED';
            IF received IS NULL OR NEW.amount_cents <> received THEN
                RAISE EXCEPTION 'accepting a review sets the amount to the amount received'
                    USING ERRCODE = 'check_violation';
            END IF;
        ELSIF NEW.amount_cents <> OLD.amount_cents THEN
            RAISE EXCEPTION 'cancelling a review does not change the amount'
                USING ERRCODE = 'restrict_violation';
        END IF;
    ELSIF NEW.amount_cents <> OLD.amount_cents AND NOT editable THEN
        RAISE EXCEPTION 'the amount changes only while an expense is a draft or being corrected (or when a review accepts the amount received)'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

# Rules that look at the parent of a reimbursement or of a refund, at creation time. The parent row
# is locked, so two concurrent returns cannot both fit in what is left.
FUNCTIONS["ft_check_relations"] = f"""
CREATE FUNCTION public.ft_check_relations() RETURNS trigger {HEADER} AS $body$
DECLARE
    parent record;
    paid_by text;
    approved bigint;
    available bigint;
    returned bigint;
BEGIN
    SELECT p.status, p.amount_cents INTO parent
    FROM public.financial_transactions p
    WHERE p.id = NEW.parent_transaction_id AND p.organization_id = NEW.organization_id
      AND p.school_id = NEW.school_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN NEW;  -- the composite foreign key refuses it, with the same error for any missing parent
    END IF;
    SELECT e.paid_by, e.approved_amount_cents INTO paid_by, approved
    FROM public.expenses e WHERE e.transaction_id = NEW.parent_transaction_id;
    IF NEW.kind = 'REIMBURSEMENT' THEN
        IF parent.status <> 'APPROVED' OR paid_by IS DISTINCT FROM 'COLLABORATOR'
           OR approved IS NULL OR NEW.amount_cents <> approved THEN
            RAISE EXCEPTION 'a reimbursement needs an APPROVED collaborator expense and its approved amount'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        IF parent.status <> 'PAID' THEN
            RAISE EXCEPTION 'only a PAID expense or reimbursement can be returned'
                USING ERRCODE = 'check_violation';
        END IF;
        available := CASE NEW.parent_kind WHEN 'EXPENSE' THEN approved ELSE parent.amount_cents END;
        IF available IS NULL THEN
            RAISE EXCEPTION 'the expense has no approved amount' USING ERRCODE = 'check_violation';
        END IF;
        SELECT coalesce(sum(r.amount_cents), 0) INTO returned
        FROM public.financial_transactions r
        WHERE r.parent_transaction_id = NEW.parent_transaction_id AND r.kind = 'REFUND'
          AND r.status IN ('REQUESTED', 'AWAITING_CONFIRMATION', 'CONFIRMED');
        IF returned + NEW.amount_cents > available THEN
            RAISE EXCEPTION 'returns would exceed the amount paid' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$body$
"""

# Booking of a settlement (ADR-015, "ajuste tardio"). The shared lock keeps a month closing (which
# takes the exclusive one) from running in the middle of a settlement: the entry either lands in
# the snapshot or is booked now and flagged.
FUNCTIONS["ft_settle"] = f"""
CREATE FUNCTION public.ft_settle() RETURNS trigger {HEADER} AS $body$
DECLARE
    tz text;
    closed_through date;
BEGIN
    IF NEW.settled_at IS NULL OR (TG_OP = 'UPDATE' AND OLD.settled_at IS NOT NULL) THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock_shared({LOCK_PERIOD}, hashtext(NEW.school_id::text));
    IF NEW.settled_at > clock_timestamp() + interval '5 minutes' THEN
        RAISE EXCEPTION 'settled_at cannot be in the future' USING ERRCODE = 'check_violation';
    END IF;
    SELECT s.timezone INTO tz FROM public.school_settings s WHERE s.school_id = NEW.school_id;
    IF tz IS NULL THEN
        RAISE EXCEPTION 'the school has no visible settings' USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(c.period_end) INTO closed_through
    FROM public.monthly_closings c WHERE c.school_id = NEW.school_id AND c.reopened_at IS NULL;
    NEW.late_adjustment := false;
    IF closed_through IS NOT NULL AND (NEW.settled_at AT TIME ZONE tz)::date <= closed_through THEN
        NEW.settled_at := clock_timestamp();
        NEW.late_adjustment := true;
    END IF;
    RETURN NEW;
END
$body$
"""

# Rules that look at two tables at once, so they run at COMMIT (the order of the statements in the
# transaction does not matter) and read the final state of the row.
FUNCTIONS["ft_check_consistency"] = f"""
CREATE FUNCTION public.ft_check_consistency() RETURNS trigger {HEADER} AS $body$
DECLARE
    r public.financial_transactions%ROWTYPE;
    v_contribution public.contributions%ROWTYPE;
    v_expense public.expenses%ROWTYPE;
    v_reimbursement public.reimbursements%ROWTYPE;
    v_refund public.refunds%ROWTYPE;
BEGIN
    SELECT * INTO r FROM public.financial_transactions f WHERE f.id = NEW.id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF r.kind = 'CONTRIBUTION' THEN
        SELECT * INTO v_contribution FROM public.contributions c WHERE c.transaction_id = r.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'contribution % has no contributions row', r.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF v_contribution.method IN ('CASH', 'TRANSFER', 'OTHER')
           AND (r.status <> 'PAID' OR r.created_by_user_id IS NULL) THEN
            RAISE EXCEPTION 'a manual contribution (cash, transfer, other) is born PAID and records who entered it'
                USING ERRCODE = 'check_violation';
        END IF;
        -- A direct Pix is registered by the management (so it always records who), is never
        -- PENDING_PAYMENT or EXPIRED (there is no charge to wait for) and, with no charge, is
        -- not compared with one.
        IF v_contribution.method = 'PIX_DIRECT' AND (
            r.created_by_user_id IS NULL OR r.status NOT IN ('PAID', 'REVIEW_REQUIRED', 'CANCELLED')
        ) THEN
            RAISE EXCEPTION 'a direct Pix contribution records who registered it and is PAID, in review or cancelled'
                USING ERRCODE = 'check_violation';
        END IF;
        IF v_contribution.method = 'PIX' AND r.status = 'PAID' AND NOT EXISTS (
            SELECT 1 FROM public.pix_charges p
            WHERE p.transaction_id = r.id AND p.status IN ('PAID', 'REVIEW_REQUIRED')
              AND p.received_amount_cents = r.amount_cents
        ) THEN
            RAISE EXCEPTION 'a Pix contribution is only PAID with a charge that received exactly its amount'
                USING ERRCODE = 'check_violation';
        END IF;
        IF v_contribution.method = 'PIX' AND r.status = 'REVIEW_REQUIRED' AND NOT EXISTS (
            SELECT 1 FROM public.pix_charges p WHERE p.transaction_id = r.id AND p.status = 'REVIEW_REQUIRED'
        ) THEN
            RAISE EXCEPTION 'a contribution in review needs a charge in review'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF r.kind = 'EXPENSE' THEN
        SELECT * INTO v_expense FROM public.expenses e WHERE e.transaction_id = r.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'expense % has no expenses row', r.id USING ERRCODE = 'check_violation';
        END IF;
        IF r.status IN ('SUBMITTED', 'CORRECTION_REQUESTED', 'APPROVED', 'REJECTED', 'PAID') THEN
            IF NOT EXISTS (SELECT 1 FROM public.expense_attachments a WHERE a.transaction_id = r.id) THEN
                RAISE EXCEPTION 'an expense sent for review needs at least one attachment'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF v_expense.purchase_reason IS NULL OR v_expense.payment_method IS NULL THEN
                RAISE EXCEPTION 'an expense sent for review records the reason of the purchase and how it was paid'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        IF r.status IN ('APPROVED', 'REJECTED', 'PAID') AND v_expense.approved_by_user_id IS NULL THEN
            RAISE EXCEPTION 'a decided expense records who decided' USING ERRCODE = 'check_violation';
        END IF;
        IF r.status IN ('APPROVED', 'PAID') AND (
            v_expense.approved_amount_cents IS NULL OR v_expense.approved_amount_cents > r.amount_cents
            OR (v_expense.approved_amount_cents < r.amount_cents AND v_expense.paid_by <> 'COLLABORATOR')
        ) THEN
            RAISE EXCEPTION 'the approved amount is at most the requested amount, and lower only for an expense paid by a collaborator'
                USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'REJECTED' AND length(btrim(coalesce(v_expense.decision_reason, ''))) < 3 THEN
            RAISE EXCEPTION 'a rejection records its reason' USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'CORRECTION_REQUESTED'
           AND length(btrim(coalesce(v_expense.correction_reason, ''))) < 3 THEN
            RAISE EXCEPTION 'a correction request records what to correct'
                USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'PAID' AND v_expense.paid_by = 'COLLABORATOR' AND NOT EXISTS (
            SELECT 1 FROM public.financial_transactions c
            WHERE c.parent_transaction_id = r.id AND c.kind = 'REIMBURSEMENT' AND c.status = 'PAID'
        ) THEN
            RAISE EXCEPTION 'an expense paid by a collaborator is PAID only with a PAID reimbursement'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF r.kind = 'REIMBURSEMENT' THEN
        SELECT * INTO v_reimbursement FROM public.reimbursements d WHERE d.transaction_id = r.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'reimbursement % has no reimbursements row', r.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'PAID' AND (v_reimbursement.payment_reference IS NULL
                                  OR v_reimbursement.paid_by_user_id IS NULL) THEN
            RAISE EXCEPTION 'a PAID reimbursement records who paid it and its payment reference'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT * INTO v_refund FROM public.refunds d WHERE d.transaction_id = r.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'refund % has no refunds row', r.id USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'CONFIRMED' AND v_refund.confirmed_by_user_id IS NULL THEN
            RAISE EXCEPTION 'a CONFIRMED refund records who confirmed it'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NULL;
END
$body$
"""

# --- details ---------------------------------------------------------------------------------

FUNCTIONS["expenses_set_decision_time"] = f"""
CREATE FUNCTION public.expenses_set_decision_time() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF NEW.approved_by_user_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR OLD.approved_by_user_id IS NULL) THEN
        NEW.approved_at := clock_timestamp();
    END IF;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["expenses_edit_only_when_editable"] = f"""
CREATE FUNCTION public.expenses_edit_only_when_editable() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF (NEW.description IS DISTINCT FROM OLD.description OR NEW.vendor IS DISTINCT FROM OLD.vendor
        OR NEW.purchase_reason IS DISTINCT FROM OLD.purchase_reason
        OR NEW.payment_method IS DISTINCT FROM OLD.payment_method)
       AND NOT EXISTS (
           SELECT 1 FROM public.financial_transactions f
           WHERE f.id = NEW.transaction_id AND f.status IN ('DRAFT', 'CORRECTION_REQUESTED')
       ) THEN
        RAISE EXCEPTION 'an expense is edited only while it is a draft or being corrected'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.correction_reason IS DISTINCT FROM OLD.correction_reason AND NOT EXISTS (
           SELECT 1 FROM public.financial_transactions f
           WHERE f.id = NEW.transaction_id AND f.status IN ('SUBMITTED', 'CORRECTION_REQUESTED')
       ) THEN
        RAISE EXCEPTION 'a correction request is written while the expense is under review'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["expense_attachments_check_state"] = f"""
CREATE FUNCTION public.expense_attachments_check_state() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.financial_transactions f
        WHERE f.id = NEW.transaction_id AND f.organization_id = NEW.organization_id
          AND f.school_id = NEW.school_id AND f.status IN ('REJECTED', 'PAID', 'CANCELLED')
    ) THEN
        RAISE EXCEPTION 'attachments cannot be added to a final expense'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["contributions_check_review"] = f"""
CREATE FUNCTION public.contributions_check_review() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF NEW.review_decision_reason IS DISTINCT FROM OLD.review_decision_reason AND NOT EXISTS (
        SELECT 1 FROM public.financial_transactions f
        WHERE f.id = NEW.transaction_id AND f.status = 'REVIEW_REQUIRED'
    ) THEN
        RAISE EXCEPTION 'a review reason is written only while the contribution is REVIEW_REQUIRED'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

# A Pix is never two contributions: the end-to-end id of a PIX_DIRECT contribution is distinct
# from every end-to-end id of the charges of the school (the mirror of pix_charges_check_end_to_end_id).
# The same advisory lock in both triggers serialises the two inserters of a school, so neither
# misses the row of the other; the error is a unique_violation, as the UNIQUE index would raise.
# And a PIX_DIRECT row is born PAID or REVIEW_REQUIRED only; REVIEW_REQUIRED is for it alone.
FUNCTIONS["contributions_check_insert"] = f"""
CREATE FUNCTION public.contributions_check_insert() RETURNS trigger {HEADER} AS $body$
DECLARE
    current_status text;
BEGIN
    IF NEW.external_reference IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock({LOCK_PIX_REFERENCE}, hashtext(NEW.school_id::text));
        IF EXISTS (
            SELECT 1 FROM public.pix_charges p
            WHERE p.school_id = NEW.school_id AND p.end_to_end_id = NEW.external_reference
        ) THEN
            RAISE EXCEPTION 'this Pix is already the end-to-end id of a charge of the school'
                USING ERRCODE = 'unique_violation';
        END IF;
    END IF;
    SELECT f.status INTO current_status
    FROM public.financial_transactions f
    WHERE f.id = NEW.transaction_id AND f.organization_id = NEW.organization_id
      AND f.school_id = NEW.school_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the composite foreign key refuses it, uniformly
    END IF;
    IF current_status = 'REVIEW_REQUIRED' AND NEW.method <> 'PIX_DIRECT' THEN
        RAISE EXCEPTION 'only a direct Pix contribution can be born REVIEW_REQUIRED'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.method = 'PIX_DIRECT' AND current_status NOT IN ('PAID', 'REVIEW_REQUIRED') THEN
        RAISE EXCEPTION 'a direct Pix contribution is born PAID or REVIEW_REQUIRED'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["pix_charges_check_end_to_end_id"] = f"""
CREATE FUNCTION public.pix_charges_check_end_to_end_id() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF NEW.end_to_end_id IS NULL
       OR (TG_OP = 'UPDATE' AND NEW.end_to_end_id IS NOT DISTINCT FROM OLD.end_to_end_id) THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock({LOCK_PIX_REFERENCE}, hashtext(NEW.school_id::text));
    IF EXISTS (
        SELECT 1 FROM public.contributions c
        WHERE c.school_id = NEW.school_id AND c.external_reference = NEW.end_to_end_id
    ) THEN
        RAISE EXCEPTION 'this Pix is already registered as a direct Pix contribution of the school'
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["pix_charges_check_contribution"] = f"""
CREATE FUNCTION public.pix_charges_check_contribution() RETURNS trigger {HEADER} AS $body$
DECLARE
    contribution record;
    account record;
BEGIN
    SELECT f.status, f.amount_cents, c.method INTO contribution
    FROM public.financial_transactions f
    JOIN public.contributions c ON c.transaction_id = f.id
    WHERE f.id = NEW.transaction_id AND f.organization_id = NEW.organization_id
      AND f.school_id = NEW.school_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the composite foreign key refuses it, uniformly
    END IF;
    IF contribution.method <> 'PIX' OR contribution.status <> 'PENDING_PAYMENT'
       OR contribution.amount_cents <> NEW.amount_cents THEN
        RAISE EXCEPTION 'a Pix charge needs a PENDING_PAYMENT Pix contribution and its exact amount'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT a.status, a.provider INTO account FROM public.payment_accounts a
    WHERE a.id = NEW.payment_account_id AND a.organization_id = NEW.organization_id
      AND a.school_id = NEW.school_id;
    IF FOUND AND (account.status <> 'ACTIVE' OR account.provider <> NEW.provider) THEN
        RAISE EXCEPTION 'a Pix charge needs the ACTIVE payment account of the school, of the same provider'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["school_settings_lock_timezone"] = f"""
CREATE FUNCTION public.school_settings_lock_timezone() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF NEW.timezone IS DISTINCT FROM OLD.timezone AND (
        EXISTS (SELECT 1 FROM public.financial_transactions f
                WHERE f.school_id = OLD.school_id AND f.settled_at IS NOT NULL)
        OR EXISTS (SELECT 1 FROM public.monthly_closings c WHERE c.school_id = OLD.school_id)
    ) THEN
        RAISE EXCEPTION 'the time zone cannot change after the first settlement'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["schools_create_settings"] = f"""
CREATE FUNCTION public.schools_create_settings() RETURNS trigger {HEADER} AS $body$
BEGIN
    INSERT INTO public.school_settings (school_id, organization_id)
    VALUES (NEW.id, NEW.organization_id) ON CONFLICT DO NOTHING;
    RETURN NEW;
END
$body$
"""

# --- statement (cash basis) ------------------------------------------------------------------
# CASH = settled and, for an expense, paid by the APM: an expense paid by a collaborator moves the
# cash only through its reimbursement. Signed amount: IN positive, OUT negative. The window is
# [p_from 00:00, p_to + 1 day 00:00) in the time zone of the school.
# The join to expenses is restricted to the school (e.school_id = p_school): without it the planner
# may read the whole expenses table, of every school, to compute an opening balance.

STATEMENT_WINDOW = """
    SELECT s.timezone,
           p_from::timestamp AT TIME ZONE s.timezone AS t0,
           (p_to + 1)::timestamp AT TIME ZONE s.timezone AS t1
    FROM public.school_settings s WHERE s.school_id = p_school
"""

SUMMARY_COLUMNS = """
    timezone text, opening_balance_cents bigint, contributions_in_cents bigint,
    other_in_cents bigint, refunds_in_cents bigint, total_in_cents bigint,
    expenses_out_cents bigint, reimbursements_out_cents bigint, total_out_cents bigint,
    closing_balance_cents bigint, pending_reimbursements_cents bigint,
    balance_after_pending_cents bigint, entries_count integer
"""

# One row per cash entry of the period, with what a bank-like statement shows: the type, the
# origin, the purpose, the status and a description (a reimbursement carries the description of its
# expense and the beneficiary). The opening balance and the running balance are computed over ALL
# the entries; the optional filters (type, category, status, person) only choose which rows come
# back, so a filtered statement shows the same balances.
FUNCTIONS["statement_entries"] = f"""
CREATE FUNCTION public.statement_entries(
    p_school uuid, p_from date, p_to date, p_type text DEFAULT NULL, p_category uuid DEFAULT NULL,
    p_status text DEFAULT NULL, p_person uuid DEFAULT NULL
)
RETURNS TABLE (
    transaction_id uuid, reference_code bigint, kind text, display_type text, direction text,
    settled_at timestamptz, occurred_at timestamptz, local_date date, amount_cents bigint,
    signed_amount_cents bigint, late_adjustment boolean, status text, status_label text,
    origin_type text, origin_user_id uuid, origin_label text, category_id uuid,
    category_key text, report_group text, description text, beneficiary_user_id uuid,
    beneficiary_label text, opening_balance_cents bigint, running_balance_cents bigint
)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    WITH win AS ({STATEMENT_WINDOW}),
    opening AS (
        SELECT coalesce(sum(CASE f.direction WHEN 'IN' THEN f.amount_cents ELSE -f.amount_cents END), 0)::bigint AS amount
        FROM win, public.financial_transactions f
        LEFT JOIN public.expenses e ON e.transaction_id = f.id AND e.school_id = p_school
        WHERE f.school_id = p_school AND f.settled_at IS NOT NULL AND f.settled_at < win.t0
          AND (f.kind <> 'EXPENSE' OR e.paid_by = 'APM')
    ),
    moves AS (
        SELECT f.id, f.reference_code, f.kind,
               CASE f.kind WHEN 'CONTRIBUTION' THEN 'INCOME' WHEN 'REFUND' THEN 'REFUND' ELSE 'EXPENSE' END AS display_type,
               f.direction, f.settled_at, f.occurred_at,
               (f.settled_at AT TIME ZONE win.timezone)::date AS local_date, f.amount_cents,
               (CASE f.direction WHEN 'IN' THEN f.amount_cents ELSE -f.amount_cents END)::bigint AS signed,
               f.late_adjustment, f.status,
               CASE WHEN f.kind = 'REIMBURSEMENT' AND f.status = 'PAID' THEN 'REIMBURSED' ELSE f.status END AS status_label,
               f.origin_type, f.origin_user_id,
               coalesce(f.origin_name, ou.full_name, c.guardian_name) AS origin_label,
               f.category_id, cat.key AS category_key, cat.report_group,
               CASE f.kind WHEN 'CONTRIBUTION' THEN cat.name WHEN 'EXPENSE' THEN e.description
                           WHEN 'REIMBURSEMENT' THEN pe.description ELSE rf.reason END AS description,
               rb.beneficiary_user_id, bu.full_name AS beneficiary_label
        FROM win, public.financial_transactions f
        JOIN public.categories cat ON cat.id = f.category_id AND cat.school_id = p_school
        LEFT JOIN public.expenses e ON e.transaction_id = f.id AND e.school_id = p_school
        LEFT JOIN public.contributions c ON c.transaction_id = f.id AND c.school_id = p_school
        LEFT JOIN public.reimbursements rb ON rb.transaction_id = f.id AND rb.school_id = p_school
        LEFT JOIN public.refunds rf ON rf.transaction_id = f.id AND rf.school_id = p_school
        LEFT JOIN public.expenses pe ON pe.transaction_id = f.parent_transaction_id AND pe.school_id = p_school
        LEFT JOIN public.users ou ON ou.id = f.origin_user_id
        LEFT JOIN public.users bu ON bu.id = rb.beneficiary_user_id
        WHERE f.school_id = p_school AND f.settled_at >= win.t0 AND f.settled_at < win.t1
          AND (f.kind <> 'EXPENSE' OR e.paid_by = 'APM')
    ),
    ranked AS (
        SELECT m.*, o.amount AS opening_amount,
               (o.amount + sum(m.signed) OVER (
                   ORDER BY m.settled_at, m.reference_code ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
               ))::bigint AS running
        FROM moves m CROSS JOIN opening o
    )
    SELECT r.id, r.reference_code, r.kind, r.display_type, r.direction, r.settled_at, r.occurred_at,
           r.local_date, r.amount_cents, r.signed, r.late_adjustment, r.status, r.status_label,
           r.origin_type, r.origin_user_id, r.origin_label, r.category_id, r.category_key,
           r.report_group, r.description, r.beneficiary_user_id, r.beneficiary_label,
           r.opening_amount, r.running
    FROM ranked r
    WHERE (p_type IS NULL OR r.display_type = p_type)
      AND (p_category IS NULL OR r.category_id = p_category)
      AND (p_status IS NULL OR r.status = p_status OR r.status_label = p_status)
      AND (p_person IS NULL OR r.origin_user_id = p_person OR r.beneficiary_user_id = p_person)
    ORDER BY r.settled_at, r.reference_code
$body$
"""

# The cash figures of a period, and two balances. "In cash" is what came in and went out. "After
# pending reimbursements" also subtracts the reimbursements still waiting to be paid (committed
# money): the product document counts them as already spent. Returns (by source): contributions
# (category group CONTRIBUTIONS), other income (the other groups), confirmed returns; expenses paid
# by the APM and reimbursements paid. The pending figure is the status NOW (the history of a status
# is not stored): it is not part of a closing's hash.
FUNCTIONS["statement_summary"] = f"""
CREATE FUNCTION public.statement_summary(p_school uuid, p_from date, p_to date)
RETURNS TABLE ({SUMMARY_COLUMNS})
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    WITH win AS ({STATEMENT_WINDOW}),
    cash AS (
        SELECT f.kind, f.direction, f.amount_cents, f.settled_at, cat.report_group
        FROM win, public.financial_transactions f
        JOIN public.categories cat ON cat.id = f.category_id AND cat.school_id = p_school
        LEFT JOIN public.expenses e ON e.transaction_id = f.id AND e.school_id = p_school
        WHERE f.school_id = p_school AND f.settled_at IS NOT NULL AND f.settled_at < win.t1
          AND (f.kind <> 'EXPENSE' OR e.paid_by = 'APM')
    ),
    agg AS (
        SELECT
            coalesce(sum(CASE c.direction WHEN 'IN' THEN c.amount_cents ELSE -c.amount_cents END)
                     FILTER (WHERE c.settled_at < w.t0), 0)::bigint AS opening,
            coalesce(sum(c.amount_cents) FILTER (WHERE c.settled_at >= w.t0
                     AND c.kind = 'CONTRIBUTION' AND c.report_group = 'CONTRIBUTIONS'), 0)::bigint AS contributions_in,
            coalesce(sum(c.amount_cents) FILTER (WHERE c.settled_at >= w.t0
                     AND c.kind = 'CONTRIBUTION' AND c.report_group <> 'CONTRIBUTIONS'), 0)::bigint AS other_in,
            coalesce(sum(c.amount_cents) FILTER (WHERE c.settled_at >= w.t0 AND c.kind = 'REFUND'), 0)::bigint AS refunds_in,
            coalesce(sum(c.amount_cents) FILTER (WHERE c.settled_at >= w.t0 AND c.kind = 'EXPENSE'), 0)::bigint AS expenses_out,
            coalesce(sum(c.amount_cents) FILTER (WHERE c.settled_at >= w.t0 AND c.kind = 'REIMBURSEMENT'), 0)::bigint AS reimbursements_out,
            (count(*) FILTER (WHERE c.settled_at >= w.t0))::integer AS entries
        FROM win w LEFT JOIN cash c ON true
        GROUP BY w.t0
    ),
    pending AS (
        SELECT coalesce(sum(f.amount_cents), 0)::bigint AS amount
        FROM win w, public.financial_transactions f
        WHERE f.school_id = p_school AND f.kind = 'REIMBURSEMENT' AND f.status = 'PENDING'
          AND f.occurred_at < w.t1
    )
    SELECT w.timezone, a.opening, a.contributions_in, a.other_in, a.refunds_in,
           (a.contributions_in + a.other_in + a.refunds_in)::bigint,
           a.expenses_out, a.reimbursements_out, (a.expenses_out + a.reimbursements_out)::bigint,
           (a.opening + a.contributions_in + a.other_in + a.refunds_in - a.expenses_out - a.reimbursements_out)::bigint,
           p.amount,
           (a.opening + a.contributions_in + a.other_in + a.refunds_in - a.expenses_out - a.reimbursements_out - p.amount)::bigint,
           a.entries
    FROM win w, agg a, pending p
$body$
"""

# The consolidated view of a network: one row per school of the organization that the context can
# see (an organization-wide context sees all of them; a school context only its own school; another
# organization sees nothing: row level security applies, the function is SECURITY INVOKER).
FUNCTIONS["org_statement_summary"] = f"""
CREATE FUNCTION public.org_statement_summary(p_org uuid, p_from date, p_to date)
RETURNS TABLE (school_id uuid, school_name text, {SUMMARY_COLUMNS})
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    SELECT s.id, s.name, r.timezone, r.opening_balance_cents, r.contributions_in_cents,
           r.other_in_cents, r.refunds_in_cents, r.total_in_cents, r.expenses_out_cents,
           r.reimbursements_out_cents, r.total_out_cents, r.closing_balance_cents,
           r.pending_reimbursements_cents, r.balance_after_pending_cents, r.entries_count
    FROM public.schools s
    CROSS JOIN LATERAL public.statement_summary(s.id, p_from, p_to) r
    WHERE s.organization_id = p_org
    ORDER BY s.name, s.id
$body$
"""

FUNCTIONS["statement_pending"] = """
CREATE FUNCTION public.statement_pending(p_school uuid)
RETURNS TABLE (
    transaction_id uuid, reference_code bigint, kind text, direction text, status text,
    amount_cents bigint, section text, occurred_at timestamptz
)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    SELECT f.id, f.reference_code, f.kind, f.direction, f.status, f.amount_cents,
           CASE WHEN f.kind = 'EXPENSE' AND f.status = 'SUBMITTED' THEN 'AWAITING_APPROVAL'
                WHEN f.kind = 'EXPENSE' AND f.status = 'CORRECTION_REQUESTED' THEN 'AWAITING_CORRECTION'
                WHEN f.kind = 'CONTRIBUTION' AND f.status = 'REVIEW_REQUIRED' THEN 'REVIEW'
                WHEN f.direction = 'IN' THEN 'RECEIVABLE'
                ELSE 'PAYABLE' END,
           f.occurred_at
    FROM public.financial_transactions f
    LEFT JOIN public.expenses e ON e.transaction_id = f.id AND e.school_id = p_school
    WHERE f.school_id = p_school AND f.settled_at IS NULL AND (
        (f.kind = 'CONTRIBUTION' AND f.status IN ('PENDING_PAYMENT', 'REVIEW_REQUIRED'))
        OR (f.kind = 'EXPENSE' AND (f.status IN ('SUBMITTED', 'CORRECTION_REQUESTED')
                                    OR (f.status = 'APPROVED' AND e.paid_by = 'APM')))
        OR (f.kind = 'REIMBURSEMENT' AND f.status = 'PENDING')
        OR (f.kind = 'REFUND' AND f.status IN ('REQUESTED', 'AWAITING_CONFIRMATION'))
    )
    ORDER BY f.occurred_at, f.reference_code
$body$
"""

# Canonical text hashed by a closing (UTF-8, sha256, hex). Header line:
#   school_id|period_start|period_end|opening_balance_cents
# then, for each cash entry of the period ordered by (settled_at, reference_code), a line:
#   reference_code|transaction_id|kind|signed_amount_cents|settled_at(UTC, µs)|late_adjustment
# Lines are joined by LF, with no trailing one. The hash covers the movements, as before.
FUNCTIONS["closing_entries_hash"] = """
CREATE FUNCTION public.closing_entries_hash(p_school uuid, p_from date, p_to date)
RETURNS text
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    SELECT encode(sha256(convert_to(
        p_school::text || '|' || to_char(p_from, 'YYYY-MM-DD') || '|' || to_char(p_to, 'YYYY-MM-DD')
        || '|' || (SELECT s.opening_balance_cents FROM public.statement_summary(p_school, p_from, p_to) s)::text
        || coalesce((
            SELECT string_agg(
                chr(10) || e.reference_code::text || '|' || e.transaction_id::text || '|' || e.kind
                || '|' || e.signed_amount_cents::text
                || '|' || to_char(e.settled_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                || '|' || e.late_adjustment::text,
                '' ORDER BY e.settled_at, e.reference_code)
            FROM public.statement_entries(p_school, p_from, p_to) e
        ), ''),
        'UTF8')), 'hex')
$body$
"""

# {report_group: {in, out, count, categories: {category_key: {in, out, count}}}} of the cash entries
# of the period, in cents. Deterministic (the key and the group of a category never change), so a
# closing can be verified against it.
FUNCTIONS["closing_breakdown"] = """
CREATE FUNCTION public.closing_breakdown(p_school uuid, p_from date, p_to date)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    SELECT coalesce(jsonb_object_agg(g.report_group, g.body), '{}'::jsonb)
    FROM (
        SELECT x.report_group,
               jsonb_build_object(
                   'in', sum(x.in_cents)::bigint, 'out', sum(x.out_cents)::bigint,
                   'count', sum(x.entries)::bigint,
                   'categories', jsonb_object_agg(x.category_key, jsonb_build_object(
                       'in', x.in_cents, 'out', x.out_cents, 'count', x.entries))
               ) AS body
        FROM (
            SELECT e.report_group, e.category_key,
                   coalesce(sum(e.amount_cents) FILTER (WHERE e.direction = 'IN'), 0)::bigint AS in_cents,
                   coalesce(sum(e.amount_cents) FILTER (WHERE e.direction = 'OUT'), 0)::bigint AS out_cents,
                   count(*)::bigint AS entries
            FROM public.statement_entries(p_school, p_from, p_to) e
            GROUP BY e.report_group, e.category_key
        ) x
        GROUP BY x.report_group
    ) g
$body$
"""

# Recomputes the cash figures, the hash and the breakdown from the ledger and compares them with
# the stored closing: true for an intact one. Only meaningful for an ACTIVE closing. It does not
# compare the pending reimbursements nor the balance after them (a photograph of the moment).
FUNCTIONS["verify_closing"] = """
CREATE FUNCTION public.verify_closing(p_id uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    SELECT s.opening_balance_cents = c.opening_balance_cents
       AND s.contributions_in_cents = c.contributions_in_cents AND s.other_in_cents = c.other_in_cents
       AND s.refunds_in_cents = c.refunds_in_cents AND s.total_in_cents = c.total_in_cents
       AND s.expenses_out_cents = c.expenses_out_cents
       AND s.reimbursements_out_cents = c.reimbursements_out_cents
       AND s.total_out_cents = c.total_out_cents AND s.closing_balance_cents = c.closing_balance_cents
       AND s.entries_count = c.entries_count
       AND public.closing_entries_hash(c.school_id, c.period_start, c.period_end) = c.entries_hash
       AND public.closing_breakdown(c.school_id, c.period_start, c.period_end) = c.breakdown
    FROM public.monthly_closings c
    CROSS JOIN LATERAL public.statement_summary(c.school_id, c.period_start, c.period_end) s
    WHERE c.id = p_id
$body$
"""

# --- monthly closing ---------------------------------------------------------------------------

FUNCTIONS["monthly_closings_snapshot"] = f"""
CREATE FUNCTION public.monthly_closings_snapshot() RETURNS trigger {HEADER} AS $body$
DECLARE
    tz text;
    last_end date;
    s record;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'a month can only be closed in a READ COMMITTED transaction'
            USING ERRCODE = 'check_violation';
    END IF;
    PERFORM pg_advisory_xact_lock({LOCK_PERIOD}, hashtext(NEW.school_id::text));
    SELECT st.timezone INTO tz FROM public.school_settings st WHERE st.school_id = NEW.school_id;
    IF tz IS NULL THEN
        RAISE EXCEPTION 'the school has no visible settings' USING ERRCODE = 'check_violation';
    END IF;
    NEW.period_end := (NEW.period_start + interval '1 month')::date - 1;
    IF NEW.period_end >= (clock_timestamp() AT TIME ZONE tz)::date THEN
        RAISE EXCEPTION 'the period has not ended yet' USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(c.period_end) INTO last_end
    FROM public.monthly_closings c WHERE c.school_id = NEW.school_id AND c.reopened_at IS NULL;
    IF last_end IS NOT NULL AND NEW.period_start <> last_end + 1 THEN
        RAISE EXCEPTION 'closings are sequential: the next period starts on %', last_end + 1
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO s FROM public.statement_summary(NEW.school_id, NEW.period_start, NEW.period_end);
    NEW.timezone := tz;
    NEW.opening_balance_cents := s.opening_balance_cents;
    NEW.contributions_in_cents := s.contributions_in_cents;
    NEW.other_in_cents := s.other_in_cents;
    NEW.refunds_in_cents := s.refunds_in_cents;
    NEW.total_in_cents := s.total_in_cents;
    NEW.expenses_out_cents := s.expenses_out_cents;
    NEW.reimbursements_out_cents := s.reimbursements_out_cents;
    NEW.total_out_cents := s.total_out_cents;
    NEW.closing_balance_cents := s.closing_balance_cents;
    NEW.pending_reimbursements_cents := s.pending_reimbursements_cents;
    NEW.closing_after_pending_cents := s.balance_after_pending_cents;
    NEW.entries_count := s.entries_count;
    NEW.entries_hash := public.closing_entries_hash(NEW.school_id, NEW.period_start, NEW.period_end);
    NEW.breakdown := public.closing_breakdown(NEW.school_id, NEW.period_start, NEW.period_end);
    NEW.closed_at := clock_timestamp();
    NEW.report_ref := NULL;
    NEW.reopened_at := NULL;
    NEW.reopened_by_user_id := NULL;
    NEW.reopen_reason := NULL;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["monthly_closings_reopen"] = f"""
CREATE FUNCTION public.monthly_closings_reopen() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF OLD.reopened_at IS NOT NULL OR NEW.reopened_by_user_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'a month can only be reopened in a READ COMMITTED transaction'
            USING ERRCODE = 'check_violation';
    END IF;
    PERFORM pg_advisory_xact_lock({LOCK_PERIOD}, hashtext(OLD.school_id::text));
    IF EXISTS (
        SELECT 1 FROM public.monthly_closings c
        WHERE c.school_id = OLD.school_id AND c.reopened_at IS NULL AND c.period_start > OLD.period_start
    ) THEN
        RAISE EXCEPTION 'only the latest active closing can be reopened' USING ERRCODE = 'check_violation';
    END IF;
    NEW.reopened_at := clock_timestamp();
    RETURN NEW;
END
$body$
"""

# --- audit -------------------------------------------------------------------------------------
# The actor never comes from a column the application writes: it comes from transaction-local
# settings (contract in docs/financial-model.md): app.user_id, app.actor_type, app.request_id and,
# optionally, app.client_ip. Without them the actor is NULL and actor_type PUBLIC.

FUNCTIONS["audit_logs_fill_actor"] = f"""
CREATE FUNCTION public.audit_logs_fill_actor() RETURNS trigger {HEADER} AS $body$
BEGIN
    NEW.actor_user_id := nullif(current_setting('app.user_id', true), '')::uuid;
    NEW.actor_type := coalesce(
        nullif(current_setting('app.actor_type', true), ''),
        CASE WHEN NEW.actor_user_id IS NULL THEN 'PUBLIC' ELSE 'USER' END);
    NEW.request_id := left(nullif(current_setting('app.request_id', true), ''), 200);
    NEW.ip := nullif(current_setting('app.client_ip', true), '')::inet;
    NEW.occurred_at := clock_timestamp();
    RETURN NEW;
END
$body$
"""

# Arguments: (1) columns copied, (2) columns recorded only as "<column>_present" (free text that
# might hold personal data), (3) the primary key column, (4) where the readable reference comes
# from: 'self' (reference_code of the row), 'tx' (that of its ledger row) or empty. On UPDATE only the changed keys are kept.
FUNCTIONS["audit_row_change"] = f"""
CREATE FUNCTION public.audit_row_change() RETURNS trigger {HEADER} AS $body$
DECLARE
    copied text[] := string_to_array(TG_ARGV[0], ',');
    flagged text[] := coalesce(string_to_array(nullif(TG_ARGV[1], ''), ','), ARRAY[]::text[]);
    new_row jsonb := to_jsonb(NEW);
    old_row jsonb;
    after_image jsonb := '{{}}'::jsonb;
    before_image jsonb := NULL;
    col text;
    reference bigint;
BEGIN
    FOREACH col IN ARRAY copied LOOP
        after_image := after_image || jsonb_build_object(col, new_row -> col);
    END LOOP;
    FOREACH col IN ARRAY flagged LOOP
        after_image := after_image || jsonb_build_object(col || '_present', (new_row -> col) <> 'null'::jsonb);
    END LOOP;
    IF TG_OP = 'UPDATE' THEN
        old_row := to_jsonb(OLD);
        before_image := '{{}}'::jsonb;
        FOREACH col IN ARRAY copied LOOP
            before_image := before_image || jsonb_build_object(col, old_row -> col);
        END LOOP;
        FOREACH col IN ARRAY flagged LOOP
            before_image := before_image || jsonb_build_object(col || '_present', (old_row -> col) <> 'null'::jsonb);
        END LOOP;
        SELECT coalesce(jsonb_object_agg(a.key, a.value), '{{}}'::jsonb) INTO after_image
        FROM jsonb_each(after_image) a WHERE a.value IS DISTINCT FROM (before_image -> a.key);
        IF after_image = '{{}}'::jsonb THEN
            RETURN NULL;  -- nothing audited changed (for example only updated_at)
        END IF;
        SELECT coalesce(jsonb_object_agg(b.key, b.value), '{{}}'::jsonb) INTO before_image
        FROM jsonb_each(before_image) b WHERE b.key IN (SELECT jsonb_object_keys(after_image));
    END IF;
    IF TG_ARGV[3] = 'self' THEN
        reference := (new_row ->> 'reference_code')::bigint;
    ELSIF TG_ARGV[3] = 'tx' THEN
        SELECT f.reference_code INTO reference FROM public.financial_transactions f
        WHERE f.id = (new_row ->> 'transaction_id')::uuid;
    END IF;
    INSERT INTO public.audit_logs
        (organization_id, school_id, action, entity_type, entity_id, entity_reference,
         before_data, after_data)
    VALUES (
        NEW.organization_id, NEW.school_id, TG_TABLE_NAME || '.' || lower(TG_OP), TG_TABLE_NAME,
        (new_row ->> TG_ARGV[2])::uuid, reference, before_image, after_image);
    RETURN NULL;
END
$body$
"""

# Functions the application role may call directly (EXECUTE); every other one is a trigger function.
CALLABLE = (
    "statement_entries(uuid, date, date, text, uuid, text, uuid)",
    "statement_summary(uuid, date, date)",
    "org_statement_summary(uuid, date, date)",
    "statement_pending(uuid)",
    "closing_entries_hash(uuid, date, date)",
    "closing_breakdown(uuid, date, date)",
    "verify_closing(uuid)",
)

# Creation order: a SQL function is checked when it is created, so what it calls comes first.
FUNCTION_ORDER = (
    "forbid_delete",
    "forbid_update",
    "forbid_truncate",
    "assert_immutable_columns",
    "assert_set_once_columns",
    "assert_anonymize_only",
    "freeze_when_final",
    "assert_active_member",
    "ft_assign_reference_code",
    "ft_check_initial",
    "ft_check_change",
    "ft_check_relations",
    "ft_settle",
    "ft_check_consistency",
    "expenses_set_decision_time",
    "expenses_edit_only_when_editable",
    "expense_attachments_check_state",
    "contributions_check_review",
    "contributions_check_insert",
    "pix_charges_check_contribution",
    "pix_charges_check_end_to_end_id",
    "school_settings_lock_timezone",
    "schools_create_settings",
    "statement_entries",
    "statement_summary",
    "org_statement_summary",
    "statement_pending",
    "closing_entries_hash",
    "closing_breakdown",
    "verify_closing",
    "monthly_closings_snapshot",
    "monthly_closings_reopen",
    "audit_logs_fill_actor",
    "audit_row_change",
)

# A row in one of these never changes again (a contribution in REVIEW_REQUIRED is not final: the
# management still decides it; a refund is final when CONFIRMED or REJECTED).
FINAL_FT = ("PAID", "EXPIRED", "CANCELLED", "REJECTED", "CONFIRMED")
FINAL_PIX = ("PAID", "REVIEW_REQUIRED", "EXPIRED", "CANCELLED")
# Never change, settled or not. The amount, the purpose and the date are NOT here: they are
# editable in a narrow window that ft_07_change enforces.
FT_IMMUTABLE = (
    "id",
    "organization_id",
    "school_id",
    "kind",
    "direction",
    "parent_transaction_id",
    "parent_kind",
    "reference_code",
    "created_at",
    "created_by_user_id",
    "origin_type",
    "origin_name",
    "origin_user_id",
)


def _literal(values: Sequence[str]) -> str:
    return ", ".join("'" + value + "'" for value in values)


def _trigger(
    table: str,
    name: str,
    timing: str,
    function: str,
    *args: str,
    when: str | None = None,
    deferred: bool = False,
    statement: bool = False,
) -> str:
    """CREATE TRIGGER text. `name` carries the numeric prefix that fixes the firing order."""
    kind = "CONSTRAINT TRIGGER" if deferred else "TRIGGER"
    defer = "DEFERRABLE INITIALLY DEFERRED " if deferred else ""
    each = "FOR EACH STATEMENT" if statement else "FOR EACH ROW"
    condition = f" WHEN ({when})" if when else ""
    return (
        f"CREATE {kind} {name} {timing} ON public.{table} {defer}{each}{condition} "
        f"EXECUTE FUNCTION public.{function}({_literal(args)})"
    )


# Audit allow-lists: (columns copied, columns recorded only as "<column>_present", primary key,
# where the readable reference comes from). Free text and secrets are never copied (they may hold
# personal data or be secret): only whether they are set.
AUDIT = {
    "financial_transactions": (
        "id,organization_id,school_id,kind,direction,amount_cents,status,category_id,occurred_at,"
        "settled_at,late_adjustment,parent_transaction_id,parent_kind,created_by_user_id,"
        "reference_code,origin_type,origin_user_id",
        "origin_name",
        "id",
        "self",
    ),
    "contributions": (
        "transaction_id,method,receipt_expires_at",
        "guardian_name,student_name,class_name,contributor_email,contributor_phone,"
        "receipt_token_hash,review_decision_reason,external_reference",
        "transaction_id",
        "tx",
    ),
    "expenses": (
        "transaction_id,paid_by,payment_method,submitted_by_user_id,approved_by_user_id,"
        "approved_at,approved_amount_cents",
        "description,vendor,purchase_reason,decision_reason,correction_reason",
        "transaction_id",
        "tx",
    ),
    "reimbursements": (
        "transaction_id,beneficiary_user_id,paid_by_user_id",
        "payment_reference",
        "transaction_id",
        "tx",
    ),
    "refunds": (
        "transaction_id,confirmed_by_user_id",
        "reason,payment_reference",
        "transaction_id",
        "tx",
    ),
    "pix_charges": (
        "id,transaction_id,payment_account_id,provider,txid,status,amount_cents,"
        "received_amount_cents,expires_at,end_to_end_id,paid_at",
        "emv_payload,divergence_reason",
        "id",
        "tx",
    ),
    "expense_attachments": (
        "id,transaction_id,kind,content_type,size_bytes,sha256,uploaded_by_user_id",
        "file_name,storage_key",
        "id",
        "tx",
    ),
    "categories": ("id,key,name,applies_to,report_group,is_active", "", "id", ""),
    "school_settings": (
        "school_id,timezone,min_contribution_cents,max_contribution_cents,"
        "suggested_amounts_cents,allow_custom_amount,pix_expiration_minutes,required_fields,"
        "optional_fields,brand_accent,brand_accent_contrast,approval_limit_cents",
        "",
        "school_id",
        "",
    ),
    # NEVER the account id of the bank, the secret reference or the hash of the webhook secret.
    "payment_accounts": (
        "id,provider,status",
        "external_account_id,secret_ref,webhook_secret_hash",
        "id",
        "",
    ),
    "monthly_closings": (
        "id,period_start,period_end,timezone,opening_balance_cents,contributions_in_cents,"
        "other_in_cents,refunds_in_cents,total_in_cents,expenses_out_cents,"
        "reimbursements_out_cents,total_out_cents,closing_balance_cents,"
        "pending_reimbursements_cents,closing_after_pending_cents,entries_count,entries_hash,"
        "closed_by_user_id,closed_at,reopened_at,reopened_by_user_id",
        "report_ref,reopen_reason",
        "id",
        "",
    ),
}

# payment_accounts comes before pix_charges so that the downgrade (which drops in reverse) drops the
# charges first.
NEW_TABLES = (
    "categories",
    "school_settings",
    "financial_transactions",
    "contributions",
    "expenses",
    "reimbursements",
    "refunds",
    "expense_attachments",
    "payment_accounts",
    "pix_charges",
    "webhook_events",
    "audit_logs",
    "monthly_closings",
)


def _create_triggers() -> None:
    def immutable(table: str, *columns: str) -> str:
        return _trigger(
            table, f"{table}_05_immutable", "BEFORE UPDATE", "assert_immutable_columns", *columns
        )

    def set_once(table: str, *columns: str) -> str:
        return _trigger(
            table, f"{table}_10_set_once", "BEFORE UPDATE", "assert_set_once_columns", *columns
        )

    statements = [
        # --- financial_transactions: guards, state machine, booking, checks at commit -----------
        _trigger(
            "financial_transactions",
            "ft_05_immutable",
            "BEFORE UPDATE",
            "assert_immutable_columns",
            *FT_IMMUTABLE,
        ),
        _trigger(
            "financial_transactions",
            "ft_06_freeze",
            "BEFORE UPDATE",
            "freeze_when_final",
            *FINAL_FT,
        ),
        _trigger("financial_transactions", "ft_07_change", "BEFORE UPDATE", "ft_check_change"),
        _trigger("financial_transactions", "ft_08_initial", "BEFORE INSERT", "ft_check_initial"),
        _trigger(
            "financial_transactions",
            "ft_10_reference_code",
            "BEFORE INSERT",
            "ft_assign_reference_code",
        ),
        _trigger(
            "financial_transactions",
            "ft_20_relations",
            "BEFORE INSERT",
            "ft_check_relations",
            when="NEW.parent_transaction_id IS NOT NULL",
        ),
        _trigger(
            "financial_transactions",
            "ft_25_member",
            "BEFORE INSERT",
            "assert_active_member",
            "created_by_user_id",
            "origin_user_id",
        ),
        _trigger("financial_transactions", "ft_30_settle", "BEFORE INSERT OR UPDATE", "ft_settle"),
        _trigger(
            "financial_transactions",
            "ft_90_consistency",
            "AFTER INSERT OR UPDATE",
            "ft_check_consistency",
            deferred=True,
        ),
        # --- details ----------------------------------------------------------------------------
        immutable(
            "contributions",
            "transaction_id",
            "organization_id",
            "school_id",
            "kind",
            "method",
            "external_reference",
            "receipt_token_hash",
            "receipt_expires_at",
            "created_at",
        ),
        _trigger(
            "contributions",
            "contributions_10_anonymize",
            "BEFORE UPDATE",
            "assert_anonymize_only",
            "guardian_name",
            "student_name",
            "class_name",
            "contributor_email",
            "contributor_phone",
        ),
        _trigger(
            "contributions",
            "contributions_12_set_once",
            "BEFORE UPDATE",
            "assert_set_once_columns",
            "review_decision_reason",
        ),
        _trigger(
            "contributions",
            "contributions_15_review",
            "BEFORE UPDATE",
            "contributions_check_review",
        ),
        _trigger(
            "contributions",
            "contributions_20_insert",
            "BEFORE INSERT",
            "contributions_check_insert",
        ),
        immutable(
            "expenses",
            "transaction_id",
            "organization_id",
            "school_id",
            "kind",
            "paid_by",
            "submitted_by_user_id",
            "created_at",
        ),
        set_once(
            "expenses",
            "approved_by_user_id",
            "approved_at",
            "approved_amount_cents",
            "decision_reason",
        ),
        _trigger(
            "expenses",
            "expenses_15_decision_time",
            "BEFORE INSERT OR UPDATE",
            "expenses_set_decision_time",
        ),
        _trigger(
            "expenses", "expenses_20_editable", "BEFORE UPDATE", "expenses_edit_only_when_editable"
        ),
        _trigger(
            "expenses",
            "expenses_25_member",
            "BEFORE INSERT OR UPDATE OF submitted_by_user_id, approved_by_user_id",
            "assert_active_member",
            "submitted_by_user_id",
            "approved_by_user_id",
        ),
        immutable(
            "reimbursements",
            "transaction_id",
            "organization_id",
            "school_id",
            "kind",
            "beneficiary_user_id",
            "created_at",
        ),
        set_once("reimbursements", "payment_reference", "paid_by_user_id"),
        _trigger(
            "reimbursements",
            "reimbursements_25_member",
            "BEFORE INSERT OR UPDATE OF beneficiary_user_id, paid_by_user_id",
            "assert_active_member",
            "beneficiary_user_id",
            "paid_by_user_id",
        ),
        immutable(
            "refunds",
            "transaction_id",
            "organization_id",
            "school_id",
            "kind",
            "reason",
            "created_at",
        ),
        set_once("refunds", "payment_reference", "confirmed_by_user_id"),
        _trigger(
            "refunds",
            "refunds_25_member",
            "BEFORE INSERT OR UPDATE OF confirmed_by_user_id",
            "assert_active_member",
            "confirmed_by_user_id",
        ),
        _trigger(
            "expense_attachments",
            "expense_attachments_05_no_update",
            "BEFORE UPDATE",
            "forbid_update",
        ),
        _trigger(
            "expense_attachments",
            "expense_attachments_20_state",
            "BEFORE INSERT",
            "expense_attachments_check_state",
        ),
        _trigger(
            "expense_attachments",
            "expense_attachments_25_member",
            "BEFORE INSERT",
            "assert_active_member",
            "uploaded_by_user_id",
        ),
        # --- support ----------------------------------------------------------------------------
        immutable(
            "categories",
            "id",
            "organization_id",
            "school_id",
            "key",
            "applies_to",
            "report_group",
            "created_at",
        ),
        immutable("school_settings", "school_id", "organization_id", "created_at"),
        _trigger(
            "school_settings",
            "school_settings_10_timezone",
            "BEFORE UPDATE",
            "school_settings_lock_timezone",
        ),
        immutable(
            "payment_accounts", "id", "organization_id", "school_id", "provider", "created_at"
        ),
        immutable(
            "pix_charges",
            "id",
            "organization_id",
            "school_id",
            "transaction_id",
            "payment_account_id",
            "provider",
            "txid",
            "amount_cents",
            "expires_at",
            "created_at",
        ),
        _trigger(
            "pix_charges", "pix_charges_06_freeze", "BEFORE UPDATE", "freeze_when_final", *FINAL_PIX
        ),
        set_once(
            "pix_charges",
            "end_to_end_id",
            "paid_at",
            "received_amount_cents",
            "divergence_reason",
            "emv_payload",
        ),
        _trigger(
            "pix_charges",
            "pix_charges_20_contribution",
            "BEFORE INSERT",
            "pix_charges_check_contribution",
        ),
        _trigger(
            "pix_charges",
            "pix_charges_20_end_to_end_id",
            "BEFORE INSERT OR UPDATE OF end_to_end_id",
            "pix_charges_check_end_to_end_id",
        ),
        immutable(
            "webhook_events",
            "id",
            "organization_id",
            "school_id",
            "provider",
            "idempotency_key",
            "end_to_end_id",
            "raw_payload",
            "signature_valid",
            "received_at",
        ),
        set_once("webhook_events", "processed_at"),
        # --- closings ---------------------------------------------------------------------------
        immutable(
            "monthly_closings",
            "id",
            "organization_id",
            "school_id",
            "period_start",
            "period_end",
            "timezone",
            "opening_balance_cents",
            "contributions_in_cents",
            "other_in_cents",
            "refunds_in_cents",
            "total_in_cents",
            "expenses_out_cents",
            "reimbursements_out_cents",
            "total_out_cents",
            "closing_balance_cents",
            "pending_reimbursements_cents",
            "closing_after_pending_cents",
            "entries_count",
            "entries_hash",
            "breakdown",
            "closed_by_user_id",
            "closed_at",
        ),
        set_once(
            "monthly_closings", "report_ref", "reopened_at", "reopened_by_user_id", "reopen_reason"
        ),
        _trigger(
            "monthly_closings",
            "monthly_closings_15_reopen",
            "BEFORE UPDATE",
            "monthly_closings_reopen",
        ),
        _trigger(
            "monthly_closings",
            "monthly_closings_20_snapshot",
            "BEFORE INSERT",
            "monthly_closings_snapshot",
        ),
        _trigger(
            "monthly_closings",
            "monthly_closings_25_member",
            "BEFORE INSERT OR UPDATE OF closed_by_user_id, reopened_by_user_id",
            "assert_active_member",
            "closed_by_user_id",
            "reopened_by_user_id",
        ),
        # --- audit_logs: the actor comes from the settings, and the log only grows ---------------
        _trigger("audit_logs", "audit_logs_05_actor", "BEFORE INSERT", "audit_logs_fill_actor"),
        _trigger(
            "audit_logs",
            "audit_logs_10_member",
            "BEFORE INSERT",
            "assert_active_member",
            "actor_user_id",
        ),
        _trigger("audit_logs", "audit_logs_90_no_update", "BEFORE UPDATE", "forbid_update"),
        # --- a school is born with its settings ---------------------------------------------------
        _trigger("schools", "schools_10_settings", "AFTER INSERT", "schools_create_settings"),
    ]
    for table, (copied, flagged, key, reference) in AUDIT.items():
        statements.append(
            _trigger(
                table,
                f"{table}_95_audit",
                "AFTER INSERT OR UPDATE",
                "audit_row_change",
                copied,
                flagged,
                key,
                reference,
            )
        )
    for table in NEW_TABLES:
        statements.append(_trigger(table, f"{table}_no_delete", "BEFORE DELETE", "forbid_delete"))
        statements.append(
            _trigger(
                table, f"{table}_no_truncate", "BEFORE TRUNCATE", "forbid_truncate", statement=True
            )
        )
    for statement in statements:
        op.execute(statement)


# --------------------------------------------------------------------------------------------
# Row level security and grants
# --------------------------------------------------------------------------------------------

# table -> (INSERT columns, UPDATE columns). SELECT is table-wide, except payment_accounts (below).
# Never: DELETE; UPDATE of id, organization_id, school_id, kind or any parent. Columns the database
# assigns itself (id, reference_code, late_adjustment, approved_at, reopened_at, the actor of an
# audit row, timestamps) are in no INSERT list, so the application cannot choose them.
GRANTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "financial_transactions": (
        (
            "organization_id",
            "school_id",
            "kind",
            "direction",
            "amount_cents",
            "status",
            "category_id",
            "origin_type",
            "origin_name",
            "origin_user_id",
            "occurred_at",
            "settled_at",
            "parent_transaction_id",
            "parent_kind",
            "created_by_user_id",
        ),
        ("status", "settled_at", "amount_cents", "category_id", "occurred_at", "updated_at"),
    ),
    "contributions": (
        (
            "transaction_id",
            "organization_id",
            "school_id",
            "method",
            "external_reference",
            "guardian_name",
            "student_name",
            "class_name",
            "contributor_email",
            "contributor_phone",
            "receipt_token_hash",
            "receipt_expires_at",
        ),
        (
            "guardian_name",
            "student_name",
            "class_name",
            "contributor_email",
            "contributor_phone",
            "review_decision_reason",
            "updated_at",
        ),
    ),
    "expenses": (
        (
            "transaction_id",
            "organization_id",
            "school_id",
            "description",
            "vendor",
            "purchase_reason",
            "payment_method",
            "paid_by",
            "submitted_by_user_id",
        ),
        (
            "description",
            "vendor",
            "purchase_reason",
            "payment_method",
            "approved_by_user_id",
            "approved_amount_cents",
            "decision_reason",
            "correction_reason",
            "updated_at",
        ),
    ),
    "reimbursements": (
        ("transaction_id", "organization_id", "school_id", "beneficiary_user_id"),
        ("payment_reference", "paid_by_user_id", "updated_at"),
    ),
    "refunds": (
        ("transaction_id", "organization_id", "school_id", "reason"),
        ("payment_reference", "confirmed_by_user_id", "updated_at"),
    ),
    "expense_attachments": (
        (
            "organization_id",
            "school_id",
            "transaction_id",
            "kind",
            "storage_key",
            "file_name",
            "content_type",
            "size_bytes",
            "sha256",
            "uploaded_by_user_id",
        ),
        (),
    ),
    "categories": (
        ("organization_id", "school_id", "key", "name", "applies_to", "report_group", "is_active"),
        ("name", "is_active", "updated_at"),
    ),
    "school_settings": (
        (
            "school_id",
            "organization_id",
            "timezone",
            "min_contribution_cents",
            "max_contribution_cents",
            "suggested_amounts_cents",
            "allow_custom_amount",
            "pix_expiration_minutes",
            "required_fields",
            "optional_fields",
            "brand_accent",
            "brand_accent_contrast",
            "approval_limit_cents",
        ),
        (
            "timezone",
            "min_contribution_cents",
            "max_contribution_cents",
            "suggested_amounts_cents",
            "allow_custom_amount",
            "pix_expiration_minutes",
            "required_fields",
            "optional_fields",
            "brand_accent",
            "brand_accent_contrast",
            "approval_limit_cents",
            "updated_at",
        ),
    ),
    "payment_accounts": (
        (
            "organization_id",
            "school_id",
            "provider",
            "external_account_id",
            "status",
            "secret_ref",
            "webhook_secret_hash",
        ),
        ("external_account_id", "status", "secret_ref", "webhook_secret_hash", "updated_at"),
    ),
    "pix_charges": (
        (
            "transaction_id",
            "organization_id",
            "school_id",
            "payment_account_id",
            "provider",
            "txid",
            "status",
            "amount_cents",
            "expires_at",
            "emv_payload",
        ),
        (
            "status",
            "end_to_end_id",
            "paid_at",
            "received_amount_cents",
            "divergence_reason",
            "emv_payload",
            "updated_at",
        ),
    ),
    "webhook_events": (
        (
            "organization_id",
            "school_id",
            "provider",
            "idempotency_key",
            "end_to_end_id",
            "raw_payload",
            "signature_valid",
        ),
        ("processed_at", "processing_error", "attempts"),
    ),
    "audit_logs": (
        (
            "organization_id",
            "school_id",
            "action",
            "entity_type",
            "entity_id",
            "entity_reference",
            "before_data",
            "after_data",
        ),
        (),
    ),
    "monthly_closings": (
        ("organization_id", "school_id", "period_start", "closed_by_user_id"),
        ("reopened_by_user_id", "reopen_reason", "report_ref"),
    ),
}

# The application role may read every column of these tables but one: the hash of the webhook secret
# is read only by the narrow SECURITY DEFINER function of ADR-016 (TASK-006), like password_hash.
SELECT_COLUMNS: dict[str, tuple[str, ...]] = {
    "payment_accounts": (
        "id",
        "organization_id",
        "school_id",
        "provider",
        "external_account_id",
        "status",
        "secret_ref",
        "created_at",
        "updated_at",
    ),
}


def _write_check(table: str) -> str:
    """WITH CHECK of the write policies: the scope predicate AND "the school belongs to the
    organization of the row". Without the second half, an organization-wide context could write a
    row with its own organization_id and a school of ANOTHER organization: the policy would pass
    (it only compares the organization), and a unique index would answer before the composite
    foreign key does, with a duplicate-key error that reveals the other school has data. The
    subquery runs under the row level security of schools, so a school of another tenant is simply
    not there: the write fails with the one error of the policy, before any index."""
    in_school = (
        "EXISTS (SELECT 1 FROM public.schools s "
        f"WHERE s.id = {table}.school_id AND s.organization_id = {table}.organization_id)"
    )
    if table == "audit_logs":  # an event of the organization itself has no school
        in_school = f"({table}.school_id IS NULL OR {in_school})"
    return f"({_scope('organization_id', 'school_id')} AND {in_school})"


def _enable_rls_and_grant() -> None:
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        scope = _scope("organization_id", "school_id")
        write_check = _write_check(table)
        insert_columns, update_columns = GRANTS[table]
        op.execute(f"CREATE POLICY {table}_select ON {table} FOR SELECT USING ({scope})")
        op.execute(f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({write_check})")
        if update_columns:
            op.execute(
                f"CREATE POLICY {table}_update ON {table} FOR UPDATE "
                f"USING ({scope}) WITH CHECK ({write_check})"
            )
        # No DELETE policy, no DELETE grant: a financial row is never removed.
        if table in SELECT_COLUMNS:
            op.execute(f"GRANT SELECT ({', '.join(SELECT_COLUMNS[table])}) ON {table} TO apm_app")
        else:
            op.execute(f"GRANT SELECT ON {table} TO apm_app")
        op.execute(f"GRANT INSERT ({', '.join(insert_columns)}) ON {table} TO apm_app")
        if update_columns:
            op.execute(f"GRANT UPDATE ({', '.join(update_columns)}) ON {table} TO apm_app")


def _create_functions() -> None:
    for name in FUNCTION_ORDER:
        op.execute(FUNCTIONS[name])
    names = ", ".join(f"'{name}'" for name in FUNCTION_ORDER)
    # Nothing is executable by everybody; only the statement functions go to apm_app. Trigger
    # functions need no EXECUTE: the privilege is checked when the trigger is created.
    op.execute(
        f"""
        DO $$
        DECLARE
            f record;
        BEGIN
            FOR f IN
                SELECT p.oid::regprocedure AS signature FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname = ANY (ARRAY[{names}])
            LOOP
                EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.signature);
            END LOOP;
        END
        $$
        """
    )
    for signature in CALLABLE:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO apm_app")


def upgrade() -> None:
    op.execute("SET LOCAL ROLE apm_owner")
    _create_support_tables()
    _create_ledger()
    _create_details()
    _create_pix_and_webhooks()
    _create_audit_and_closings()
    _create_functions()
    _create_triggers()
    _enable_rls_and_grant()
    op.execute("RESET ROLE")
    # Schools that already exist get their settings row (new ones get it from the trigger). This
    # runs as the admin: with a non-superuser admin row level security would hide the schools, so
    # the first deployment must not have any (docs/financial-model.md, accepted risks).
    op.execute(
        "INSERT INTO school_settings (school_id, organization_id) "
        "SELECT id, organization_id FROM schools ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    # Everything this revision created goes, data included: a financial schema cannot be
    # downgraded without losing its rows.
    op.execute("SET LOCAL ROLE apm_owner")
    op.execute("DROP TRIGGER IF EXISTS schools_10_settings ON public.schools")
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    names = ", ".join(f"'{name}'" for name in reversed(FUNCTION_ORDER))
    op.execute(
        f"""
        DO $$
        DECLARE
            f record;
        BEGIN
            FOR f IN
                SELECT p.oid::regprocedure AS signature FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname = ANY (ARRAY[{names}])
            LOOP
                EXECUTE format('DROP FUNCTION IF EXISTS %s', f.signature);
            END LOOP;
        END
        $$
        """
    )
    op.execute("RESET ROLE")
