"""Opaque cursors for keyset pagination (ADR-017: `?cursor=&limit=` answers `{items, next_cursor}`).

A cursor only says "after this position" (the sort key of the last row sent). It carries nothing
about a tenant: the rows it filters are the ones row level security already lets the caller read, so
a forged cursor can skip or repeat rows of the caller's own view and nothing more.
"""

import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.errors import ProblemError


def invalid_cursor() -> ProblemError:
    return ProblemError(
        422,
        "validation_error",
        "Invalid request",
        errors=[{"field": "cursor", "code": "invalid_cursor"}],
    )


def encode_cursor(**position: Any) -> str:
    """`datetime` values are written in ISO 8601 (with their offset), uuids as text."""
    plain = {
        key: value.isoformat()
        if isinstance(value, datetime)
        else str(value)
        if isinstance(value, UUID)
        else value
        for key, value in position.items()
    }
    raw = json.dumps(plain, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


BIGINT_MAX = 2**63 - 1
# Every instant of the system is a recent one; a cursor outside this range was not made by us, and
# keeping it narrow keeps a forged value away from the limits of the database types.
FIRST_YEAR, LAST_YEAR = 1970, 2200
MAX_OFFSET = timedelta(hours=15, minutes=59)


def _instant(value: object) -> datetime:
    """An ISO 8601 instant WITH an offset, within the range above."""
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("instant")
    moment = datetime.fromisoformat(value)
    offset = moment.utcoffset()
    if offset is None or abs(offset) > MAX_OFFSET or not FIRST_YEAR <= moment.year <= LAST_YEAR:
        raise ValueError("instant")
    return moment


def _integer(value: object) -> int:
    """A whole number that fits a bigint (and is not a boolean, which is an int in Python)."""
    if type(value) is not int or not 0 <= value <= BIGINT_MAX:
        raise ValueError("integer")
    return value


def _uuid(value: object) -> UUID:
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("uuid")
    return UUID(value)


def decode_cursor(
    cursor: str,
    *,
    instants: tuple[str, ...],
    integers: tuple[str, ...] = (),
    uuids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The position a cursor holds: the keys in `instants` as aware datetimes, the keys in
    `integers` as ints, the keys in `uuids` as UUIDs, and nothing else. Every field is checked for
    its type AND its range, and anything that does not fit is a 422 `invalid_cursor`: a forged
    cursor never reaches the database and never becomes a 500."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(data, dict) or set(data) != {*instants, *integers, *uuids}:
            raise ValueError("shape")
        position: dict[str, Any] = {}
        position.update({key: _instant(data[key]) for key in instants})
        position.update({key: _integer(data[key]) for key in integers})
        position.update({key: _uuid(data[key]) for key in uuids})
    except (ValueError, TypeError, OverflowError, RecursionError, binascii.Error):
        raise invalid_cursor() from None
    return position
