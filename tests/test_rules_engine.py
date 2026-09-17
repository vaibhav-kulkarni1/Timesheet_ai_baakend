"""
Unit tests for the Rules Engine decision table — the one piece of this
system that must never be wrong, since it's the deterministic source of
truth the LLM is only allowed to explain. No DB, no FastAPI, no LLM needed.
"""
from datetime import date

import pytest

from app.models.schemas import EntryStatus, TimesheetEntry
from app.services.rules_engine import evaluate_timesheet

PS = date(2026, 8, 11)
PE = date(2026, 8, 17)


def entry(**overrides) -> TimesheetEntry:
    base = dict(
        employee_id="emp_1",
        employee_name="Test Employee",
        period_start=PS,
        period_end=PE,
        entry_id="e1",
        status=EntryStatus.closed,
        hours=8.0,
        task_type="project",
        utilization_pct=0,
        has_notes=False,
        is_weekend=False,
        is_duplicate=False,
    )
    base.update(overrides)
    return TimesheetEntry(**base)


def test_clean_full_week_is_approved():
    entries = [entry(entry_id=f"e{i}", hours=8.0) for i in range(5)]  # 40h, all closed
    result = evaluate_timesheet(entries)
    assert result.recommendation.value == "APPROVE"
    assert result.checklist.unexplained_hours == 0
    assert result.checklist.variance == 0


def test_rejected_entry_flags_regardless_of_other_conditions():
    entries = [entry(entry_id=f"e{i}", hours=8.0) for i in range(5)]
    entries[0] = entry(entry_id="e0", hours=8.0, status=EntryStatus.rejected)
    result = evaluate_timesheet(entries)
    assert result.recommendation.value == "FLAGGED"
    assert "rejected" in result.triggered_rule.detail.lower()


def test_duplicate_flags_even_without_rejection():
    entries = [entry(entry_id=f"e{i}", hours=8.0) for i in range(5)]
    entries[1] = entry(entry_id="e1", hours=8.0, is_duplicate=True)
    result = evaluate_timesheet(entries)
    assert result.recommendation.value == "FLAGGED"
    assert "duplicate" in result.triggered_rule.detail.lower()


def test_rejected_takes_priority_over_duplicate():
    entries = [entry(entry_id="e0", hours=40.0, status=EntryStatus.rejected, is_duplicate=True)]
    result = evaluate_timesheet(entries)
    assert "rejected" in result.triggered_rule.detail.lower()


def test_unexplained_hours_trigger_review():
    entries = [entry(entry_id="e0", hours=32.0)]  # 8h short, no PTO to cover it
    result = evaluate_timesheet(entries)
    assert result.recommendation.value == "REVIEW"
    assert result.checklist.unexplained_hours == 8.0


def test_pto_covers_the_gap_so_no_unexplained_hours():
    entries = [
        entry(entry_id="e0", hours=32.0, task_type="project"),
        entry(entry_id="e1", hours=8.0, task_type="PTO"),
    ]
    result = evaluate_timesheet(entries)
    assert result.checklist.unexplained_hours == 0
    assert result.recommendation.value == "APPROVE"


def test_overtime_without_preapproval_triggers_review():
    entries = [entry(entry_id="e0", hours=48.0)]  # 8h over expected
    result = evaluate_timesheet(entries, overtime_pre_approved=False)
    assert result.recommendation.value == "REVIEW"


def test_overtime_with_preapproval_does_not_trigger_that_rule():
    entries = [entry(entry_id="e0", hours=48.0)]
    result = evaluate_timesheet(entries, overtime_pre_approved=True)
    # Should fall through the overtime rule; 48h > 40h expected also means
    # unexplained_hours is 0 (clamped), so this should approve.
    assert result.recommendation.value == "APPROVE"


def test_open_entries_trigger_review():
    entries = [
        entry(entry_id="e0", hours=40.0, status=EntryStatus.open),
    ]
    result = evaluate_timesheet(entries)
    assert result.recommendation.value == "REVIEW"
    assert "open" in result.triggered_rule.detail.lower()


def test_high_non_standard_utilization_triggers_review():
    entries = [
        entry(entry_id=f"e{i}", hours=8.0, utilization_pct=-45) for i in range(5)
    ]
    result = evaluate_timesheet(entries)
    assert result.checklist.non_standard_util_pct == 1.0
    assert result.recommendation.value == "REVIEW"


def test_empty_entries_does_not_crash():
    result = evaluate_timesheet([])
    assert result.checklist.non_standard_util_pct == 0.0


# --- Real-data-shaped scenarios (header-only Workfront integration) ---

def test_open_timesheet_with_zero_hours_is_not_ready():
    """An Open timesheet with nothing logged is a future/incomplete period,
    not a violation — mirrors Arun Das's 8/24 and 8/31 real timesheets."""
    entries = [entry(entry_id="e0", hours=0.0, status=EntryStatus.open)]
    result = evaluate_timesheet(entries)
    assert result.recommendation.value == "NOT_READY"


def test_closed_full_week_approves():
    """A Closed 40h timesheet — Arun Das's 8/3, 8/10, 8/17 real timesheets."""
    entries = [entry(entry_id="e0", hours=40.0, status=EntryStatus.closed)]
    result = evaluate_timesheet(entries)
    assert result.recommendation.value == "APPROVE"


def test_open_but_partially_logged_is_not_not_ready():
    """Open with some hours logged should still be evaluated (REVIEW for the
    open status), not silently marked NOT_READY."""
    entries = [entry(entry_id="e0", hours=20.0, status=EntryStatus.open)]
    result = evaluate_timesheet(entries)
    assert result.recommendation.value != "NOT_READY"
