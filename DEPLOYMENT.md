# Deploying to the cloud (100% free)

**Architecture:** Render hosts the web service (dashboard + API) and the
Postgres database, both on free tiers. The two sync scripts run as **GitHub
Actions scheduled workflows** instead of Render cron jobs — Render doesn't
offer a free plan for cron jobs, only for web services and databases, so
this split avoids any cost entirely.

```
Render (free)              GitHub Actions (free)
├── web service      <───  writes to same DB   ── sync-workfront.yml (every 30 min)
└── Postgres database <──  writes to same DB   ── sync-hours.yml (every 2 hours)
```

## Step 1 — Push the code to GitHub

```bash
cd timesheet_ai_backend
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/<you>/timesheet-ai-backend.git
git branch -M main
git push -u origin main
```

**Confirm `.env` and `*.db` are not tracked**: run `git status` before your
first push and make sure neither appears. If they do, your `.gitignore`
isn't catching them — add `.env` and `*.db` to it and re-commit.

## Step 2 — Deploy the Render Blueprint (web service + database only)

1. Sign up at [render.com](https://render.com) (GitHub login is easiest)
2. **New → Blueprint** → connect your repo
3. Render reads `render.yaml` and shows a preview: **1 database + 1 web
   service** — no payment info needed, everything here is free
4. Click **Apply**

## Step 3 — Set the web service's secrets

Open the `timesheet-ai-backend` web service → **Environment** tab, fill in:

| Key | Value |
|---|---|
| `WORKFRONT_BASE_URL` | your Workfront instance API URL |
| `WORKFRONT_CLIENT_ID` | from your Adobe Developer Console OAuth Server-to-Server credential |
| `WORKFRONT_CLIENT_SECRET` | same credential |
| `OPENAI_API_KEY` | a fresh OpenAI key |

Save — Render redeploys automatically. Wait for "Live," then visit the URL
Render gives you (e.g. `https://timesheet-ai-backend.onrender.com`) and
check `/health` and `/docs` load.

## Step 4 — Get your database's EXTERNAL connection string

GitHub Actions runs outside Render's private network, so it needs the
**External Database URL**, not the internal one the web service uses
automatically.

1. Open the `timesheet-ai-db` database in Render
2. Find **External Database URL** (labeled separately from the internal one)
3. Copy it — you'll paste it into GitHub next

## Step 5 — Add GitHub Actions secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of these:

| Secret name | Value |
|---|---|
| `DATABASE_URL` | the External Database URL from Step 4 |
| `WORKFRONT_BASE_URL` | same as Step 3 |
| `WORKFRONT_CLIENT_ID` | same as Step 3 |
| `WORKFRONT_CLIENT_SECRET` | same as Step 3 |
| `OPENAI_API_KEY` | same as Step 3 |

The two workflow files (`.github/workflows/sync-workfront.yml` and
`sync-hours.yml`) are already in your repo and read these secrets
automatically — no further setup needed.

## Step 6 — Trigger both syncs manually the first time

Don't wait for the schedule. In GitHub: **Actions tab → select "Sync
Workfront timesheets" → Run workflow**. Then do the same for "Sync Workfront
hours." Watch each run's log to confirm it completes without errors.

## Step 7 — Check the live dashboard

Visit `https://<your-app>.onrender.com/dashboard` — all five cards should
now show real data, refreshed automatically every 30 min / 2 hours from here
on, forever, at no cost.

## Notes and tradeoffs

- **Web service sleep**: the free Render web service sleeps after 15 min of
  no traffic. First request after that takes ~30-60s to wake. The GitHub
  Actions syncs still run on schedule regardless — only the *dashboard's*
  first load after idle time is slow, not the data freshness.
- **GitHub Actions scheduling is best-effort**: a `*/30 * * * *` job may run
  a few minutes late under GitHub's platform load. Not a problem for this
  use case.
- **Free Postgres is deleted after 90 days of inactivity** (not "90 days
  total" — regular use resets this). Keep an eye on it if the app goes
  unused for a long stretch.
- **Client-credentials auth means zero manual token maintenance** — this
  whole deployment runs indefinitely without anyone pasting a token,
  provided `WORKFRONT_CLIENT_ID`/`SECRET` stay valid.

## If you want always-on later (no cold-start delay)

Upgrade only the web service to the `starter` plan (~$7/mo) in Render's
dashboard — a one-click change, no code or redeploy needed. The database and
GitHub Actions syncs stay exactly as they are.