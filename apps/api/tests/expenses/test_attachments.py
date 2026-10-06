"""Attachments: the type comes from the bytes, the size is bounded, the hash is of the bytes, the
file lives behind the store, and only whoever can see the expense can download it."""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.expenses import repository
from app.expenses.service import MAX_ATTACHMENTS_PER_EXPENSE, clean_file_name
from app.storage import LocalDiskStore
from tests.authsupport import no_unhandled_error
from tests.expenses.support import (
    Client,
    Scene,
    jpeg,
    pdf,
    png,
    refusal,
    storage_overrides,
    stored_files,
    webp,
)


def detail(client: Client, expense_id: str) -> dict[str, Any]:
    found: dict[str, Any] = client.get(f"/expenses/{expense_id}").json()
    return found


# --- what is accepted -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (pdf(), "application/pdf"),
        (png(), "image/png"),
        (jpeg(), "image/jpeg"),
        (webp(), "image/webp"),
    ],
    ids=["pdf", "png", "jpeg", "webp"],
)
def test_an_image_or_a_pdf_is_stored_with_its_size_and_hash(
    scene: Scene, content: bytes, media_type: str
) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)

    # whatever the client SAYS the file is (name, declared type), the bytes decide
    response = author.upload(
        expense["id"], content, name="foto.bin", content_type="application/octet-stream"
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content_type"] == media_type
    assert body["size_bytes"] == len(content)
    assert body["sha256"] == hashlib.sha256(content).hexdigest()
    assert body["kind"] == "INVOICE" and body["uploaded_by_user_id"] == str(author.user.id)
    assert "storage_key" not in body
    assert len(stored_files(scene.directory)) == 1
    assert stored_files(scene.directory)[0].read_bytes() == content
    assert detail(author, expense["id"])["attachments"][0]["id"] == body["id"]


@pytest.mark.parametrize(
    "content",
    [
        b"MZ\x90\x00 an executable pretending",
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<html><script>alert(1)</script></html>",
        b"just text",
        b"GIF89a",  # a type nobody asked for
        b" %PDF-1.4",  # the signature must be the first bytes
    ],
)
def test_a_file_that_is_not_an_image_or_a_pdf_is_refused_whatever_it_claims_to_be(
    scene: Scene, content: bytes
) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)

    response = author.upload(
        expense["id"], content, name="nota.pdf", content_type="application/pdf"
    )

    assert response.status_code == 415
    assert response.json()["code"] == "attachment_type_not_allowed"
    assert stored_files(scene.directory) == []
    assert detail(author, expense["id"])["attachments"] == []


def test_an_empty_file_or_a_missing_one_or_a_wrong_kind_is_a_422(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    path = f"/expenses/{expense['id']}/attachments"

    empty = author.upload(expense["id"], b"")
    assert empty.status_code == 422 and empty.json()["errors"] == [
        {"field": "file", "code": "empty"}
    ]
    no_file = author.post(path, data={"kind": "INVOICE"}, files={"other": ("a.pdf", pdf())})
    assert no_file.status_code == 422 and no_file.json()["errors"][0]["field"] == "file"
    as_text = author.post(path, data={"file": "not a file"})
    assert as_text.status_code == 422
    json_body = author.post(path, {"file": "x"})
    assert json_body.status_code == 422
    bad_kind = author.upload(expense["id"], kind="RECEIPT")
    assert bad_kind.status_code == 422 and bad_kind.json()["errors"][0]["field"] == "kind"
    assert stored_files(scene.directory) == []


def test_the_kind_defaults_to_other(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)

    response = author.upload(expense["id"], kind=None)

    assert response.status_code == 201 and response.json()["kind"] == "OTHER"
    for kind in ("INVOICE", "PAYMENT_PROOF"):
        assert author.upload(expense["id"], kind=kind).json()["kind"] == kind


# --- size -----------------------------------------------------------------------------------------


def test_a_file_over_the_limit_is_refused_and_nothing_is_kept(
    scene_with_limit: Callable[[int], Scene],
) -> None:
    scene = scene_with_limit(1_000)
    author = scene.person("staff", label="author")
    expense = scene.draft(author)

    assert author.upload(expense["id"], pdf(extra=900)).status_code == 201  # under the limit
    over = author.upload(expense["id"], pdf(extra=1_001))  # the file is over, the request is not
    huge = author.upload(expense["id"], pdf(extra=300_000))  # the declared size says so at once

    for response in (over, huge):
        assert response.status_code == 413 and response.json()["code"] == "payload_too_large"
    assert len(stored_files(scene.directory)) == 1
    assert len(detail(author, expense["id"])["attachments"]) == 1


def test_a_request_that_does_not_declare_its_size_is_refused_before_it_is_read(
    scene: Scene,
) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    boundary = "xxboundaryxx"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + pdf()
        + f"\r\n--{boundary}--\r\n".encode()
    )

    response = author.raw_post(
        f"/expenses/{expense['id']}/attachments",
        iter([body[:50], body[50:]]),  # chunked: no Content-Length
        f"multipart/form-data; boundary={boundary}",
    )

    assert response.status_code == 411 and response.json()["code"] == "length_required"
    assert stored_files(scene.directory) == []


