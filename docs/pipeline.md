# Pipeline

`just all`: `db-up → migrate → seed → ingest-overture → ingest-dohmh → match → ingest-yelp → analyze → check`.

Yelp is the **primary discovery source** (its curated `bubbletea` category);
Overture supplies brand + geometry, DOHMH supplies the timeline. Yelp linking
runs *after* `match` because it reuses the Overture/DOHMH points.

```mermaid
flowchart TD
    OV[(Overture Maps<br/>place · public S3 GeoParquet)]
    DH[(NYC DOHMH<br/>inspections · Socrata)]
    NB[(NYC Open Data<br/>borough polygons)]
    YP[(Yelp Fusion API<br/>bubbletea category)]

    NB -->|boba/seed.py| BORO[(boroughs<br/>5 MultiPolygons)]

    OV -->|overturemaps download<br/>5-borough bbox| OVDL[/data/overture_places_&lt;release&gt;.parquet/]
    DH -->|wide dba LIKE net<br/>+ beverage cuisines| DHDL[/data/dohmh_raw_last.parquet/]

    OVDL -->|category pre-filter<br/>→ OverturePlaceRecord pydantic<br/>→ overture_is_boba → drop non-NY| OVI[ingest/overture.py]
    DHDL -->|dohmh_frame_schema pandera<br/>→ normalize| DHI[ingest/dohmh.py]

    OVI --> OP[(overture_places<br/>~449, latest release, mark & sweep)]
    DHI --> DE[(dohmh_establishments<br/>~2800, 195 boba-name)]
    DHI --> DI[(dohmh_inspections<br/>~27.5k, incl. inspection_type)]
    DI -. recompute first/last inspection<br/>(prefer pre-permit), closed/reopened .-> DE

    OP --> M[match.py]
    DE --> M
    M -->|ST_DWithin 120 m + rapidfuzz<br/>+ label propagation| PM[(place_matches<br/>~174)]

    YP -->|discover: search bubbletea over<br/>an adaptive NYC grid<br/>→ YelpBusinessRecord pydantic<br/>→ keep if bubbletea is primary| YD[ingest/yelp.py · discover]
    YD --> YB[(yelp_businesses<br/>~446, incl. is_closed)]

    YB --> YL[ingest/yelp.py · link]
    OP --> YL
    DE --> YL
    YL -->|name + distance<br/>→ best Overture id + CAMIS| YM[(yelp_matches<br/>~240)]

    YB --> A[analyze.py]
    YM --> A
    OP --> A
    DE --> A
    PM --> A
    BORO -->|ST_Contains → assign borough,<br/>drop points outside NYC| A
    A -->|seed order: Yelp → Overture-only → DOHMH-only<br/>· dedup · first_seen / last_seen<br/>· status + status_basis · identified_by| BS[(boba_shops<br/>~655)]
    A --> CSV[/data/boba_status.csv/]

    BS --> DASH[dashboard/app.py<br/>Streamlit + Plotly]

    OVI -. manifest .-> IR[(ingest_runs<br/>volume · coverage · drift_warnings)]
    DHI -. manifest .-> IR
    M -. manifest .-> IR
    YD -. manifest .-> IR
    YL -. manifest .-> IR
    A -. manifest .-> IR

    BS --> CHK[checks.py<br/>SQL invariants · just check]
    PM --> CHK
    YM --> CHK
    DE --> CHK
```

## The seams

| Stage | Contract / guard |
|---|---|
| Yelp discover | `YelpBusinessRecord` pydantic per business; a business is kept only if `bubbletea` is its **primary** category (or the name matches) — drops ~260 restaurants that merely serve boba; mark-and-sweep guarded against a rate-limited thin sweep |
| Yelp link | name + distance within 160 m against Overture and DOHMH points; kept only at `name_sim ≥ 60` |
| Overture ingest | pydantic per record — fails on unknown `operating_status`, bad `confidence`, missing `categories`; aborts if >10% of candidates fail |
| DOHMH ingest | pandera on the frame — hard-fails on bad `camis`; warns on unknown `action` / out-of-NYC coords |
| every ingest | `ingest_runs` row + `drift_warnings()` vs the last good run |
| schema | `alembic check` — models ↔ migration ↔ DB |
| post-run | `checks.py` — FK resolution, score ranges, date order, status enums, `yelp_matches` integrity |

Yelp discovery is skipped if the last successful `yelp_discover` run was within
`--max-age-days` (default 14); `--rediscover` forces a fresh sweep. The whole
Yelp stage no-ops without `YELP_API_KEY`.

See [methodology.md](methodology.md) for identification / matching / date logic,
[data-sources.md](data-sources.md) for why three sources, and
[decisions.md](decisions.md) for infrastructure choices.
