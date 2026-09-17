"""
Workfront client. Downstream code depends only on the WorkfrontClient
interface, so switching mock <-> real is one line in get_workfront_client(),
driven by WORKFRONT_MODE in .env.

Real integration (Adobe Workfront API v21):
- Primary call: /tshet/search?userID=<id>&fields=<rich header + optional hours>
- The rich HEADER (status, regularHours, overtimeHours, totalHours, hasNotes,
  approverID) is enough for the full rules engine.
- If the response ALSO includes a nested `hours` collection (when the caller
  requests fields=hours:...), we use it for richer per-entry detail
  (weekend, per-day duplicates, PTO vs project). This is automatic and
  optional — absence of nested hours simply falls back to header-only.
"""
from __future__ import annotations

import hashlib
import random
import re
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta

from app.config import get_settings
from app.models.schemas import EntryStatus, TimesheetEntry


class WorkfrontClient(ABC):
    @abstractmethod
    def fetch_entries(self, employee_id: str, period_start: date) -> list[TimesheetEntry]:
        ...

    @abstractmethod
    def write_decision(self, employee_id: str, period_start: date, decision: str, note: str) -> bool:
        ...

    def list_timesheets(self, user_id: str | None = None, limit: int = 200) -> list[dict]:
        return []

    def fetch_hours(self, owner_id: str, limit: int = 2000) -> list:
        """Fetch normalized HOUR entries for one user. Feeds Utilization +
        Productivity cards. Returns list[HourEntry]. Default empty."""
        return []


# ---------------------------------------------------------------------------
# Mock — deterministic fake data for demo/tests.
# ---------------------------------------------------------------------------
class MockWorkfrontClient(WorkfrontClient):
    TASK_TYPES = ["project", "task", "PTO", "holiday"]

    def fetch_entries(self, employee_id: str, period_start: date) -> list[TimesheetEntry]:
        seed = int(hashlib.sha256(f"{employee_id}:{period_start}".encode()).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)
        period_end = period_start + timedelta(days=6)
        entries: list[TimesheetEntry] = []
        for i in range(rng.randint(3, 8)):
            task_type = rng.choices(self.TASK_TYPES, weights=[60, 20, 15, 5])[0]
            status = rng.choices(list(EntryStatus), weights=[10, 55, 30, 5])[0]
            entry_date = period_start + timedelta(days=rng.randint(0, 6))
            entries.append(
                TimesheetEntry(
                    employee_id=employee_id,
                    employee_name=f"Employee {employee_id.upper()}",
                    period_start=period_start,
                    period_end=period_end,
                    entry_id=f"{employee_id}-{period_start}-{i}",
                    status=status,
                    hours=round(rng.uniform(1, 9), 1),
                    task_type=task_type,
                    utilization_pct=rng.choice([None, 0, -20, -45, 10]),
                    has_notes=rng.random() > 0.6,
                    is_weekend=entry_date.weekday() >= 5,
                    is_duplicate=rng.random() > 0.92,
                )
            )
        return entries

    def write_decision(self, employee_id: str, period_start: date, decision: str, note: str) -> bool:
        return True

    def list_timesheets(self, user_id: str | None = None, limit: int = 200) -> list[dict]:
        # A few fake headers so the sync script demos without a real token.
        base = date.today() - timedelta(days=date.today().weekday())
        out = []
        for i in range(3):
            ps = base - timedelta(weeks=i)
            out.append(
                {
                    "ID": f"mock-{i}",
                    "displayName": f"Mock User {ps.month}/{ps.day}/{ps.strftime('%y')} - ...",
                    "userID": "mock-user",
                    "approverID": "mock-manager",
                    "startDate": ps.isoformat(),
                    "endDate": (ps + timedelta(days=6)).isoformat(),
                    "status": "C",
                    "regularHours": 40.0,
                    "overtimeHours": 0.0,
                    "totalHours": 40.0,
                    "hasNotes": False,
                }
            )
        return out


