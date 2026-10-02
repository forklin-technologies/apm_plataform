"""The models carry the same names as the database for everything they declare.

`compare_metadata` (tests/test_migrations.py) already proves the columns, types, nullability, keys
and indexes match. It does not look at CHECK constraints, so this test compares their NAMES (and the
names of foreign keys, unique constraints and indexes) between the models and the migrated schema.
"""

from sqlalchemy import CheckConstraint, Engine, text

from app import models  # noqa: F401  (registers the models)
from app.db.base import Base
from tests.financial.test_isolation import TABLES


def test_the_models_declare_exactly_the_checks_of_the_database(admin_engine: Engine) -> None:
    declared = {
        constraint.name
        for table in Base.metadata.sorted_tables
        if table.name in TABLES
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    with admin_engine.connect() as connection:
        catalog = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT c.conname FROM pg_constraint c JOIN pg_class cl ON cl.oid = c.conrelid "
                    "WHERE c.contype = 'c' AND cl.relname = ANY(:t)"
                ),
                {"t": TABLES},
            )
        }
    assert declared == catalog


def test_the_models_declare_exactly_the_keys_and_indexes_of_the_database(
    admin_engine: Engine,
) -> None:
    declared: set[str] = set()
    for table in Base.metadata.sorted_tables:
        if table.name not in TABLES:
            continue
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) or constraint.name is None:
                continue
            declared.add(str(constraint.name))
        declared |= {str(index.name) for index in table.indexes if index.name is not None}
    with admin_engine.connect() as connection:
        catalog = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT c.conname FROM pg_constraint c JOIN pg_class cl ON cl.oid = c.conrelid "
                    "WHERE c.contype IN ('p', 'u', 'f') AND cl.relname = ANY(:t) "
                    "UNION SELECT ic.relname FROM pg_index x "
                    "JOIN pg_class ic ON ic.oid = x.indexrelid "
                    "JOIN pg_class i ON i.oid = x.indrelid WHERE i.relname = ANY(:t)"
                ),
                {"t": TABLES},
            )
        }
    assert declared == catalog
    assert all(
        len(name) <= 63 for name in catalog
    )  # Postgres would truncate a longer name silently
