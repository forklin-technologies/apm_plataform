"""The routes of the public contribution flow (no login): /api/v1/public/schools/{slug}/...

The school comes from the slug of the URL (ADR-010), never from a parameter the client can pick.
Every response carries `Cache-Control: private, no-store` and `Referrer-Policy: no-referrer`.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from app.auth.deps import Db, Settings, public
from app.auth.ratelimit import client_ip
from app.auth.responses import problem_responses
from app.core.config import ApiSettings
from app.core.errors import ProblemError
from app.db.request_context import bind_request_context
from app.db.tenant import TenantContext, bind_tenant
from app.public import service
from app.public.schemas import (
    ChargeOut,
    ContributionCreatedOut,
    ContributionIn,
    ContributionStateOut,
    PublicSchoolOut,
    ReceiptOut,
)

NO_STORE = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}


def _private(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(
    prefix="/public/schools/{slug}",
    tags=["public"],
    dependencies=[Depends(public()), Depends(_private)],
)


def _ip(request: Request) -> str:
    return client_ip(request.client.host if request.client else None)


def _bind(db: Session, school: service.PublicSchool) -> None:
    bind_tenant(db, TenantContext(school.organization_id, school.school_id))


def _state_out(state: service.ContributionState) -> ContributionStateOut:
    return ContributionStateOut(
        status=state.status,
        amount_cents=state.amount_cents,
        charge=None
        if state.charge is None
        else ChargeOut(
            status=state.charge.status,  # type: ignore[arg-type]
            amount_cents=state.charge.amount_cents,
            emv_payload=state.charge.emv_payload,
            expires_at=state.charge.expires_at,
        ),
    )


def _open_existing(
    db: Session, settings: ApiSettings, request: Request, slug: str, token: str
) -> tuple[service.PublicSchool, service.ContributionRef]:
    """The school and the contribution a token opens, or the one 404 (and a count against the
    client address)."""
    bind_request_context(db, actor_type="PUBLIC")
    keys = service.guard_token_guessing(db, settings, _ip(request))
    school = service.resolve_school(db, slug)
    ref = service.resolve_contribution(db, token)
    if school is None or ref is None or ref.school_id != school.school_id:
        service.fail_lookup(db, keys)
    _bind(db, school)
    return school, ref


@router.get(
    "",
    operation_id="public_school",
    summary="The public page of a school: suggested amounts, limits and the form fields",
    response_model=PublicSchoolOut,
    responses=problem_responses(404, 422),
)
def get_school(slug: str, db: Db) -> PublicSchoolOut:
    bind_request_context(db, actor_type="PUBLIC")
    school = service.resolve_school(db, slug)
    if school is None:
        raise service.not_found()
    return PublicSchoolOut(
        slug=school.slug,
        name=school.name,
        apm_name=f"APM da {school.name}",
        accent_color=school.accent_color,
        accent_contrast_color=school.accent_contrast_color,
        suggested_amounts_cents=school.suggested_amounts_cents,
        allow_custom_amount=school.allow_custom_amount,
        min_amount_cents=school.min_amount_cents,
        max_amount_cents=school.max_amount_cents,
        identification=school.identification(),  # type: ignore[arg-type]
    )


@router.post(
    "/contributions",
    operation_id="public_contribution_create",
    summary="Start a contribution: creates it and its Pix charge",
    status_code=201,
    response_model=ContributionCreatedOut,
    responses=problem_responses(404, 409, 422, 429, 501),
)
def create_contribution(
    slug: str,
    body: ContributionIn,
    request: Request,
    db: Db,
    settings: Settings,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ContributionCreatedOut:
    """The server revalidates the amount and the fields against the settings of the school. The
    `Idempotency-Key` (a uuid made by the page for each attempt) makes a repeated POST answer with
    the same contribution instead of creating another."""
    bind_request_context(db, actor_type="PUBLIC")
    try:
        key = uuid.UUID(idempotency_key or "")
    except ValueError:
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "Idempotency-Key", "code": "required"}],
        ) from None
    service.guard_creation(db, settings, _ip(request))
    school = service.resolve_school(db, slug)
    if school is None:
        db.commit()  # the attempt still counts against the client address
        raise service.not_found()
    _bind(db, school)
    token, state = service.create_contribution(db, settings, school, key, body)
    db.commit()
    return ContributionCreatedOut(token=token, **_state_out(state).model_dump())


@router.get(
    "/contributions/{token}/charge",
    operation_id="public_contribution_charge",
    summary="The state of a contribution and of its latest charge (the page polls this)",
    response_model=ContributionStateOut,
    responses=problem_responses(404, 422, 429),
)
def get_charge(
    slug: str, token: str, request: Request, db: Db, settings: Settings
) -> ContributionStateOut:
    _school, ref = _open_existing(db, settings, request, slug, token)
    state = service.read_state(db, ref.transaction_id)
    db.commit()  # the lazy expiry of an old charge
    return _state_out(state)


@router.post(
    "/contributions/{token}/charges",
    operation_id="public_contribution_charge_renew",
    summary="A new charge when the previous one expired",
    response_model=ContributionStateOut,
    responses=problem_responses(404, 409, 422, 429, 501),
)
def renew_charge(
    slug: str, token: str, request: Request, db: Db, settings: Settings
) -> ContributionStateOut:
    school, ref = _open_existing(db, settings, request, slug, token)
    state = service.renew_charge(db, school, ref)
    db.commit()
    return _state_out(state)


@router.get(
    "/contributions/{token}/receipt",
    operation_id="public_contribution_receipt",
    summary="The receipt of a PAID contribution (409 while it is not paid)",
    response_model=ReceiptOut,
    responses=problem_responses(404, 409, 422, 429),
)
def get_receipt(slug: str, token: str, request: Request, db: Db, settings: Settings) -> ReceiptOut:
    school, ref = _open_existing(db, settings, request, slug, token)
    return ReceiptOut(**service.read_receipt(db, school, ref))
