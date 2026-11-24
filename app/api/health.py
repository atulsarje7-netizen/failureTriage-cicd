from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.payment import probe_payment
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    """Report database connectivity and payment stub status."""
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    payment = probe_payment()
    overall = "ok" if db_status == "ok" and payment["status"] == "ok" else "degraded"
    return HealthOut(
        status=overall,
        database={"status": db_status},
        payment=payment,
    )
