"""Fixtures of the public contribution flow: a COMMITTED school with an ACTIVE sandbox account whose
webhook secret the test knows (the API runs in its own connection and must see it)."""

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, text

from app.pix.provider import sandbox_provider
from tests.financial.support import Fresh, drop_school, make_school

WEBHOOK_SECRET = "sandbox-webhook-secret-for-tests-0123456789"  # noqa: S105  (fake, tests only)


@dataclass(frozen=True)
class PublicSchool:
    fresh: Fresh
    slug: str
    name: str


@pytest.fixture(autouse=True)
def _clean_sandbox() -> Iterator[None]:
    sandbox_provider().reset()
    yield
    sandbox_provider().reset()


def _make(admin_engine: Engine, secret: str = WEBHOOK_SECRET) -> PublicSchool:
    with admin_engine.begin() as conn:
        fresh = make_school(conn)
        conn.execute(
            text("UPDATE payment_accounts SET webhook_secret_hash = :h WHERE id = :a"),
            {"h": hashlib.sha256(secret.encode()).hexdigest(), "a": fresh.account},
        )
        row = conn.execute(
            text("SELECT slug, name FROM schools WHERE id = :s"), {"s": fresh.school}
        ).one()
    return PublicSchool(fresh, row[0], row[1])


@pytest.fixture
def school(admin_engine: Engine) -> Iterator[PublicSchool]:
    made = _make(admin_engine)
    yield made
    with admin_engine.begin() as conn:
        drop_school(conn, made.fresh)


@pytest.fixture
def other_school(admin_engine: Engine) -> Iterator[PublicSchool]:
    # A different secret: the hash of a webhook secret is unique across all schools.
    made = _make(admin_engine, secret=uuid.uuid4().hex)
    yield made
    with admin_engine.begin() as conn:
        drop_school(conn, made.fresh)
