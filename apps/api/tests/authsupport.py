"""Support for the API tests: a world of two organizations, users made on demand, and a client that
behaves like the web app (an Origin on every write, the cookies, the CSRF header).

Everything is created through the admin connection (a superuser in development, so RLS is bypassed
to set the scene) and removed at the end of the session by the markers each row carries: the users'
e-mails start with `t4-<suffix>-`, the organizations are the world's own, the attempt counters are
found by the (HMAC of the) client addresses the tests used.
"""

import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.auth.passwords import hash_password
from app.auth.tokens import keyed_digest
from app.core.config import ApiSettings
from app.main import create_app
from tests.dbsupport import purge_financial
from tests.helpers import TEST_AUTH_SECRET, TEST_ORIGIN, make_settings

PASSWORD = "correct horse battery staple"  # noqa: S105  (fake, tests only)
OTHER_PASSWORD = "another long passphrase 42"  # noqa: S105
SECURE_ORIGIN = "https://app.example.test"
# Hashed once: every test user shares it, so creating a user costs nothing.
PASSWORD_HASH = hash_password(PASSWORD)


@dataclass(frozen=True)
class OutboxMessage:
    path: Path
    kind: str
    to: str
    subject: str
    body: str
    file_mode: int

    @property
    def token(self) -> str:
        """The invitation token inside the accept link."""
        found = re.search(r"token=([A-Za-z0-9_-]{43})", self.body)
        assert found is not None, "no token in the message"
        return found.group(1)


def _kind_of(path: Path) -> str:
    found = re.fullmatch(r"\d{8}T\d{6}-(.+)-[0-9a-f]{8}\.txt", path.name)
    assert found is not None, f"unexpected outbox file name: {path.name}"
    return found.group(1)


def read_outbox(directory: Path) -> list[OutboxMessage]:
    """What the file outbox holds, oldest first."""
    if not directory.exists():
        return []
    messages = []
    for path in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime_ns):
        header, _, body = path.read_text(encoding="utf-8").partition("\n\n")
        fields = dict(line.split(": ", 1) for line in header.splitlines())
        messages.append(
            OutboxMessage(
                path=path,
                kind=_kind_of(path),
                to=fields["To"],
                subject=fields["Subject"],
                body=body,
                file_mode=path.stat().st_mode & 0o777,
            )
        )
    return messages


@dataclass(frozen=True)
class World:
    """Two organizations; A has two schools, B one. Users are made per test (see `UserFactory`)."""

    org_a: uuid.UUID
    org_b: uuid.UUID
    school_a1: uuid.UUID
    school_a2: uuid.UUID
    school_b1: uuid.UUID
    suffix: str
    used_ips: list[str] = field(default_factory=list)

    def email(self, label: str) -> str:
        return f"t4-{self.suffix}-{label}-{uuid.uuid4().hex[:6]}@example.test"


@dataclass(frozen=True)
class TestUser:
    __test__ = False  # not a test class, whatever pytest thinks of the name

    id: uuid.UUID
    email: str
    membership_ids: tuple[uuid.UUID, ...]
    password: str | None = PASSWORD


def create_world(connection: Any) -> World:
    suffix = uuid.uuid4().hex[:8]
    ids = {name: uuid.uuid4() for name in ("org_a", "org_b", "school_a1", "school_a2", "school_b1")}
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, slug) "
            "VALUES (:a, 'Org A', :sa), (:b, 'Org B', :sb)"
        ),
        {"a": ids["org_a"], "b": ids["org_b"], "sa": f"t4-a-{suffix}", "sb": f"t4-b-{suffix}"},
    )
    connection.execute(
        text(
            "INSERT INTO schools (id, organization_id, name, slug) VALUES "
            "(:a1, :a, 'School A1', :s1), (:a2, :a, 'School A2', :s2), (:b1, :b, 'School B1', :s3)"
        ),
        {
            "a": ids["org_a"],
            "b": ids["org_b"],
            "a1": ids["school_a1"],
            "a2": ids["school_a2"],
            "b1": ids["school_b1"],
            "s1": f"t4-a1-{suffix}",
            "s2": f"t4-a2-{suffix}",
            "s3": f"t4-b1-{suffix}",
        },
    )
    return World(
        org_a=ids["org_a"],
        org_b=ids["org_b"],
        school_a1=ids["school_a1"],
        school_a2=ids["school_a2"],
        school_b1=ids["school_b1"],
        suffix=suffix,
    )


