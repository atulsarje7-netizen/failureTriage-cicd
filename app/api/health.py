from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    """Report database connectivity and in-process payment stub status."""
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    overall = "ok" if db_status == "ok" else "degraded"
    return HealthOut(
        status=overall,
        database={"status": db_status},
        payment={"status": "ok", "mode": "in_process_stub"},
    )
