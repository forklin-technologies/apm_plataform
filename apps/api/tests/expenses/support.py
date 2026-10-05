"""Support for the expenses tests: a client that acts as a person of a school, and builders for the
files and the expenses a test needs."""

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from httpx2 import Response
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.storage import (
    AttachmentStore,
    LocalDiskStore,
    StorageSettings,
    get_attachment_store,
    get_storage_settings,
)
from tests.authsupport import Api, ApiFactory, TestUser, UserFactory, World

PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def pdf(extra: int = 0) -> bytes:
    """A (tiny, fake) PDF: it starts like one, and every call makes different bytes (a different
    sha256), so the same test can attach several."""
    return b"%PDF-1.4\n%" + os.urandom(8).hex().encode() + b"\n" + b"0" * extra + b"\n%%EOF\n"


def png() -> bytes:
    return PNG_HEAD + os.urandom(24)


def jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0" + os.urandom(24)


def webp() -> bytes:
    return b"RIFF" + b"\x10\x00\x00\x00" + b"WEBP" + os.urandom(12)


@dataclass
class Client:
    """A logged-in person, acting on one school (`school`, default A1)."""

    api: Api
    user: TestUser
    world: World
    school: uuid.UUID

    def path(self, tail: str = "", *, school: uuid.UUID | str | None = None) -> str:
        return f"/api/v1/schools/{school or self.school}{tail}"

    def request(
        self,
        method: str,
        tail: str = "",
        *,
        json: Any = None,
        school: uuid.UUID | str | None = None,
        origin: bool = True,
        **kwargs: Any,
    ) -> Response:
        headers = dict(kwargs.pop("headers", {}))
        if origin:
            headers["Origin"] = self.api.origin
        if method not in ("GET", "HEAD") and self.api.csrf:
            headers["X-CSRF-Token"] = self.api.csrf
        return self.api.client.request(
            method, self.path(tail, school=school), json=json, headers=headers, **kwargs
        )

    def get(self, tail: str = "", **kwargs: Any) -> Response:
        return self.request("GET", tail, **kwargs)

    def post(self, tail: str = "", json: Any = None, **kwargs: Any) -> Response:
        return self.request("POST", tail, json=json, **kwargs)

    def patch(self, tail: str = "", json: Any = None, **kwargs: Any) -> Response:
        return self.request("PATCH", tail, json=json, **kwargs)

    def raw_post(self, tail: str, content: Any, content_type: str) -> Response:
        """A POST whose body the test client does not shape (to leave out the Content-Length)."""
        headers = {
            "Origin": self.api.origin,
            "Content-Type": content_type,
            "X-CSRF-Token": self.api.csrf or "",
        }
        return self.api.client.post(self.path(tail), content=content, headers=headers)

    def upload(
        self,
        expense_id: str,
        content: bytes | None = None,
        *,
        name: str = "nota.pdf",
        content_type: str = "application/pdf",
        kind: str | None = "INVOICE",
        **kwargs: Any,
    ) -> Response:
        data = {} if kind is None else {"kind": kind}
        return self.post(
            f"/expenses/{expense_id}/attachments",
            files={"file": (name, pdf() if content is None else content, content_type)},
            data=data,
            **kwargs,
        )


