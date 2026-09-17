"""
GET /api/anomalies — feeds the dashboard's "AI Anomaly Detection" card.

Reads the pre-computed recommendation rows and derives individual anomalies
(with HIGH/MEDIUM/LOW severity) from each stored checklist. No recomputation,
no Workfront/LLM calls in the request path — same fast read-only pattern as
the recommendations endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.db_models import TimesheetRecommendation
from app.models.schemas import Checklist
from app.services.anomaly_detector import detect_anomalies, sort_anomalies

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"])


@router.get("")
def list_anomalies(
    manager_id: str | None = None,
    severity: str | None = None,
    anomaly_type: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict:
    """
    Query params (all optional):
      manager_id     filter to one manager's team (Workfront approverID)
      severity       HIGH | MEDIUM | LOW
      anomaly_type   excessive_hours | duplicate_entry | rejected_entry |
                     missing_time | idle_resource | open_entries |
                     non_standard_utilization
      limit          max anomalies returned (default 100)

    Returns:
      {
        "counts": {"HIGH": n, "MEDIUM": n, "LOW": n, "total": n},
        "anomalies": [ {id, employee_name, type, severity, title, description, ...}, ... ]
      }
    """
    query = db.query(TimesheetRecommendation).filter(
        TimesheetRecommendation.recommendation != "NOT_READY"
    )
    if manager_id:
        query = query.filter(TimesheetRecommendation.manager_id == manager_id)

    all_anoms: list[dict] = []
    for row in query.all():
        checklist = Checklist(**row.checklist)
        # overtime_pre_approved isn't stored per-row; the stored recommendation
        # already accounts for it, so we pass False here and rely on the
        # variance rule matching what produced the recommendation.
        row_anoms = detect_anomalies(
            employee_id=row.employee_id,
            employee_name=row.employee_name,
            period_start=row.period_start,
            period_end=row.period_end,
            checklist=checklist,
            recommendation=row.recommendation,
        )
        all_anoms.extend(row_anoms)

    if severity:
        all_anoms = [a for a in all_anoms if a["severity"] == severity.upper()]
    if anomaly_type:
        all_anoms = [a for a in all_anoms if a["type"] == anomaly_type]

    all_anoms = sort_anomalies(all_anoms)

    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for a in all_anoms:
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1
    counts["total"] = len(all_anoms)

    return {"counts": counts, "anomalies": all_anoms[:limit]}