def test_the_body_of_an_upload_is_not_read_for_someone_who_may_not_upload(scene: Scene) -> None:
    viewer = scene.person("viewer", label="viewer")
    huge = viewer.upload(str(uuid.uuid4()), pdf(extra=3_000_000))

    assert huge.status_code == 403  # the permission is checked before the size or the form


# --- one file, once -------------------------------------------------------------------------------


def test_the_same_file_twice_is_a_409_and_the_second_copy_is_not_kept(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    content = pdf()
    assert author.upload(expense["id"], content).status_code == 201

    again = author.upload(expense["id"], content, name="outro-nome.pdf")

    assert again.status_code == 409 and again.json()["code"] == "attachment_duplicate"
    assert len(stored_files(scene.directory)) == 1
    assert len(detail(author, expense["id"])["attachments"]) == 1
    # a different expense may carry the same file
    other = scene.draft(author)
    assert author.upload(other["id"], content).status_code == 201


def test_an_expense_carries_a_limited_number_of_attachments(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)

    for _ in range(MAX_ATTACHMENTS_PER_EXPENSE):
        assert author.upload(expense["id"]).status_code == 201
    response = author.upload(expense["id"])

    assert response.status_code == 409 and response.json()["code"] == "attachment_limit"
    assert len(stored_files(scene.directory)) == MAX_ATTACHMENTS_PER_EXPENSE


# --- who and when ---------------------------------------------------------------------------------


def test_only_the_author_attaches(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    director = scene.person("school_admin", label="director")
    treasurer = scene.person("treasurer", label="treasurer")
    expense = scene.draft(author)

    for other in (director, treasurer):
        denied = other.upload(expense["id"])
        assert denied.status_code in (403, 409)
    assert director.upload(expense["id"]).json()["code"] == "author_only"
    assert author.upload(expense["id"]).status_code == 201
    assert len(detail(author, expense["id"])["attachments"]) == 1


def _in_state(scene: Scene, author: Client, approver: Client, state: str) -> str:
    """An expense of `author`, taken to `state` through the real routes."""
    if state in ("DRAFT", "CANCELLED"):
        expense = scene.draft(author)
        if state == "CANCELLED":
            assert author.post(f"/expenses/{expense['id']}/cancel").status_code == 200
    else:
        expense = scene.submitted(author)
        steps: dict[str, tuple[str, Any]] = {
            "SUBMITTED": ("", None),
            "CORRECTION_REQUESTED": ("request-correction", {"reason": "falta algo"}),
            "APPROVED": ("approve", {}),
            "REJECTED": ("reject", {"reason": "nao cabe"}),
            "PAID": ("approve", {}),
        }
        action, body = steps[state]
        if action:
            assert approver.post(f"/expenses/{expense['id']}/{action}", body).status_code == 200
        if state == "PAID":
            assert approver.post(f"/expenses/{expense['id']}/pay").status_code == 200
    assert scene.row(expense["id"]).status == state
    return str(expense["id"])


@pytest.mark.parametrize("role", ["staff", "school_admin"])
@pytest.mark.parametrize(
    ("state", "allowed"),
    [
        ("DRAFT", True),
        ("CORRECTION_REQUESTED", True),
        ("SUBMITTED", False),
        ("APPROVED", False),
        ("REJECTED", False),
        ("PAID", False),
        ("CANCELLED", False),
    ],
)
def test_files_are_attached_only_while_the_expense_is_a_draft_or_awaits_a_correction(
    scene: Scene, role: str, state: str, allowed: bool
) -> None:
    """After it was sent, the evidence is what the approver saw: nobody adds to it, whatever the
    role of the author (the reimbursement pays against what was approved)."""
    author = scene.person(role, label="author")
    approver = scene.person("treasurer", label="approver")
    expense_id = _in_state(scene, author, approver, state)
    before = len(detail(author, expense_id)["attachments"])

    response = author.upload(expense_id)

    if allowed:
        assert response.status_code == 201, response.text
        assert len(detail(author, expense_id)["attachments"]) == before + 1
    else:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "attachments_closed"
        assert state in response.json()["detail"]
        assert len(detail(author, expense_id)["attachments"]) == before
    assert len(stored_files(scene.directory)) == before + (1 if allowed else 0)


# --- the name -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("nota.pdf", "nota.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\ana\\nota fiscal.pdf", "nota fiscal.pdf"),
        ("nota\x00\x1b[31m.pdf", "nota[31m.pdf"),
        ("", "anexo.pdf"),
        (None, "anexo.pdf"),
        ("...", "anexo.pdf"),
        ("a" * 500 + ".pdf", "a" * 200),
        ("recibo ção.pdf", "recibo ção.pdf"),
    ],
)
def test_the_file_name_is_only_a_clean_label(raw: str | None, expected: str) -> None:
    assert clean_file_name(raw, ".pdf") == expected


