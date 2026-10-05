"""The webhook of the Pix provider (ADR-011, ADR-018): the only way a Pix contribution becomes PAID.

A notification is treated as an untrusted claim. It is authenticated by a secret in the header
`X-Webhook-Secret` (never in the URL; only its SHA-256 is compared with the one of the payment
account), recorded once by its event id (a repeated delivery is dropped), and then the provider is
ASKED what really happened before anything changes: the amount is compared with the expected one,
and a different amount sends the contribution to the management instead of counting it.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.deps import Db, Settings, public
from app.auth.ratelimit import attempt_keys, blocked_for, client_ip, record_attempt
from app.auth.responses import problem_responses
from app.core.errors import ProblemError
from app.db.request_context import bind_request_context
from app.db.tenant import TenantContext, bind_tenant
from app.pix.provider import ChargeTarget, get_provider

Result = Literal["PAID", "REVIEW_REQUIRED", "NOT_CONFIRMED", "IGNORED", "DUPLICATE"]
DIVERGENCE = "the amount received differs from the amount expected"


MAX_PAYLOAD_BYTES = 20_000


def process_notification(
    db: Session,
    *,
    env: str,
    provider: str,
    target: ChargeTarget,
    event_id: str,
    txid: str,
    payload: dict[str, Any],
) -> Result:
    """Everything after the notification was authenticated and the school bound (caller commits)."""
    event = db.execute(
        text(
            "INSERT INTO webhook_events (organization_id, school_id, provider, idempotency_key, "
            "raw_payload, signature_valid) VALUES (:org, :school, :provider, :key, "
            "CAST(:payload AS jsonb), true) "
            "ON CONFLICT (school_id, provider, idempotency_key) DO NOTHING RETURNING id"
        ),
        {
            "org": target.organization_id,
            "school": target.school_id,
            "provider": provider,
            "key": event_id,
            "payload": json.dumps(payload, default=str),
        },
    ).scalar_one_or_none()
    if event is None:
        # Seen before. If that attempt reached a conclusion it is a plain duplicate; if it did not
        # (the provider had not confirmed yet), the same event id is processed again, otherwise a
        # retry of the provider could never settle the payment.
        previous = db.execute(
            text(
                "SELECT id, processed_at FROM webhook_events WHERE school_id = :school "
                "AND provider = :provider AND idempotency_key = :key FOR UPDATE"
            ),
            {"school": target.school_id, "provider": provider, "key": event_id},
        ).one_or_none()
        if previous is None or previous[1] is not None:
            return "DUPLICATE"
        event = previous[0]

    # The charge must belong to the account that authenticated this notification.
    charge = db.execute(
        text(
            "SELECT id, transaction_id, status, amount_cents FROM pix_charges "
            "WHERE school_id = :school AND provider = :provider AND txid = :txid "
            "AND payment_account_id = :account FOR UPDATE"
        ),
        {
            "school": target.school_id,
            "provider": provider,
            "txid": txid,
            "account": target.payment_account_id,
        },
    ).one_or_none()
    if charge is None or charge[2] != "PENDING":
        return _finish(db, event, "IGNORED", "no pending charge for that txid")

    payment = get_provider(provider, env).fetch_payment(target, txid)
    if (
        payment is None
        or payment.status != "PAID"
        or payment.received_amount_cents is None
        or payment.paid_at is None
        or payment.end_to_end_id is None
    ):
        # Not conclusive: the event stays open so that a retry of the provider can settle it.
        return _finish(
            db, event, "NOT_CONFIRMED", "the provider does not confirm a payment", processed=False
        )

    paid_at = min(payment.paid_at, datetime.now(UTC))
    expected = int(charge[3])
    scope = {
        "id": charge[0],
        "tx": charge[1],
        "e2e": payment.end_to_end_id,
        "paid_at": paid_at,
        "received": payment.received_amount_cents,
    }
    if payment.received_amount_cents == expected:
        db.execute(
            text(
                "UPDATE pix_charges SET status = 'PAID', end_to_end_id = :e2e, paid_at = :paid_at, "
                "received_amount_cents = :received WHERE id = :id AND status = 'PENDING'"
            ),
            scope,
        )
        db.execute(
            text(
                "UPDATE financial_transactions SET status = 'PAID', settled_at = :paid_at "
                "WHERE id = :tx AND status = 'PENDING_PAYMENT'"
            ),
            scope,
        )
        return _finish(db, event, "PAID", None)
    db.execute(
        text(
            "UPDATE pix_charges SET status = 'REVIEW_REQUIRED', end_to_end_id = :e2e, "
            "paid_at = :paid_at, received_amount_cents = :received, divergence_reason = :why "
            "WHERE id = :id AND status = 'PENDING'"
        ),
        {**scope, "why": DIVERGENCE},
    )
    db.execute(
        text(
            "UPDATE financial_transactions SET status = 'REVIEW_REQUIRED' "
            "WHERE id = :tx AND status = 'PENDING_PAYMENT'"
        ),
        scope,
    )
    return _finish(db, event, "REVIEW_REQUIRED", None)


def _finish(
    db: Session, event: Any, result: Result, note: str | None, *, processed: bool = True
) -> Result:
    db.execute(
        text(
            "UPDATE webhook_events SET "
            "processed_at = CASE WHEN :processed THEN now() ELSE processed_at END, "
            "attempts = attempts + 1, processing_error = :note WHERE id = :id"
        ),
        {"id": event, "note": note, "processed": processed},
    )
    return result


# --- the routes -----------------------------------------------------------------------------------


class WebhookIn(BaseModel):
    """What the sandbox sends. The raw body is kept as received (extra fields included)."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=200)
    txid: str = Field(pattern=r"^[A-Za-z0-9]{26,35}$")