class Scene:
    """What a test needs to act as people: users made on demand, and the ledger of the world."""

    def __init__(
        self,
        apis: ApiFactory,
        users: UserFactory,
        engine: Engine,
        directory: Path,
        configure: Callable[[Any], None],
    ) -> None:
        self.apis = apis
        self.users = users
        self.engine = engine
        self.directory = directory
        self.world = users.world
        self._configure = configure

    def person(
        self,
        role: str = "staff",
        *,
        school: str | None = "1",
        org: str = "a",
        label: str | None = None,
        **extra: Any,
    ) -> Client:
        user = self.users.make(role, school=school, org=org, label=label, **extra)
        api = self.apis.make(configure=self._configure)
        response = api.login(user)
        assert response.status_code == 200, response.text
        if org == "b":
            target = self.world.school_b1
        elif school == "2":
            target = self.world.school_a2
        else:
            target = self.world.school_a1
        return Client(api, user, self.world, target)

    def category(self, school: uuid.UUID, key: str = "school_supplies") -> str:
        with self.engine.connect() as connection:
            found: uuid.UUID = connection.execute(
                text("SELECT id FROM categories WHERE school_id = :s AND key = :k"),
                {"s": school, "k": key},
            ).scalar_one()
        return str(found)

    def draft(self, client: Client, **overrides: Any) -> dict[str, Any]:
        """Create an expense as `client` (a DRAFT) and return it."""
        body: dict[str, Any] = {
            "amount_cents": 12_500,
            "occurred_at": "2026-09-10",
            "category_id": self.category(client.school),
            "description": "Papel sulfite e canetas",
            "vendor": "Papelaria Central",
            "purchase_reason": "Material da feira de ciencias",
            "payment_method": "CARD",
            "paid_by": "APM",
            **overrides,
        }
        response = client.post("/expenses", body)
        assert response.status_code == 201, response.text
        created: dict[str, Any] = response.json()
        return created

    def submitted(self, client: Client, **overrides: Any) -> dict[str, Any]:
        """A DRAFT with an attachment, sent for approval."""
        expense = self.draft(client, **overrides)
        assert client.upload(expense["id"]).status_code == 201
        response = client.post(f"/expenses/{expense['id']}/submit")
        assert response.status_code == 200, response.text
        sent: dict[str, Any] = response.json()
        return sent

    def approved(self, author: Client, approver: Client, **overrides: Any) -> dict[str, Any]:
        expense = self.submitted(author, **overrides)
        response = approver.post(f"/expenses/{expense['id']}/approve", {})
        assert response.status_code == 200, response.text
        done: dict[str, Any] = response.json()
        return done

    def row(self, expense_id: str) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT ft.status, ft.amount_cents, ft.settled_at, e.approved_amount_cents, "
                    "e.approved_by_user_id, e.submitted_by_user_id, e.decision_reason, "
                    "e.correction_reason FROM financial_transactions ft JOIN expenses e "
                    "ON e.transaction_id = ft.id WHERE ft.id = :id"
                ),
                {"id": expense_id},
            ).one()

    def audit(self, expense_id: str) -> list[Any]:
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    text(
                        "SELECT action, actor_user_id, actor_type, request_id FROM audit_logs "
                        "WHERE entity_id = :id ORDER BY occurred_at, id"
                    ),
                    {"id": expense_id},
                ).all()
            )


def storage_overrides(
    directory: Path, max_bytes: int, store: AttachmentStore | None = None
) -> Callable[[Any], None]:
    """Make an app store its files in `directory` (or in `store`) and accept at most `max_bytes`."""

    def configure(app: Any) -> None:
        chosen = store or LocalDiskStore(directory)
        settings = StorageSettings(attachments_dir=str(directory), attachment_max_bytes=max_bytes)
        app.dependency_overrides[get_attachment_store] = lambda: chosen
        app.dependency_overrides[get_storage_settings] = lambda: settings

    return configure


def stored_files(directory: Path) -> list[Path]:
    """Every file the store holds (none, when the directory was never made)."""
    return (
        sorted(path for path in directory.rglob("*") if path.is_file())
        if directory.exists()
        else []
    )


def refusal(sqlstate: str, constraint: str | None = None) -> DBAPIError:
    """What the driver raises: an error with a SQLSTATE and, for a constraint, its name."""
    orig = SimpleNamespace(sqlstate=sqlstate, diag=SimpleNamespace(constraint_name=constraint))
    return DBAPIError("UPDATE secret_table SET secret = 1", {}, orig)  # type: ignore[arg-type]
