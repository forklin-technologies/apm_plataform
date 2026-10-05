"""Calls and queries the public-flow tests share."""

import uuid
from typing import Any

from httpx2 import Response
from sqlalchemy import Engine, text

from tests.authsupport import Api


def contribute(
    api: Api,
    slug: str,
    amount: int = 3000,
    *,
    key: uuid.UUID | str | None = None,
    **fields: Any,
) -> Response:
    headers = {} if key == "" else {"Idempotency-Key": str(key or uuid.uuid4())}
    return api.post(
        f"/api/v1/public/schools/{slug}/contributions",
        {"amount_cents": amount, **fields},
        csrf=False,
        headers=headers,
    )


def charge_url(slug: str, token: str, tail: str = "charge") -> str:
    return f"/api/v1/public/schools/{slug}/contributions/{token}/{tail}"


def txid_of(response: Response) -> str:
    """The sandbox payload is `PIX-SANDBOX:<txid>:<amount>`: the txid is read from it."""
    payload: str = response.json()["charge"]["emv_payload"]
    return payload.split(":")[1]


def row(admin_engine: Engine, sql: str, **params: Any) -> Any:
    with admin_engine.connect() as conn:
        return conn.execute(text(sql), params).one_or_none()
