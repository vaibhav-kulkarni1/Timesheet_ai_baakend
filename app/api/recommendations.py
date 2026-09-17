"""
Step 5/6 — Serving API + Manager Action API.

GET routes read ONLY from the pre-computed `timesheet_recommendations`
table — no on-demand Workfront calls or LLM calls in the request path, so
the dashboard stays fast, per the architecture doc.

POST /decision writes the manager's action back to Workfront and updates
the local row. POST /revalidate re-runs the pipeline for one employee/period
(e.g. after the employee resubmits a corrected timesheet).
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.db_models import TimesheetRecommendation
from app.models.schemas import DecisionRequest, DecisionResponse, RecommendationRecord
from app.services.pipeline import run_pipeline_for_period
from app.services.workfront_client import get_workfront_client

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


def _row_to_record(row: TimesheetRecommendation) -> RecommendationRecord:
    return RecommendationRecord(
        employee_id=row.employee_id,
        employee_name=row.employee_name,
        period_start=row.period_start,
        period_end=row.period_end,
        checklist=row.checklist,
        recommendation=row.recommendation,
        reason=row.reason,
        next_best_actions=row.next_best_actions,
        computed_at=row.computed_at,
        manager_decision=row.manager_decision,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
    )


@router.get("", response_model=list[RecommendationRecord])
def list_recommendations(
    manager_id: str | None = None,
    period_start: date | None = None,
    pending_only: bool = True,
    recommendation: str | None = None,
    exclude_not_ready: bool = True,
    db: Session = Depends(get_db),
) -> list[RecommendationRecord]:
    """The manager's queue. Filters happen server-side against the
    pre-computed table — nothing here triggers a fresh pipeline run.

    Query params:
      manager_id        filter to one manager's queue (Workfront approverID)
      period_start      filter to one timesheet period (YYYY-MM-DD)
      pending_only      only rows the manager hasn't decided yet (default true)
      recommendation    filter to APPROVE / REVIEW / FLAGGED / NOT_READY
      exclude_not_ready hide not-yet-submitted timesheets (default true)
    """
    query = db.query(TimesheetRecommendation)
    if manager_id:
        query = query.filter(TimesheetRecommendation.manager_id == manager_id)
    if period_start:
        query = query.filter(TimesheetRecommendation.period_start == period_start)
    if pending_only:
        query = query.filter(TimesheetRecommendation.manager_decision.is_(None))
    if recommendation:
        query = query.filter(TimesheetRecommendation.recommendation == recommendation.upper())
    if exclude_not_ready:
        query = query.filter(TimesheetRecommendation.recommendation != "NOT_READY")

    rows = query.order_by(TimesheetRecommendation.computed_at.desc()).all()
    return [_row_to_record(r) for r in rows]


@router.get("/stats")
def queue_stats(manager_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Counts by recommendation for the dashboard's summary cards. Counts only
    undecided rows. NOT_READY is reported separately so the frontend can show
    'X waiting to be submitted' without lumping it into the action queue."""
    query = db.query(TimesheetRecommendation).filter(TimesheetRecommendation.manager_decision.is_(None))
    if manager_id:
        query = query.filter(TimesheetRecommendation.manager_id == manager_id)

    counts = {"APPROVE": 0, "REVIEW": 0, "FLAGGED": 0, "NOT_READY": 0}
    for row in query.all():
        counts[row.recommendation] = counts.get(row.recommendation, 0) + 1

    actionable = counts["APPROVE"] + counts["REVIEW"] + counts["FLAGGED"]
    return {
        "manager_id": manager_id,
        "counts": counts,
        "actionable_total": actionable,
        "not_ready_total": counts["NOT_READY"],
    }


@router.get("/{employee_id}/{period_start}", response_model=RecommendationRecord)
def get_recommendation(employee_id: str, period_start: date, db: Session = Depends(get_db)) -> RecommendationRecord:
    row = db.get(TimesheetRecommendation, (employee_id, period_start))
    if row is None:
        raise HTTPException(status_code=404, detail="No recommendation found for that employee/period.")
    return _row_to_record(row)


@router.post("/{employee_id}/{period_start}/decision", response_model=DecisionResponse)
def submit_decision(
    employee_id: str,
    period_start: date,
    body: DecisionRequest,
    db: Session = Depends(get_db),
) -> DecisionResponse:
    row = db.get(TimesheetRecommendation, (employee_id, period_start))
    if row is None:
        raise HTTPException(status_code=404, detail="No recommendation found for that employee/period.")

    client = get_workfront_client()
    write_back_ok = client.write_decision(employee_id, period_start, body.decision, body.note)
    if not write_back_ok:
        raise HTTPException(status_code=502, detail="Failed to write decision back to Workfront.")

    row.manager_decision = body.decision
    row.decided_at = datetime.utcnow()
    row.decided_by = body.decided_by
    db.commit()

    return DecisionResponse(
        employee_id=employee_id,
        period_start=period_start,
        decision=body.decision,
        decided_at=row.decided_at,
        workfront_write_back=write_back_ok,
    )


@router.post("/{employee_id}/{period_start}/revalidate", response_model=RecommendationRecord)
def revalidate(
    employee_id: str,
    period_start: date,
    manager_id: str | None = None,
    db: Session = Depends(get_db),
) -> RecommendationRecord:
    """Re-runs ingestion -> rules -> narrative for one employee/period —
    used after a manager returns a timesheet and the employee resubmits."""
    try:
        row = run_pipeline_for_period(db, employee_id, period_start, manager_id=manager_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _row_to_record(row)