# ---------------------------------------------------------------------------
# Real Workfront implementation.
# ---------------------------------------------------------------------------
_DISPLAYNAME_RE = re.compile(r"^(?P<name>.*?)\s+\d{1,2}/\d{1,2}/\d{2,4}\s*-\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$")

# Timesheet-level status codes (header `status`).
_TS_STATUS_MAP = {
    "O": EntryStatus.open,
    "S": EntryStatus.submitted,
    "C": EntryStatus.closed,
    "R": EntryStatus.rejected,
}
# Hour-entry approvalStatus codes (nested `hours[].approvalStatus`), if present.
# Extend this once you confirm your instance's real values.
_HOUR_STATUS_MAP = {
    "A": EntryStatus.closed,      # Approved
    "APPROVED": EntryStatus.closed,
    "P": EntryStatus.submitted,   # Pending
    "PENDING": EntryStatus.submitted,
    "S": EntryStatus.submitted,
    "R": EntryStatus.rejected,    # Rejected
    "REJECTED": EntryStatus.rejected,
    "O": EntryStatus.open,
    "N": EntryStatus.open,        # New
}
_PTO_HINTS = ("pto", "vacation", "holiday", "sick", "leave", "time off")

# Header fields we always request. `hours:*` is appended when we want detail.
_HEADER_FIELDS = [
    "ID", "displayName", "startDate", "endDate", "status",
    "totalHours", "regularHours", "overtimeHours", "hoursDuration",
    "totalDays", "hasNotes", "isOvertimeDisabled", "approverID", "userID",
]
# Nested hour sub-fields. NOTE: this Workfront instance rejects the nested
# `hours:*` field syntax on /tshet/search with a 422, so we leave this empty
# and run header-only (which carries everything the rules engine needs:
# status, regularHours, overtimeHours, totalHours, hasNotes, approverID).
# If your instance later supports it, add the fields back here.
_HOUR_FIELDS: list[str] = []