def test_a_hostile_name_does_not_reach_the_store_or_the_response(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)

    response = author.upload(expense["id"], name="../../../../tmp/evil.pdf")

    assert response.status_code == 201 and response.json()["file_name"] == "evil.pdf"
    [stored] = stored_files(scene.directory)
    assert "evil" not in str(stored)  # the key is made by the server
    assert scene.directory in stored.parents
    assert stored.stat().st_mode & 0o777 == 0o600


# --- download -------------------------------------------------------------------------------------


def test_who_can_see_the_expense_downloads_the_file_exactly_as_it_was_sent(scene: Scene) -> None:
    author = scene.person("staff", label="author")
    treasurer = scene.person("treasurer", label="treasurer")
    director = scene.person("school_admin", label="director")
    colleague = scene.person("staff", label="colleague")
    expense = scene.draft(author)
    content = pdf(extra=300)
    uploaded = author.upload(expense["id"], content, name="Nota Fiscal ção.pdf").json()
    path = f"/expenses/{expense['id']}/attachments/{uploaded['id']}"

    for reader in (author, treasurer, director):
        response = reader.get(path)
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-length"] == str(len(content))
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment; filename=")
        assert "filename*=UTF-8''Nota%20Fiscal%20%C3%A7%C3%A3o.pdf" in disposition

    # a person who may not see the expense, and a file that is not of that expense: the same 404
    wrong = [
        colleague.get(path),
        author.get(f"/expenses/{expense['id']}/attachments/{uuid.uuid4()}"),
        author.get(f"/expenses/{uuid.uuid4()}/attachments/{uploaded['id']}"),
    ]
    other = scene.draft(author)
    wrong.append(author.get(f"/expenses/{other['id']}/attachments/{uploaded['id']}"))
    for response in wrong:
        assert response.status_code == 404 and response.json()["code"] == "not_found"


