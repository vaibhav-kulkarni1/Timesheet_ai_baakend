"""
Step 2 — Rules Engine. Pure Python, deterministic, NO LLM call. This is the
source of truth for every number and for the APPROVE/REVIEW/FLAGGED
decision. The Narrative Generator (step 3) is only ever allowed to explain
what this module already decided.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.config import get_settings
from app.models.schemas import Checklist, Recommendation, RuleTrigger, RulesEngineResult, TimesheetEntry

HOURS_PER_WORKDAY = 8.0


def _workdays_between(period_start: date, period_end: date) -> int:
    """Count weekdays (Mon–Fri) inclusive between two dates. Used to prorate
    expected hours so a partial-week timesheet isn't measured against a full
    40h. Falls back to 5 workdays if the dates are missing or inverted."""
    if not period_start or not period_end or period_end < period_start:
        return 5
    days = (period_end - period_start).days + 1
    workdays = sum(
        1 for i in range(days) if (period_start + timedelta(days=i)).weekday() < 5
    )
    return workdays or 5


def _expected_for_period(entries: list[TimesheetEntry], override: float | None) -> float:
    """Prorated expected hours: 8h × weekdays in the actual timesheet period.
    An explicit override (or the configured default when no dates exist) wins."""
    if override is not None:
        return override
    if entries and entries[0].period_start and entries[0].period_end:
        workdays = _workdays_between(entries[0].period_start, entries[0].period_end)
        return HOURS_PER_WORKDAY * workdays
    return get_settings().default_expected_hours


def compute_checklist(entries: list[TimesheetEntry], expected_hours: float | None = None) -> Checklist:
    expected_hours = _expected_for_period(entries, expected_hours)

    logged = sum(e.hours for e in entries if e.task_type == "project")
    pto = sum(e.hours for e in entries if e.task_type in ("PTO", "holiday"))
    variance = (logged + pto) - expected_hours
    unexplained = expected_hours - logged - pto

    rejected = [e for e in entries if e.status == "Rejected" or e.status.value == "Rejected"]
    open_ = [e for e in entries if e.status == "Open" or e.status.value == "Open"]
    submitted = [e for e in entries if e.status == "Submitted" or e.status.value == "Submitted"]
    closed = [e for e in entries if e.status == "Closed" or e.status.value == "Closed"]
    weekend_hours = sum(e.hours for e in entries if e.is_weekend)
    duplicates = [e for e in entries if e.is_duplicate]
    non_standard_util = [e for e in entries if e.utilization_pct not in (0, None)]

    return Checklist(
        expected_hours=expected_hours,
        logged_hours=logged,
        pto_hours=pto,
        variance=variance,
        unexplained_hours=max(unexplained, 0),
        rejected_count=len(rejected),
        open_count=len(open_),
        submitted_count=len(submitted),
        closed_count=len(closed),
        weekend_hours=weekend_hours,
        duplicate_count=len(duplicates),
        non_standard_util_pct=(len(non_standard_util) / len(entries)) if entries else 0.0,
    )


def decide(checklist: Checklist, overtime_pre_approved: bool = False) -> RuleTrigger:
    """
    Table-driven decision logic, checked in priority order — exactly the
    table from the architecture doc. Kept as an ordered list of
    (predicate, recommendation, message) so new rules can be inserted /
    reordered without touching anything else, including the LLM layer.
    """
    settings = get_settings()

    # Not-ready gate (checked first): an Open timesheet with nothing logged is
    # a future/incomplete period, not a violation — there's simply nothing to
    # approve yet. Only applies when there are zero logged hours AND no
    # rejections/duplicates to surface.
    nothing_logged = checklist.logged_hours == 0 and checklist.pto_hours == 0
    only_open = checklist.open_count > 0 and checklist.submitted_count == 0 and checklist.closed_count == 0
    no_hard_flags = checklist.rejected_count == 0 and checklist.duplicate_count == 0
    if nothing_logged and only_open and no_hard_flags:
        return RuleTrigger(
            rule="NOT_READY",
            detail="Timesheet is still open with no hours logged — nothing to approve yet.",
        )

    rules: list[tuple[bool, Recommendation, str]] = [
        (
            checklist.rejected_count > 0,
            Recommendation.flagged,
            f"{checklist.rejected_count} rejected entr{'y' if checklist.rejected_count == 1 else 'ies'} in this period.",
        ),
        (
            checklist.duplicate_count > 0,
            Recommendation.flagged,
            f"{checklist.duplicate_count} duplicate entr{'y' if checklist.duplicate_count == 1 else 'ies'} detected.",
        ),
        (
            checklist.unexplained_hours > 0,
            Recommendation.review,
            f"{checklist.unexplained_hours:g} unexplained hours (no PTO/holiday covers the gap).",
        ),
        (
            checklist.variance > 0 and not overtime_pre_approved,
            Recommendation.review,
            f"{checklist.variance:g} hours of variance over expected, and overtime was not pre-approved.",
        ),
        (
            checklist.open_count > 0,
            Recommendation.review,
            f"{checklist.open_count} entr{'y' if checklist.open_count == 1 else 'ies'} still in Open status.",
        ),
        (
            checklist.non_standard_util_pct > settings.non_standard_util_threshold,
            Recommendation.review,
            f"{checklist.non_standard_util_pct:.0%} of entries have non-standard utilization "
            f"(threshold {settings.non_standard_util_threshold:.0%}).",
        ),
    ]

    for triggered, recommendation, detail in rules:
        if triggered:
            return RuleTrigger(rule=f"{recommendation.value}", detail=detail)

    return RuleTrigger(rule="APPROVE", detail="All checks passed: no rejections, duplicates, unexplained hours, unapproved overtime, open entries, or excess non-standard utilization.")


def evaluate_timesheet(
    entries: list[TimesheetEntry],
    expected_hours: float | None = None,
    overtime_pre_approved: bool = False,
) -> RulesEngineResult:
    """Single entry point the pipeline calls: entries in, deterministic
    checklist + recommendation out."""
    checklist = compute_checklist(entries, expected_hours=expected_hours)
    trigger = decide(checklist, overtime_pre_approved=overtime_pre_approved)
    recommendation = Recommendation(trigger.rule) if trigger.rule in Recommendation._value2member_map_ else Recommendation.approve
    return RulesEngineResult(checklist=checklist, recommendation=recommendation, triggered_rule=trigger)
