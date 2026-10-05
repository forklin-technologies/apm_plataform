# ruff: noqa: E501  (SQL text)
"""F11: a payment account holds NO credential, and there is one ACTIVE account per school.

secret_ref is only a REFERENCE to a secrets manager ("env:NAME", "file:path", "vault:path"). The
hash of the webhook secret is not a credential (it is the SHA-256 of a 128-bit secret, like a
session token), but the application role still cannot read it: only the narrow function of ADR-016
that TASK-006 adds will. The tests prove it by the catalog and by trying every way to read it.
"""

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.db.tenant import TenantContext, tenant_session
from app.models import PaymentAccount
from tests.dbsupport import Tenants, transaction
from tests.financial.conftest import Ledger
from tests.financial.support import Fresh, create_account, make_school, token_hash

COLUMNS = {
    "id", "organization_id", "school_id", "provider", "external_account_id", "status",
    "secret_ref", "webhook_secret_hash", "created_at", "updated_at",
}  # fmt: skip
CREDENTIAL_WORDS = re.compile(
    r"password|passwd|secret|private|credential|certificate|cert\b|pfx|pem\b|api_?key|token|client_id|auth",
    re.I,
)
# The only columns of the financial schema that may mention a secret, and why.
ALLOWED_NAMES = {
    "secret_ref": "a REFERENCE to a secrets manager, never the secret",
    "webhook_secret_hash": "the SHA-256 of the webhook secret (a hash, unreadable by the application role)",
    "receipt_token_hash": "the SHA-256 of the receipt link token (ADR-010)",
    "receipt_expires_at": "the expiry of that token",
}


def test_payment_accounts_has_exactly_the_documented_columns(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        columns = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'payment_accounts' AND table_schema = 'public'"
                )
            )
        }
    assert columns == COLUMNS


def test_no_column_of_the_financial_schema_holds_a_credential(admin_engine: Engine) -> None:
    """By the catalog: no column is named like a password, a key, a certificate or a token, except the
    documented ones, and those are references or hashes (their CHECKs prove the shape)."""
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name IN ("
                "'payment_accounts', 'pix_charges', 'webhook_events', 'contributions', 'school_settings', "
                "'expenses', 'expense_attachments', 'categories', 'monthly_closings', 'audit_logs')"
            )
        ).all()
        suspicious = [
            (t, c) for t, c, _ in rows if CREDENTIAL_WORDS.search(c) and c not in ALLOWED_NAMES
        ]
        assert suspicious == []
        # No binary column anywhere (a certificate or a key file would need one).
        assert [(t, c) for t, c, d in rows if d == "bytea"] == []
        definitions = {
            r[0]: r[1]
            for r in conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'public.payment_accounts'::regclass AND contype = 'c'"
                )
            )
        }
    assert "env:[A-Z]" in definitions["ck_payment_accounts_secret_ref_format"]
    assert "vault:" in definitions["ck_payment_accounts_secret_ref_format"]
    assert "[0-9a-f]{64}" in definitions["ck_payment_accounts_webhook_secret_hash_format"]


@pytest.mark.parametrize(
    "reference",
    [
        "env:APM_AURORA_BB",
        "vault:kv/apm/aurora-bb",
        "vault:schools/a1/pix_bb",
    ],
)
def test_a_reference_to_a_secrets_manager_is_accepted(
    world: tuple[Connection, Fresh], reference: str
) -> None:
    conn, f = world
    conn.execute(
        text("UPDATE payment_accounts SET secret_ref = :r WHERE id = :a"),
        {"r": reference, "a": f.account},
    )


@pytest.mark.parametrize(
    "raw",
    [
        "a-raw-secret-value",
        "env:",
        "env: spaced",
        "ENV:UPPER",
        "key=abc123",
        ":nothing",
        "-----BEGIN PRIVATE KEY-----",
        "p" * 260,
        "env:" + "x" * 201,
        "e:short-scheme-ok-but-too-short",
        # review of 2026-10-05: no other scheme, no '..', no absolute path, no empty segment
        "file:/run/secrets/aurora",
        "file:../../etc/passwd",
        "aws_sm:apm/prod/bb",
        "env:lower_case",
        "env:" + "A" * 101,
        "vault:../other-school",
        "vault:/absolute/path",
        "vault:a//b",
        "vault:" + "/".join(["seg"] * 9),
    ],
)
def test_a_literal_secret_does_not_fit_in_secret_ref(
    world: tuple[Connection, Fresh], raw: str
) -> None:
    conn, f = world
    with (
        pytest.raises(DBAPIError, match="ck_payment_accounts_secret_ref_format"),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE payment_accounts SET secret_ref = :r WHERE id = :a"),
            {"r": raw, "a": f.account},
        )


# --- one ACTIVE account per school ------------------------------------------------------------------


