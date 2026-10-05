from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tests.authsupport import ApiFactory, UserFactory
from tests.expenses.support import Scene, storage_overrides

DEFAULT_LIMIT = 1024 * 1024


@pytest.fixture
def scene(apis: ApiFactory, users: UserFactory, admin_engine: Engine, tmp_path: Path) -> Scene:
    """People of the two organizations, over a store in a temporary directory (1 MiB per file)."""
    return Scene(
        apis,
        users,
        admin_engine,
        tmp_path / "files",
        storage_overrides(tmp_path / "files", DEFAULT_LIMIT),
    )


@pytest.fixture
def scene_with_limit(
    apis: ApiFactory, users: UserFactory, admin_engine: Engine, tmp_path: Path
) -> Callable[[int], Scene]:
    def build(max_bytes: int) -> Scene:
        directory = tmp_path / "files"
        return Scene(apis, users, admin_engine, directory, storage_overrides(directory, max_bytes))

    return build