class RealWorkfrontClient(WorkfrontClient):
    def __init__(self) -> None:
        import httpx

        settings = get_settings()
        if not settings.workfront_base_url:
            raise RuntimeError("WORKFRONT_MODE=real requires WORKFRONT_BASE_URL in .env")
        self.base_url = settings.workfront_base_url.rstrip("/")

        has_client_creds = bool(settings.workfront_client_id and settings.workfront_client_secret)

        if has_client_creds:
            # Preferred: the app fetches and auto-refreshes its own token.
            # No manual pasting, no expiry babysitting — see workfront_auth.py.
            from app.services.workfront_auth import WorkfrontTokenAuth

            auth = WorkfrontTokenAuth(
                token_url=settings.workfront_token_url,
                client_id=settings.workfront_client_id,
                client_secret=settings.workfront_client_secret,
                scope=settings.workfront_token_scope,
            )
            self._client = httpx.Client(base_url=self.base_url, auth=auth, timeout=30.0)
        elif settings.workfront_api_key:
            # Fallback: a manually pasted static token. Works, but expires
            # (~24h) and needs manual renewal — prefer client credentials.
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {settings.workfront_api_key}"},
                timeout=30.0,
            )
        else:
            raise RuntimeError(
                "WORKFRONT_MODE=real requires either "
                "(WORKFRONT_CLIENT_ID + WORKFRONT_CLIENT_SECRET) for auto-refreshing "
                "auth, or WORKFRONT_API_KEY as a static fallback token."
            )

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _name_from_display(display_name: str) -> str:
        m = _DISPLAYNAME_RE.match(display_name or "")
        return m.group("name").strip() if m else (display_name or "").strip()

    @staticmethod
    def _map_ts_status(raw: str | None) -> EntryStatus:
        return _TS_STATUS_MAP.get(str(raw or "").strip().upper(), EntryStatus.submitted)

    @staticmethod
    def _map_hour_status(raw: str | None, ts_fallback: EntryStatus) -> EntryStatus:
        if not raw:
            return ts_fallback
        return _HOUR_STATUS_MAP.get(str(raw).strip().upper(), ts_fallback)

    @staticmethod
    def _map_task_type(raw: str | None) -> str:
        low = (raw or "").lower()
        if any(h in low for h in _PTO_HINTS):
            return "holiday" if "holiday" in low else "PTO"
        return "project"

    def _search(self, user_id: str | None, period_start: date | None, want_hours: bool, limit: int) -> list[dict]:
        import httpx

        fields = list(_HEADER_FIELDS)
        if want_hours and _HOUR_FIELDS:
            fields += _HOUR_FIELDS

        def _do(field_list: list[str]) -> list[dict]:
            params = {"fields": ",".join(field_list), "$$LIMIT": limit}
            if user_id:
                params["userID"] = user_id
            if period_start:
                params["startDate"] = period_start.isoformat()
            resp = self._client.get("/tshet/search", params=params)
            resp.raise_for_status()
            return resp.json().get("data", [])

        try:
            return _do(fields)
        except httpx.HTTPStatusError as e:
            # 422 = Workfront rejected a field. Retry with a minimal, known-good
            # set so one unsupported field never breaks the whole sync.
            if e.response is not None and e.response.status_code == 422:
                minimal = [
                    "ID", "displayName", "startDate", "endDate", "status",
                    "totalHours", "regularHours", "overtimeHours",
                    "hasNotes", "approverID", "userID",
                ]
                return _do(minimal)
            raise

    def _header_to_entries(self, header: dict) -> list[TimesheetEntry]:
        employee_id = str(header.get("userID") or header.get("ID"))
        employee_name = self._name_from_display(header.get("displayName", ""))
        period_start = _parse_wf_date(header.get("startDate")) or date.today()
        period_end = _parse_wf_date(header.get("endDate")) or (period_start + timedelta(days=6))
        ts_status = self._map_ts_status(header.get("status"))
        has_notes = bool(header.get("hasNotes"))

        # --- Richer path: nested hours collection present ---
        nested = header.get("hours")
        if isinstance(nested, list) and nested:
            seen: set[tuple] = set()
            entries: list[TimesheetEntry] = []
            for h in nested:
                entry_date = _parse_wf_date(h.get("entryDate")) or period_start
                task_label = (h.get("hourType") or {}).get("label") if isinstance(h.get("hourType"), dict) else h.get("hourType")
                hours_val = float(h.get("hours") or 0)
                dup_key = (entry_date, task_label, hours_val)
                is_dup = dup_key in seen
                seen.add(dup_key)
                entries.append(
                    TimesheetEntry(
                        employee_id=employee_id,
                        employee_name=employee_name,
                        period_start=period_start,
                        period_end=period_end,
                        entry_id=str(h.get("ID") or f"{header['ID']}-{len(entries)}"),
                        status=self._map_hour_status(h.get("approvalStatus") or h.get("status"), ts_status),
                        hours=hours_val,
                        task_type=self._map_task_type(task_label),
                        utilization_pct=None,
                        has_notes=bool(h.get("description")) or has_notes,
                        is_weekend=entry_date.weekday() >= 5,
                        is_duplicate=is_dup,
                    )
                )
            if entries:
                return entries

        # --- Header-only fallback ---
        regular = float(header.get("regularHours") or 0)
        overtime = float(header.get("overtimeHours") or 0)
        total = float(header.get("totalHours") or 0)
        if regular == 0 and total > 0 and overtime == 0:
            regular = total

        entries = []
        if regular > 0:
            entries.append(self._synthetic(header, employee_id, employee_name, period_start, period_end,
                                           ts_status, has_notes, regular, "regular"))
        if overtime > 0:
            entries.append(self._synthetic(header, employee_id, employee_name, period_start, period_end,
                                           ts_status, has_notes, overtime, "overtime"))
        if not entries:
            entries.append(self._synthetic(header, employee_id, employee_name, period_start, period_end,
                                           ts_status, has_notes, 0.0, "empty"))
        return entries

    @staticmethod
    def _synthetic(header, employee_id, employee_name, period_start, period_end, status, has_notes, hours, tag):
        return TimesheetEntry(
            employee_id=employee_id,
            employee_name=employee_name,
            period_start=period_start,
            period_end=period_end,
            entry_id=f"{header['ID']}-{tag}",
            status=status,
            hours=hours,
            task_type="project",
            utilization_pct=None,
            has_notes=has_notes,
            is_weekend=False,
            is_duplicate=False,
        )

    # -- interface ---------------------------------------------------------
    def fetch_entries(self, employee_id: str, period_start: date) -> list[TimesheetEntry]:
        rows = self._search(employee_id, period_start, want_hours=True, limit=1)
        if not rows:
            # Retry header-only in case the hours:* fields caused a rejection.
            rows = self._search(employee_id, period_start, want_hours=False, limit=1)
        if not rows:
            return []
        return self._header_to_entries(rows[0])

    def write_decision(self, employee_id: str, period_start: date, decision: str, note: str) -> bool:
        rows = self._search(employee_id, period_start, want_hours=False, limit=1)
        if not rows:
            return False
        timesheet_id = rows[0]["ID"]
        try:
            new_status = "C" if decision == "approve" else "R"
            resp = self._client.put(f"/tshet/{timesheet_id}", params={"status": new_status})
            resp.raise_for_status()
            return True
        except Exception:
            return False

    def list_timesheets(self, user_id: str | None = None, limit: int = 200) -> list[dict]:
        # Try with nested hours first; if that errors, fall back to header-only.
        try:
            return self._search(user_id, None, want_hours=True, limit=limit)
        except Exception:
            return self._search(user_id, None, want_hours=False, limit=limit)

    def fetch_hours(self, owner_id: str, limit: int = 2000) -> list:
        """Pull all HOUR entries for one user via /hour/search, normalized
        into HourEntry. Pulls ALL hours (no task-status filter) so both
        utilization (all hours) and completed-work (filtered in code) can be
        computed from one dataset. Also captures the per-day comment
        (Workfront's `description` field) and the owning weekly timesheet ID,
        used to build the Approval Recommendation card's comments column."""
        from app.models.schemas import HourEntry

        params = {
            "ownerID": owner_id,
            "fields": ",".join([
                "project:name", "project:status",
                "task:name", "task:status", "task:work", "task:actualWork",
                "hours", "entryDate", "owner:name", "owner:role:name",
                "description", "timesheet:ID", "timesheet:status",
            ]),
            "$$LIMIT": limit,
        }
        resp = self._client.get("/hour/search", params=params)
        resp.raise_for_status()
        rows = resp.json().get("data", [])

        out: list[HourEntry] = []
        for h in rows:
            project = h.get("project") or {}
            task = h.get("task") or {}
            owner = h.get("owner") or {}
            role = (owner.get("role") or {}) if isinstance(owner, dict) else {}
            timesheet = h.get("timesheet") or {}
            entry_date = _parse_wf_date(h.get("entryDate")) or date.today()
            comment = h.get("description")
            out.append(HourEntry(
                hour_id=str(h.get("ID")),
                owner_id=str(owner.get("ID") or owner_id),
                owner_name=owner.get("name") or "",
                owner_role=role.get("name"),
                project_id=project.get("ID"),
                project_name=project.get("name"),
                project_status=project.get("status"),
                task_id=task.get("ID"),
                task_name=task.get("name"),
                task_status=task.get("status"),
                task_planned_work=_num(task.get("work")),
                task_actual_work=_num(task.get("actualWork")),
                hours=float(h.get("hours") or 0),
                entry_date=entry_date,
                comment=comment.strip() if isinstance(comment, str) and comment.strip() else None,
                timesheet_id=timesheet.get("ID"),
            ))
        return out


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_wf_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    s = str(value)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s[: len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except ValueError:
        return None


def get_workfront_client() -> WorkfrontClient:
    settings = get_settings()
    if settings.workfront_mode == "real":
        return RealWorkfrontClient()
    return MockWorkfrontClient()