# boba_joints

NYC boba shops 2022–2026: **~512 shops**, when each was first seen, and whether
it's open now — from **Overture Places** (label + location) + **NYC DOHMH
inspections** (the rough timeline) + **Yelp** (current status).

## Scope & honesty

- **No source has real opening/closing dates.** DOHMH is *inspection* data — the
  first inspection lags the true opening by the permit gap, so `first_seen_date`
  reads as "operating by this date, ±1 quarter", not "opened on".
- **2020–2021 is a gap** — Overture starts July 2023, DOHMH's boba-name records
  start 2022. The deliverable is 2022–2026.
- Every count is a **lower bound** — shops are identified by category/name, not a
  ground-truth list.

## Why it's built this way

- [docs/pipeline.md](docs/pipeline.md) — the flow as a diagram, and where the contracts sit
- [docs/data-sources.md](docs/data-sources.md) — why three sources, why matching, the shop populations
- [docs/methodology.md](docs/methodology.md) — identification, matching, borough assignment, dates — and the limits
- [docs/decisions.md](docs/decisions.md) — non-obvious infrastructure choices

## How it works

```
seed.py             load NYC borough polygons (once)
ingest/overture.py  download 5-borough `place` extract → filter to boba
                    candidates → validate via pydantic → upsert overture_places
ingest/dohmh.py     Socrata pull → dohmh_establishments + dohmh_inspections;
                    derive first (pre-permit) / last inspection + closure signals
match.py            PostGIS ST_DWithin (120 m) + rapidfuzz → place_matches
                    (+ label propagation: recover boba CAMIS the regex missed)
ingest/yelp.py      /businesses/matches by name+address → is_closed → yelp_status
analyze.py          merge → boba_shops (borough via ST_Contains, dedup,
                    first_seen / last_seen, status + status_basis) + boba_status.csv
```

Every ingest writes an `ingest_runs` manifest row; `drift_warnings()` flags when a
source changes volume, coverage, or version between runs.

## Quickstart

Requires [`uv`](https://docs.astral.sh/uv/), Docker, and [`just`](https://github.com/casey/just).

```sh
cp .env.example .env          # optional: SOCRATA_APP_TOKEN, YELP_API_KEY
just all                      # db-up → migrate → seed → ingest → match → yelp → analyze → check
just dashboard                # build + serve the dashboard at localhost:8501
```

Individual steps: `just seed`, `just ingest-overture`, `just ingest-dohmh`,
`just match`, `just ingest-yelp`, `just analyze`. `just --list` shows everything.
`ingest-yelp` no-ops without `YELP_API_KEY`.

`docker compose up -d --build` brings up Postgres **and** the dashboard container
together.

## Development

```sh
just test          # pytest (needs the db container up)
just lint / fmt    # ruff check+format / autofix
just migrate-check # fail if models drifted from migrations
just check         # post-pipeline data invariants
```

`pre-commit install` wires ruff + `alembic check` into commits. CI
(`.github/workflows/ci.yml`) runs lint + `alembic upgrade/check` + pytest against
a PostGIS service on every push.

## Layout

```
boba/
  config.py        paths, NYC bbox, boba category/name filters, API keys
  models.py        SQLAlchemy schema (reference / raw ingest / linking / derived)
  contracts.py     pydantic (Overture record) + pandera (DOHMH frame) + IngestRun manifest
  filters.py       the boba predicate + name_key(), shared by match/yelp/notebooks
  db.py            engine, session, bulk upsert helper
  net.py           retrying HTTP session (Socrata / Yelp)
  log.py           one logger
  seed.py          load the NYC borough polygons
  checks.py        post-pipeline SQL invariants
  ingest/          overture.py, dohmh.py, yelp.py
  match.py, analyze.py
dashboard/app.py   Streamlit + Plotly; runs in its own container
Dockerfile         image for the dashboard service
migrations/        Alembic — one squashed schema migration (GeoAlchemy2 helpers)
db/init/99_trim.sql   drops the image's TIGER/topology extensions
notebooks/         03_recall_precision.py — the recall/precision workbench
tests/             contracts, migration round-trip, invariants
```

## Notes

- **PostGIS image**: `imresamu/postgis` (official `postgis/postgis` has no arm64
  build). `db/init/99_trim.sql` removes the bundled TIGER geocoder + topology
  extensions so Alembic autogenerate stays scoped to our tables.
- **DOHMH** has no "bubble tea" cuisine — boba shops are found by name-matching
  `dba` (`boba/config.py:BOBA_NAME_PATTERN`), so the set is a lower bound.
