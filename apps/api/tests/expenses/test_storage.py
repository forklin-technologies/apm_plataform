"""The local attachment store and the type of a file."""

import os
from pathlib import Path

import pytest

from app.expenses.attachment_types import ALLOWED_TYPES, sniff
from app.storage import (
    LocalDiskStore,
    StorageConflictError,
    StorageKeyError,
    StorageNotFoundError,
    StorageSettings,
)

KEY = "11111111-1111/22222222-2222/33333333-3333/abcdef0123456789"


def read(store: LocalDiskStore, key: str) -> bytes:
    return b"".join(store.open(key))


def test_a_file_is_written_once_and_read_back(tmp_path: Path) -> None:
    store = LocalDiskStore(tmp_path / "root")

    size = store.put(KEY, iter([b"hello ", b"world"]))

    assert size == 11 and read(store, KEY) == b"hello world" and store.exists(KEY)
    assert (tmp_path / "root" / KEY).stat().st_mode & 0o777 == 0o600
    for directory in (tmp_path / "root", tmp_path / "root" / KEY.split("/")[0]):
        assert directory.stat().st_mode & 0o777 == 0o700
    with pytest.raises(StorageConflictError):
        store.put(KEY, iter([b"other"]))
    assert read(store, KEY) == b"hello world"  # never replaced


def test_a_large_file_is_streamed_in_chunks(tmp_path: Path) -> None:
    store = LocalDiskStore(tmp_path)
    payload = os.urandom(300_000)
    store.put(KEY, iter([payload[:100_000], payload[100_000:]]))

    chunks = list(store.open(KEY))

    assert len(chunks) > 1 and b"".join(chunks) == payload


def test_a_write_that_fails_leaves_nothing_behind(tmp_path: Path) -> None:
    store = LocalDiskStore(tmp_path)

    def broken() -> object:
        yield b"partial"
        raise RuntimeError("the source failed")

    with pytest.raises(RuntimeError):
        store.put(KEY, broken())  # type: ignore[arg-type]

    assert not store.exists(KEY)
    store.put(KEY, iter([b"fine"]))  # the key is free again
    assert read(store, KEY) == b"fine"


def test_what_is_not_there_is_not_found_and_delete_is_idempotent(tmp_path: Path) -> None:
    store = LocalDiskStore(tmp_path)

    with pytest.raises(StorageNotFoundError):
        store.open(KEY)
    assert not store.exists(KEY)
    store.delete(KEY)  # not an error
    store.put(KEY, iter([b"x"]))
    store.delete(KEY)
    store.delete(KEY)
    assert not store.exists(KEY)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../outside",
        "a/../../outside",
        "a/./b",
        "a//b",
        "a/b/",
        ".hidden",
        "a/.hidden",
        "with space",
        "a/b\x00c",
        "a\\b",
        "ção",
        "a" * 101,
        "/".join(["a"] * 9),
        "a/" * 249 + "a",
    ],
)
def test_a_key_that_could_leave_the_root_is_refused_on_every_operation(
    tmp_path: Path, key: str
) -> None:
    store = LocalDiskStore(tmp_path / "root")
    for operation in (
        lambda: store.put(key, iter([b"x"])),
        lambda: store.open(key),
        lambda: store.exists(key),
        lambda: store.delete(key),
    ):
        with pytest.raises(StorageKeyError):
            operation()
    assert not (tmp_path / "outside").exists()


def test_a_symlink_cannot_lead_out_of_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "file").write_bytes(b"secret")
    store = LocalDiskStore(root)
    store.put("keep/me", iter([b"ok"]))
    (root / "evil").symlink_to(secret)

    with pytest.raises(StorageKeyError):
        store.open("evil/file")
    with pytest.raises(StorageKeyError):
        store.put("evil/new", iter([b"x"]))
    assert not (secret / "new").exists()


def test_the_settings_have_safe_defaults_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTACHMENTS_DIR", raising=False)
    monkeypatch.delenv("ATTACHMENT_MAX_BYTES", raising=False)
    defaults = StorageSettings(_env_file=None)  # type: ignore[call-arg]
    assert defaults.attachments_dir == "/attachments"
    assert defaults.attachment_max_bytes == 10 * 1024 * 1024
    for bad in (0, -1, 20 * 1024 * 1024 + 1):  # the database refuses more than 20 MiB
        with pytest.raises(ValueError, match="attachment_max_bytes"):
            StorageSettings(_env_file=None, attachment_max_bytes=bad)  # type: ignore[call-arg]
    assert StorageSettings(_env_file=None, attachment_max_bytes=20 * 1024 * 1024)  # type: ignore[call-arg]


def test_the_types_are_read_from_the_first_bytes() -> None:
    assert sniff(b"%PDF-1.7 rest").content_type == "application/pdf"  # type: ignore[union-attr]
    assert sniff(b"\x89PNG\r\n\x1a\nrest").content_type == "image/png"  # type: ignore[union-attr]
    assert sniff(b"\xff\xd8\xff\xdbrest").content_type == "image/jpeg"  # type: ignore[union-attr]
    assert sniff(b"RIFF\x24\x00\x00\x00WEBPVP8 ").content_type == "image/webp"  # type: ignore[union-attr]
    for not_allowed in (
        b"",
        b"MZ",
        b"GIF89a",
        b"RIFF\x00\x00\x00\x00WAVEfmt ",
        b"%PDF",
        b"PK\x03\x04",
    ):
        assert sniff(not_allowed) is None
    # every type is reachable and has a distinct media type and extension
    assert len({t.content_type for t in ALLOWED_TYPES}) == len(ALLOWED_TYPES)
    assert len({t.extension for t in ALLOWED_TYPES}) == len(ALLOWED_TYPES)
