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
            CONSTRAINT ck_categories_applies_to_valid CHECK (applies_to IN ('IN', 'OUT'))
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
            pix_expiration_minutes integer NOT NULL DEFAULT 30,
            identification_mode text NOT NULL DEFAULT 'OPTIONAL',
            required_fields text[] NOT NULL DEFAULT '{{}}',
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
            CONSTRAINT ck_school_settings_pix_expiration_range
                CHECK (pix_expiration_minutes BETWEEN 5 AND 1440),
            CONSTRAINT ck_school_settings_identification_mode_valid
                CHECK (identification_mode IN ('ANONYMOUS', 'OPTIONAL', 'REQUIRED')),
            CONSTRAINT ck_school_settings_required_fields_valid
                CHECK (required_fields <@ ARRAY['guardian_name', 'student_name', 'class_name']::text[]
                       AND (identification_mode <> 'ANONYMOUS' OR cardinality(required_fields) = 0)
                       AND (identification_mode <> 'REQUIRED' OR cardinality(required_fields) >= 1)),
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
            amount_cents bigint NOT NULL,
            status text NOT NULL,
            category_id uuid,
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
                    AND status IN ('PENDING_PAYMENT', 'PAID', 'EXPIRED', 'CANCELLED'))
                OR (kind = 'EXPENSE'
                    AND status IN ('SUBMITTED', 'APPROVED', 'REJECTED', 'PAID', 'CANCELLED'))
                OR (kind = 'REIMBURSEMENT' AND status IN ('PENDING', 'PAID', 'CANCELLED'))
                OR (kind = 'REFUND' AND status IN ('PENDING', 'PAID', 'FAILED'))),
            -- direction follows from the kind and, for a refund, from the kind of its parent.
            CONSTRAINT ck_financial_transactions_shape CHECK (
                (kind = 'CONTRIBUTION' AND direction = 'IN'
                    AND parent_transaction_id IS NULL AND parent_kind IS NULL)
                OR (kind = 'EXPENSE' AND direction = 'OUT'
                    AND parent_transaction_id IS NULL AND parent_kind IS NULL)
                OR (kind = 'REIMBURSEMENT' AND direction = 'OUT'
                    AND parent_transaction_id IS NOT NULL AND parent_kind = 'EXPENSE')
                OR (kind = 'REFUND' AND parent_transaction_id IS NOT NULL
                    AND ((parent_kind = 'CONTRIBUTION' AND direction = 'OUT')
                         OR (parent_kind = 'EXPENSE' AND direction = 'IN')))),
            CONSTRAINT ck_financial_transactions_not_own_parent
                CHECK (parent_transaction_id IS NULL OR parent_transaction_id <> id),
            CONSTRAINT ck_financial_transactions_paid_iff_settled
                CHECK ((status = 'PAID') = (settled_at IS NOT NULL)),
            CONSTRAINT ck_financial_transactions_late_needs_settled
                CHECK (NOT late_adjustment OR settled_at IS NOT NULL),
            -- Only a public Pix contribution (ADR-010) has no author.
            CONSTRAINT ck_financial_transactions_author_required
                CHECK (kind = 'CONTRIBUTION' OR created_by_user_id IS NOT NULL),
            CONSTRAINT ck_financial_transactions_expense_has_category
                CHECK (kind <> 'EXPENSE' OR category_id IS NOT NULL),
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
        "CREATE INDEX ix_financial_transactions_category "
        "ON financial_transactions (category_id) WHERE category_id IS NOT NULL",
        "CREATE INDEX ix_financial_transactions_organization_id "
        "ON financial_transactions (organization_id)",
        "CREATE UNIQUE INDEX uq_financial_transactions_one_active_reimbursement "
        "ON financial_transactions (parent_transaction_id) "
        "WHERE kind = 'REIMBURSEMENT' AND status IN ('PENDING', 'PAID')",
    ):
        op.execute(statement)


