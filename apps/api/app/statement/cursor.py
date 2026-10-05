"""Opaque cursors for keyset pagination (ADR-017: `?cursor=&limit=` answers `{items, next_cursor}`).

A cursor only says "after this position" (the sort key of the last row sent). It carries nothing
about a tenant: the rows it filters are the ones row level security already lets the caller read, so
a forged cursor can skip or repeat rows of the caller's own view and nothing more.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from app.core.errors import ProblemError


def invalid_cursor() -> ProblemError:
    return ProblemError(
        422,
        "validation_error",
        "Invalid request",
        errors=[{"field": "cursor", "code": "invalid_cursor"}],
    )


def encode_cursor(**position: Any) -> str:
    """`datetime` values are written in ISO 8601 (with their offset), everything else as it is."""
    plain = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in position.items()
    }
    raw = json.dumps(plain, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(
    cursor: str, *, instants: tuple[str, ...], integers: tuple[str, ...]
) -> dict[str, Any]:
    """The position a cursor holds: the keys in `instants` as aware datetimes, the keys in
    `integers` as ints, and nothing else. Anything that does not fit is a 422 `invalid_cursor`."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(data, dict) or set(data) != set(instants) | set(integers):
            raise ValueError
        position: dict[str, Any] = {}
        for key in instants:
            moment = datetime.fromisoformat(data[key])
            if moment.tzinfo is None:
                raise ValueError
            position[key] = moment
        for key in integers:
            if type(data[key]) is not int:
                raise ValueError
            position[key] = data[key]
    except (ValueError, TypeError, binascii.Error):
        raise invalid_cursor() from None
    return position
