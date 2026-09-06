# Deployment

Local dev runs the whole thing against a throwaway Docker Postgres (`just db-up`).
The hosted setup keeps a **current census** live without anyone running `just`:

```
GitHub Actions cron ──weekly──> Neon Postgres <──reads── Streamlit Community Cloud
   (.github/workflows/pipeline.yml)   (persistent)         (dashboard/app.py)
        │
        └── pings healthchecks.io (dead-man's-switch)
```

## Neon — the persistent database

A Docker Postgres only exists for one CI run; the census has to survive between
weekly runs, so it lives in **Neon** (serverless Postgres, free tier, PostGIS
enabled). Project `silent-sun-45494095`, branch `production`, region `us-east-2`.

- `neon.ts` is the infra-as-code (committed). `.neon/` (the link state) and the
  `DATABASE_URL` it writes into `.env` are gitignored.
- Neon auto-suspends when idle and wakes on the next connection — fine for a
  once-a-week writer and an occasional dashboard reader (a few seconds' cold
  start).
- **Schema changes:** now that real data lives here, never re-squash the single
  migration (that needed a DB wipe). Each change is a *new* incremental migration
  on top of `84673e8d52ca`; the cron's `alembic upgrade head` applies it.

## GitHub Actions — the weekly pipeline

`.github/workflows/pipeline.yml` runs `migrate → seed → ingest-dohmh →
ingest-yelp → analyze → checks` on `cron: "0 6 * * 1"` (Monday 06:00 **UTC**),
plus `workflow_dispatch` for a manual run from the Actions tab.

- A full Yelp discovery sweep costs ~195–240 of the 500/UTC-day free quota, so
  weekly has ample headroom (the earlier quota exhaustion was from bunching many
  runs into one day, not one sweep).
- A failed step or a raised `ContractViolation` fails the job — GitHub emails the
  repo owner, and `checks.py` exits non-zero on any broken invariant.

### Repo secrets (Settings → Secrets and variables → Actions)

| Secret | Used by | Notes |
|---|---|---|
| `DATABASE_URL` | every step | the Neon connection string (`postgresql://…neon.tech/neondb?sslmode=require`) |
| `YELP_API_KEY` | `ingest-yelp` | free Yelp Fusion key |
| `HEALTHCHECK_URL` | ping steps | healthchecks.io base ping URL; steps are inert if unset |
| `SOCRATA_APP_TOKEN` | `ingest-dohmh` | optional; the DOHMH pull is one request/run so the anon throttle is fine without it |

## Streamlit Community Cloud — the dashboard

`dashboard/app.py` deployed from the public repo, entrypoint `dashboard/app.py`,
branch `main`. Auto-redeploys on every push. Public URL:
<https://bobajoints-dhemy8pwj2epnyv2iyv9je.streamlit.app/>

- **Dependencies:** Streamlit Cloud installs from `uv.lock`, which resolves only
  `[project.dependencies]` — so `streamlit` / `plotly` live there, not in a
  `[dependency-groups]` block (which its installer ignores). A `requirements.txt`
  would be *lower* precedence than `uv.lock` and get skipped.
- **Secrets:** Streamlit's own Secrets store (separate from GitHub's), TOML:
  `DATABASE_URL = "postgresql://…"`. Its values are **not** guaranteed in
  `os.environ`, so `app.py` bridges `st.secrets` → `os.environ` before importing
  `boba.db` (which reads `DATABASE_URL` via `os.environ`, shared with the CLI).
- `@st.cache_data(ttl=300)` on the DB reads — the page reflects a cron update
  within ~5 min of a visit.

## healthchecks.io — the dead-man's-switch

GitHub emails on a *failed* run, but not on one that **never happens** (cron
misfire, Actions outage, workflow disabled). A free healthchecks.io check
(`boba_joints`, cron schedule `0 6 * * 1` UTC, grace ~6–12 h) closes that gap:

- `pipeline.yml` pings `$HEALTHCHECK_URL/start` at the top, the base URL on
  success, `$HEALTHCHECK_URL/fail` on a failed step. Every ping is best-effort
  (`|| true`) so a healthchecks outage never fails the pipeline.
- No ping within the grace window → healthchecks emails you.
- **When it alerts:** check the pipeline run in the Actions tab; if it never
  started, check whether Actions had an incident or the workflow got disabled
  (GitHub disables schedules on repos with 60 days of no activity).

## In-app health panel

The dashboard's **Data quality → Pipeline health** section reads `ingest_runs`:
per-source status, last-run age, row/kept counts, and the notable `detail` keys
(`missing_from_sweep`, `verify_calls`, `newly_closed`, `gone_404`). The header
also carries a "Data as of …" line that turns into a warning if the newest run
failed or is older than `STALE_DAYS` (10). This is the zero-dependency view of
pipeline state — no Grafana/Datadog, which would be overkill for one weekly job.
