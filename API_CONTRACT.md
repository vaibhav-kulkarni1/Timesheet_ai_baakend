# API Contract — AI Timesheet Approval Backend

For the frontend team. This is the stable contract: base URL, every endpoint,
exact request/response shapes, and the enums you'll render. Nothing here
requires understanding Workfront or the pipeline internals.

Interactive version (try requests live): **`GET /docs`** on the running server
(Swagger UI). Machine-readable schema: **`GET /openapi.json`** — you can
generate a typed client from this.

---

## Base URL

Local dev: `http://localhost:8000`
All endpoints below are relative to this.

Auth: none in v1 (the API is intended to run behind your existing
Workfront/SSO session or gateway). If you need the frontend to pass a manager
identity, use the `manager_id` query param for now — see below.

CORS is open (`*`) in dev; lock it to your dashboard origin before prod.

---

## Core concept

Each row is one **timesheet recommendation** = one employee + one weekly
period, with a computed recommendation and a manager-readable explanation.
The backend pre-computes everything; these endpoints only read/serve it, so
they're fast (no live Workfront or AI calls on the request path).

The primary key for a row is the pair **`(employee_id, period_start)`**.
`period_start` is an ISO date string, `YYYY-MM-DD`.

---

## Enums to render

**`recommendation`** — the AI/rules verdict:
| Value | Meaning | Suggested UI |
|---|---|---|
| `APPROVE` | Clean; safe to approve | green |
| `REVIEW` | Needs a look (unexplained hours, un-preapproved overtime, still open) | amber |
| `FLAGGED` | Hard problem (rejected or duplicate entries) | red |
| `NOT_READY` | Not submitted yet / nothing logged — not actionable | grey; usually hidden |

**`manager_decision`** — null until the manager acts, then one of:
`approve`, `return_for_correction`, `request_info`.

---

## Endpoints

### 1. List the manager's queue
```
GET /api/recommendations
```
Query params (all optional):
| Param | Type | Default | Purpose |
|---|---|---|---|
| `manager_id` | string | — | Filter to one manager (Workfront approverID) |
| `period_start` | date | — | Filter to one period, `YYYY-MM-DD` |
| `pending_only` | bool | `true` | Only undecided rows |
| `recommendation` | string | — | Filter to `APPROVE`/`REVIEW`/`FLAGGED`/`NOT_READY` |
| `exclude_not_ready` | bool | `true` | Hide not-yet-submitted timesheets |

Returns: array of **RecommendationRecord** (see shape below).

Example:
```
GET /api/recommendations?manager_id=6a689fc90018dbdf786139f8c23e2db7&pending_only=true
```

### 2. Summary counts (for dashboard cards)
```
GET /api/recommendations/stats?manager_id=...
```
Returns:
```json
{
  "manager_id": "6a689fc9...",
  "counts": { "APPROVE": 4, "REVIEW": 2, "FLAGGED": 1, "NOT_READY": 3 },
  "actionable_total": 7,
  "not_ready_total": 3
}
```

### 3. One recommendation (detail view)
```
GET /api/recommendations/{employee_id}/{period_start}
```
Returns a single **RecommendationRecord**, or `404` if not found.

### 4. Manager makes a decision
```
POST /api/recommendations/{employee_id}/{period_start}/decision
```
Body:
```json
{
  "decision": "approve",            // approve | return_for_correction | request_info
  "note": "Looks good",             // optional
  "decided_by": "mgr_identity"      // who acted
}
```
Returns:
```json
{
  "employee_id": "6a4612...",
  "period_start": "2026-08-03",
  "decision": "approve",
  "decided_at": "2026-08-27T10:15:00Z",
  "workfront_write_back": true
}
```
`workfront_write_back` tells you whether the push back to Workfront succeeded.
On failure the API returns `502` and the row is NOT marked decided.

### 5. Re-validate one timesheet (after employee resubmits)
```
POST /api/recommendations/{employee_id}/{period_start}/revalidate
```
Re-runs the pipeline for that row and returns the fresh **RecommendationRecord**.
Use after a returned timesheet is corrected. This one DOES call Workfront +
AI, so it's slower (show a spinner).

