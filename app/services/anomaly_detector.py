"""
Anomaly detection for the dashboard's "AI Anomaly Detection" card.

The rules engine (rules_engine.decide) returns only the single highest-priority
trigger, because that drives the one APPROVE/REVIEW/FLAGGED decision. The
anomaly card is different: it lists EVERY issue found across timesheets, each
with a severity (HIGH / MEDIUM / LOW).

So this module re-reads the same deterministic Checklist and emits one Anomaly
per problem found. No new data source and no LLM — it's the same header-derived
facts, just surfaced individually instead of collapsed to one verdict.

Anomaly types map to the mockup:
    HIGH   excessive_hours   overtime over expected, not pre-approved
    HIGH   duplicate_entry   duplicate hour entries
    HIGH   rejected_entry    a rejected entry is present
    MEDIUM missing_time      logged well under expected, no PTO/holiday cover
    MEDIUM idle_resource     open period, zero hours (needs assignment data
                             for the full "has assignment but 0h" version)
    LOW    open_entries      still-open entries at review time

Severity thresholds live here so they're easy to tune.
"""
from __future__ import annotations

from datetime import date

from app.config import get_settings
from app.models.schemas import Checklist


# How big a shortfall counts as MEDIUM "missing time" (hours).
MISSING_TIME_MIN_GAP = 4.0


def detect_anomalies(
    employee_id: str,
    employee_name: str,
    period_start: date,
    period_end: date,
    checklist: Checklist,
    recommendation: str,
    overtime_pre_approved: bool = False,
) -> list[dict]:
    """Return a list of anomaly dicts for one timesheet. Empty list = clean."""
    settings = get_settings()
    anomalies: list[dict] = []

    def add(anomaly_type: str, severity: str, title: str, description: str) -> None:
        anomalies.append(
            {
                "id": f"{employee_id}:{period_start.isoformat()}:{anomaly_type}",
                "employee_id": employee_id,
                "employee_name": employee_name,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "type": anomaly_type,
                "severity": severity,
                "title": title,
                "description": description,
            }
        )

    # NOT_READY timesheets are future/empty periods — not anomalies.
    if recommendation == "NOT_READY":
        return anomalies

    has_rejection = checklist.rejected_count > 0

    # HIGH — rejected entry present
    if has_rejection:
        n = checklist.rejected_count
        add(
            "rejected_entry", "HIGH", "Rejected Entry",
            f"{employee_name} has {n} rejected entr{'y' if n == 1 else 'ies'} this period that must be corrected and resubmitted.",
        )

    # HIGH — duplicate entries
    if checklist.duplicate_count > 0:
        n = checklist.duplicate_count
        add(
            "duplicate_entry", "HIGH", "Duplicate Entries",
            f"{n} duplicate entr{'y' if n == 1 else 'ies'} detected on {employee_name}'s timesheet (same day, task, and hours logged more than once).",
        )

    # HIGH — excessive hours (overtime over expected, not pre-approved)
    if checklist.variance > 0 and not overtime_pre_approved:
        add(
            "excessive_hours", "HIGH", "Excessive Hours",
            f"{employee_name} logged {checklist.logged_hours:g} hours "
            f"(target {checklist.expected_hours:g}h) — {checklist.variance:g}h of overtime without prior approval.",
        )

    # MEDIUM — missing time (meaningful shortfall not covered by PTO/holiday).
    # Suppressed when a rejection is the real cause of the missing hours.
    if checklist.unexplained_hours >= MISSING_TIME_MIN_GAP and not has_rejection:
        add(
            "missing_time", "MEDIUM", "Missing Time",
            f"{employee_name} has {checklist.unexplained_hours:g} unexplained hours "
            f"({checklist.logged_hours:g} logged of {checklist.expected_hours:g} expected) with no PTO or holiday to account for the gap.",
        )

    # MEDIUM — idle resource (period active but nothing logged, and not the
    # NOT_READY or rejected case). Header-only proxy for "has assignment, 0h".
    if checklist.logged_hours == 0 and checklist.pto_hours == 0 and not has_rejection:
        add(
            "idle_resource", "MEDIUM", "Idle Resource",
            f"{employee_name} has an active timesheet for this period but logged 0 hours.",
        )

    # LOW — still-open entries at review time (but hours were logged)
    if checklist.open_count > 0 and checklist.logged_hours > 0:
        n = checklist.open_count
        verb = "is" if n == 1 else "are"
        add(
            "open_entries", "LOW", "Open Entries",
            f"{n} entr{'y' if n == 1 else 'ies'} on {employee_name}'s timesheet {verb} still in Open status and not yet submitted.",
        )

    # LOW — high non-standard utilization
    if checklist.non_standard_util_pct > settings.non_standard_util_threshold:
        add(
            "non_standard_utilization", "LOW", "Non-Standard Utilization",
            f"{checklist.non_standard_util_pct:.0%} of {employee_name}'s entries have non-standard utilization values.",
        )

    return anomalies


# Ordering so the card can show HIGH first, matching the mockup.
_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def sort_anomalies(anomalies: list[dict]) -> list[dict]:
    return sorted(anomalies, key=lambda a: _SEVERITY_ORDER.get(a["severity"], 99))
