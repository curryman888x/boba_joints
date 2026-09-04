# boba_joints

**A current census of NYC boba shops** — where each is, whether it's open, and
(where NYC DOHMH has an inspection) roughly when it was first operating.
Discovered from **Yelp**'s curated `bubbletea` category, dated from **NYC DOHMH
inspections**.

**[Live dashboard →](https://bobajoints-dhemy8pwj2epnyv2iyv9je.streamlit.app/)**
Refreshed weekly by a GitHub Actions cron (`.github/workflows/pipeline.yml`)
against a persistent Neon Postgres.

## Why it's built this way

- [docs/pipeline.md](docs/pipeline.md) — the flow as a diagram, and where the contracts sit
- [docs/data-sources.md](docs/data-sources.md) — why two sources, why linking, the shop populations
- [docs/methodology.md](docs/methodology.md) — identification, linking, borough assignment, dates — and the limits
- [docs/decisions.md](docs/decisions.md) — non-obvious infrastructure choices

## How it works

```
seed.py           load NYC borough polygons (once)
ingest/dohmh.py   Socrata pull → dohmh_establishments + dohmh_inspections;
                  derive first (pre-permit) / last inspection + closure signals
ingest/yelp.py    discover: search bubbletea over an adaptive NYC grid → yelp_businesses
                  link: name + distance → each Yelp business's DOHMH CAMIS → yelp_matches
analyze.py        merge (Yelp first, then boba-name DOHMH CAMIS) → boba_shops
                  (borough via ST_Contains, dedup, first_seen / last_seen,
                  status + status_basis) + boba_status.csv
```

Every ingest writes an `ingest_runs` manifest row; `drift_warnings()` flags when a
source changes volume, coverage, or version between runs.

## Quickstart

Requires [`uv`](https://docs.astral.sh/uv/), Docker, and [`just`](https://github.com/casey/just).

```sh
cp .env.example .env          # optional: SOCRATA_APP_TOKEN, YELP_API_KEY
just all                      # db-up → migrate → seed → ingest-dohmh → ingest-yelp → analyze → check
just dashboard                # build + serve the dashboard at localhost:8501
```

Individual steps: `just seed`, `just ingest-dohmh`, `just ingest-yelp`,
`just analyze`. `just --list` shows everything. `ingest-yelp` no-ops without
`YELP_API_KEY` (the set is then just the DOHMH boba-name tail, ~190 shops); its
raw sweep is cached to `data/yelp_raw_last.json` so a later rate-limited run
rebuilds `yelp_businesses` instead of wiping it.

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
  config.py        paths, NYC bbox, boba name pattern, API keys
  models.py        SQLAlchemy schema (reference / discovery / linking / derived)
  contracts.py     pydantic (Yelp record) + pandera (DOHMH frame) + IngestRun manifest
  filters.py       the boba name predicate + name_key(), shared by yelp/notebooks
  db.py            engine, session, bulk upsert helper
  net.py           retrying HTTP session (Socrata / Yelp)
  log.py           one logger
  seed.py          load the NYC borough polygons
  checks.py        post-pipeline SQL invariants
  ingest/          yelp.py (primary discovery + link), dohmh.py
  analyze.py
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
- **Discovery** is Yelp's `bubbletea` category first (`ingest/yelp.py`), then a
  name regex on the DOHMH `dba` (`boba/config.py:BOBA_NAME_PATTERN` — DOHMH has
  no "bubble tea" cuisine). Each layer only *adds* shops, so the set is a lower
  bound.
