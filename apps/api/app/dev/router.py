"""POST /dev/sandbox/pix/{txid}/pay: the "pay" button of the Pix sandbox, for development and tests.

It makes the fake bank pay a charge (with another amount if asked, to see the review of a different
amount) and hands the notification to the same code the real webhook runs after authentication, so
the whole flow can be tried without a bank. In production it does not exist: the answer is the
ordinary 404.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import Db, Settings, public
from app.auth.responses import problem_responses
from app.db.request_context import bind_request_context
from app.db.tenant import TenantContext, bind_tenant
from app.pix.provider import sandbox_provider
from app.pix.webhook import Result, process_notification
from app.public.service import not_found

router = APIRouter(prefix="/dev/sandbox/pix", tags=["dev"], dependencies=[Depends(public())])


class SandboxPayIn(BaseModel):
    amount_cents: int | None = Field(default=None, gt=0, le=1_000_000_000)


class SandboxPayOut(BaseModel):
    status: Result


@router.post(
    "/{txid}/pay",
    operation_id="dev_sandbox_pix_pay",
    summary="Pay a sandbox Pix charge (development and tests only)",
    response_model=SandboxPayOut,
    responses=problem_responses(404, 422),
)
def pay(txid: str, db: Db, settings: Settings, body: SandboxPayIn | None = None) -> SandboxPayOut:
    if settings.env not in ("development", "test"):
        raise not_found()
    target = sandbox_provider().simulate_payment(txid, None if body is None else body.amount_cents)
    if target is None:
        raise not_found()
    bind_request_context(db, actor_type="SYSTEM")
    bind_tenant(db, TenantContext(target.organization_id, target.school_id))
    result = process_notification(
        db,
        provider="SANDBOX",
        target=target,
        event_id=f"dev-{uuid.uuid4().hex}",
        txid=txid,
        payload={"source": "dev-sandbox-pay", "txid": txid},
    )
    db.commit()
    return SandboxPayOut(status=result)
