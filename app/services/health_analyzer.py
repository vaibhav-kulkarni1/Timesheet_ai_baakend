"""
Employee Health analysis for the dashboard's "Employee Health" card.

Unlike the per-timesheet recommendation and anomaly cards, this one aggregates
ACROSS each employee's timesheet history to surface burnout risk. Two signals,
both computable from the stored checklists (no new Workfront data needed):

  1. Sustained overtime — total overtime hours over a recent window.
  2. No recent leave — no PTO/holiday logged in the last ~60 days.

Risk is scored HIGH / MEDIUM / LOW / NONE from those signals. Thresholds live
here so they're easy to tune.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

# --- Tunable thresholds (calibrated for a 40h services team) --------------
# Burnout here = chronic low-grade overtime without recovery, not one big week.
OVERTIME_WINDOW_DAYS = 60          # how far back to sum overtime
NO_LEAVE_WINDOW_DAYS = 60          # "no PTO in the last N days"
OT_HIGH_HOURS = 15.0               # sustained ~2h/wk extra over the window
OT_MEDIUM_HOURS = 8.0              # moderate accumulated overtime
MIN_ACTIVE_PERIODS = 2             # need at least this many worked periods to judge
CONSISTENCY_PERIODS = 3            # 3+ overtime periods bumps risk up one level


def _period_overtime(checklist: dict) -> float:
    """Overtime for one period = positive variance (logged over expected).
    Uses the stored, already-prorated expected_hours, so short weeks don't
    count as negative and full-week overtime is captured correctly."""
    variance = float(checklist.get("variance", 0) or 0)
    return variance if variance > 0 else 0.0


def analyze_employee(name: str, rows: list) -> dict | None:
    """
    rows: list of TimesheetRecommendation for ONE employee.
    Returns a health record dict, or None if there isn't enough signal to judge
    (e.g. the person only has empty/future timesheets).
    """
    today = date.today()
    ot_cutoff = today - timedelta(days=OVERTIME_WINDOW_DAYS)
    leave_cutoff = today - timedelta(days=NO_LEAVE_WINDOW_DAYS)

    # Only consider periods where the person actually logged work.
    worked = [
        r for r in rows
        if float((r.checklist or {}).get("logged_hours", 0) or 0) > 0
    ]
    if len(worked) < MIN_ACTIVE_PERIODS:
        return None

    # --- Signal 1: total overtime in the recent window ---
    recent = [r for r in worked if r.period_start >= ot_cutoff]
    window_rows = recent if recent else worked  # fall back to all if no recent data
    total_overtime = sum(_period_overtime(r.checklist) for r in window_rows)
    overtime_weeks = sum(1 for r in window_rows if _period_overtime(r.checklist) > 0)

    # --- Signal 2: recent leave ---
    recent_pto = sum(
        float((r.checklist or {}).get("pto_hours", 0) or 0)
        for r in worked if r.period_end >= leave_cutoff
    )
    no_recent_leave = recent_pto == 0

    # Most recent worked period, for display.
    latest = max(worked, key=lambda r: r.period_start)

    # --- Risk scoring ---
    ot_high = total_overtime >= OT_HIGH_HOURS
    ot_medium = total_overtime >= OT_MEDIUM_HOURS

    if ot_high and no_recent_leave:
        risk = "HIGH"
    elif ot_high or (ot_medium and no_recent_leave):
        risk = "MEDIUM"
    elif ot_medium:
        risk = "LOW"
    else:
        risk = "NONE"

    # Consistency booster: recurring overtime (3+ periods) is a stronger
    # burnout signal than one heavy week, so bump risk up one level.
    if overtime_weeks >= CONSISTENCY_PERIODS and risk != "HIGH":
        risk = {"NONE": "LOW", "LOW": "MEDIUM", "MEDIUM": "HIGH"}[risk]

    # --- Human-readable reason ---
    parts: list[str] = []
    if total_overtime > 0:
        parts.append(
            f"{total_overtime:g}h of overtime across {overtime_weeks} "
            f"period{'s' if overtime_weeks != 1 else ''} in the last {OVERTIME_WINDOW_DAYS} days"
        )
    if no_recent_leave:
        parts.append(f"no PTO or holiday logged in the last {NO_LEAVE_WINDOW_DAYS} days")
    reason = f"{name} has " + " and ".join(parts) + "." if parts else f"{name} shows no burnout signals."

    return {
        "employee_id": latest.employee_id,
        "employee_name": name,
        "risk": risk,
        "reason": reason,
        "metrics": {
            "overtime_hours_window": round(total_overtime, 1),
            "overtime_periods": overtime_weeks,
            "recent_pto_hours": round(recent_pto, 1),
            "no_recent_leave": no_recent_leave,
            "window_days": OVERTIME_WINDOW_DAYS,
        },
        "latest_period_start": latest.period_start.isoformat(),
        "latest_period_end": latest.period_end.isoformat(),
    }


_RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}


def analyze_all(rows: list) -> list[dict]:
    """Group all recommendation rows by employee and analyze each."""
    by_employee: dict[str, list] = defaultdict(list)
    for r in rows:
        by_employee[r.employee_id].append(r)

    results: list[dict] = []
    for emp_rows in by_employee.values():
        name = emp_rows[0].employee_name
        record = analyze_employee(name, emp_rows)
        if record is not None:
            results.append(record)

    results.sort(key=lambda h: (_RISK_ORDER.get(h["risk"], 9), -h["metrics"]["overtime_hours_window"]))
    return results
