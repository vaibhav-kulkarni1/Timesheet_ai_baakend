"""
Pydantic models — the data contracts passed between pipeline stages and
returned by the API. These map 1:1 onto the shapes in the architecture doc
(section 2 TimesheetEntry, section 3 Checklist, section 5 the DB row).
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TaskType(str, Enum):
    project = "project"
    task = "task"
    pto = "PTO"
    holiday = "holiday"


class EntryStatus(str, Enum):
    open = "Open"
    submitted = "Submitted"
    closed = "Closed"
    rejected = "Rejected"


class TimesheetEntry(BaseModel):
    """Normalized shape of one Workfront timesheet line item."""

    employee_id: str
    employee_name: str
    period_start: date
    period_end: date
    entry_id: str
    status: EntryStatus
    hours: float = Field(ge=0)
    task_type: str  # kept as free string (project/task/PTO/holiday/etc.) per source doc
    utilization_pct: Optional[float] = None
    has_notes: bool = False
    is_weekend: bool = False
    is_duplicate: bool = False


class HourEntry(BaseModel):
    """Normalized shape of one Workfront HOUR entry (from /hour/search).
    Feeds the Utilization + Productivity cards."""

    hour_id: str
    owner_id: str
    owner_name: str
    owner_role: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    project_status: Optional[str] = None
    task_id: Optional[str] = None
    task_name: Optional[str] = None
    task_status: Optional[str] = None
    task_planned_work: Optional[float] = None
    task_actual_work: Optional[float] = None
    hours: float = Field(ge=0)
    entry_date: date


class Recommendation(str, Enum):
    approve = "APPROVE"
    review = "REVIEW"
    flagged = "FLAGGED"
    not_ready = "NOT_READY"  # timesheet not submitted yet / nothing to approve


class Checklist(BaseModel):
    """Deterministic output of the Rules Engine. No LLM involvement."""

    expected_hours: float
    logged_hours: float
    pto_hours: float
    variance: float
    unexplained_hours: float
    rejected_count: int
    open_count: int
    submitted_count: int
    closed_count: int
    weekend_hours: float
    duplicate_count: int
    non_standard_util_pct: float


class RuleTrigger(BaseModel):
    """Which rule fired and why — used both for the decision and for
    grounding the narrative generator so it can't wander from the facts."""

    rule: str
    detail: str


class RulesEngineResult(BaseModel):
    checklist: Checklist
    recommendation: Recommendation
    triggered_rule: RuleTrigger  # the first (highest-priority) rule that fired


class Narrative(BaseModel):
    reason: str
    next_best_actions: list[str]


class RecommendationRecord(BaseModel):
    """Full row as stored / served — matches the `timesheet_recommendations` table."""

    employee_id: str
    employee_name: str
    period_start: date
    period_end: date
    checklist: Checklist
    recommendation: Recommendation
    reason: str
    next_best_actions: list[str]
    computed_at: datetime
    manager_decision: Optional[str] = None
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None

    @property
    def is_stale(self) -> bool:
        from app.config import get_settings

        threshold = get_settings().recommendation_staleness_minutes
        age_minutes = (datetime.utcnow() - self.computed_at).total_seconds() / 60
        return age_minutes > threshold


class DecisionRequest(BaseModel):
    decision: Literal["approve", "return_for_correction", "request_info"]
    note: str = ""
    decided_by: str = "unknown_manager"

    @field_validator("note")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class DecisionResponse(BaseModel):
    employee_id: str
    period_start: date
    decision: str
    decided_at: datetime
    workfront_write_back: bool