class WebhookOut(BaseModel):
    status: Result


router = APIRouter(prefix="/webhooks/pix", tags=["webhooks"], dependencies=[Depends(public())])


def _receive(
    request: Request, db: Session, settings: Any, provider: str, secret: str | None, body: WebhookIn
) -> WebhookOut:
    if provider == "SANDBOX" and settings.env == "production":
        raise ProblemError(404, "not_found", "Not found")  # the fake bank does not exist there
    bind_request_context(db, actor_type="SYSTEM")
    ip = client_ip(request.client.host if request.client else None)
    keys = attempt_keys(settings.auth_secret, ip, ip)
    wait = blocked_for(db, "webhook", keys)
    if wait:
        db.commit()
        raise ProblemError(
            429,
            "rate_limited",
            "Too many attempts, try again later",
            headers={"Retry-After": str(wait)},
        )
    row = None
    if secret and len(secret) <= 200:
        row = db.execute(
            text("SELECT * FROM public.resolve_webhook_target(:provider, :hash)"),
            {"provider": provider, "hash": hashlib.sha256(secret.encode()).hexdigest()},
        ).one_or_none()
    if row is None:
        record_attempt(db, "webhook", keys, succeeded=False)
        db.commit()
        raise ProblemError(401, "webhook_unauthorized", "The webhook is not authorized")
    target = ChargeTarget(row[0], row[1], row[2])
    payload = body.model_dump()
    if len(json.dumps(payload, default=str)) > MAX_PAYLOAD_BYTES:
        raise ProblemError(413, "payload_too_large", "The notification is too large")
    bind_tenant(db, TenantContext(target.organization_id, target.school_id))
    result = process_notification(
        db,
        env=settings.env,
        provider=provider,
        target=target,
        event_id=body.event_id,
        txid=body.txid,
        payload=payload,
    )
    db.commit()
    return WebhookOut(status=result)


@router.post(
    "/sandbox",
    operation_id="webhook_pix_sandbox",
    summary="Notification of the sandbox Pix provider",
    response_model=WebhookOut,
    responses=problem_responses(
        401, 404, 422, 429, 501, extra={413: {"description": "payload_too_large"}}
    ),
)
def sandbox(
    request: Request,
    body: WebhookIn,
    db: Db,
    settings: Settings,
    secret: Annotated[str | None, Header(alias="X-Webhook-Secret")] = None,
) -> WebhookOut:
    return _receive(request, db, settings, "SANDBOX", secret, body)


@router.post(
    "/bb",
    operation_id="webhook_pix_bb",
    summary="Notification of Banco do Brasil (not available until M2)",
    response_model=WebhookOut,
    responses=problem_responses(
        401, 404, 422, 429, 501, extra={413: {"description": "payload_too_large"}}
    ),
)
def banco_do_brasil(
    request: Request,
    body: WebhookIn,
    db: Db,
    settings: Settings,
    secret: Annotated[str | None, Header(alias="X-Webhook-Secret")] = None,
) -> WebhookOut:
    return _receive(request, db, settings, "BB", secret, body)
