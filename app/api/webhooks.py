"""
Workfront webhook receiver — triggers the pipeline on timesheet submission,
per the architecture doc's ingestion trigger (a). Runs the pipeline inline
for simplicity; swap to a background task queue (Celery/RQ) if pipeline
latency ever becomes a webhook-timeout problem.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.pipeline import run_pipeline_for_period

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class WorkfrontSubmissionEvent(BaseModel):
    employee_id: str
    period_start: date
    manager_id: str | None = None


@router.post("/workfront/timesheet-submitted")
def timesheet_submitted(event: WorkfrontSubmissionEvent, db: Session = Depends(get_db)):
    row = run_pipeline_for_period(db, event.employee_id, event.period_start, manager_id=event.manager_id)
    return {"status": "processed", "recommendation": row.recommendation}
