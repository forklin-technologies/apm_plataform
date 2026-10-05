"""The Pix provider behind an interface, and the sandbox that stands in for the bank (ADR-011).

The API never talks to a bank directly: it asks a `PixProvider` to create a dynamic charge and,
when the provider says a payment happened (the webhook), it asks the provider AGAIN what really
happened before it believes the notification. The real provider (Banco do Brasil, mTLS, the
credentials of the school behind `secret_ref`) is M2; until then the only implementation is
`SandboxPixProvider`, a fake bank that lives in the memory of the API process. It is for
development and tests only: a restart forgets its charges, and ENV=production never uses it.
"""

import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.core.errors import ProblemError

SANDBOX = "SANDBOX"


@dataclass(frozen=True)
class ChargeTarget:
    """Whose charge it is. The sandbox keeps it to be able to simulate the webhook of a charge."""

    organization_id: uuid.UUID
    school_id: uuid.UUID
    payment_account_id: uuid.UUID


@dataclass(frozen=True)
class ProviderCharge:
    txid: str
    emv_payload: str  # the "copia e cola"; the site draws the QR code from it
    expires_at: datetime


@dataclass(frozen=True)
class ProviderPayment:
    txid: str
    status: str  # PENDING, PAID, EXPIRED or CANCELLED, as the provider sees it
    received_amount_cents: int | None
    paid_at: datetime | None
    end_to_end_id: str | None


class PixProvider(Protocol):
    name: str

    def create_charge(
        self, target: ChargeTarget, *, txid: str, amount_cents: int, expires_at: datetime
    ) -> ProviderCharge: ...

    def fetch_payment(self, target: ChargeTarget, txid: str) -> ProviderPayment | None:
        """What the provider says about a charge NOW (None: it does not know that txid)."""
        ...


@dataclass
class _Record:
    target: ChargeTarget
    amount_cents: int
    expires_at: datetime
    received_amount_cents: int | None = None
    paid_at: datetime | None = None
    end_to_end_id: str | None = None


class SandboxPixProvider:
    """A fake bank. `simulate_payment` is what the developer's "pay" button calls."""

    name = SANDBOX

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._charges: dict[str, _Record] = {}

    def create_charge(
        self, target: ChargeTarget, *, txid: str, amount_cents: int, expires_at: datetime
    ) -> ProviderCharge:
        with self._lock:
            self._charges[txid] = _Record(target, amount_cents, expires_at)
        # Not a valid BR Code on purpose: it says what it is and carries what the sandbox needs.
        return ProviderCharge(txid, f"PIX-SANDBOX:{txid}:{amount_cents}", expires_at)

    def fetch_payment(self, target: ChargeTarget, txid: str) -> ProviderPayment | None:
        with self._lock:
            record = self._charges.get(txid)
            if record is None or record.target.school_id != target.school_id:
                return None
            if record.paid_at is None:
                expired = datetime.now(UTC) >= record.expires_at
                return ProviderPayment(txid, "EXPIRED" if expired else "PENDING", None, None, None)
            return ProviderPayment(
                txid, "PAID", record.received_amount_cents, record.paid_at, record.end_to_end_id
            )

    def simulate_payment(self, txid: str, amount_cents: int | None = None) -> ChargeTarget | None:
        """Mark the charge as paid (with another amount if asked). None: no such charge."""
        with self._lock:
            record = self._charges.get(txid)
            if record is None:
                return None
            record.received_amount_cents = (
                record.amount_cents if amount_cents is None else amount_cents
            )
            record.paid_at = datetime.now(UTC)
            # The end-to-end id of a real Pix: E followed by 31 letters and digits.
            record.end_to_end_id = "E" + secrets.token_hex(16)[:31]
            return record.target

    def reset(self) -> None:
        with self._lock:
            self._charges.clear()


_SANDBOX = SandboxPixProvider()


def sandbox_provider() -> SandboxPixProvider:
    return _SANDBOX


def get_provider(name: str, env: str) -> PixProvider:
    """The provider of a payment account (its `provider` column). The sandbox is a fake bank that
    lives in the memory of one process: it is never served in production."""
    if name == SANDBOX and env != "production":
        return _SANDBOX
    raise ProblemError(501, "provider_not_configured", "This payment provider is not available yet")