def test_a_file_missing_from_the_store_is_a_server_problem_not_a_missing_expense(
    scene: Scene,
) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    uploaded = author.upload(expense["id"]).json()
    [stored] = stored_files(scene.directory)
    stored.unlink()

    response = author.get(f"/expenses/{expense['id']}/attachments/{uploaded['id']}")

    assert response.status_code == 500 and response.json()["code"] == "attachment_unavailable"


def test_the_row_records_the_key_and_the_actor_and_nothing_of_the_client(
    scene: Scene,
) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    uploaded = author.upload(expense["id"], name="minha nota.pdf").json()

    with scene.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT storage_key, uploaded_by_user_id, organization_id, school_id "
                "FROM expense_attachments WHERE id = :id"
            ),
            {"id": uploaded["id"]},
        ).one()

    assert row.uploaded_by_user_id == author.user.id
    assert row.storage_key.startswith(f"{row.organization_id}/{row.school_id}/{expense['id']}/")
    assert "minha" not in row.storage_key
    assert Path(scene.directory, row.storage_key).is_file()


# --- when the write fails -------------------------------------------------------------------------


class SpyStore(LocalDiskStore):
    """A store that remembers what it was asked to delete, and can fail to do it."""

    def __init__(self, root: Path, *, delete_fails: bool = False) -> None:
        super().__init__(root)
        self.deleted: list[str] = []
        self.delete_fails = delete_fails

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        if self.delete_fails:
            raise OSError(f"the disk refused to remove /secret/place/{key}")
        super().delete(key)


def with_store(scene: Scene, store: SpyStore) -> Scene:
    return Scene(
        scene.apis,
        scene.users,
        scene.engine,
        scene.directory,
        storage_overrides(scene.directory, 1024 * 1024, store),
    )


def test_a_failing_delete_never_replaces_the_error_the_person_must_see(
    scene: Scene, caplog: pytest.LogCaptureFixture
) -> None:
    store = SpyStore(scene.directory, delete_fails=True)
    scene = with_store(scene, store)
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    content = pdf()
    assert author.upload(expense["id"], content).status_code == 201

    with caplog.at_level(logging.ERROR):
        again = author.upload(expense["id"], content)  # the unique index refuses it

    assert again.status_code == 409 and again.json()["code"] == "attachment_duplicate"
    assert len(store.deleted) == 1  # it tried
    assert len(stored_files(scene.directory)) == 2  # and the file it could not remove stays
    no_unhandled_error(caplog)
    [entry] = [r for r in caplog.records if "could not remove an attachment" in r.getMessage()]
    assert entry.error_class == "OSError"  # type: ignore[attr-defined]
    assert "/secret/place" not in caplog.text and store.deleted[0] not in caplog.text
    assert "/secret/place" not in again.text
    assert len(detail(author, expense["id"])["attachments"]) == 1


def test_a_file_whose_row_was_refused_is_removed(scene: Scene) -> None:
    store = SpyStore(scene.directory)
    scene = with_store(scene, store)
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    content = pdf()
    assert author.upload(expense["id"], content).status_code == 201

    assert author.upload(expense["id"], content).status_code == 409

    assert len(store.deleted) == 1 and len(stored_files(scene.directory)) == 1


def _commit_fails(monkeypatch: pytest.MonkeyPatch, *, reaches_the_server: bool) -> None:
    """Make the commit of the NEXT attachment fail, as if the answer of the database was lost
    (`reaches_the_server`: the commit did happen) or the database said no."""
    armed = {"on": False}
    real_insert = repository.insert_attachment
    real_commit = Session.commit

    def insert(*args: Any, **kwargs: Any) -> Any:
        armed["on"] = True
        return real_insert(*args, **kwargs)

    def commit(self: Session) -> None:
        if not armed["on"]:
            return real_commit(self)
        armed["on"] = False
        if reaches_the_server:
            real_commit(self)
            raise refusal("08006")  # the connection dropped after the commit
        raise refusal("23514")

    monkeypatch.setattr(repository, "insert_attachment", insert)
    monkeypatch.setattr(Session, "commit", commit)


