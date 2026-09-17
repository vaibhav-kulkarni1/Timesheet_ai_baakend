# AI Timesheet Approval — Backend

Python/FastAPI backend that reads timesheets from Adobe Workfront, decides
APPROVE / REVIEW / FLAGGED / NOT_READY with a deterministic rules engine,
and writes a short AI explanation for each — then serves it all to the
manager dashboard.

**Frontend team: start with [`API_CONTRACT.md`](./API_CONTRACT.md).** That's
the stable contract you build against. Everything below is for running the
backend.

---

## Quick start (mock data — runs with zero external setup)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS/Linux
pip install -r requirements.txt

copy .env.example .env            # Windows  (cp on macOS/Linux)

python -m scripts.seed_mock_data  # fills the DB with demo recommendations
python -m uvicorn app.main:app --reload
```

Open **http://localhost:8000/docs** — the whole API, ready to try.
Root `/` redirects there too.

## Run on real Workfront data

Set these in `.env`:
```
WORKFRONT_MODE=real
WORKFRONT_BASE_URL=https://dizitechptrsd.my.workfront.com/attask/api/v21.0
WORKFRONT_API_KEY=<your OAuth access token>
```
Then:
```bash
python -m scripts.sync_workfront                # everyone
python -m scripts.sync_workfront <workfront_userID>   # one person
python -m uvicorn app.main:app --reload
```
See [`REAL_DATA_SETUP.md`](./REAL_DATA_SETUP.md) for the full walkthrough.

## Turn on real AI narratives (optional)

```
MOCK_LLM=false
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1
```
No code change. A guardrail blocks the model from ever introducing a number
that isn't in the computed facts — falls back to a safe template if it tries.

---

## How it works (one paragraph)

A background pipeline pulls each timesheet, a **pure-Python rules engine**
computes every number and the APPROVE/REVIEW/FLAGGED/NOT_READY verdict, and
an **LLM writes only the explanation** (never the numbers). Results are stored
pre-computed, so the dashboard's read endpoints are fast and never wait on
Workfront or the AI. Managers act on a row; the decision is written back to
Workfront.

```
Workfront ─► Ingestion ─► Rules Engine ─► AI Narrative ─► Store ─► Serving API ─► Dashboard
   ▲                        (numbers)      (words only)                              │
   └──────────────── write-back (approve / return) ◄──────────────────────── decision
```

## Decision rules (checked in priority order)

| Condition | Verdict |
|---|---|
| Rejected entries present | FLAGGED |
| Duplicate entries present | FLAGGED |
| Unexplained hours (gap not covered by PTO/holiday) | REVIEW |
| Overtime not pre-approved | REVIEW |
| Still-open entries (with hours) | REVIEW |
| >30% non-standard utilization | REVIEW |
| Open + nothing logged yet | NOT_READY |
| None of the above | APPROVE |

Expected hours: 40/week (`DEFAULT_EXPECTED_HOURS` in `.env`).

## Tests

```bash
pytest
```
`tests/test_rules_engine.py` covers the decision table (fast, no deps).
`tests/test_api.py` covers the endpoints end-to-end against mock data.

## Project layout

```
app/
  main.py                  FastAPI app
  config.py                env settings
  models/schemas.py        API request/response shapes (Pydantic)
  models/db_models.py      DB tables (SQLAlchemy)
  db/session.py            DB engine (SQLite default, Postgres via env)
  services/
    workfront_client.py    Workfront integration (mock + real)
    ingestion.py           pull + stage entries
    rules_engine.py        deterministic decision (the source of truth)
    narrative_generator.py LLM explanation + hallucination guardrail
    pipeline.py            orchestrates the four steps
  api/
    recommendations.py     serving + decision + revalidate + stats
    events.py              SSE live updates
    webhooks.py            Workfront submission trigger
  realtime/events.py       Redis pub/sub (optional)
  workers/scheduler.py     scheduled re-check fallback
scripts/
  seed_mock_data.py        demo data
  sync_workfront.py        pull real Workfront data
  inspect_workfront_hours.py  diagnostic for hour-entry fields
tests/
API_CONTRACT.md            <- give this to the frontend team
REAL_DATA_SETUP.md         real Workfront + OpenAI setup
```

## Database

SQLite by default (a local file, zero setup). For production set
`DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db` and add
`psycopg[binary]` to requirements — no code changes. Use Alembic for
migrations beyond the initial auto-created tables.
