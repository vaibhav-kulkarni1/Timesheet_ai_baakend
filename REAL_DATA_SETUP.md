# Connecting to real Workfront + OpenAI (header-only v1)

Good news: your per-user `/tshet/search` response carries everything the
rules engine needs in the timesheet HEADER (status, regularHours,
overtimeHours, totalHours, hasNotes, approverID). So v1 runs on real data
with just that ONE call — no second /hour/search needed.

## Step 1 — Point .env at real Workfront

```
WORKFRONT_MODE=real
WORKFRONT_BASE_URL=https://dizitechptrsd.my.workfront.com/attask/api/v21.0
WORKFRONT_API_KEY=<your OAuth access token>
```

## Step 2 — Pull real timesheets and run the pipeline

```bash
# all timesheets:
python -m scripts.sync_workfront

# or just one user (e.g. Arun Das):
python -m scripts.sync_workfront 6a46123b0039b00ca7cede44495b8aef
```

You'll see real names and recommendations. On Arun Das's real data this
produces:

```
Arun Das  2026-08-03  C  APPROVE     All checks passed.
Arun Das  2026-08-10  C  APPROVE     All checks passed.
Arun Das  2026-08-17  C  APPROVE     All checks passed.
Arun Das  2026-08-24  O  NOT_READY   Timesheet still open, no hours logged yet.
Arun Das  2026-08-31  O  NOT_READY   Timesheet still open, no hours logged yet.
```

## Step 3 — Serve it

```bash
python -m uvicorn app.main:app --reload
# http://localhost:8000/docs
# GET /api/recommendations                      -> all pending
# GET /api/recommendations?manager_id=<approverID>  -> one manager's queue
```

The `manager_id` filter now works because the sync populates it from
Workfront's `approverID` on each timesheet.

## Step 4 — Real LLM narratives (optional)

```
MOCK_LLM=false
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1
```

No code change; the guardrail still blocks any hallucinated number.

## How the header maps to the rules engine

| Workfront header field | Used as |
|---|---|
| `status` (O/S/C/R) | Open / Submitted / Closed / Rejected |
| `regularHours` | logged hours (vs 40h expected) |
| `overtimeHours` | overtime detection |
| `totalHours` | variance |
| `hasNotes` | notes present |
| `approverID` | manager_id (queue filtering) |
| `userID` | employee_id |

Expected hours are 40h flat (DEFAULT_EXPECTED_HOURS in .env).

## Decision states

- **APPROVE** — closed/submitted, hours reconcile, no flags
- **REVIEW** — unexplained hours, un-preapproved overtime, or still-open-with-hours
- **FLAGGED** — rejected or duplicate entries present
- **NOT_READY** — open timesheet, nothing logged yet (future/incomplete period)

## What's deferred to v2 (needs /hour/search)

Header-only can't see per-day detail, so these are NOT active yet:
weekend-hours detection, per-entry duplicate detection, and PTO-vs-project
breakdown. The `/hour/search` scaffolding is preserved in git history if you
want to layer these in later — just say the word and share one hour-entry
response.

## Write-back caveat

`write_decision()` PUTs `status=C` (approve) or `status=R` (reject) to the
timesheet. Confirm those are the correct approval actions for your Workfront
approval workflow before using the decision endpoint against production.
