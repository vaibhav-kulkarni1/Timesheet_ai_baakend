"""
Pull HOUR entries from Workfront and store them for the Utilization +
Productivity cards.

For each user found in the timesheet recommendations (so we reuse the users
we already know about), pulls all their hour entries via /hour/search and
stores them in hour_entries_staging.

Delete-and-replace: the active user list comes from timesheet_recommendations,
which sync_workfront.py now keeps in sync with the (filtered) Workfront
query. Any owner whose hour data is still sitting in hour_entries_staging
but who no longer appears in that active list (e.g. their timesheet stopped
matching the status filter) is cleaned up here too, so Utilization /
Productivity don't keep showing stale employees.

Usage:
    python -m scripts.sync_hours                 # all known users
    python -m scripts.sync_hours <workfront_userID>   # one user

Requires the same .env as the timesheet sync (WORKFRONT_MODE=real, base URL,
token). Run scripts.sync_workfront first so we have the user list.
"""
from __future__ import annotations

import sys

from app.db.session import SessionLocal, init_db
from app.models.db_models import HourEntryStaging, TimesheetRecommendation
from app.services.workfront_client import get_workfront_client


def main() -> None:
    one_user = sys.argv[1] if len(sys.argv) > 1 else None

    init_db()
    client = get_workfront_client()
    db = SessionLocal()

    try:
        if one_user:
            user_ids = [one_user]
        else:
            # Reuse the users we already discovered via the timesheet sync.
            user_ids = [
                row[0] for row in
                db.query(TimesheetRecommendation.employee_id).distinct().all()
            ]

        if not user_ids:
            print("No users found. Run 'python -m scripts.sync_workfront' first.")
            return

        # --- Delete-and-replace ---------------------------------------
        # Only run the broad cleanup during a full sync (no single-user
        # argument) — otherwise a one-user spot-check would wipe out every
        # other employee's hour data.
        if not one_user:
            wiped = db.query(HourEntryStaging).filter(
                ~HourEntryStaging.owner_id.in_(user_ids)
            ).delete(synchronize_session=False)
            db.commit()
            if wiped:
                print(f"Cleared {wiped} stale hour entrie(s) for employees no "
                      f"longer in the active (Submitted) set.\n")

        print(f"Pulling hours for {len(user_ids)} user(s)...\n")
        total_entries = 0
        users_with_hours = 0

        for uid in user_ids:
            try:
                entries = client.fetch_hours(uid)
            except Exception as e:  # noqa: BLE001
                print(f"  {uid[:12]}… FAILED: {e}")
                continue

            if not entries:
                continue

            # Replace-on-refresh for this owner.
            db.query(HourEntryStaging).filter(HourEntryStaging.owner_id == uid).delete()
            for e in entries:
                db.add(HourEntryStaging(
                    hour_id=e.hour_id,
                    owner_id=e.owner_id,
                    owner_name=e.owner_name,
                    owner_role=e.owner_role,
                    project_id=e.project_id,
                    project_name=e.project_name,
                    project_status=e.project_status,
                    task_id=e.task_id,
                    task_name=e.task_name,
                    task_status=e.task_status,
                    task_planned_work=e.task_planned_work,
                    task_actual_work=e.task_actual_work,
                    hours=e.hours,
                    entry_date=e.entry_date,
                ))
            db.commit()
            total_entries += len(entries)
            users_with_hours += 1
            name = entries[0].owner_name or uid[:12]
            print(f"  {name:24s} {len(entries):4d} hour entries")

        print(f"\nDone. {total_entries} hour entries across {users_with_hours} user(s).")
        if total_entries:
            print("\nNow the Utilization + Productivity endpoints have data:")
            print("  GET /api/utilization")
            print("  GET /api/productivity")
    finally:
        db.close()


if __name__ == "__main__":
    main()