def test_a_commit_that_may_have_reached_the_server_keeps_the_file_its_row_points_to(
    scene: Scene, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = SpyStore(scene.directory)
    scene = with_store(scene, store)
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    content = pdf()
    _commit_fails(monkeypatch, reaches_the_server=True)

    lost = author.upload(expense["id"], content)  # the row IS committed, the answer is lost

    assert lost.status_code == 500  # the client does not know; it can look at the expense
    monkeypatch.undo()
    attachments = detail(author, expense["id"])["attachments"]
    assert len(attachments) == 1 and store.deleted == []
    downloaded = author.get(f"/expenses/{expense['id']}/attachments/{attachments[0]['id']}")
    assert downloaded.status_code == 200 and downloaded.content == content


def test_a_commit_the_database_refuses_keeps_an_orphan_file_and_no_row(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SpyStore(scene.directory)
    scene = with_store(scene, store)
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    _commit_fails(monkeypatch, reaches_the_server=False)

    refused = author.upload(expense["id"])

    assert refused.status_code == 422 and refused.json()["code"] == "rule_violation"
    monkeypatch.undo()
    assert detail(author, expense["id"])["attachments"] == []
    assert store.deleted == [] and len(stored_files(scene.directory)) == 1  # an orphan, on purpose


# --- the declared size ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "status", "code"),
    [
        (b"12abc", 411, "length_required"),
        (b"-5", 411, "length_required"),
        (b"", 411, "length_required"),
        (b"9" * 16, 413, "payload_too_large"),  # far over any limit
        (b"9" * 5_000, 413, "payload_too_large"),  # int() of this many digits is refused
        (b"0" * 5_000, 413, "payload_too_large"),
    ],
)
def test_a_declared_size_that_is_not_a_number_is_a_411_and_a_huge_one_a_413_never_a_500(
    scene: Scene, caplog: pytest.LogCaptureFixture, declared: bytes, status: int, code: str
) -> None:
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    headers = {
        b"origin": author.api.origin.encode(),
        b"x-csrf-token": (author.api.csrf or "").encode(),
        b"content-type": b"multipart/form-data; boundary=xx",
        b"content-length": declared,  # as the client wrote it: raw bytes, not always ASCII
    }

    response = author.api.client.post(
        author.path(f"/expenses/{expense['id']}/attachments"), content=b"--xx--", headers=headers
    )

    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    no_unhandled_error(caplog)
    assert stored_files(scene.directory) == []


@pytest.mark.parametrize("declared", [b"\xb2", b"\xb3", b"\xb9\xb9", b"1\xb2"])
def test_a_declared_size_in_superscript_digits_is_a_411_never_a_500(
    scene: Scene, caplog: pytest.LogCaptureFixture, declared: bytes
) -> None:
    """ "²" is `str.isdigit()` and still not a number `int()` accepts. A test client re-encodes
    header values, so the request goes to the application itself, byte for byte as a hostile
    client would write it."""
    author = scene.person("staff", label="author")
    expense = scene.draft(author)
    path = author.path(f"/expenses/{expense['id']}/attachments")
    cookies = "; ".join(f"{c.name}={c.value}" for c in author.api.client.cookies.jar)
    headers = [
        (b"host", b"localhost"),
        (b"origin", author.api.origin.encode()),
        (b"cookie", cookies.encode()),
        (b"x-csrf-token", (author.api.csrf or "").encode()),
        (b"content-type", b"multipart/form-data; boundary=xx"),
        (b"content-length", declared),
    ]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"--xx--", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("10.9.8.7", 50000),
        "server": ("localhost", 3000),
        "state": {},
    }
    asyncio.run(author.api.client.app(scope, receive, send))  # type: ignore[arg-type]

    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert start["status"] == 411, body
    assert b"length_required" in body
    no_unhandled_error(caplog)
    assert stored_files(scene.directory) == []
