"""An attachment store on the local disk: one file per key under a root directory.

Files are created with mode 0600 and directories with 0700, written once (`O_EXCL`) and flushed to
disk before `put` returns. The key is checked segment by segment, and the final path is checked to
be inside the root, so no key can reach outside it.
"""

import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from app.storage.base import (
    CHUNK_SIZE,
    StorageConflictError,
    StorageKeyError,
    StorageNotFoundError,
)

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
MAX_SEGMENTS = 8
MAX_KEY_LENGTH = 500


class LocalDiskStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        segments = key.split("/")
        if (
            not key
            or len(key) > MAX_KEY_LENGTH
            or len(segments) > MAX_SEGMENTS
            or not all(_SEGMENT.fullmatch(segment) for segment in segments)
        ):
            raise StorageKeyError("not a valid storage key")
        path = self.root.joinpath(*segments)
        root = self.root.resolve()
        # The segments cannot hold `..` or `/`; this also refuses a symlink that points outside.
        if root not in path.resolve().parents:
            raise StorageKeyError("not a valid storage key")
        return path

    def _make_directories(self, directory: Path) -> None:
        missing: list[Path] = []
        current = directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        for path in reversed(missing):
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                continue
            path.chmod(0o700)  # mkdir's mode is subject to the umask

    def put(self, key: str, chunks: Iterable[bytes]) -> int:
        path = self._path(key)
        self._make_directories(path.parent)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise StorageConflictError("something is already stored under this key") from None
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                for chunk in chunks:
                    handle.write(chunk)
                    size += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return size

    def open(self, key: str) -> Iterator[bytes]:
        path = self._path(key)
        if not path.is_file():
            raise StorageNotFoundError("nothing is stored under this key")
        # The file is opened by the generator itself, when the first chunk is asked for and closed
        # when it ends, fails or is closed: nothing holds a descriptor between this call and the
        # first read, so a response that is never sent (the client left) leaks none.
        return _read(path)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def _read(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            yield chunk
