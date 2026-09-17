"""
Analytics for the Utilization Trends and Productivity Insights cards,
computed from stored HOUR entries (Workfront /hour/search).

Both are pure aggregation over hour_entries_staging — no LLM, no Workfront
calls in the request path. All numbers are deterministic.

Data available per hour entry (from the real /hour/search response):
  hours, entry_date, project_name/status, owner_role,
  task_name/status, task_planned_work (task.work), task_actual_work (task.actualWork)

What we can and can't compute (honest limits):
  ✓ Project utilization  = hours grouped by project
  ✓ Role utilization     = hours grouped by owner role (proxy for department)
  ✓ Planned vs actual    = task.work vs task.actualWork (dedup per task)
  ✓ Deliverables done    = distinct tasks with status == CPL
  ✗ Billable ratio       = no billable flag in the data; omitted, not faked
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

# Workfront task status codes.
TASK_COMPLETE = "CPL"


def _rows_in_window(rows: list, days: int | None) -> list:
    if not days:
        return rows
    cutoff = date.today() - timedelta(days=days)
    return [r for r in rows if r.entry_date >= cutoff]


# ---------------------------------------------------------------------------
# Utilization
# ---------------------------------------------------------------------------
def utilization(rows: list, window_days: int | None = None) -> dict:
    """Hours grouped by project and by role, plus totals."""
    rows = _rows_in_window(rows, window_days)
    total_hours = sum(r.hours for r in rows)

    by_project: dict[str, float] = defaultdict(float)
    by_role: dict[str, float] = defaultdict(float)
    for r in rows:
        by_project[r.project_name or "Unassigned"] += r.hours
        by_role[r.owner_role or "Unassigned"] += r.hours

    def _distribution(mapping: dict) -> list[dict]:
        items = [
            {
                "name": name,
                "hours": round(h, 1),
                "pct": round(100 * h / total_hours, 1) if total_hours else 0.0,
            }
            for name, h in mapping.items()
        ]
        items.sort(key=lambda x: -x["hours"])
        return items

    return {
        "total_hours": round(total_hours, 1),
        "window_days": window_days,
        "by_project": _distribution(by_project),
        "by_role": _distribution(by_role),
    }


# ---------------------------------------------------------------------------
# Productivity
# ---------------------------------------------------------------------------
def productivity(rows: list, window_days: int | None = None) -> dict:
    """
    Planned vs actual (from task.work vs task.actualWork, de-duplicated per
    task so a task with several hour entries is only counted once), plus a
    count of completed tasks (deliverables).
    """
    rows = _rows_in_window(rows, window_days)

    # De-dup task-level planned/actual: a task appears on many hour rows, but
    # task.work / task.actualWork are task-level totals, so count each task once.
    tasks: dict[str, dict] = {}
    logged_hours = 0.0
    for r in rows:
        logged_hours += r.hours
        if not r.task_id:
            continue
        if r.task_id not in tasks:
            tasks[r.task_id] = {
                "name": r.task_name,
                "planned": r.task_planned_work or 0.0,
                "actual": r.task_actual_work or 0.0,
                "status": r.task_status,
            }

    planned_hours = sum(t["planned"] for t in tasks.values())
    actual_hours = sum(t["actual"] for t in tasks.values())
    completed = [t for t in tasks.values() if t["status"] == TASK_COMPLETE]

    variance = actual_hours - planned_hours
    efficiency = round(100 * planned_hours / actual_hours, 1) if actual_hours else None

    return {
        "window_days": window_days,
        "planned_hours": round(planned_hours, 1),
        "actual_hours": round(actual_hours, 1),
        "logged_hours": round(logged_hours, 1),
        "variance_hours": round(variance, 1),
        "efficiency_pct": efficiency,   # planned/actual: >100 = under budget
        "tasks_total": len(tasks),
        "deliverables_completed": len(completed),
    }


# ---------------------------------------------------------------------------
# Helpers to load rows from the DB into lightweight objects
# ---------------------------------------------------------------------------
class _Row:
    """Lightweight view over a HourEntryStaging ORM row (or dict)."""
    __slots__ = ("hours", "entry_date", "project_name", "owner_role",
                 "task_id", "task_name", "task_status",
                 "task_planned_work", "task_actual_work", "owner_id")

    def __init__(self, o):
        self.hours = float(o.hours or 0)
        self.entry_date = o.entry_date
        self.project_name = o.project_name
        self.owner_role = o.owner_role
        self.task_id = o.task_id
        self.task_name = o.task_name
        self.task_status = o.task_status
        self.task_planned_work = o.task_planned_work
        self.task_actual_work = o.task_actual_work
        self.owner_id = o.owner_id


def rows_from_orm(orm_rows: list) -> list:
    return [_Row(o) for o in orm_rows]
