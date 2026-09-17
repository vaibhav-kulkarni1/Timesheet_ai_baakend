"""
Utilization Trends analysis, computed from stored HOUR entries
(Workfront /hour/search).

Aggregates hours by project and by role (role is a proxy for department —
it's what Workfront's hour data exposes). Pure aggregation, deterministic,
no LLM or Workfront calls.

Honest limit: a true billable ratio isn't available (no billable flag in the
hour data), so it's omitted rather than faked.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta


def _rows_in_window(rows: list, days: int | None) -> list:
    if not days:
        return rows
    cutoff = date.today() - timedelta(days=days)
    return [r for r in rows if r.entry_date >= cutoff]


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