def test_a_school_has_at_most_one_active_account(world: tuple[Connection, Fresh]) -> None:
    conn, f = world  # make_school created one ACTIVE account
    with (
        pytest.raises(DBAPIError, match="uq_payment_accounts_one_active_per_school"),
        conn.begin_nested(),
    ):
        create_account(conn, f.org, f.school, status="ACTIVE")
    create_account(conn, f.org, f.school, status="PENDING")  # any number of the others
    create_account(conn, f.org, f.school, status="PENDING")
    create_account(conn, f.org, f.school, status="INACTIVE")


def test_activating_a_second_account_needs_the_first_to_be_deactivated(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    second = create_account(conn, f.org, f.school, status="PENDING")
    with (
        pytest.raises(DBAPIError, match="uq_payment_accounts_one_active_per_school"),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE payment_accounts SET status = 'ACTIVE' WHERE id = :a"), {"a": second}
        )
    conn.execute(
        text("UPDATE payment_accounts SET status = 'INACTIVE' WHERE id = :a"), {"a": f.account}
    )
    conn.execute(text("UPDATE payment_accounts SET status = 'ACTIVE' WHERE id = :a"), {"a": second})
    assert (
        conn.execute(
            text(
                "SELECT count(*) FROM payment_accounts WHERE school_id = :s AND status = 'ACTIVE'"
            ),
            {"s": f.school},
        ).scalar_one()
        == 1
    )


def test_every_school_can_have_its_own_active_account(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    other = make_school(conn)  # another school, another ACTIVE account
    assert (
        conn.execute(
            text(
                "SELECT count(*) FROM payment_accounts WHERE status = 'ACTIVE' AND school_id = ANY(:s)"
            ),
            {"s": [f.school, other.school]},
        ).scalar_one()
        == 2
    )


def test_the_same_bank_account_is_not_registered_twice_for_a_school(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    external: str = conn.execute(
        text("SELECT external_account_id FROM payment_accounts WHERE id = :a"), {"a": f.account}
    ).scalar_one()
    with (
        pytest.raises(
            DBAPIError, match="uq_payment_accounts_school_id_provider_external_account_id"
        ),
        conn.begin_nested(),
    ):
        conn.execute(
            text(
                "INSERT INTO payment_accounts (organization_id, school_id, provider, external_account_id, status, secret_ref) VALUES (:o, :s, 'SANDBOX', :e, 'PENDING', 'env:X')"
            ),
            {"o": f.org, "s": f.school, "e": external},
        )


def test_the_provider_of_an_account_never_changes(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    with pytest.raises(DBAPIError, match="can never change"), conn.begin_nested():
        conn.execute(
            text("UPDATE payment_accounts SET provider = 'BB' WHERE id = :a"), {"a": f.account}
        )


# --- the webhook secret hash --------------------------------------------------------------------------


def test_the_hash_is_unique_across_every_school_for_the_lookup_without_a_tenant(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    other = make_school(conn)
    shared = token_hash()
    conn.execute(
        text("UPDATE payment_accounts SET webhook_secret_hash = :h WHERE id = :a"),
        {"h": shared, "a": f.account},
    )
    with (
        pytest.raises(DBAPIError, match="uq_payment_accounts_webhook_secret_hash"),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE payment_accounts SET webhook_secret_hash = :h WHERE id = :a"),
            {"h": shared, "a": other.account},
        )
    conn.execute(
        text("UPDATE payment_accounts SET webhook_secret_hash = NULL WHERE id = :a"),
        {"a": other.account},
    )  # NULLs do not clash


@pytest.mark.parametrize("bad", ["xyz", "A" * 64, "0" * 63, "0" * 65])
def test_the_hash_has_the_shape_of_a_sha256(world: tuple[Connection, Fresh], bad: str) -> None:
    conn, f = world
    with (
        pytest.raises(DBAPIError, match="ck_payment_accounts_webhook_secret_hash_format"),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE payment_accounts SET webhook_secret_hash = :h WHERE id = :a"),
            {"h": bad, "a": f.account},
        )


READ_ATTEMPTS = [
    "SELECT webhook_secret_hash FROM payment_accounts",
    "SELECT * FROM payment_accounts",
    "SELECT p.* FROM payment_accounts p",
    "SELECT p FROM payment_accounts p",
    "SELECT to_jsonb(p) FROM payment_accounts p",
    "SELECT row_to_json(p) FROM payment_accounts p",
    "SELECT count(*) FROM payment_accounts WHERE webhook_secret_hash IS NOT NULL",
    "SELECT id FROM payment_accounts WHERE webhook_secret_hash = 'x'",
    "SELECT id FROM payment_accounts ORDER BY webhook_secret_hash",
    "SELECT webhook_secret_hash, count(*) FROM payment_accounts GROUP BY webhook_secret_hash",
    "SELECT length(webhook_secret_hash) FROM payment_accounts",
    "SELECT md5(webhook_secret_hash) FROM payment_accounts",
    "SELECT id FROM payment_accounts WHERE webhook_secret_hash LIKE '0%'",
    "UPDATE payment_accounts SET status = status WHERE webhook_secret_hash IS NOT NULL",
    "UPDATE payment_accounts SET secret_ref = secret_ref RETURNING webhook_secret_hash",
    "UPDATE payment_accounts SET status = 'INACTIVE' RETURNING *",
    "INSERT INTO payment_accounts (organization_id, school_id, provider, external_account_id, secret_ref) SELECT organization_id, school_id, provider, external_account_id || 'x', webhook_secret_hash FROM payment_accounts",
    "SELECT a.id FROM payment_accounts a JOIN payment_accounts b ON b.webhook_secret_hash = a.webhook_secret_hash",
    "WITH x AS (SELECT webhook_secret_hash AS h FROM payment_accounts) SELECT h FROM x",
    "SELECT (SELECT webhook_secret_hash FROM payment_accounts LIMIT 1)",
]


@pytest.mark.parametrize("sql", READ_ATTEMPTS, ids=[f"read-{i}" for i in range(len(READ_ATTEMPTS))])
def test_the_application_role_cannot_read_the_hash_by_any_path(
    app_engine: Engine, tenants: Tenants, ledger: Ledger, sql: str
) -> None:
    for context in (TenantContext(tenants.org_a), TenantContext(tenants.org_a, tenants.school_a1)):
        with (
            transaction(app_engine, context=context) as conn,
            pytest.raises(DBAPIError, match="permission denied"),
        ):
            conn.execute(text(sql))


def test_the_application_role_still_reads_everything_else_and_can_rotate_the_hash(
    app_engine: Engine, admin_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    context = TenantContext(tenants.org_a, tenants.school_a1)
    new_hash = token_hash()
    with transaction(app_engine, context=context) as conn:
        row = conn.execute(
            text(
                "SELECT id, provider, status, secret_ref, external_account_id FROM payment_accounts WHERE id = :a"
            ),
            {"a": ledger.payment_account},
        ).one()
        assert row.secret_ref == "env:T5_SANDBOX_SECRET"  # noqa: S105 (a reference to the secret, not the secret)
        updated = conn.execute(
            text("UPDATE payment_accounts SET webhook_secret_hash = :h WHERE id = :a"),
            {"h": new_hash, "a": ledger.payment_account},
        )
        assert updated.rowcount == 1  # a rotation: written, never read back
        # The owner (not the application role) can see that it was written (this transaction is rolled back).
    with admin_engine.connect() as conn:
        stored: str = conn.execute(
            text("SELECT webhook_secret_hash FROM payment_accounts WHERE id = :a"),
            {"a": ledger.payment_account},
        ).scalar_one()
    assert stored != new_hash  # the rotation above was rolled back with its transaction
    assert re.fullmatch("[0-9a-f]{64}", stored)  # and the column holds a hash


def test_the_statistics_of_the_column_are_hidden_from_the_application_role(
    app_engine: Engine, admin_engine: Engine, tenants: Tenants
) -> None:
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("ANALYZE payment_accounts")
    with transaction(app_engine, context=TenantContext(tenants.org_a)) as conn:
        stats: Any = (
            conn.execute(text("SELECT attname FROM pg_stats WHERE tablename = 'payment_accounts'"))
            .scalars()
            .all()
        )
    assert "webhook_secret_hash" not in stats  # pg_stats only shows columns the role can SELECT


def test_no_function_of_the_schema_returns_or_mentions_the_hash(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        mentions: Any = (
            conn.execute(
                text(
                    "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.prosrc ILIKE '%webhook_secret_hash%'"
                )
            )
            .scalars()
            .all()
        )
    # Only the audit trigger function names columns generically, from its arguments (never this one).
    assert mentions == []


def test_the_orm_model_defers_the_column_so_a_normal_load_works_for_the_application_role(
    app_engine: Engine, tenants: Tenants, ledger: Ledger
) -> None:
    factory = sessionmaker(app_engine)
    context = TenantContext(tenants.org_a, tenants.school_a1)
    with tenant_session(factory, context) as session:
        account = session.get(PaymentAccount, ledger.payment_account)
        assert account is not None and account.secret_ref.startswith("env:")
        with pytest.raises(DBAPIError, match="permission denied"):
            _ = account.webhook_secret_hash  # touching it is the only way to ask for it: denied


def test_a_charge_cannot_use_an_account_of_another_school(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    other = make_school(conn)
    from tests.financial.support import add_transaction

    tx = add_transaction(
        conn, f, kind="CONTRIBUTION", direction="IN", amount=3000, status="PENDING_PAYMENT"
    )
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, receipt_token_hash, receipt_expires_at) VALUES (:t, :o, :s, 'PIX', :h, now() + interval '1 day')"
        ),
        {"t": tx, "o": f.org, "s": f.school, "h": token_hash()},
    )
    with (
        pytest.raises(DBAPIError, match="fk_pix_charges_payment_account_id_payment_accounts"),
        conn.begin_nested(),
    ):
        conn.execute(
            text(
                "INSERT INTO pix_charges (organization_id, school_id, transaction_id, payment_account_id, provider, txid, amount_cents, expires_at) VALUES (:o, :s, :t, :a, 'SANDBOX', :x, 3000, now() + interval '30 minutes')"
            ),
            {"o": f.org, "s": f.school, "t": tx, "a": other.account, "x": uuid.uuid4().hex},
        )