def _create_details() -> None:
    op.execute(
        f"""
        CREATE TABLE contributions (
            transaction_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            kind text NOT NULL DEFAULT 'CONTRIBUTION',
            method text NOT NULL,
            guardian_name text,
            student_name text,
            class_name text,
            receipt_token_hash text,
            receipt_expires_at timestamptz,
            {TIMESTAMPS},
            CONSTRAINT pk_contributions PRIMARY KEY (transaction_id),
            {_tenant_fks("contributions")},
            {_detail_fk("contributions", "CONTRIBUTION")},
            -- Target of pix_charges.
            CONSTRAINT uq_contributions_transaction_id_organization_id_school_id
                UNIQUE (transaction_id, organization_id, school_id),
            -- Global on purpose: the token is a 128-bit secret stored only as a hash, so a clash
            -- reveals nothing, and resolve_receipt (ADR-016) looks it up without a tenant.
            CONSTRAINT uq_contributions_receipt_token_hash UNIQUE (receipt_token_hash),
            CONSTRAINT ck_contributions_method_valid CHECK (method IN ('PIX', 'CASH')),
            CONSTRAINT ck_contributions_guardian_name_length
                CHECK (guardian_name IS NULL OR length(btrim(guardian_name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_contributions_student_name_length
                CHECK (student_name IS NULL OR length(btrim(student_name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_contributions_class_name_length
                CHECK (class_name IS NULL OR length(btrim(class_name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_contributions_receipt_token_hash_format
                CHECK (receipt_token_hash IS NULL OR receipt_token_hash ~ '{HEX64}'),
            CONSTRAINT ck_contributions_pix_has_receipt
                CHECK (method <> 'PIX'
                       OR (receipt_token_hash IS NOT NULL AND receipt_expires_at IS NOT NULL))
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
            paid_by text NOT NULL,
            submitted_by_user_id uuid NOT NULL,
            -- Who decided (approved OR rejected). approved_at is set by the trigger.
            approved_by_user_id uuid,
            approved_at timestamptz,
            decision_reason text,
            {TIMESTAMPS},
            CONSTRAINT pk_expenses PRIMARY KEY (transaction_id),
            {_tenant_fks("expenses")},
            {_detail_fk("expenses", "EXPENSE")},
            CONSTRAINT fk_expenses_submitted_by_user_id_users
                FOREIGN KEY (submitted_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_expenses_approved_by_user_id_users
                FOREIGN KEY (approved_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT uq_expenses_transaction_id_organization_id_school_id
                UNIQUE (transaction_id, organization_id, school_id),
            CONSTRAINT ck_expenses_description_length
                CHECK (length(btrim(description)) BETWEEN 1 AND 500),
            CONSTRAINT ck_expenses_vendor_length
                CHECK (vendor IS NULL OR length(btrim(vendor)) BETWEEN 1 AND 200),
            CONSTRAINT ck_expenses_paid_by_valid CHECK (paid_by IN ('APM', 'COLLABORATOR')),
            -- Separation of duties, in the database: nobody decides their own expense.
            CONSTRAINT ck_expenses_decider_is_not_submitter
                CHECK (approved_by_user_id IS NULL OR approved_by_user_id <> submitted_by_user_id),
            CONSTRAINT ck_expenses_decision_complete
                CHECK ((approved_by_user_id IS NULL) = (approved_at IS NULL)),
            CONSTRAINT ck_expenses_decision_reason_length
                CHECK (decision_reason IS NULL OR length(btrim(decision_reason)) BETWEEN 1 AND 500)
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
            payment_reference text,
            {TIMESTAMPS},
            CONSTRAINT pk_reimbursements PRIMARY KEY (transaction_id),
            {_tenant_fks("reimbursements")},
            {_detail_fk("reimbursements", "REIMBURSEMENT")},
            CONSTRAINT fk_reimbursements_beneficiary_user_id_users
                FOREIGN KEY (beneficiary_user_id) REFERENCES users (id) ON DELETE RESTRICT,
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
            {TIMESTAMPS},
            CONSTRAINT pk_refunds PRIMARY KEY (transaction_id),
            {_tenant_fks("refunds")},
            {_detail_fk("refunds", "REFUND")},
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
            CONSTRAINT uq_expense_attachments_transaction_id_sha256 UNIQUE (transaction_id, sha256),
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
    op.execute(
        f"""
        CREATE TABLE pix_charges (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid NOT NULL,
            transaction_id uuid NOT NULL,
            provider text NOT NULL,
            txid text NOT NULL,
            status text NOT NULL DEFAULT 'PENDING',
            amount_cents bigint NOT NULL,
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
            -- Per school, never global: a global UNIQUE would be an oracle across tenants, and each
            -- APM has its own bank account (ADR-011).
            CONSTRAINT uq_pix_charges_school_id_provider_txid UNIQUE (school_id, provider, txid),
            CONSTRAINT ck_pix_charges_provider_valid CHECK (provider IN ('BB', 'SANDBOX')),
            CONSTRAINT ck_pix_charges_txid_format CHECK (txid ~ '^[A-Za-z0-9]{{26,35}}$'),
            CONSTRAINT ck_pix_charges_status_valid
                CHECK (status IN ('PENDING', 'PAID', 'EXPIRED', 'CANCELLED')),
            CONSTRAINT ck_pix_charges_amount_range
                CHECK (amount_cents > 0 AND amount_cents <= 1000000000000),
            CONSTRAINT ck_pix_charges_expires_after_creation CHECK (expires_at > created_at),
            CONSTRAINT ck_pix_charges_end_to_end_id_format
                CHECK (end_to_end_id IS NULL OR end_to_end_id ~ '^E[A-Za-z0-9]{{31}}$'),
            CONSTRAINT ck_pix_charges_paid_iff_confirmed
                CHECK ((status = 'PAID') = (end_to_end_id IS NOT NULL AND paid_at IS NOT NULL)),
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
        "ON pix_charges (transaction_id) WHERE status = 'PENDING'"
    )
    op.execute("CREATE INDEX ix_pix_charges_transaction_id ON pix_charges (transaction_id)")
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
            opening_balance_cents bigint NOT NULL,
            total_in_cents bigint NOT NULL,
            total_out_cents bigint NOT NULL,
            closing_balance_cents bigint NOT NULL,
            entries_count integer NOT NULL,
            entries_hash text NOT NULL,
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
            CONSTRAINT ck_monthly_closings_balance_arithmetic
                CHECK (closing_balance_cents = opening_balance_cents + total_in_cents - total_out_cents),
            CONSTRAINT ck_monthly_closings_totals_non_negative
                CHECK (total_in_cents >= 0 AND total_out_cents >= 0 AND entries_count >= 0),
            CONSTRAINT ck_monthly_closings_entries_hash_format CHECK (entries_hash ~ '{HEX64}'),
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

# Rules that look at the parent of a reimbursement or a refund, at creation time. The parent row is
# locked, so two concurrent refunds cannot both fit in what is left.
FUNCTIONS["ft_check_relations"] = f"""
CREATE FUNCTION public.ft_check_relations() RETURNS trigger {HEADER} AS $body$
DECLARE
    parent record;
    paid_by text;
    refunded bigint;
BEGIN
    SELECT p.status, p.amount_cents INTO parent
    FROM public.financial_transactions p
    WHERE p.id = NEW.parent_transaction_id AND p.organization_id = NEW.organization_id
      AND p.school_id = NEW.school_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN NEW;  -- the composite foreign key refuses it, with the same error for any missing parent
    END IF;
    SELECT e.paid_by INTO paid_by FROM public.expenses e WHERE e.transaction_id = NEW.parent_transaction_id;
    IF NEW.kind = 'REIMBURSEMENT' THEN
        IF parent.status <> 'APPROVED' OR paid_by IS DISTINCT FROM 'COLLABORATOR'
           OR NEW.amount_cents <> parent.amount_cents THEN
            RAISE EXCEPTION 'a reimbursement needs an APPROVED collaborator expense and its exact amount'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        IF parent.status <> 'PAID' THEN
            RAISE EXCEPTION 'only a PAID transaction can be refunded' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.parent_kind = 'EXPENSE' AND paid_by IS DISTINCT FROM 'APM' THEN
            RAISE EXCEPTION 'an expense paid by a collaborator is corrected by its reimbursement'
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT coalesce(sum(r.amount_cents), 0) INTO refunded
        FROM public.financial_transactions r
        WHERE r.parent_transaction_id = NEW.parent_transaction_id AND r.kind = 'REFUND'
          AND r.status IN ('PENDING', 'PAID');
        IF refunded + NEW.amount_cents > parent.amount_cents THEN
            RAISE EXCEPTION 'refunds would exceed the original amount' USING ERRCODE = 'check_violation';
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
    method text;
    ex public.expenses%ROWTYPE;
    reference text;
BEGIN
    SELECT * INTO r FROM public.financial_transactions f WHERE f.id = NEW.id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF r.kind = 'CONTRIBUTION' THEN
        SELECT c.method INTO method FROM public.contributions c WHERE c.transaction_id = r.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'contribution % has no contributions row', r.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF method = 'CASH' AND (r.status <> 'PAID' OR r.created_by_user_id IS NULL) THEN
            RAISE EXCEPTION 'a cash contribution is born PAID and records who entered it'
                USING ERRCODE = 'check_violation';
        END IF;
        IF method = 'PIX' AND r.status = 'PAID' AND NOT EXISTS (
            SELECT 1 FROM public.pix_charges p
            WHERE p.transaction_id = r.id AND p.status = 'PAID' AND p.amount_cents = r.amount_cents
        ) THEN
            RAISE EXCEPTION 'a Pix contribution is only PAID with a PAID Pix charge of the same amount'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF r.kind = 'EXPENSE' THEN
        SELECT * INTO ex FROM public.expenses e WHERE e.transaction_id = r.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'expense % has no expenses row', r.id USING ERRCODE = 'check_violation';
        END IF;
        IF r.status IN ('APPROVED', 'REJECTED', 'PAID') AND ex.approved_by_user_id IS NULL THEN
            RAISE EXCEPTION 'a decided expense records who decided' USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'REJECTED' AND length(btrim(coalesce(ex.decision_reason, ''))) < 3 THEN
            RAISE EXCEPTION 'a rejection records its reason' USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'PAID' AND ex.paid_by = 'COLLABORATOR' AND NOT EXISTS (
            SELECT 1 FROM public.financial_transactions c
            WHERE c.parent_transaction_id = r.id AND c.kind = 'REIMBURSEMENT' AND c.status = 'PAID'
        ) THEN
            RAISE EXCEPTION 'an expense paid by a collaborator is PAID only with a PAID reimbursement'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF r.kind = 'REIMBURSEMENT' THEN
        SELECT d.payment_reference INTO reference
        FROM public.reimbursements d WHERE d.transaction_id = r.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'reimbursement % has no reimbursements row', r.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'PAID' AND reference IS NULL THEN
            RAISE EXCEPTION 'a PAID reimbursement records its payment reference'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT d.payment_reference INTO reference FROM public.refunds d WHERE d.transaction_id = r.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'refund % has no refunds row', r.id USING ERRCODE = 'check_violation';
        END IF;
        IF r.status = 'PAID' AND reference IS NULL THEN
            RAISE EXCEPTION 'a PAID refund records its payment reference'
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

FUNCTIONS["expenses_edit_only_while_submitted"] = f"""
CREATE FUNCTION public.expenses_edit_only_while_submitted() RETURNS trigger {HEADER} AS $body$
BEGIN
    IF (NEW.description IS DISTINCT FROM OLD.description OR NEW.vendor IS DISTINCT FROM OLD.vendor)
       AND NOT EXISTS (
           SELECT 1 FROM public.financial_transactions f
           WHERE f.id = NEW.transaction_id AND f.status = 'SUBMITTED'
       ) THEN
        RAISE EXCEPTION 'an expense can only be edited while SUBMITTED' USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$body$
"""

FUNCTIONS["pix_charges_check_contribution"] = f"""
CREATE FUNCTION public.pix_charges_check_contribution() RETURNS trigger {HEADER} AS $body$
DECLARE
    contribution record;
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

STATEMENT_WINDOW = """
    SELECT s.timezone,
           p_from::timestamp AT TIME ZONE s.timezone AS t0,
           (p_to + 1)::timestamp AT TIME ZONE s.timezone AS t1
    FROM public.school_settings s WHERE s.school_id = p_school
"""

FUNCTIONS["statement_entries"] = f"""
CREATE FUNCTION public.statement_entries(p_school uuid, p_from date, p_to date)
RETURNS TABLE (
    transaction_id uuid, reference_code bigint, kind text, direction text,
    settled_at timestamptz, occurred_at timestamptz, local_date date, amount_cents bigint,
    signed_amount_cents bigint, late_adjustment boolean,
    opening_balance_cents bigint, running_balance_cents bigint
)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    WITH win AS ({STATEMENT_WINDOW}),
    opening AS (
        SELECT coalesce(sum(CASE f.direction WHEN 'IN' THEN f.amount_cents ELSE -f.amount_cents END), 0)::bigint AS amount
        FROM win, public.financial_transactions f
        LEFT JOIN public.expenses e ON e.transaction_id = f.id
        WHERE f.school_id = p_school AND f.settled_at IS NOT NULL AND f.settled_at < win.t0
          AND (f.kind <> 'EXPENSE' OR e.paid_by = 'APM')
    ),
    moves AS (
        SELECT f.id, f.reference_code, f.kind, f.direction, f.settled_at, f.occurred_at,
               (f.settled_at AT TIME ZONE win.timezone)::date AS local_date, f.amount_cents,
               (CASE f.direction WHEN 'IN' THEN f.amount_cents ELSE -f.amount_cents END)::bigint AS signed,
               f.late_adjustment
        FROM win, public.financial_transactions f
        LEFT JOIN public.expenses e ON e.transaction_id = f.id
        WHERE f.school_id = p_school AND f.settled_at >= win.t0 AND f.settled_at < win.t1
          AND (f.kind <> 'EXPENSE' OR e.paid_by = 'APM')
    )
    SELECT m.id, m.reference_code, m.kind, m.direction, m.settled_at, m.occurred_at, m.local_date,
           m.amount_cents, m.signed, m.late_adjustment, o.amount,
           (o.amount + sum(m.signed) OVER (
               ORDER BY m.settled_at, m.reference_code ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ))::bigint
    FROM moves m CROSS JOIN opening o
    ORDER BY m.settled_at, m.reference_code
$body$
"""

FUNCTIONS["statement_summary"] = f"""
CREATE FUNCTION public.statement_summary(p_school uuid, p_from date, p_to date)
RETURNS TABLE (
    timezone text, opening_balance_cents bigint, total_in_cents bigint, total_out_cents bigint,
    closing_balance_cents bigint, entries_count integer
)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    WITH win AS ({STATEMENT_WINDOW}),
    cash AS (
        SELECT f.settled_at, f.direction, f.amount_cents
        FROM win, public.financial_transactions f
        LEFT JOIN public.expenses e ON e.transaction_id = f.id
        WHERE f.school_id = p_school AND f.settled_at IS NOT NULL AND f.settled_at < win.t1
          AND (f.kind <> 'EXPENSE' OR e.paid_by = 'APM')
    ),
    agg AS (
        SELECT
            coalesce(sum(CASE c.direction WHEN 'IN' THEN c.amount_cents ELSE -c.amount_cents END)
                     FILTER (WHERE c.settled_at < w.t0), 0)::bigint AS opening,
            coalesce(sum(c.amount_cents) FILTER (WHERE c.settled_at >= w.t0 AND c.direction = 'IN'), 0)::bigint AS total_in,
            coalesce(sum(c.amount_cents) FILTER (WHERE c.settled_at >= w.t0 AND c.direction = 'OUT'), 0)::bigint AS total_out,
            (count(*) FILTER (WHERE c.settled_at >= w.t0))::integer AS entries
        FROM win w LEFT JOIN cash c ON true
        GROUP BY w.t0
    )
    SELECT w.timezone, a.opening, a.total_in, a.total_out,
           a.opening + a.total_in - a.total_out, a.entries
    FROM win w, agg a
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
                WHEN f.direction = 'IN' THEN 'RECEIVABLE'
                ELSE 'PAYABLE' END,
           f.occurred_at
    FROM public.financial_transactions f
    LEFT JOIN public.expenses e ON e.transaction_id = f.id
    WHERE f.school_id = p_school AND f.settled_at IS NULL AND (
        (f.kind = 'CONTRIBUTION' AND f.status = 'PENDING_PAYMENT')
        OR (f.kind = 'EXPENSE' AND (f.status = 'SUBMITTED'
                                    OR (f.status = 'APPROVED' AND e.paid_by = 'APM')))
        OR (f.kind = 'REIMBURSEMENT' AND f.status = 'PENDING')
        OR (f.kind = 'REFUND' AND f.status = 'PENDING')
    )
    ORDER BY f.occurred_at, f.reference_code
$body$
"""

# Canonical text hashed by a closing (UTF-8, sha256, hex). Header line:
#   school_id|period_start|period_end|opening_balance_cents
# then, for each cash entry of the period ordered by (settled_at, reference_code), a line:
#   reference_code|transaction_id|kind|signed_amount_cents|settled_at(UTC, µs)|late_adjustment
# Lines are joined by LF, with no trailing one.
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

FUNCTIONS["verify_closing"] = """
CREATE FUNCTION public.verify_closing(p_id uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $body$
    SELECT s.opening_balance_cents = c.opening_balance_cents
       AND s.total_in_cents = c.total_in_cents AND s.total_out_cents = c.total_out_cents
       AND s.closing_balance_cents = c.closing_balance_cents AND s.entries_count = c.entries_count
       AND public.closing_entries_hash(c.school_id, c.period_start, c.period_end) = c.entries_hash
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
    NEW.total_in_cents := s.total_in_cents;
    NEW.total_out_cents := s.total_out_cents;
    NEW.closing_balance_cents := s.closing_balance_cents;
    NEW.entries_count := s.entries_count;
    NEW.entries_hash := public.closing_entries_hash(NEW.school_id, NEW.period_start, NEW.period_end);
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
# might hold personal data), (3) the primary key column. On UPDATE only the changed keys are kept.
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
    INSERT INTO public.audit_logs
        (organization_id, school_id, action, entity_type, entity_id, before_data, after_data)
    VALUES (
        NEW.organization_id, NEW.school_id, TG_TABLE_NAME || '.' || lower(TG_OP), TG_TABLE_NAME,
        (new_row ->> TG_ARGV[2])::uuid, before_image, after_image);
    RETURN NULL;
END
$body$
"""

# Functions the application role may call directly (EXECUTE); every other one is a trigger function.
CALLABLE = (
    "statement_entries(uuid, date, date)",
    "statement_summary(uuid, date, date)",
    "statement_pending(uuid)",
    "closing_entries_hash(uuid, date, date)",
    "verify_closing(uuid)",
)

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
    "ft_check_relations",
    "ft_settle",
    "ft_check_consistency",
    "expenses_set_decision_time",
    "expenses_edit_only_while_submitted",
    "pix_charges_check_contribution",
    "school_settings_lock_timezone",
    "schools_create_settings",
    "statement_entries",
    "statement_summary",
    "statement_pending",
    "closing_entries_hash",
    "verify_closing",
    "monthly_closings_snapshot",
    "monthly_closings_reopen",
    "audit_logs_fill_actor",
    "audit_row_change",
)