### 6. Live updates (optional, nice-to-have)
```
GET /api/events/stream        (Server-Sent Events)
```
Emits a small JSON event whenever a row is recomputed:
```json
{ "employee_id": "...", "period_start": "2026-08-03",
  "recommendation": "REVIEW", "manager_id": "...", "computed_at": "..." }
```
On receiving an event, refetch the affected row (or the whole queue). If you
don't wire SSE, just poll `GET /api/recommendations` — the data is identical.

## Utilization & Productivity cards

These two read from a **separate data source** — Workfront HOUR entries
(`/hour/search`), pulled by `scripts/sync_hours.py` into `hour_entries_staging`.
Run that sync after the timesheet sync to populate them.

### GET /api/utilization
Hours grouped by project and by role (role is a proxy for department — it's
what Workfront's hour data exposes).

Query params: `owner_id` (one employee), `window_days` (last N days; omit = all).

Returns:
```json
{
  "total_hours": 16.0,
  "window_days": null,
  "by_project": [ { "name": "Content Supply Chain Demo", "hours": 16.0, "pct": 100.0 } ],
  "by_role":    [ { "name": "Consultant", "hours": 16.0, "pct": 100.0 } ]
}
```
Render `by_project` and `by_role` as the distribution bars. **Note:** a true
billable ratio isn't available (no billable flag in Workfront's hour data), so
it's omitted rather than faked — don't show a billable % on this card.

### GET /api/productivity
Planned vs actual hours (from Workfront `task.work` vs `task.actualWork`, counted
once per task) and completed-deliverable count.

Query params: `owner_id`, `window_days`.

Returns:
```json
{
  "window_days": null,
  "planned_hours": 19.0,
  "actual_hours": 16.0,
  "logged_hours": 16.0,
  "variance_hours": -3.0,
  "efficiency_pct": 118.8,
  "tasks_total": 2,
  "deliverables_completed": 2
}
```
`planned_hours` vs `actual_hours` is the planned-vs-actual comparison;
`efficiency_pct` >100 means under budget. `deliverables_completed` = tasks with
Workfront status CPL.

---

## Employee Health card

### GET /api/employee-health
Feeds the "Employee Health" card — per-employee burnout risk, aggregated
across each person's timesheet history (not per-period). Two signals: sustained
overtime over a 60-day window, and no PTO/holiday logged in the last 60 days.

Query params (all optional):
| Param | Type | Purpose |
|---|---|---|
| `manager_id` | string | Filter to one manager's team (approverID) |
| `risk` | string | `HIGH` / `MEDIUM` / `LOW` / `NONE` |
| `at_risk_only` | bool | Hide NONE-risk employees (default true) |
| `limit` | int | max returned (default 100) |

Returns:
```json
{
  "counts": { "HIGH": 1, "MEDIUM": 2, "LOW": 3, "total": 6 },
  "employees": [
    {
      "employee_id": "6a32...",
      "employee_name": "Anoop Johnson",
      "risk": "MEDIUM",
      "reason": "Anoop Johnson has 9h of overtime across 2 periods in the last 60 days and no PTO or holiday logged in the last 60 days.",
      "metrics": {
        "overtime_hours_window": 9.0,
        "overtime_periods": 2,
        "recent_pto_hours": 0.0,
        "no_recent_leave": true,
        "window_days": 60
      },
      "latest_period_start": "2026-08-17",
      "latest_period_end": "2026-08-23"
    }
  ]
}
```

Risk scoring (calibrated for a 40h services team):
| Risk | Condition |
|---|---|
| HIGH | ≥15h overtime in 60 days AND no recent leave |
| MEDIUM | ≥15h overtime, OR ≥8h overtime with no leave |
| LOW | ≥8h overtime |
| NONE | below thresholds |

Recurring overtime (3+ periods) bumps risk up one level, since chronic
low-grade overtime is a stronger burnout signal than one heavy week. `employees`
is sorted highest-risk first. Render `risk` as the badge, `reason` as the body,
and `metrics` for a details view — matching the mockup's burnout card.

Thresholds live in `app/services/health_analyzer.py` and are easy to tune.

---

