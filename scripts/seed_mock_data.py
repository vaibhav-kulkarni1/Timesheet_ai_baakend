"""
Populates the recommendations table by running the full pipeline against
mock Workfront data for a handful of demo employees, so the dashboard has
something to show immediately after `pip install` + `uvicorn`.

Usage:
    python -m scripts.seed_mock_data
"""
from datetime import date

from app.db.session import SessionLocal, init_db
from app.services.pipeline import run_pipeline_for_period

DEMO_EMPLOYEES = [
    ("emp_1", "mgr_1"),
    ("emp_2", "mgr_1"),
    ("emp_3", "mgr_1"),
    ("emp_4", "mgr_2"),
    ("emp_5", "mgr_2"),
]

PERIOD_START = date(2026, 8, 11)  # a Tuesday in the current sprint, arbitrary demo period


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        for employee_id, manager_id in DEMO_EMPLOYEES:
            row = run_pipeline_for_period(db, employee_id, PERIOD_START, manager_id=manager_id)
            print(f"{employee_id:8s} ({manager_id})  ->  {row.recommendation:8s}  {row.reason}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
