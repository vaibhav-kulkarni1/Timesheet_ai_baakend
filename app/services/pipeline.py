"""
Orchestrates Ingestion -> Rules Engine -> Narrative Generator -> Store for
one timesheet (employee/period). This is what the webhook handler, the
scheduled poller, the manual /revalidate endpoint, and the sync script call.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.db_models import TimesheetRecommendation
from app.services.ingestion import ingest_period
from app.services.narrative_generator import generate_narrative
from app.services.rules_engine import evaluate_timesheet
from app.services.workfront_client import WorkfrontClient


def run_pipeline_for_period(
    db: Session,
    employee_id: str,
    period_start: date,
    manager_id: str | None = None,
    client: WorkfrontClient | None = None,
    overtime_pre_approved: bool = False,
) -> TimesheetRecommendation:
    entries = ingest_period(db, employee_id, period_start, client=client)

    if not entries:
        raise ValueError(f"No timesheet entries found for {employee_id} / {period_start}")

    result = evaluate_timesheet(entries, overtime_pre_approved=overtime_pre_approved)
    narrative = generate_narrative(result.checklist, result.recommendation, result.triggered_rule)

    row = db.get(TimesheetRecommendation, (employee_id, period_start))
    if row is None:
        row = TimesheetRecommendation(employee_id=employee_id, period_start=period_start)
        db.add(row)

    row.employee_name = entries[0].employee_name
    row.period_end = entries[0].period_end
    row.checklist = result.checklist.model_dump()
    row.recommendation = result.recommendation.value
    row.reason = narrative.reason
    row.next_best_actions = narrative.next_best_actions
    row.computed_at = datetime.utcnow()
    if manager_id:
        row.manager_id = manager_id
    row.manager_decision = None
    row.decided_at = None
    row.decided_by = None

    db.commit()
    db.refresh(row)

    _publish_update(row)
    return row


def _publish_update(row: TimesheetRecommendation) -> None:
    """Fire-and-forget notify for the real-time layer. Kept non-fatal so a
    Redis outage never breaks the pipeline — dashboard falls back to polling."""
    try:
        from app.realtime.events import publish_recommendation_update

        publish_recommendation_update(row)
    except Exception:
        pass
