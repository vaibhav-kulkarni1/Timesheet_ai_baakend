"""
Productivity Insights analysis, computed from stored HOUR entries
(Workfront /hour/search).

Computes planned vs actual hours (from task.work vs task.actualWork,
de-duplicated per task) and a count of completed deliverables (tasks with
status CPL). Pure aggregation, deterministic, no LLM or Workfront calls.
"""
from __future__ import annotations

from datetime import date, timedelta

# Workfront task status code for a completed task.
TASK_COMPLETE = "CPL"


def _rows_in_window(rows: list, days: int | None) -> list:
    if not days:
        return rows
    cutoff = date.today() - timedelta(days=days)
    return [r for r in rows if r.entry_date >= cutoff]


def productivity(rows: list, window_days: int | None = None) -> dict:
    """
    Planned vs actual (from task.work vs task.actualWork, de-duplicated per
    task so a task with several hour entries is only counted once), plus a
    count of completed tasks (deliverables).
    """
    rows = _rows_in_window(rows, window_days)

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


class _Row:
    """Lightweight view over a HourEntryStaging ORM row."""
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
