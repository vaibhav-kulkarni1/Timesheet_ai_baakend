"""
SQLAlchemy ORM models. Two tables, matching the architecture doc:

- `timesheet_entries_staging` — raw normalized entries the Ingestion Service
  writes, keyed by (employee_id, period_start). Input to the Rules Engine.
- `timesheet_recommendations`  — the pre-computed store the Serving API
  reads. This is the ONLY table the dashboard's read path touches.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimesheetEntryStaging(Base):
    __tablename__ = "timesheet_entries_staging"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String, index=True)
    employee_name: Mapped[str] = mapped_column(String)
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date)
    entry_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    hours: Mapped[float] = mapped_column(Float)
    task_type: Mapped[str] = mapped_column(String)
    utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_notes: Mapped[bool] = mapped_column(Boolean, default=False)
    is_weekend: Mapped[bool] = mapped_column(Boolean, default=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TimesheetRecommendation(Base):
    __tablename__ = "timesheet_recommendations"

    employee_id: Mapped[str] = mapped_column(String, primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    employee_name: Mapped[str] = mapped_column(String)
    period_end: Mapped[date] = mapped_column(Date)
    checklist: Mapped[dict] = mapped_column(JSON)
    recommendation: Mapped[str] = mapped_column(String, index=True)
    reason: Mapped[str] = mapped_column(String)
    next_best_actions: Mapped[list] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    manager_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    # In a real deployment, add manager_id (or a manager<->employee mapping
    # table) so /api/recommendations?manager_id=... can filter server-side
    # instead of joining against an org-chart service at request time.
    manager_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)


class HourEntryStaging(Base):
    """
    Individual HOUR entries pulled from Workfront's /hour/search, aggregated
    by the Utilization + Productivity cards. Separate from the timesheet-header
    tables because it's a different Workfront object (per-hour, keyed by owner
    + project + task) feeding a different set of cards.
    """
    __tablename__ = "hour_entries_staging"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hour_id: Mapped[str] = mapped_column(String, index=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    owner_name: Mapped[str] = mapped_column(String)
    owner_role: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    project_name: Mapped[str | None] = mapped_column(String, index=True)
    project_status: Mapped[str | None] = mapped_column(String, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    task_name: Mapped[str | None] = mapped_column(String, nullable=True)
    task_status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    task_planned_work: Mapped[float | None] = mapped_column(Float, nullable=True)   # task.work
    task_actual_work: Mapped[float | None] = mapped_column(Float, nullable=True)    # task.actualWork
    hours: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
