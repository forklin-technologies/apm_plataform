"""What the database refuses, as problems with a stable `code`.

The service checks every rule it knows before writing, so these are the net under it: a race, or a
rule the service did not foresee. No text of the database error ever reaches the client (it names
tables and columns), only a fixed title.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import ProblemError

# By constraint name: the cases a person can act on.
_BY_CONSTRAINT: dict[str, tuple[int, str, str]] = {
    "uq_financial_transactions_one_active_reimbursement": (
        409,
        "reimbursement_exists",
        "This expense already has a reimbursement",
    ),
    "uq_expense_attachments_school_id_transaction_id_sha256": (
        409,
        "attachment_duplicate",
        "This file is already attached to the expense",
    ),
    "ck_expenses_decider_is_not_submitter": (
        403,
        "self_approval_forbidden",
        "Nobody decides on their own expense",
    ),
}

# By SQLSTATE: the class of the refusal.
_BY_SQLSTATE: dict[str, tuple[int, str, str]] = {
    "23505": (409, "conflict", "The request conflicts with data that already exists"),
    "23514": (422, "rule_violation", "The change breaks a rule of the ledger"),
    "23001": (409, "state_conflict", "The expense is not in a state that allows this"),
    "23503": (422, "reference_invalid", "A reference in the request is not valid here"),
    "22003": (422, "value_out_of_range", "A value is out of range"),
    "40001": (409, "retry", "The request collided with another one, try again"),
    "40P01": (409, "retry", "The request collided with another one, try again"),
}


def problem_for(error: DBAPIError) -> ProblemError | None:
    """The problem for a refusal of the database, or None when it is not one we know."""
    orig = error.orig
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    found = _BY_CONSTRAINT.get(constraint or "") or _BY_SQLSTATE.get(
        str(getattr(orig, "sqlstate", ""))
    )
    if found is None:
        return None
    status, code, title = found
    return ProblemError(status, code, title)


@contextmanager
def database_rules(db: Session) -> Iterator[None]:
    """Run a write (and its commit) and turn a refusal of the database into a problem.

    The transaction is rolled back first. Anything that is not a refusal we know (the database is
    down, say) is raised as it is and becomes a plain 500.
    """
    try:
        yield
    except DBAPIError as error:
        db.rollback()
        problem = problem_for(error)
        if problem is None:
            raise
        raise problem from None
