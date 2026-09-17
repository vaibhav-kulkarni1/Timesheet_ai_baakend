"""
Step 1 — Ingestion Service.

Pulls raw entries for one employee/period from Workfront (via the
WorkfrontClient interface), normalizes them (already normalized by the
client into TimesheetEntry — this layer's job is persistence + staging),
and writes them to the staging table keyed by (employee_id, period_start).

Triggered by: webhook handler, scheduled poll (see app/workers/scheduler.py),
or a manual re-validate call from the Serving API.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.db_models import TimesheetEntryStaging
from app.models.schemas import TimesheetEntry
from app.services.workfront_client import WorkfrontClient, get_workfront_client


def ingest_period(
    db: Session,
    employee_id: str,
    period_start: date,
    client: WorkfrontClient | None = None,
) -> list[TimesheetEntry]:
    """Fetch + stage entries for one employee/period. Returns the normalized
    entries so the caller (the pipeline) doesn't have to re-read them back
    from the DB."""
    client = client or get_workfront_client()
    entries = client.fetch_entries(employee_id, period_start)

    # Replace-on-refresh: clear any prior staged rows for this key before
    # writing the new pull, so re-validation doesn't accumulate duplicates.
    db.query(TimesheetEntryStaging).filter(
        TimesheetEntryStaging.employee_id == employee_id,
        TimesheetEntryStaging.period_start == period_start,
    ).delete()

    for e in entries:
        db.add(
            TimesheetEntryStaging(
                employee_id=e.employee_id,
                employee_name=e.employee_name,
                period_start=e.period_start,
                period_end=e.period_end,
                entry_id=e.entry_id,
                status=e.status.value if hasattr(e.status, "value") else e.status,
                hours=e.hours,
                task_type=e.task_type,
                utilization_pct=e.utilization_pct,
                has_notes=e.has_notes,
                is_weekend=e.is_weekend,
                is_duplicate=e.is_duplicate,
            )
        )
    db.commit()
    return entries
