"""What the database refuses, turned into stable problems.

The triggers of `monthly_closings` raise with readable texts; the API NEVER repeats them (nor any
other text of Postgres). It recognises the refusal by its constraint name or by the first words of
the message, and answers with a fixed title and a stable `code`.
"""

from typing import Any

from sqlalchemy.exc import DBAPIError

from app.core.errors import ProblemError


def _facts(error: DBAPIError) -> tuple[str, str, str]:
    """(sqlstate, constraint name, message) of a database error; empty when unknown."""
    original: Any = error.orig
    diag = getattr(original, "diag", None)
    return (
        str(getattr(original, "sqlstate", "") or ""),
        str(getattr(diag, "constraint_name", "") or ""),
        str(getattr(diag, "message_primary", "") or ""),
    )


def closing_refusal(error: DBAPIError) -> ProblemError | None:
    """The problem for a refused INSERT into `monthly_closings`, or None for anything else (which
    stays an internal error)."""
    _, constraint, message = _facts(error)
    if message.startswith("the period has not ended yet"):
        return ProblemError(409, "period_not_ended", "The month has not ended yet")
    if message.startswith("closings are sequential"):
        return ProblemError(409, "closing_out_of_sequence", "Months are closed in order")
    if constraint == "uq_monthly_closings_active_period":
        return ProblemError(409, "period_already_closed", "That month is already closed")
    if message.startswith("the school has no visible settings"):
        return ProblemError(409, "school_not_configured", "The school has no settings yet")
    if constraint == "ck_monthly_closings_bank_balance_range":
        return ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "bank_balance_reported_cents", "code": "out_of_range"}],
        )
    return None


def reopen_refusal(error: DBAPIError) -> ProblemError | None:
    """The problem for a refused reopening, or None for anything else."""
    _, constraint, message = _facts(error)
    if message.startswith("only the latest active closing can be reopened"):
        return ProblemError(
            409, "closing_not_latest", "Only the latest active closing can be reopened"
        )
    if constraint == "ck_monthly_closings_reopen_reason_length":
        return ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "reason", "code": "invalid_length"}],
        )
    return None
