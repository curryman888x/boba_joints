# Decisions — why the infrastructure looks like this

Short log of non-obvious choices. Newest first.

## Dates are `first_seen` / `last_seen`, not `opened` / `closed`
DOHMH has no opening or closing field — it's inspection data. Calling the first
inspection date an "opening date" overclaims (it lags the true opening by the
permit gap). Fields renamed to `first_seen_date` / `last_seen_date`, read as
"operating by / still operating at", ±1 quarter. `closed_date` is set only on a
real closure signal. The year summary is "first seen per year", a proxy.

## Yelp for current status (name + address match)
Overture's `operating_status` is unreliable (said "open" for Come Buy / Tea La
Ra, both closed). `boba/ingest/yelp.py` resolves each shop to a Yelp business via
`/businesses/matches` (name + address1 + city + state + zip) then reads
`is_closed` from `/businesses/{id}`. Feeds `analyze` as `yelp_closed` (beats a
stale inspection) / `yelp_open` (outranks `overture_open`). Capped + cached;
no-ops without a key. DOHMH stays the timeline; Yelp is the current-status layer.

## Dropped `status_events` and `overture_place_snapshots`
`status_events` just split `boba_shops` columns into rows and nothing read it.
`overture_place_snapshots` was written every run and never read (release-diffing
we didn't build); `first_seen_release` on `overture_places` still tracks first
appearance. Both removed; migrations re-squashed to one.

## Borough is point-in-polygon, not a locality string
Overture `locality` is neighbourhood-level ("Woodside", "Flushing") and DOHMH
`boro` and Overture disagree on names ("Manhattan" vs "New York"). Worse, the NYC
bbox rectangle covers slices of Nassau (Great Neck, Manhasset) and Westchester
(New Rochelle). `boba/seed.py` loads NYC's official borough polygons
(`boroughs` table); `analyze.py` does `ST_Contains` to assign the borough and
**drops any shop outside all five** (~14). DOHMH `boro` is a fallback only for a
point that lands just off a polygon (shoreline geocode).

## BOBA_NAME_PATTERN carries newer chains explicitly
HeyTea, Molly Tea, Auntea Jenny, Chun Yang, Sunright, Chagee etc. contain no
generic keyword, so they're listed by name. Without them these chains only
appeared where an Overture `bubble_tea` point happened to be within 60 m.

## Contracts: pydantic + pandera + a manifest table
Data pipelines rot silently when a source changes shape. `boba/contracts.py`
validates every Overture record (pydantic — nested structs, fails on an unknown
`operating_status`, bad `confidence`, missing `categories`) and the whole DOHMH
frame (pandera — flat/tabular, warns on unknown `action` strings). `ingest_runs`
records volume / coverage / version per run; `drift_warnings()` flags a ≥25%
swing or coverage reaching further back. `boba/checks.py` has 13 post-pipeline
SQL invariants (`just check`).

## PostGIS image: `imresamu/postgis`, not `postgis/postgis`
The official image has **no arm64 build**. `imresamu/postgis` is the maintained
multi-arch mirror with the same tags.

## `db/init/99_trim.sql` drops the TIGER geocoder + topology extensions
The image auto-installs `postgis_tiger_geocoder` + `postgis_topology`, which
create ~40 tables in `tiger`/`topology` schemas **and push those schemas onto
`search_path`**. Alembic autogenerate then tried to `DROP` all of them. We do our
own spatial matching (`ST_DWithin`), so the init script removes both extensions
and resets `search_path` to `public`. `migrations/env.py` also scopes
autogenerate to `public` as a second layer.

## `MetaData` naming convention + squashed initial migration
A 5-key `naming_convention` on `Base.metadata` makes FK/PK/index names
deterministic, so `alembic check` stays trustworthy. Migrations were squashed to
one clean initial while nothing is deployed — never rewrite a migration once it
has run elsewhere.

## `uq_dohmh_inspection_row` is `NULLS NOT DISTINCT`
Postgres treats `NULL`s in a unique constraint as distinct by default, so
inspection rows with a null `violation_code`/`action` re-inserted on every run.
`NULLS NOT DISTINCT` (PG15+) makes the upsert actually idempotent.

## DOHMH ingest is always a full pull
`record_date` is a per-publish dataset timestamp (≈uniform across all rows), so
it can't drive a row-level incremental cursor. The candidate set is small
(~28k rows / ~15 s) — full pull every run; upserts dedupe.

## Overture ingest: NJ filter + mark-and-sweep
The NYC bbox rectangle overlaps NJ (Jersey City, Hoboken, Newark). `region` is
100% populated in Overture, so rows outside NY are dropped (78 of 521).
`overture_places` is then swept to the current id set each run (with a guard
against a thin run), so vanished places leave — their history stays in
`overture_place_snapshots` for future release diffing.

## `overture_places` mirrors one release; snapshots keep history
`overture_places` = current boba inventory. `overture_place_snapshots` gets a
slim row per `(release, place_id)` so successive releases can be diffed later for
open/close churn (a secondary signal to DOHMH).

## `source_update_time` is not an "opened" proxy
It's when Overture last touched the record — it clusters in the current year and
produced 300+ fake "2026 openings" before it was removed. Opened dates come from
DOHMH first inspection, or Overture `first_seen_release` once we have ≥2 ingests.

## Task runner: `just`, not Make
`just` is a command runner (named args, `.env` loading, `--list`); Make is a
build system you'd fight with `.PHONY`.

## Notebooks: jupytext `.py` percent, `.ipynb` gitignored
Clean diffs, runnable as scripts, still open as notebooks. The boba predicate
lives in `boba/filters.py` so notebooks and the pipeline can't diverge.
