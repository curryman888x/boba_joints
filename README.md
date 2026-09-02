# boba_joints

Tracking when NYC boba shops opened and closed, by joining **Overture Maps
Places** (current inventory) to **NYC DOHMH restaurant inspections** (the time
dimension). Current run: **~523 shops**, 2022–2026.

## Scope: 2022–2026

The original question was "since 2020", but the free sources don't reach that far:

| Source | Earliest data | Open/close dates? |
|---|---|---|
| Overture Places | ~July 2023 | no — snapshot + a current-only `operating_status` |
| DOHMH inspections | **2022-01-25** for boba names (dataset itself goes to ~2016) | first inspection ≈ opened; `Establishment Closed` action is health-dept only (~4 shops), so closings also lean on "went silent" |

So the deliverable covers **2022 → 2026** with confidence bands; **2020–2021 is an
acknowledged gap**.

## Why it's built this way

- [docs/pipeline.md](docs/pipeline.md) — the flow as a diagram, and where the contracts sit
- [docs/data-sources.md](docs/data-sources.md) — why two sources, why matching, the three shop populations
- [docs/methodology.md](docs/methodology.md) — how shops are identified, matched, placed in a borough, and dated — and the limits
- [docs/decisions.md](docs/decisions.md) — non-obvious infrastructure choices

## How it works

```
seed.py             load NYC borough polygons (once)
ingest/overture.py  download 5-borough `place` extract → filter to boba
                    candidates → validate each via pydantic → upsert overture_places
ingest/dohmh.py     Socrata pull → dohmh_establishments + dohmh_inspections,
                    derive first/last inspection + closure signals
match.py            PostGIS ST_DWithin (120 m) + rapidfuzz name score → place_matches
                    (+ label propagation: recover boba CAMIS the regex missed)
analyze.py          blend signals → boba_shops (+ borough via ST_Contains,
                    drop non-NYC) + status_events + data/boba_status.csv
```

Every ingest writes an `ingest_runs` manifest row; `drift_warnings()` flags when a
source changes volume, coverage, or version between runs.

## Quickstart

Requires [`uv`](https://docs.astral.sh/uv/), Docker, and [`just`](https://github.com/casey/just).

```sh
cp .env.example .env          # optional: add a free SOCRATA_APP_TOKEN
just all                      # db-up → migrate → seed → ingest → match → analyze → check
just dashboard                # build + serve the dashboard at localhost:8501
```

Individual steps: `just seed`, `just ingest-overture`, `just ingest-dohmh`,
`just match`, `just analyze`. `just --list` shows everything.

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
  config.py        paths, NYC bbox, boba category/name filters, DOHMH dataset id
  models.py        SQLAlchemy schema (reference / raw ingest / linking / derived)
  contracts.py     pydantic (Overture record) + pandera (DOHMH frame) + IngestRun manifest
  filters.py       the boba predicate, shared with notebooks
  db.py            engine, session, bulk upsert helper
  net.py           retrying HTTP session (+ Socrata app token)
  log.py           one logger
  seed.py          load the NYC borough polygons
  checks.py        13 post-pipeline SQL invariants
  ingest/          overture.py, dohmh.py
  match.py, analyze.py
dashboard/app.py   Streamlit + pydeck; runs in its own container
Dockerfile         image for the dashboard service
migrations/        Alembic (GeoAlchemy2 helpers, public-schema scoped)
db/init/99_trim.sql   drops the image's TIGER/topology extensions
notebooks/         jupytext-paired exploration + the recall/precision workbench
tests/             contracts, migration round-trip, invariants
```

## Notes

- **PostGIS image**: `imresamu/postgis` (official `postgis/postgis` has no arm64
  build). `db/init/99_trim.sql` removes the bundled TIGER geocoder + topology
  extensions so Alembic autogenerate stays scoped to our tables.
- **DOHMH** has no "bubble tea" cuisine — boba shops are found by name-matching
  `dba` (`boba/config.py:BOBA_NAME_PATTERN`), so the set is a lower bound.