def delete_world(connection: Any, world: World, secret: SecretStr) -> None:
    pattern = f"t4-{world.suffix}-%"
    orgs = [world.org_a, world.org_b]
    purge_financial(connection, orgs)  # every school is born with its settings row (0007)
    connection.execute(
        text("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email LIKE :p)"),
        {"p": pattern},
    )
    connection.execute(text("DELETE FROM invitations WHERE organization_id = ANY(:o)"), {"o": orgs})
    connection.execute(text("DELETE FROM memberships WHERE organization_id = ANY(:o)"), {"o": orgs})
    connection.execute(text("DELETE FROM users WHERE email LIKE :p"), {"p": pattern})
    connection.execute(text("DELETE FROM schools WHERE organization_id = ANY(:o)"), {"o": orgs})
    connection.execute(text("DELETE FROM organizations WHERE id = ANY(:o)"), {"o": orgs})
    ip_digests = [keyed_digest(secret, "attempt-ip", ip) for ip in world.used_ips]
    connection.execute(
        text("DELETE FROM login_attempts WHERE ip_hmac = ANY(:ips)"), {"ips": ip_digests}
    )


class UserFactory:
    """`make(...)` inserts a user with its memberships and returns what a test needs to log in."""

    def __init__(self, engine: Engine, world: World) -> None:
        self.engine = engine
        self.world = world

    def make(
        self,
        role: str | None = "staff",
        *,
        org: str = "a",
        school: str | None = "1",
        extra: tuple[tuple[str, str, str | None], ...] = (),
        password: str | None = PASSWORD,
        active: bool = True,
        label: str | None = None,
    ) -> TestUser:
        """`school` is "1"/"2" (of the organization) or None (the whole organization); `extra` adds
        more memberships as (role, org, school). `role=None` makes a user with no membership."""
        world = self.world
        email = world.email(label or role or "nobody")
        user_id = uuid.uuid4()
        memberships = [] if role is None else [(role, org, school), *extra]
        membership_ids: list[uuid.UUID] = []
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, full_name, password_hash, is_active) "
                    "VALUES (:id, :email, :name, :hash, :active)"
                ),
                {
                    "id": user_id,
                    "email": email,
                    "name": f"Test {label or role or 'nobody'}",
                    "hash": PASSWORD_HASH if password == PASSWORD else None,
                    "active": active,
                },
            )
            for member_role, member_org, member_school in memberships:
                membership_id = uuid.uuid4()
                membership_ids.append(membership_id)
                connection.execute(
                    text(
                        "INSERT INTO memberships (id, user_id, organization_id, school_id, role, "
                        "status) VALUES (:id, :user, :org, :school, :role, 'active')"
                    ),
                    {
                        "id": membership_id,
                        "user": user_id,
                        "org": world.org_a if member_org == "a" else world.org_b,
                        "school": None
                        if member_school is None
                        else self._school_id(member_org, member_school),
                        "role": member_role,
                    },
                )
        return TestUser(user_id, email, tuple(membership_ids), password)

    def _school_id(self, org: str, school: str) -> uuid.UUID:
        if org == "b":
            return self.world.school_b1
        return self.world.school_a1 if school == "1" else self.world.school_a2

    def set_membership_status(self, membership_id: uuid.UUID, status: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE memberships SET status = :s WHERE id = :id"),
                {"s": status, "id": membership_id},
            )

    def set_active(self, user_id: uuid.UUID, active: bool) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET is_active = :a WHERE id = :id"), {"a": active, "id": user_id}
            )


