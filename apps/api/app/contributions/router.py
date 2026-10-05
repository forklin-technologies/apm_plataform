"""POST /schools/{school_id}/contributions: the treasury enters a contribution received in cash, by
transfer or otherwise. It is born PAID, records who entered it, and has no Pix charge or receipt
link. The school of the path must be the school of the session (anything else is the same 404)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import Db, Principal, require
from app.auth.permissions import Permission
from app.auth.responses import problem_responses
from app.core.errors import ProblemError
from app.public.service import FIELD_NAMES, field_problem, not_found

router = APIRouter(prefix="/schools/{school_id}/contributions", tags=["contributions"])

CategoryKey = Literal["parent_contribution", "donation", "other_income", "apm_revenue"]


class CashContributionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_cents: int = Field(gt=0, le=1_000_000_000)
    method: Literal["CASH", "TRANSFER", "OTHER"] = "CASH"
    category_key: CategoryKey = "parent_contribution"
    guardian_name: str | None = Field(default=None, max_length=120)
    student_name: str | None = Field(default=None, max_length=120)
    class_name: str | None = Field(default=None, max_length=120)
    contributor_email: str | None = Field(default=None, max_length=254)
    contributor_phone: str | None = Field(default=None, max_length=30)


class CashContributionOut(BaseModel):
    id: UUID
    reference_code: str
    status: Literal["PAID"]
    amount_cents: int
    method: str


@router.post(
    "",
    operation_id="contributions_record_cash",
    summary="Record a contribution received in cash, by transfer or otherwise",
    status_code=201,
    response_model=CashContributionOut,
    responses=problem_responses(401, 403, 404, 409, 422),
    openapi_extra={"x-permission": Permission.CONTRIBUTIONS_RECORD_CASH.value},
)
def record_cash(
    school_id: UUID,
    body: CashContributionIn,
    principal: Annotated[Principal, Depends(require(Permission.CONTRIBUTIONS_RECORD_CASH))],
    db: Db,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CashContributionOut:
    """With an `Idempotency-Key` (a uuid per attempt) a retry or a double click answers with the
    contribution already recorded (200) instead of recording the money twice."""
    tenant = principal.tenant
    if tenant is None or tenant.school_id != school_id:
        raise not_found()
    key: UUID | None = None
    if idempotency_key is not None:
        try:
            key = UUID(idempotency_key)
        except ValueError:
            raise ProblemError(
                422,
                "validation_error",
                "Invalid request",
                errors=[{"field": "Idempotency-Key", "code": "invalid"}],
            ) from None
        replay = _recorded(db, school_id, key, body)
        if replay is not None:
            response.status_code = 200
            return replay
    errors: list[dict[str, str]] = []
    values: dict[str, str | None] = {}
    for name in FIELD_NAMES:
        raw: str | None = getattr(body, name)
        value = raw.strip() if raw else ""
        problem = field_problem(name, value) if value else None
        if problem is not None:
            errors.append({"field": name, "code": problem})
        values[name] = value or None
    if errors:
        raise ProblemError(422, "validation_error", "Invalid request", errors=errors)
    category = db.execute(
        text(
            "SELECT id FROM categories WHERE school_id = :school AND key = :key "
            "AND applies_to = 'IN' AND is_active"
        ),
        {"school": school_id, "key": body.category_key},
    ).scalar_one_or_none()
    if category is None:
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "category_key", "code": "unknown"}],
        )
    row = db.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, origin_type, created_by_user_id, occurred_at, "
            "settled_at) VALUES (:org, :school, 'CONTRIBUTION', 'IN', :amount, 'PAID', :category, "
            "'GUARDIAN', :user, now(), now()) RETURNING id, reference_code"
        ),
        {
            "org": tenant.organization_id,
            "school": school_id,
            "amount": body.amount_cents,
            "category": category,
            "user": principal.user_id,
        },
    ).one()
    db.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
            "guardian_name, student_name, class_name, contributor_email, contributor_phone, "
            "idempotency_key) VALUES (:tx, :org, :school, :method, :guardian_name, :student_name, "
            ":class_name, :contributor_email, :contributor_phone, :key)"
        ),
        {
            **values,
            "tx": row[0],
            "org": tenant.organization_id,
            "school": school_id,
            "method": body.method,
            "key": key,
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # the same key arrived twice at once: answer with the winner
        replay = None if key is None else _recorded(db, school_id, key, body)
        if replay is None:
            raise
        response.status_code = 200
        return replay
    return CashContributionOut(
        id=row[0],
        reference_code=f"APM-{int(row[1]):06d}",
        status="PAID",
        amount_cents=body.amount_cents,
        method=body.method,
    )


def _recorded(
    db: Session, school_id: UUID, key: UUID, body: CashContributionIn
) -> CashContributionOut | None:
    row = db.execute(
        text(
            "SELECT f.id, f.reference_code, f.amount_cents, c.method FROM financial_transactions f "
            "JOIN contributions c ON c.transaction_id = f.id "
            "WHERE c.school_id = :school AND c.idempotency_key = :key"
        ),
        {"school": school_id, "key": key},
    ).one_or_none()
    if row is None:
        return None
    if row[2] != body.amount_cents or row[3] != body.method:
        raise ProblemError(
            409, "idempotency_key_reused", "That Idempotency-Key was used for another contribution"
        )
    return CashContributionOut(
        id=row[0], reference_code=f"APM-{int(row[1]):06d}", status="PAID",
        amount_cents=row[2], method=row[3],
    )  # fmt: skip
