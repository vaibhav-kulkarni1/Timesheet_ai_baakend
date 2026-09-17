"""
GET /api/health — feeds the dashboard's "Employee Health" card.

Aggregates each employee's stored timesheet history into a burnout-risk
record (HIGH / MEDIUM / LOW / NONE) from sustained overtime + lack of recent
leave. Read-only over the pre-computed store, same fast pattern as the other
cards.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.db_models import TimesheetRecommendation
from app.services.health_analyzer import analyze_all

router = APIRouter(prefix="/api/employee-health", tags=["employee-health"])


@router.get("")
def list_employee_health(
    manager_id: str | None = None,
    risk: str | None = None,
    at_risk_only: bool = True,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict:
    """
    Query params (all optional):
      manager_id     filter to one manager's team (Workfront approverID)
      risk           HIGH | MEDIUM | LOW | NONE
      at_risk_only   hide NONE-risk employees (default true)
      limit          max records returned (default 100)

    Returns:
      {
        "counts": {"HIGH": n, "MEDIUM": n, "LOW": n, "total": n},
        "employees": [ {employee_name, risk, reason, metrics, ...}, ... ]
      }
    """
    query = db.query(TimesheetRecommendation)
    if manager_id:
        query = query.filter(TimesheetRecommendation.manager_id == manager_id)

    records = analyze_all(query.all())

    if at_risk_only:
        records = [r for r in records if r["risk"] != "NONE"]
    if risk:
        records = [r for r in records if r["risk"] == risk.upper()]

    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for r in records:
        if r["risk"] in counts:
            counts[r["risk"]] += 1
    counts["total"] = sum(counts.values())

    return {"counts": counts, "employees": records[:limit]}
