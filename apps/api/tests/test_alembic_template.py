"""N6 (TASK-001): a new empty revision must not start with unused imports."""

import ast
import shutil
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_new_empty_revision_has_no_unused_imports(tmp_path: Path) -> None:
    script_dir = tmp_path / "migrations"
    (script_dir / "versions").mkdir(parents=True)
    shutil.copy(MIGRATIONS / "script.py.mako", script_dir / "script.py.mako")
    config = Config()
    config.set_main_option("script_location", str(script_dir))

    command.revision(config, message="empty", rev_id="abc123")

    (revision_file,) = (script_dir / "versions").glob("abc123_*.py")
    tree = ast.parse(revision_file.read_text())
    imported = {
        (alias.asname or alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert imported - used == set()
    assert "sa" not in imported
    assert "op" not in imported
