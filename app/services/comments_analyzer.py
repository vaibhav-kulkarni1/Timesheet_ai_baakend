"""
Daily comments for the Approval Recommendation card.

Rolls up the per-day comments logged on HOUR entries (Workfront's
`description` field) into the one-row-per-employee-per-week shape Card 1
uses. Joins by employee + date falling inside the timesheet's period — this
is more robust than joining on timesheet_id alone, since not every hour
entry is guaranteed to carry that link, but every entry always has an
entry_date and owner_id.

Pure aggregation over already-stored HourEntryStaging rows — no Workfront or
LLM calls in the request path.
"""
from __future__ import annotations

from datetime import date

_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_daily_comments(hour_rows: list, period_start: date, period_end: date) -> list[dict]:
    """
    hour_rows: HourEntryStaging ORM rows for ONE employee (already filtered
    to that employee's owner_id by the caller).

    Returns a list of {entry_date, comment} for rows whose entry_date falls
    inside [period_start, period_end] and that actually have a comment,
    de-duplicated per day (a day with multiple hour entries and the same
    comment text collapses to one line) and sorted chronologically.
    """
    seen: dict[date, str] = {}
    for r in hour_rows:
        if not r.comment:
            continue
        if not (period_start <= r.entry_date <= period_end):
            continue
        # If a day has multiple distinct comments (different tasks), join them;
        # if it's the same comment repeated across entries, keep it once.
        existing = seen.get(r.entry_date)
        if existing is None:
            seen[r.entry_date] = r.comment
        elif r.comment not in existing:
            seen[r.entry_date] = f"{existing}; {r.comment}"

    return [
        {"entry_date": d, "comment": c}
        for d, c in sorted(seen.items())
    ]


def format_comments_summary(daily_comments: list[dict]) -> str:
    """'Mon: fixed API bug | Wed: client call ran long' — the display format
    for Card 1's comments column."""
    parts = []
    for item in daily_comments:
        d: date = item["entry_date"]
        abbr = _WEEKDAY_ABBR[d.weekday()]
        parts.append(f"{abbr}: {item['comment']}")
    return " | ".join(parts)