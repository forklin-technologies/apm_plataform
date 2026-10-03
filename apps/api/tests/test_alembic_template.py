"""N6 (TASK-001): a new empty revision must not start with unused imports."""

import ast
import shutil
import subprocess
from pathlib import Path

from alembic import command
from alembic.config import Config

API_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS = API_DIR / "migrations"


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


def test_a_generated_revision_goes_through_the_ruff_post_write_hooks(tmp_path: Path) -> None:
    """alembic.ini runs `ruff check --fix` and `ruff format` on every new revision, so it is born
    lint-clean. The hooks come from the real alembic.ini; only the script location is redirected."""
    script_dir = tmp_path / "migrations"
    (script_dir / "versions").mkdir(parents=True)
    shutil.copy(MIGRATIONS / "script.py.mako", script_dir / "script.py.mako")
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(script_dir))

    command.revision(config, message="hook check", rev_id="def456")

    (revision_file,) = (script_dir / "versions").glob("def456_*.py")
    for arguments in (["check", str(revision_file)], ["format", "--check", str(revision_file)]):
        result = subprocess.run(  # noqa: S603
            ["ruff", *arguments],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_hooks_really_run_they_fix_a_revision_that_would_not_pass(tmp_path: Path) -> None:
    """Control: a template that emits an unused import and sloppy formatting comes out clean."""
    script_dir = tmp_path / "migrations"
    (script_dir / "versions").mkdir(parents=True)
    (script_dir / "script.py.mako").write_text(
        '"""${message}"""\nimport os,sys\nrevision = ${repr(up_revision)}\ndown_revision=None\n'
    )
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(script_dir))

    command.revision(config, message="messy", rev_id="aaa111")

    (revision_file,) = (script_dir / "versions").glob("aaa111_*.py")
    content = revision_file.read_text()
    assert "import os" not in content and "import sys" not in content  # F401 removed by --fix
    assert "down_revision = None" in content  # reformatted by ruff format
