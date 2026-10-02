"""`python -m app.posture`: check the database posture as the application role (exit 1 on failure).

    docker compose run --rm tools python -m app.posture

It connects with DATABASE_URL, the same credential the API uses, and prints only fixed codes.
"""

import sys

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.posture import check_posture


def main() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        with engine.connect() as connection:
            findings = check_posture(connection)
    except SQLAlchemyError:
        print("posture check could not run: the database is unreachable", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    if findings:
        print("posture FAILED: " + ", ".join(findings), file=sys.stderr)
        return 1
    print("posture OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
