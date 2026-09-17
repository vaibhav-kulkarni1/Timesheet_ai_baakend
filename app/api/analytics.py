"""
GET /api/utilization and GET /api/productivity — feed the Utilization Trends
and Productivity Insights cards. Both read stored HOUR entries and aggregate,
read-only, no Workfront/LLM calls in the request path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.db_models import HourEntryStaging
from app.services import analytics

router = APIRouter(prefix="/api", tags=["analytics"])


def _load_rows(db: Session, owner_id: str | None) -> list:
    q = db.query(HourEntryStaging)
    if owner_id:
        q = q.filter(HourEntryStaging.owner_id == owner_id)
    return analytics.rows_from_orm(q.all())


@router.get("/utilization")
def get_utilization(
    owner_id: str | None = None,
    window_days: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    Hours grouped by project and by role (proxy for department), plus totals.

    Query params:
      owner_id      limit to one employee (Workfront user ID)
      window_days   only entries in the last N days (omit = all time)

    Note: a true billable ratio isn't available (no billable flag in the
    Workfront hour data), so it's intentionally omitted rather than estimated.
    """
    rows = _load_rows(db, owner_id)
    return analytics.utilization(rows, window_days=window_days)


@router.get("/productivity")
def get_productivity(
    owner_id: str | None = None,
    window_days: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    Planned vs actual hours (from task.work vs task.actualWork) and count of
    completed deliverables (tasks with status CPL).

    Query params:
      owner_id      limit to one employee
      window_days   only entries in the last N days (omit = all time)
    """
    rows = _load_rows(db, owner_id)
    return analytics.productivity(rows, window_days=window_days)
