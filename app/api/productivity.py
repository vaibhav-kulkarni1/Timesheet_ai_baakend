"""
GET /api/productivity — feeds the Productivity Insights card.

Reads stored HOUR entries and computes planned vs actual hours (from Workfront
task.work vs task.actualWork) and a count of completed deliverables (tasks with
status CPL). Read-only, no Workfront/LLM calls in the request path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.db_models import HourEntryStaging
from app.services import productivity_analyzer

router = APIRouter(prefix="/api/productivity", tags=["productivity"])


@router.get("")
def get_productivity(
    owner_id: str | None = None,
    window_days: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    Planned vs actual hours and count of completed deliverables.

    Query params:
      owner_id      limit to one employee (Workfront user ID)
      window_days   only entries in the last N days (omit = all time)
    """
    q = db.query(HourEntryStaging)
    if owner_id:
        q = q.filter(HourEntryStaging.owner_id == owner_id)
    rows = productivity_analyzer.rows_from_orm(q.all())
    return productivity_analyzer.productivity(rows, window_days=window_days)
