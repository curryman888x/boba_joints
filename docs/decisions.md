# Decisions — why the infrastructure looks like this

Short log of non-obvious choices. Newest first.

## Yelp's `bubbletea` category is the primary discovery source
The name regex + Overture `bubble_tea` tag kept missing shops (Jooy Tea, Mogee
Tea, whole chains like Kung Fu Tea locations). Yelp curates a `bubbletea`
category by hand and carries `is_closed` for free. `ingest/yelp.py::discover`
enumerates it over an adaptive NYC grid — subdividing any tile that hits Yelp's
240-result ceiling — and keeps a business only if `bubbletea` is its *primary*
category (or the name matches), dropping ~260 restaurants that merely serve boba.
~446 businesses vs ~200 net-new the other two still add. Overture and DOHMH kept:
Overture for brand + precise geometry, DOHMH for the only real timeline. Yelp
discovery is cached (skipped if the last run was < `--max-age-days`) and the
whole stage no-ops without `YELP_API_KEY`.

## Dates are `first_seen` / `last_seen`, not `opened` / `closed`
DOHMH has no opening or closing field — it's inspection data. Calling the first
inspection date an "opening date" overclaims (it lags the true opening by the
permit gap). Fields renamed to `first_seen_date` / `last_seen_date`, read as
"operating by / still operating at", ±1 quarter. `closed_date` is set only on a
real closure signal. The year summary is "first seen per year", a proxy.

## Yelp linking is by name + distance, not `/businesses/matches`
Discovery already returns full business records (name, coords, `is_closed`), so
`ingest/yelp.py::link` just scores each Yelp business against nearby Overture
places and DOHMH points (`ST_DWithin` 160 m, `name_sim ≥ 60`) — one
`yelp_matches` row per business with its best Overture id + CAMIS. No extra API
calls. Feeds `analyze`: `yelp_closed` beats a stale inspection; `yelp_open`
outranks `overture_open` but not a recent `dohmh_active` inspection.

## Dropped `status_events`, `overture_place_snapshots`, `yelp_status`
`status_events` just split `boba_shops` columns into rows and nothing read it.
`overture_place_snapshots` was written every run and never read (release-diffing
we didn't build); `first_seen_release` on `overture_places` still tracks first
appearance. `yelp_status` (the old name+address `/businesses/matches` table) was
replaced by `yelp_businesses` + `yelp_matches` when Yelp became the primary
discovery source. All removed; migrations re-squashed to one.

## Borough is point-in-polygon, not a locality string
Overture `locality` is neighbourhood-level ("Woodside", "Flushing") and DOHMH
`boro` and Overture disagree on names ("Manhattan" vs "New York"). Worse, the NYC
bbox rectangle covers slices of Nassau (Great Neck, Manhasset) and Westchester
(New Rochelle). `boba/seed.py` loads NYC's official borough polygons
(`boroughs` table); `analyze.py` does `ST_Contains` to assign the borough and
**drops any shop outside all five** (~14). DOHMH `boro` is a fallback only for a
point that lands just off a polygon (shoreline geocode).

## BOBA_NAME_PATTERN is the secondary net, and carries newer chains explicitly
Yelp's `bubbletea` category is primary discovery; the name regex now only
*recovers* Overture-only and DOHMH-only shops (DOHMH has no bubble-tea cuisine at
all). It's kept broad on purpose — trimming it silently drops those ~200 shops.
HeyTea, Molly Tea, Auntea Jenny, Chun Yang, Sunright, Chagee etc. contain no
generic keyword, so they're listed by name.

## Contracts: pydantic + pandera + a manifest table
Data pipelines rot silently when a source changes shape. `boba/contracts.py`
validates every Overture record (pydantic — nested structs, fails on an unknown
`operating_status`, bad `confidence`, missing `categories`) and the whole DOHMH
frame (pandera — flat/tabular, warns on unknown `action` strings) and every Yelp
business (`YelpBusinessRecord`). `ingest_runs` records volume / coverage /
version per run; `drift_warnings()` flags a ≥25% swing or coverage reaching
further back. `boba/checks.py` has 15 post-pipeline SQL invariants (`just check`).

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
100% populated in Overture, so rows outside NY are dropped (~78). `overture_places`
mirrors one release — it's swept to the current id set each run (with a guard
against a thin run), so vanished places leave. `first_seen_release` /
`last_seen_release` are preserved on conflict, so we still know when a place first
and last appeared without keeping a separate snapshot table.

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
