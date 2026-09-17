"""
GET /api/utilization — feeds the Utilization Trends card.

Reads stored HOUR entries and aggregates hours by project and by role
(role is a proxy for department — it's what Workfront's hour data exposes).
Read-only, no Workfront/LLM calls in the request path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.db_models import HourEntryStaging
from app.services import utilization_analyzer

router = APIRouter(prefix="/api/utilization", tags=["utilization"])


@router.get("")
def get_utilization(
    owner_id: str | None = None,
    window_days: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    Hours grouped by project and by role, plus totals.

    Query params:
      owner_id      limit to one employee (Workfront user ID)
      window_days   only entries in the last N days (omit = all time)

    Note: a true billable ratio isn't available (no billable flag in the
    Workfront hour data), so it's intentionally omitted rather than estimated.
    """
    q = db.query(HourEntryStaging)
    if owner_id:
        q = q.filter(HourEntryStaging.owner_id == owner_id)
    rows = utilization_analyzer.rows_from_orm(q.all())
    return utilization_analyzer.utilization(rows, window_days=window_days)
