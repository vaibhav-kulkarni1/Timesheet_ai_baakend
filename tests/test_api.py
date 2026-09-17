"""
End-to-end API tests: run the pipeline against MockWorkfrontClient + the
mock narrative generator, then exercise the Serving/Manager Action API.
Uses an isolated in-memory SQLite DB per test run (env var set before app
import) so this never touches your real dev DB.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["MOCK_LLM"] = "true"
os.environ["WORKFRONT_MODE"] = "mock"

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.pipeline import run_pipeline_for_period  # noqa: E402

PERIOD = date(2026, 8, 11)


@pytest.fixture(autouse=True)
def _db():
    init_db()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seeded_employee():
    db = SessionLocal()
    try:
        row = run_pipeline_for_period(db, "emp_test", PERIOD, manager_id="mgr_test")
        return row.employee_id, row.period_start, row.recommendation
    finally:
        db.close()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_recommendations_returns_seeded_row(client, seeded_employee):
    employee_id, period_start, _ = seeded_employee
    resp = client.get("/api/recommendations", params={"manager_id": "mgr_test"})
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["employee_id"] == employee_id for r in rows)


def test_get_single_recommendation(client, seeded_employee):
    employee_id, period_start, recommendation = seeded_employee
    resp = client.get(f"/api/recommendations/{employee_id}/{period_start}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendation"] == recommendation
    assert body["reason"]  # narrative was generated
    assert isinstance(body["next_best_actions"], list)


def test_get_missing_recommendation_404s(client):
    resp = client.get("/api/recommendations/does_not_exist/2026-01-01")
    assert resp.status_code == 404


def test_decision_flow_marks_row_decided_and_excludes_from_pending_queue(client, seeded_employee):
    employee_id, period_start, _ = seeded_employee

    resp = client.post(
        f"/api/recommendations/{employee_id}/{period_start}/decision",
        json={"decision": "approve", "note": "Looks good", "decided_by": "mgr_test"},
    )
    assert resp.status_code == 200
    assert resp.json()["workfront_write_back"] is True

    pending = client.get("/api/recommendations", params={"manager_id": "mgr_test", "pending_only": True}).json()
    assert not any(r["employee_id"] == employee_id for r in pending)

    all_rows = client.get("/api/recommendations", params={"manager_id": "mgr_test", "pending_only": False}).json()
    match = next(r for r in all_rows if r["employee_id"] == employee_id)
    assert match["manager_decision"] == "approve"


def test_revalidate_recomputes_the_row(client, seeded_employee):
    employee_id, period_start, _ = seeded_employee
    resp = client.post(f"/api/recommendations/{employee_id}/{period_start}/revalidate")
    assert resp.status_code == 200
    assert resp.json()["employee_id"] == employee_id
