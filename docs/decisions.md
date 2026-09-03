# Decisions — why the infrastructure looks like this

Short log of non-obvious choices. Newest first.

## Reframed: a current census, not an openings/closings timeline
The original ask was "which NYC boba shops opened or closed, and when (2022–2026)".
Free data can't support the *when*: no source has real opening dates (DOHMH first
inspection is a ±1-quarter proxy on ~40% of shops), and closings are essentially
undateable (DOHMH forced-closures are ~3 and rare; Yelp `is_closed` carries no
date; inspection silence is deliberately *not* a closure). So the deliverable is
now **a current census** — where each shop is, whether it's open, and a
*first-observed* date where DOHMH has an inspection. The year axis (2022–2026) is
the DOHMH coverage window, not a study period. `first_seen` per year is kept as a
soft "when shops entered the record" signal; there is no closings-over-time
claim.

## Dropped Overture entirely — Yelp + DOHMH only
Overture's *unique* contribution turned out to be thin: ~28 shops with a brand
Yelp doesn't have, and ~208 shops that were essentially unverifiable map labels
(`operating_status=open` from Overture, nothing else). The cost was a 500k-row
parquet download per run, `match.py`, `place_matches`, a pydantic contract, an NJ
filter, mark-and-sweep, and half of `analyze.py`. Removed: `ingest/overture.py`,
`match.py`, `overture_places`, `place_matches`, the `overture_id` columns, the
`overturemaps` / `geopandas` deps. Brand is now derived from the shop name
against a ~25-chain lookup in the dashboard. The set drops ~655 → ~450, and every
remaining shop is either in Yelp's curated category or a boba-named health permit.

## Yelp's `bubbletea` category is the primary discovery source
The name regex + Overture `bubble_tea` tag kept missing shops (Jooy Tea, Mogee
Tea, whole chains like Kung Fu Tea locations). Yelp curates a `bubbletea`
category by hand and carries `is_closed` for free. `ingest/yelp.py::discover`
enumerates it over an adaptive NYC grid — subdividing any tile that hits Yelp's
240-result ceiling — and keeps a business only if `bubbletea` is its *primary*
category (or the name matches), dropping ~260 restaurants that merely serve boba.
~446 businesses. The raw sweep is cached to `data/yelp_raw_last.json` so a later
rate-limited run (free tier is ~500 calls/day) rebuilds `yelp_businesses` instead
of wiping it. Discovery is skipped if the last run was < `--max-age-days`; the
whole stage no-ops without `YELP_API_KEY`.

## Dates are `first_seen` / `last_seen`, not `opened` / `closed`
DOHMH has no opening or closing field — it's inspection data. Calling the first
inspection date an "opening date" overclaims (it lags the true opening by the
permit gap). Fields named `first_seen_date` / `last_seen_date`, read as
"operating by / still operating at", ±1 quarter. `closed_date` is set only on a
real closure signal. The year summary is "first seen per year", a proxy.

## Inspection silence is `unknown`, not `closed`
`dohmh_inactive` (no DOHMH inspection in 18+ months, nothing else says open) sets
`status=unknown`, not `closed`. "We haven't seen it and no other source knows it"
is genuinely unknown. DOHMH is trusted for `first_seen` dates and its own
explicit "Establishment Closed by DOHMH" actions — nothing more. A current Yelp
`is_closed=false` also outranks an inspection gap for the *open* basis.

## Yelp linking is by name + distance, not `/businesses/matches`
Discovery already returns full business records (name, coords, `is_closed`), so
`ingest/yelp.py::link` just scores each Yelp business against DOHMH points within
160 m (`ST_DWithin`, `name_sim ≥ 60`) — one `yelp_matches` row per linked
business with its best CAMIS. No extra API calls. ~185 of ~446 link.

## Dropped `status_events`, `overture_place_snapshots`, `yelp_status`
`status_events` just split `boba_shops` columns into rows and nothing read it.
`overture_place_snapshots` was written every run and never read. `yelp_status`
(the old name+address `/businesses/matches` table) was replaced by
`yelp_businesses` + `yelp_matches` when Yelp became primary discovery. All gone
along with Overture itself; migrations re-squashed to one.

## Borough is point-in-polygon, not a locality string
Yelp's lat/lon radius search returns shops in Nassau (Great Neck, Manhasset),
Westchester and NJ. `boba/seed.py` loads NYC's official borough polygons
(`boroughs` table); `analyze.py` does `ST_Contains` to assign the borough and
**drops any shop outside all five** (~100). DOHMH `boro` is a fallback only for a
point that lands just off a polygon (shoreline geocode).

## BOBA_NAME_PATTERN is the secondary net, and carries newer chains explicitly
Yelp's `bubbletea` category is primary discovery; the name regex now only
recovers DOHMH-only shops (DOHMH has no bubble-tea cuisine at all) and rescues
Yelp results where `bubbletea` isn't the primary category. It's kept broad on
purpose — trimming it silently drops the ~50 DOHMH-only shops. HeyTea, Molly Tea,
Auntea Jenny, Chun Yang, Sunright, Chagee etc. contain no generic keyword, so
they're listed by name.

## Contracts: pydantic + pandera + a manifest table
Data pipelines rot silently when a source changes shape. `boba/contracts.py`
validates every Yelp business (`YelpBusinessRecord` pydantic — flattens nested
`coordinates` / `location`, fails on missing coords) and the whole DOHMH frame
(pandera — flat/tabular, warns on unknown `action` strings). `ingest_runs`
records volume / coverage / version per run; `drift_warnings()` flags a ≥25%
swing or coverage reaching further back. `boba/checks.py` has post-pipeline SQL
invariants (`just check`).

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

## Task runner: `just`, not Make
`just` is a command runner (named args, `.env` loading, `--list`); Make is a
build system you'd fight with `.PHONY`.

## Notebooks: jupytext `.py` percent, `.ipynb` gitignored
Clean diffs, runnable as scripts, still open as notebooks. The boba predicate
lives in `boba/filters.py` so notebooks and the pipeline can't diverge.
