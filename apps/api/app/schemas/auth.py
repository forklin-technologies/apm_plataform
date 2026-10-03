import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
Role = Literal["organization_admin", "school_admin", "treasurer", "staff", "viewer"]


class Strict(BaseModel):
    """Requests refuse unknown fields: a role, tenant or id smuggled in the body is an error."""

    model_config = ConfigDict(extra="forbid")


def _storable(value: str) -> str:
    """Text a database can store and a driver can encode: PostgreSQL refuses a NUL byte in text and
    the driver cannot encode a lone surrogate, and either would otherwise surface as a 500. The
    message is fixed: it never quotes the value (it may be a password)."""
    if "\x00" in value:
        raise ValueError("contains a NUL byte")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("contains an invalid character") from None
    return value


# EVERY text field of an authentication request goes through `_storable`, whether it reaches the
# database (e-mail, name) or only a hash (passwords): one rule, no field to forget.
Text = Annotated[str, AfterValidator(_storable)]
PasswordText = Annotated[str, Field(max_length=256), AfterValidator(_storable)]


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        raise ValueError("not an e-mail address")
    return value


class LoginRequest(Strict):
    email: Text
    password: PasswordText

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return _email(value)


class ContextRequest(Strict):
    membership_id: uuid.UUID


class PasswordRequest(Strict):
    current_password: PasswordText
    new_password: PasswordText


class InvitationRequest(Strict):
    email: Text
    role: Role
    school_id: uuid.UUID | None = None

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return _email(value)


class AcceptInvitationRequest(Strict):
    token: Annotated[str, Field(max_length=200), AfterValidator(_storable)]
    full_name: Annotated[str, Field(max_length=200), AfterValidator(_storable)] | None = None
    password: PasswordText | None = None


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
