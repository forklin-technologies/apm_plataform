import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
Role = Literal["organization_admin", "school_admin", "treasurer", "staff", "viewer"]


class Strict(BaseModel):
    """Requests refuse unknown fields: a role, tenant or id smuggled in the body is an error."""

    model_config = ConfigDict(extra="forbid")


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        raise ValueError("not an e-mail address")
    return value


class LoginRequest(Strict):
    email: str
    password: str = Field(max_length=256)

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return _email(value)


class ContextRequest(Strict):
    membership_id: uuid.UUID


class PasswordRequest(Strict):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class InvitationRequest(Strict):
    email: str
    role: Role
    school_id: uuid.UUID | None = None

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return _email(value)


class AcceptInvitationRequest(Strict):
    token: str = Field(max_length=200)
    full_name: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=256)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str


class OrganizationRef(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class SchoolRef(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class MembershipOut(BaseModel):
    membership_id: uuid.UUID
    organization: OrganizationRef
    school: SchoolRef | None = None
    role: Role


class ActiveMembershipOut(MembershipOut):
    permissions: list[str]


class SessionInfo(BaseModel):
    expires_at: datetime = Field(description="Absolute end of the session.")
    idle_timeout_seconds: int


class SessionResponse(BaseModel):
    """Who is logged in, where they act, and the CSRF token to send in X-CSRF-Token."""

    user: UserOut
    active_membership: ActiveMembershipOut | None
    memberships: list[MembershipOut]
    csrf_token: str
    session: SessionInfo


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    school_id: uuid.UUID | None
    expires_at: datetime


class AcceptedInvitationOut(BaseModel):
    user_id: uuid.UUID
    membership: MembershipOut
