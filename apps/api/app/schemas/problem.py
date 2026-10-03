from pydantic import BaseModel, Field


class FieldError(BaseModel):
    field: str
    code: str


class Problem(BaseModel):
    """application/problem+json. `code` is stable; `title` and `detail` are for humans."""

    type: str = Field(examples=["urn:apm-digital:problem:invalid_credentials"])
    title: str
    status: int
    code: str
    request_id: str
    detail: str | None = None
    errors: list[FieldError] | None = None