FINAL_FT = ("PAID", "EXPIRED", "CANCELLED", "REJECTED", "FAILED")
FINAL_PIX = ("PAID", "EXPIRED", "CANCELLED")
FT_IMMUTABLE = (
    "id",
    "organization_id",
    "school_id",
    "kind",
    "direction",
    "amount_cents",
    "parent_transaction_id",
    "parent_kind",
    "reference_code",
    "created_at",
    "created_by_user_id",
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


# Audit allow-lists: (columns copied, columns recorded only as "<column>_present", primary key).
# Free text and secrets are never copied (they may hold personal data): only whether they are set.
AUDIT = {
    "financial_transactions": (
        "id,organization_id,school_id,kind,direction,amount_cents,status,category_id,occurred_at,"
        "settled_at,late_adjustment,parent_transaction_id,parent_kind,created_by_user_id,"
        "reference_code",
        "",
        "id",
    ),
    "contributions": (
        "transaction_id,method,receipt_expires_at",
        "guardian_name,student_name,class_name,receipt_token_hash",
        "transaction_id",
    ),
    "expenses": (
        "transaction_id,paid_by,submitted_by_user_id,approved_by_user_id,approved_at",
        "description,vendor,decision_reason",
        "transaction_id",
    ),
    "reimbursements": ("transaction_id,beneficiary_user_id", "payment_reference", "transaction_id"),
    "refunds": ("transaction_id", "reason,payment_reference", "transaction_id"),
    "pix_charges": (
        "id,transaction_id,provider,txid,status,amount_cents,expires_at,end_to_end_id,paid_at",
        "emv_payload",
        "id",
    ),
    "expense_attachments": (
        "id,transaction_id,content_type,size_bytes,sha256,uploaded_by_user_id",
        "file_name,storage_key",
        "id",
    ),
    "categories": ("id,key,name,applies_to,is_active", "", "id"),
    "school_settings": (
        "school_id,timezone,min_contribution_cents,max_contribution_cents,"
        "pix_expiration_minutes,identification_mode,required_fields,brand_accent,"
        "brand_accent_contrast,approval_limit_cents",
        "",
        "school_id",
    ),
    "monthly_closings": (
        "id,period_start,period_end,timezone,opening_balance_cents,total_in_cents,"
        "total_out_cents,closing_balance_cents,entries_count,entries_hash,closed_by_user_id,"
        "closed_at,reopened_at,reopened_by_user_id",
        "report_ref,reopen_reason",
        "id",
    ),
}

NEW_TABLES = (
    "categories",
    "school_settings",
    "financial_transactions",
    "contributions",
    "expenses",
    "reimbursements",
    "refunds",
    "expense_attachments",
    "pix_charges",
    "webhook_events",
    "audit_logs",
    "monthly_closings",
)


def _create_triggers() -> None:
    def immutable(table: str, *columns: str, number: str = "05") -> str:
        return _trigger(
            table,
            f"{table}_{number}_immutable",
            "BEFORE UPDATE",
            "assert_immutable_columns",
            *columns,
        )

    def set_once(table: str, *columns: str) -> str:
        return _trigger(
            table, f"{table}_10_set_once", "BEFORE UPDATE", "assert_set_once_columns", *columns
        )

    statements = [
        # --- financial_transactions: guards, then booking, then the checks at commit ----------
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
        ),
        _trigger("financial_transactions", "ft_30_settle", "BEFORE INSERT OR UPDATE", "ft_settle"),
        _trigger(
            "financial_transactions",
            "ft_90_consistency",
            "AFTER INSERT OR UPDATE",
            "ft_check_consistency",
            deferred=True,
        ),
        # --- details --------------------------------------------------------------------------
        immutable(
            "contributions",
            "transaction_id",
            "organization_id",
            "school_id",
            "kind",
            "method",
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
        set_once("expenses", "approved_by_user_id", "approved_at", "decision_reason"),
        _trigger(
            "expenses",
            "expenses_15_decision_time",
            "BEFORE INSERT OR UPDATE",
            "expenses_set_decision_time",
        ),
        _trigger(
            "expenses",
            "expenses_20_editable",
            "BEFORE UPDATE",
            "expenses_edit_only_while_submitted",
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
        set_once("reimbursements", "payment_reference"),
        _trigger(
            "reimbursements",
            "reimbursements_25_member",
            "BEFORE INSERT",
            "assert_active_member",
            "beneficiary_user_id",
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
        set_once("refunds", "payment_reference"),
        _trigger(
            "expense_attachments",
            "expense_attachments_05_no_update",
            "BEFORE UPDATE",
            "forbid_update",
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
            "categories", "id", "organization_id", "school_id", "key", "applies_to", "created_at"
        ),
        immutable("school_settings", "school_id", "organization_id", "created_at"),
        _trigger(
            "school_settings",
            "school_settings_10_timezone",
            "BEFORE UPDATE",
            "school_settings_lock_timezone",
        ),
        immutable(
            "pix_charges",
            "id",
            "organization_id",
            "school_id",
            "transaction_id",
            "provider",
            "txid",
            "amount_cents",
            "expires_at",
            "created_at",
        ),
        _trigger(
            "pix_charges", "pix_charges_06_freeze", "BEFORE UPDATE", "freeze_when_final", *FINAL_PIX
        ),
        set_once("pix_charges", "end_to_end_id", "paid_at", "emv_payload"),
        _trigger(
            "pix_charges",
            "pix_charges_20_contribution",
            "BEFORE INSERT",
            "pix_charges_check_contribution",
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
            "total_in_cents",
            "total_out_cents",
            "closing_balance_cents",
            "entries_count",
            "entries_hash",
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
    for table, (copied, flagged, key) in AUDIT.items():
        statements.append(
            _trigger(
                table,
                f"{table}_95_audit",
                "AFTER INSERT OR UPDATE",
                "audit_row_change",
                copied,
                flagged,
                key,
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

# table -> (INSERT columns, UPDATE columns). SELECT is table-wide. Never: DELETE; UPDATE of id,
# organization_id, school_id, kind, amount_cents or any parent. Columns the database assigns itself
# (id, reference_code, late_adjustment, approved_at, reopened_at, the actor of an audit row,
# timestamps) are not in any INSERT list, so the application cannot choose them.
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
            "occurred_at",
            "settled_at",
            "parent_transaction_id",
            "parent_kind",
            "created_by_user_id",
        ),
        ("status", "settled_at", "updated_at"),
    ),
    "contributions": (
        (
            "transaction_id",
            "organization_id",
            "school_id",
            "method",
            "guardian_name",
            "student_name",
            "class_name",
            "receipt_token_hash",
            "receipt_expires_at",
        ),
        ("guardian_name", "student_name", "class_name", "updated_at"),
    ),
    "expenses": (
        (
            "transaction_id",
            "organization_id",
            "school_id",
            "description",
            "vendor",
            "paid_by",
            "submitted_by_user_id",
        ),
        ("description", "vendor", "approved_by_user_id", "decision_reason", "updated_at"),
    ),
    "reimbursements": (
        ("transaction_id", "organization_id", "school_id", "beneficiary_user_id"),
        ("payment_reference", "updated_at"),
    ),
    "refunds": (
        ("transaction_id", "organization_id", "school_id", "reason"),
        ("payment_reference", "updated_at"),
    ),
    "expense_attachments": (
        (
            "organization_id",
            "school_id",
            "transaction_id",
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
        ("organization_id", "school_id", "key", "name", "applies_to", "is_active"),
        ("name", "is_active", "updated_at"),
    ),
    "school_settings": (
        (
            "school_id",
            "organization_id",
            "timezone",
            "min_contribution_cents",
            "max_contribution_cents",
            "pix_expiration_minutes",
            "identification_mode",
            "required_fields",
            "brand_accent",
            "brand_accent_contrast",
            "approval_limit_cents",
        ),
        (
            "timezone",
            "min_contribution_cents",
            "max_contribution_cents",
            "pix_expiration_minutes",
            "identification_mode",
            "required_fields",
            "brand_accent",
            "brand_accent_contrast",
            "approval_limit_cents",
            "updated_at",
        ),
    ),
    "pix_charges": (
        (
            "transaction_id",
            "organization_id",
            "school_id",
            "provider",
            "txid",
            "status",
            "amount_cents",
            "expires_at",
            "emv_payload",
        ),
        ("status", "end_to_end_id", "paid_at", "emv_payload", "updated_at"),
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
