"""The sandbox provider, the secret store and the small pure pieces of the public flow."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import ProblemError
from app.dev.router import pay
from app.pix.provider import ChargeTarget, SandboxPixProvider, get_provider, sandbox_provider
from app.pix.secrets import EnvSecretStore, SecretNotFound
from app.public.schemas import ContributionIn
from app.public.service import PublicSchool, clean_input, receipt_token, token_hash
from tests.helpers import make_settings

TARGET = ChargeTarget(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
LATER = datetime.now(UTC) + timedelta(minutes=30)


def test_a_sandbox_charge_is_pending_until_it_is_paid() -> None:
    provider = SandboxPixProvider()
    created = provider.create_charge(TARGET, txid="a" * 32, amount_cents=3000, expires_at=LATER)
    assert created.emv_payload == f"PIX-SANDBOX:{'a' * 32}:3000"
    pending = provider.fetch_payment(TARGET, "a" * 32)
    assert pending is not None and pending.status == "PENDING" and pending.paid_at is None
    assert provider.simulate_payment("a" * 32) == TARGET
    paid = provider.fetch_payment(TARGET, "a" * 32)
    assert paid is not None and paid.status == "PAID" and paid.received_amount_cents == 3000
    assert paid.end_to_end_id is not None and len(paid.end_to_end_id) == 32


def test_a_sandbox_payment_can_have_another_amount_and_is_private_to_its_school() -> None:
    provider = SandboxPixProvider()
    provider.create_charge(TARGET, txid="b" * 32, amount_cents=3000, expires_at=LATER)
    provider.simulate_payment("b" * 32, 99900)
    paid = provider.fetch_payment(TARGET, "b" * 32)
    assert paid is not None and paid.received_amount_cents == 99900
    foreign = ChargeTarget(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert provider.fetch_payment(foreign, "b" * 32) is None  # another school never sees it
    assert provider.fetch_payment(TARGET, "c" * 32) is None
    assert provider.simulate_payment("c" * 32) is None
    provider.reset()
    assert provider.fetch_payment(TARGET, "b" * 32) is None


def test_an_expired_sandbox_charge_says_so() -> None:
    provider = SandboxPixProvider()
    past = datetime.now(UTC) - timedelta(minutes=1)
    provider.create_charge(TARGET, txid="d" * 32, amount_cents=1000, expires_at=past)
    status = provider.fetch_payment(TARGET, "d" * 32)
    assert status is not None and status.status == "EXPIRED"


def test_only_the_sandbox_exists_for_now_and_never_in_production() -> None:
    assert get_provider("SANDBOX", "development") is sandbox_provider()
    for name, env in (("BB", "development"), ("SANDBOX", "production")):
        with pytest.raises(ProblemError) as caught:
            get_provider(name, env)
        assert caught.value.status == 501 and caught.value.code == "provider_not_configured"


def test_the_secret_store_resolves_env_references_and_says_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIX_TEST_SECRET", "s3cret-value")
    store = EnvSecretStore()
    assert store.resolve("env:PIX_TEST_SECRET") == "s3cret-value"
    for ref in ("env:PIX_MISSING_ONE", "vault:kv/x", "env:lower", "file:/etc/passwd", ""):
        with pytest.raises(SecretNotFound) as caught:
            store.resolve(ref)
        assert "s3cret-value" not in str(caught.value)


def test_the_dev_pay_route_does_not_exist_in_production() -> None:
    settings: Any = SimpleNamespace(env="production")
    with pytest.raises(ProblemError) as caught:
        pay("e" * 32, db=None, settings=settings)  # type: ignore[arg-type]
    assert caught.value.status == 404 and caught.value.code == "not_found"


def test_the_token_of_a_contribution_comes_from_its_key_and_only_the_hash_is_stored() -> None:
    settings = make_settings(env="test")
    school, key = uuid.uuid4(), uuid.uuid4()
    token = receipt_token(settings, school, key)
    assert token == receipt_token(settings, school, key)
    assert token != receipt_token(settings, school, uuid.uuid4())
    assert token != receipt_token(settings, uuid.uuid4(), key)
    assert len(token) == 43 and len(token_hash(token)) == 64 and token_hash(token) != token


def _school(**kwargs: Any) -> PublicSchool:
    values: dict[str, Any] = {
        "organization_id": uuid.uuid4(), "school_id": uuid.uuid4(), "slug": "s", "name": "S",
        "accent_color": "#000000", "accent_contrast_color": "#ffffff",
        "suggested_amounts_cents": [2000, 3000], "allow_custom_amount": True,
        "min_amount_cents": 1000, "max_amount_cents": 500000,
        "required_fields": [], "optional_fields": ["guardian_name", "contributor_email"],
    }  # fmt: skip
    return PublicSchool(**{**values, **kwargs})


def test_only_the_fields_the_school_asks_for_are_kept_and_trimmed() -> None:
    data = ContributionIn(
        amount_cents=3000, guardian_name="  Ana  ", student_name="Davi", contributor_phone="99"
    )
    assert clean_input(_school(), data) == {"guardian_name": "Ana"}
    assert _school().identification()["student_name"] == "HIDDEN"
    assert (
        _school(required_fields=["guardian_name"]).identification()["guardian_name"] == "REQUIRED"
    )