### 7. Health (app status — not employee health)
```
GET /health        ->  { "status": "ok" }
```

---

## Anomaly Detection card

### GET /api/anomalies
Feeds the "AI Anomaly Detection" card — a flat list of individual issues
found across all timesheets, each with a severity, derived from the same
rules engine (no separate AI call).

Query params (all optional):
| Param | Type | Purpose |
|---|---|---|
| `manager_id` | string | Filter to one manager's team (approverID) |
| `severity` | string | `HIGH` / `MEDIUM` / `LOW` |
| `anomaly_type` | string | see types below |
| `limit` | int | max returned (default 100) |

Returns:
```json
{
  "counts": { "HIGH": 3, "MEDIUM": 5, "LOW": 2, "total": 10 },
  "anomalies": [
    {
      "id": "6a46...:2026-08-03:excessive_hours",
      "employee_id": "6a46...",
      "employee_name": "Anoop Johnson",
      "period_start": "2026-08-03",
      "period_end": "2026-08-09",
      "type": "excessive_hours",
      "severity": "HIGH",
      "title": "Excessive Hours",
      "description": "Anoop Johnson logged 45 hours (target 40h) — 5h of overtime without prior approval."
    }
  ]
}
```

Anomaly types and their severity:
| type | severity | trigger |
|---|---|---|
| `rejected_entry` | HIGH | a rejected entry is present |
| `duplicate_entry` | HIGH | duplicate hour entries |
| `excessive_hours` | HIGH | overtime over expected, not pre-approved |
| `missing_time` | MEDIUM | ≥4 unexplained hours, no PTO/holiday cover |
| `idle_resource` | MEDIUM | active period, zero hours logged |
| `open_entries` | LOW | still-open entries (with hours logged) |
| `non_standard_utilization` | LOW | >30% entries non-standard utilization |

`anomalies` is already sorted HIGH → MEDIUM → LOW. Use `counts` for the
severity badges/summary; render each anomaly as a card row (colored border by
severity, `title` as heading, `description` as body), matching the mockup.

---

## RecommendationRecord shape

This is what every GET returns (one object, or an array of them):

```json
{
  "employee_id": "6a46123b0039b00ca7cede44495b8aef",
  "employee_name": "Arun Das",
  "period_start": "2026-08-03",
  "period_end": "2026-08-09",
  "recommendation": "APPROVE",
  "reason": "APPROVE: All checks passed.",
  "next_best_actions": ["Approve the timesheet."],
  "checklist": {
    "expected_hours": 40.0,
    "logged_hours": 40.0,
    "pto_hours": 0.0,
    "variance": 0.0,
    "unexplained_hours": 0.0,
    "rejected_count": 0,
    "open_count": 0,
    "submitted_count": 0,
    "closed_count": 1,
    "weekend_hours": 0.0,
    "duplicate_count": 0,
    "non_standard_util_pct": 0.0
  },
  "computed_at": "2026-08-27T09:00:00Z",
  "manager_decision": null,
  "decided_at": null,
  "decided_by": null
}
```

Field notes for the UI:
- `reason` + `next_best_actions` are the AI-written explanation — render these
  as the human-facing summary. `next_best_actions` is an array; show as a list.
- `checklist` is the deterministic detail — good for an expandable "why?"
  panel. Every number here is computed by rules, never by the AI.
- `manager_decision == null` means still pending (show action buttons).
  Non-null means already decided (show the outcome instead).

---

## Suggested screens (not prescriptive)

1. **Queue** — table from `GET /api/recommendations?manager_id=...`, colored by
   `recommendation`, with summary cards from `/stats` on top.
2. **Detail drawer** — click a row → `GET .../{employee_id}/{period_start}`,
   show `reason`, `next_best_actions`, and the `checklist` breakdown.
3. **Actions** — Approve / Return buttons → `POST .../decision`. On success,
   remove from queue (or optimistic-update, then reconcile via SSE).

---

## Error responses

Standard FastAPI shape:
```json
{ "detail": "No recommendation found for that employee/period." }
```
- `404` — row not found
- `422` — bad request body (validation)
- `502` — Workfront write-back failed on a decision
