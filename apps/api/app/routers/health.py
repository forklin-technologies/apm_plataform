import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.deps import public
from app.db.posture import cheap_posture_ok
from app.db.session import get_db
from app.schemas.health import HealthResponse, ReadinessResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"], dependencies=[Depends(public())])


@router.get("/health", summary="Liveness")
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/health/ready",
    summary="Readiness",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
def ready(db: Annotated[Session, Depends(get_db)]) -> ReadinessResponse | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
        posture_ok = cheap_posture_ok(db.connection())
    except SQLAlchemyError:
        logger.warning("readiness check failed: database unavailable", exc_info=True)
        posture_ok = False
    else:
        if not posture_ok:
            logger.error("readiness check failed: the database role is not the unprivileged one")
    if not posture_ok:
        return JSONResponse(
            status_code=503,
            content=ReadinessResponse(status="unavailable").model_dump(),
        )
    return ReadinessResponse(status="ready")
