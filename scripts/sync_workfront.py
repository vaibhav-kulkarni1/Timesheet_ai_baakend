"""
Pull real timesheets from Workfront and run each through the pipeline.

Header-only: /tshet/search returns rich headers (status, regularHours,
overtimeHours, totalHours, hasNotes, approverID) — enough for the full rules
engine. This script converts each header directly (no extra per-row API call)
and runs rules -> narrative -> store.

Usage:
    python -m scripts.sync_workfront                 # all timesheets (up to SYNC_LIMIT)
    python -m scripts.sync_workfront <workfront_userID>   # just one user

Env options:
    SYNC_LIMIT=200          how many timesheet headers to pull
    SYNC_ONLY_WITH_HOURS=0  1 = skip timesheets with 0 logged hours
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from app.db.session import SessionLocal, init_db
from app.models.db_models import TimesheetRecommendation, TimesheetEntryStaging
from app.services.narrative_generator import generate_narrative
from app.services.rules_engine import evaluate_timesheet
from app.services.workfront_client import get_workfront_client, RealWorkfrontClient


def main() -> None:
    limit = int(os.environ.get("SYNC_LIMIT", "200"))
    only_with_hours = os.environ.get("SYNC_ONLY_WITH_HOURS", "0") == "1"
    user_id = sys.argv[1] if len(sys.argv) > 1 else None

    init_db()
    client = get_workfront_client()

    headers = client.list_timesheets(user_id=user_id, limit=limit)
    if not headers:
        print("No timesheets returned. Check WORKFRONT_MODE=real, base URL, and token.")
        return

    print(f"Pulled {len(headers)} timesheet header(s).\n")
    print(f"{'Employee':22s} {'Period':12s} {'Status':7s} {'Rec':10s} Reason")
    print("-" * 95)

    db = SessionLocal()
    processed = skipped = failed = 0
    try:
        for h in headers:
            total_hours = float(h.get("totalHours") or 0)
            if only_with_hours and total_hours == 0:
                skipped += 1
                continue

            try:
                # Convert the header we already have into entries — no extra
                # API call per timesheet. Works for the real client; the mock
                # client falls back to its own generator.
                if isinstance(client, RealWorkfrontClient):
                    entries = client._header_to_entries(h)
                else:
                    from datetime import datetime as _dt
                    ps = _dt.strptime(str(h["startDate"])[:10], "%Y-%m-%d").date()
                    entries = client.fetch_entries(str(h.get("userID") or h.get("ID")), ps)

                if not entries:
                    skipped += 1
                    continue

                employee_id = entries[0].employee_id
                period_start = entries[0].period_start
                manager_id = h.get("approverID")

                # Stage the raw entries (replace-on-refresh).
                db.query(TimesheetEntryStaging).filter(
                    TimesheetEntryStaging.employee_id == employee_id,
                    TimesheetEntryStaging.period_start == period_start,
                ).delete()
                for e in entries:
                    db.add(TimesheetEntryStaging(
                        employee_id=e.employee_id, employee_name=e.employee_name,
                        period_start=e.period_start, period_end=e.period_end,
                        entry_id=e.entry_id,
                        status=e.status.value if hasattr(e.status, "value") else e.status,
                        hours=e.hours, task_type=e.task_type,
                        utilization_pct=e.utilization_pct, has_notes=e.has_notes,
                        is_weekend=e.is_weekend, is_duplicate=e.is_duplicate,
                    ))

                result = evaluate_timesheet(entries)
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
                row.computed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                row.manager_id = manager_id
                row.manager_decision = None
                row.decided_at = None
                row.decided_by = None
                db.commit()

                processed += 1
                name = entries[0].employee_name
                print(f"{name:22s} {str(period_start):12s} {str(h.get('status','')):7s} "
                      f"{row.recommendation:10s} {row.reason}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                db.rollback()
                nm = h.get("displayName", "(unknown)")[:20]
                print(f"{nm:22s} {str(h.get('startDate',''))[:10]:12s} FAILED: {e}")
    finally:
        db.close()

    print(f"\nDone. processed={processed}  skipped={skipped}  failed={failed}")
    if processed:
        print("\nNext:  python -m uvicorn app.main:app --reload")
        print("Then:  http://localhost:8000/docs  ->  GET /api/recommendations")


if __name__ == "__main__":
    main()
