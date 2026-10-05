"""docs/financial-model.md lists every table, function and trigger of the schema (condition 6).

The inventory in the page is read against the catalog, so a trigger or a function added to the
migration without being documented, or documented and then dropped, fails here. The page lives in
docs/ at the root of the repository, which is not inside the API image: run the test with the
repository checked out (the docs folder is found next to apps/), or it is skipped with a message.
"""

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from tests.dbsupport import API_DIR
from tests.financial.test_isolation import TABLES

# apps/api -> repository root. In the test image the API lives at /app, whose parent is the root.
ROOT = API_DIR.parents[1] if len(API_DIR.parents) > 1 else API_DIR.parent
DOC = ROOT / "docs" / "financial-model.md"
# The two triggers every table has are documented as a pattern.
PATTERNS = ("_no_delete", "_no_truncate")


@pytest.fixture(scope="module")
def page() -> str:
    if not DOC.is_file():
        pytest.skip(f"{DOC} is not available here (run from a checkout that has docs/)")
    return DOC.read_text(encoding="utf-8")


def test_every_table_is_documented(page: str) -> None:
    missing = [table for table in TABLES if f"`{table}`" not in page]
    assert missing == []


def test_every_function_and_trigger_of_the_schema_is_documented(
    admin_engine: Engine, page: str
) -> None:
    with admin_engine.connect() as connection:
        functions = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public'"
                )
            )
        }
        triggers = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace"
                )
            )
        }
    assert [name for name in sorted(functions) if name not in page] == []
    undocumented = [
        name for name in sorted(triggers) if not name.endswith(PATTERNS) and name not in page
    ]
    assert undocumented == []
    assert "`<table>_no_delete`" in page and "`<table>_no_truncate`" in page


def test_the_page_documents_the_actor_settings_and_the_accepted_risks(page: str) -> None:
    for needle in (
        "app.user_id",
        "app.request_id",
        "app.actor_type",
        "DISABLE TRIGGER",
        "raw_payload",
        "minimise",
    ):
        assert needle in page, needle


def test_the_doc_path_is_a_file_in_the_repository_layout() -> None:
    assert isinstance(DOC, Path) and DOC.name == "financial-model.md"
