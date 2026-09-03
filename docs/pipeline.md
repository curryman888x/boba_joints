# Pipeline

`just all`: `db-up → migrate → seed → ingest-dohmh → ingest-yelp → analyze → check`.

Yelp is the **primary discovery source** (its curated `bubbletea` category);
DOHMH supplies the first-observed date. Yelp linking runs after DOHMH ingest
because it matches each Yelp business against the DOHMH points. The output is a
current census, not an openings/closings timeline — see [decisions.md](decisions.md).

```mermaid
flowchart TD
    DH[(NYC DOHMH<br/>inspections · Socrata)]
    NB[(NYC Open Data<br/>borough polygons)]
    YP[(Yelp Fusion API<br/>bubbletea category)]

    NB -->|boba/seed.py| BORO[(boroughs<br/>5 MultiPolygons)]

    DH -->|wide dba LIKE net<br/>+ beverage cuisines| DHDL[/data/dohmh_raw_last.parquet/]
    DHDL -->|dohmh_frame_schema pandera<br/>→ normalize| DHI[ingest/dohmh.py]

    DHI --> DE[(dohmh_establishments<br/>~2800, 195 boba-name)]
    DHI --> DI[(dohmh_inspections<br/>~27.5k, incl. inspection_type)]
    DI -. recompute first/last inspection<br/>(prefer pre-permit), closed/reopened .-> DE

    YP -->|discover: search bubbletea over<br/>an adaptive NYC grid<br/>→ YelpBusinessRecord pydantic<br/>→ keep if bubbletea is primary| YD[ingest/yelp.py · discover]
    YD --> RAW[/data/yelp_raw_last.json<br/>raw sweep cache/]
    YD --> YB[(yelp_businesses<br/>~446, incl. is_closed)]

    YB --> YL[ingest/yelp.py · link]
    DE --> YL
    YL -->|name + distance ≤ 160 m<br/>→ best CAMIS| YM[(yelp_matches<br/>~185)]

    YB --> A[analyze.py]
    YM --> A
    DE --> A
    BORO -->|ST_Contains → assign borough,<br/>drop points outside NYC| A
    A -->|seed order: Yelp → boba-name CAMIS<br/>· dedup · first_seen / last_seen<br/>· status + status_basis · identified_by| BS[(boba_shops<br/>~450)]
    A --> CSV[/data/boba_status.csv/]

    BS --> DASH[dashboard/app.py<br/>Streamlit + Plotly]

    DHI -. manifest .-> IR[(ingest_runs<br/>volume · coverage · drift_warnings)]
    YD -. manifest .-> IR
    YL -. manifest .-> IR
    A -. manifest .-> IR

    BS --> CHK[checks.py<br/>SQL invariants · just check]
    YM --> CHK
    DE --> CHK
```

## The seams

| Stage | Contract / guard |
|---|---|
| Yelp discover | `YelpBusinessRecord` pydantic per business; kept only if `bubbletea` is its **primary** category (or the name matches) — drops ~260 restaurants that merely serve boba; raw sweep cached to `data/yelp_raw_last.json`; mark-and-sweep guarded against a rate-limited thin sweep |
| Yelp link | name + distance within 160 m against DOHMH points, kept at `name_sim ≥ 60` |
| DOHMH ingest | pandera on the frame — hard-fails on bad `camis`; warns on unknown `action` / out-of-NYC coords |
| every ingest | `ingest_runs` row + `drift_warnings()` vs the last good run |
| schema | `alembic check` — models ↔ migration ↔ DB |
| post-run | `checks.py` — FK resolution, date order, status enums, `yelp_matches` integrity |

Yelp discovery is skipped if the last successful `yelp_discover` run was within
`--max-age-days` (default 14); `--rediscover` forces a fresh sweep. The whole
Yelp stage no-ops without `YELP_API_KEY` (the set is then just the DOHMH
boba-name tail).

See [methodology.md](methodology.md) for identification / linking / date logic,
[data-sources.md](data-sources.md) for why two sources, and
[decisions.md](decisions.md) for infrastructure choices.
