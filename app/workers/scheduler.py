"""
Scheduled poll fallback (architecture doc trigger (b)): in case a Workfront
webhook is missed, re-run the pipeline for every known employee/period on an
interval so recommendations never go stale beyond
RECOMMENDATION_STALENESS_MINUTES.

This is intentionally simple — a real deployment would pull the list of
"active periods needing a check" from Workfront or from a roster table
rather than the hardcoded demo list below.
"""
from __future__ import annotations

import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.db.session import SessionLocal
from app.services.pipeline import run_pipeline_for_period

logger = logging.getLogger(__name__)


def poll_all_active_periods(employee_period_pairs: list[tuple[str, date, str | None]]) -> None:
    """employee_period_pairs: list of (employee_id, period_start, manager_id).
    In production, replace the hardcoded caller in start_scheduler() with a
    query against your roster / active-timesheets source."""
    db = SessionLocal()
    try:
        for employee_id, period_start, manager_id in employee_period_pairs:
            try:
                run_pipeline_for_period(db, employee_id, period_start, manager_id=manager_id)
            except Exception:
                logger.exception("Pipeline run failed for %s / %s", employee_id, period_start)
    finally:
        db.close()


def start_scheduler(employee_period_pairs: list[tuple[str, date, str | None]]) -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        poll_all_active_periods,
        "interval",
        minutes=max(settings.recommendation_staleness_minutes, 5),
        args=[employee_period_pairs],
        id="poll_active_periods",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
