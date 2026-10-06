"""The expiry job (ADR-019): `python -m app.jobs.expire [--loop] [--interval SECONDS]`.

One pass finds the schools with something to expire (the one SECURITY DEFINER function of
migration 0009 answers with ids and nothing else), and for each of them opens a session of its own,
binds that school and acts as the SYSTEM under row level security: the writes, the triggers and the
audit are the ones of any other request. A school that fails is logged (its id and the kind of
error, never a value) and does not stop the others.

Without `--loop` it runs once and exits (a cron entry, a test, a manual run); with `--loop` it
repeats every `--interval` seconds until it receives SIGTERM or SIGINT.
"""

import argparse
import logging
import signal
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contributions.expiry import expire_in_school
from app.core.config import get_settings
from app.db.request_context import bind_request_context
from app.db.session import build_engine, build_session_factory
from app.db.tenant import TenantContext, bind_tenant

logger = logging.getLogger("apm.jobs.expire")

SCHOOLS_PER_PAGE = 200
MAX_PASSES_PER_SCHOOL = 100
DEFAULT_INTERVAL_SECONDS = 60

_FIND_SCHOOLS = text(
    "SELECT organization_id, school_id "
    "FROM public.find_schools_with_stale_contributions(CAST(:after AS uuid), :page)"
)


@dataclass(frozen=True)
class RunReport:
    schools: int = 0
    charges: int = 0
    contributions: int = 0
    failed: int = 0


def _request_id() -> str:
    return f"job-expire-{uuid.uuid4().hex[:16]}"


def _find_schools(
    factory: sessionmaker[Session], after: uuid.UUID | None
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    with factory() as db:
        bind_request_context(db, actor_type="SYSTEM", request_id=_request_id())
        rows = db.execute(
            _FIND_SCHOOLS,
            {"after": None if after is None else str(after), "page": SCHOOLS_PER_PAGE},
        ).all()
        return [(row[0], row[1]) for row in rows]


def _expire_school(
    factory: sessionmaker[Session], organization_id: uuid.UUID, school_id: uuid.UUID
) -> tuple[int, int]:
    charges = contributions = 0
    request_id = _request_id()
    for _ in range(MAX_PASSES_PER_SCHOOL):
        with factory() as db:
            bind_tenant(db, TenantContext(organization_id, school_id))
            bind_request_context(db, actor_type="SYSTEM", request_id=request_id)
            report = expire_in_school(db)
            db.commit()
        charges += report.charges
        contributions += report.contributions
        if not report.more:
            break
    return charges, contributions


def run_once(factory: sessionmaker[Session]) -> RunReport:
    """One pass over every school that has something due. Never raises for a single school."""
    schools = charges = contributions = failed = 0
    after: uuid.UUID | None = None
    while True:
        page = _find_schools(factory, after)
        if not page:
            break
        for organization_id, school_id in page:
            schools += 1
            try:
                done_charges, done_contributions = _expire_school(
                    factory, organization_id, school_id
                )
            except Exception as error:  # one school must not stop the rest
                failed += 1
                logger.error("school %s: expiry failed (%s)", school_id, type(error).__name__)
                continue
            charges += done_charges
            contributions += done_contributions
        after = page[-1][1]
    return RunReport(schools, charges, contributions, failed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.expire", description=__doc__)
    parser.add_argument("--loop", action="store_true", help="repeat until SIGTERM or SIGINT")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, metavar="SECONDS")
    args = parser.parse_args(argv)
    if args.interval < 1:
        parser.error("--interval must be at least 1 second")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = build_engine(get_settings())
    factory = build_session_factory(engine)

    stop = threading.Event()
    # Handlers only while looping, and put back afterwards: main() may run inside a bigger process
    # (a test, a shell) whose Ctrl-C must keep working once it returns.
    previous = {}
    if args.loop:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())

    exit_code = 0
    try:
        while True:
            try:
                report = run_once(factory)
                if report.charges or report.contributions or report.failed:
                    logger.info(
                        "schools=%d charges_expired=%d contributions_expired=%d failed=%d",
                        report.schools,
                        report.charges,
                        report.contributions,
                        report.failed,
                    )
                if report.failed:
                    exit_code = 1
            except Exception as error:  # the database may be down: keep the loop alive
                exit_code = 1
                logger.error("pass failed (%s)", type(error).__name__)
            if not args.loop or stop.wait(args.interval):
                break
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    engine.dispose()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