class Api:
    """The web app's view of the API: it keeps the cookies and sends Origin and the CSRF header."""

    def __init__(self, client: TestClient, settings: ApiSettings, ip: str) -> None:
        self.client = client
        self.settings = settings
        self.ip = ip
        self.origin = settings.allowed_origins[0]

    # --- what the browser holds ---------------------------------------------------------------
    @property
    def session_token(self) -> str | None:
        return self.client.cookies.get(self.settings.session_cookie_name)

    @property
    def csrf(self) -> str | None:
        return self.client.cookies.get(self.settings.csrf_cookie_name)

    def forget_cookies(self) -> None:
        self.client.cookies.clear()

    def set_session_cookie(self, token: str) -> None:
        """Plant a cookie on the browser as the page's own host, replacing the one it had."""
        name = self.settings.session_cookie_name
        existing = [cookie for cookie in self.client.cookies.jar if cookie.name == name]
        for cookie in existing:
            cookie.value = token
        if not existing:
            host = urlsplit(self.origin).hostname or "localhost"
            # What the cookie jar calls a host-only cookie for a name without dots.
            domain = host if "." in host else f"{host}.local"
            self.client.cookies.set(name, token, domain=domain, path="/")

    # --- calls --------------------------------------------------------------------------------
    def get(self, path: str, **kwargs: Any) -> Response:
        return self.client.get(path, **kwargs)

    def post(
        self,
        path: str,
        json: Any = None,
        *,
        origin: str | None | bool = True,
        csrf: str | bool | None = True,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """`origin=True` sends the site's own, a string sends that one, False/None sends none.
        `csrf=True` sends the value of the CSRF cookie, a string that one, False sends none."""
        sent = dict(headers or {})
        if origin is True:
            sent["Origin"] = self.origin
        elif isinstance(origin, str):
            sent["Origin"] = origin
        token = self.csrf if csrf is True else csrf if isinstance(csrf, str) else None
        if token:
            sent["X-CSRF-Token"] = token
        return self.client.post(path, json=json, headers=sent)

    def login(self, user: TestUser | str, password: str | None = None, **kwargs: Any) -> Response:
        email = user if isinstance(user, str) else user.email
        if password is None:
            password = PASSWORD if isinstance(user, str) else (user.password or PASSWORD)
        return self.post("/api/v1/auth/login", {"email": email, "password": password}, **kwargs)

    def switch(self, membership_id: uuid.UUID | str) -> Response:
        return self.post("/api/v1/auth/context", {"membership_id": str(membership_id)})

    def invite(self, email: str, role: str, school_id: uuid.UUID | str | None = None) -> Response:
        body: dict[str, Any] = {"email": email, "role": role}
        if school_id is not None:
            body["school_id"] = str(school_id)
        return self.post("/api/v1/invitations", body)


@dataclass
class ApiFactory:
    """Builds clients over the real database, each from its own address (the rate limits are per
    address, so one test never blocks another)."""

    world: World
    database_url: str
    outbox: Path
    stack: ExitStack

    def make(
        self,
        *,
        secure: bool = False,
        ip: str | None = None,
        configure: Callable[[Any], None] | None = None,
        **overrides: Any,
    ) -> Api:
        octets = (uuid.uuid4().int % 250 + 1 for _ in range(3))
        address = ip or "10." + ".".join(str(octet) for octet in octets)
        self.world.used_ips.append(address)
        origin = SECURE_ORIGIN if secure else TEST_ORIGIN
        values: dict[str, Any] = {
            "env": "test",
            "database_url": self.database_url,
            "cookie_secure": secure,
            "public_origins": origin,
            "outbox_dir": str(self.outbox),
            **overrides,
        }
        settings = make_settings(**values)
        app = create_app(settings, verify_posture=False)
        if configure is not None:
            configure(app)
        client = TestClient(
            app,
            base_url=origin,
            client=(address, 50000),
            raise_server_exceptions=False,
        )
        self.stack.enter_context(client)
        return Api(client, settings, address)


@contextmanager
def api_factory(world: World, database_url: str, outbox: Path) -> Iterator[ApiFactory]:
    with ExitStack() as stack:
        yield ApiFactory(world, database_url, outbox, stack)


__all__ = [
    "OTHER_PASSWORD",
    "PASSWORD",
    "PASSWORD_HASH",
    "TEST_AUTH_SECRET",
    "Api",
    "ApiFactory",
    "OutboxMessage",
    "TestUser",
    "UserFactory",
    "World",
    "api_factory",
    "create_world",
    "read_outbox",
    "delete_world",
]
