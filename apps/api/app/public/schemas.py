"""The JSON of the public contribution flow (ADR-017, ADR-018). Money in cents, snake_case."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Presence = Literal["REQUIRED", "OPTIONAL", "HIDDEN"]


class PublicSchoolOut(BaseModel):
    """What the page of a school needs. The ids of the school and of its organization stay on the
    server."""

    slug: str
    name: str
    apm_name: str
    accent_color: str
    accent_contrast_color: str
    suggested_amounts_cents: list[int]
    allow_custom_amount: bool
    min_amount_cents: int
    max_amount_cents: int
    identification: dict[str, Presence]


class ContributionIn(BaseModel):
    """What the family sends. The server revalidates everything against the settings of the school
    and stores only the fields that school asks for."""

    model_config = ConfigDict(extra="forbid")

    amount_cents: int = Field(gt=0, le=1_000_000_000)
    guardian_name: str | None = Field(default=None, max_length=120)
    student_name: str | None = Field(default=None, max_length=120)
    class_name: str | None = Field(default=None, max_length=120)
    contributor_email: str | None = Field(default=None, max_length=254)
    contributor_phone: str | None = Field(default=None, max_length=30)


class ChargeOut(BaseModel):
    status: Literal["PENDING", "PAID", "REVIEW_REQUIRED", "EXPIRED", "CANCELLED"]
    amount_cents: int
    emv_payload: str | None
    expires_at: datetime


class ContributionStateOut(BaseModel):
    status: str
    amount_cents: int
    charge: ChargeOut | None


class ContributionCreatedOut(ContributionStateOut):
    """The answer of the POST: the state, plus the opaque token that opens it again (the same key
    always yields the same token). It is a secret of the family: keep it out of logs and URLs shared
    with anybody."""

    token: str


class ReceiptOut(BaseModel):
    status: Literal["PAID"]
    reference_code: str
    amount_cents: int
    paid_at: datetime
    school_name: str
    method: str
