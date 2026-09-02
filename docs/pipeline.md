# Pipeline

`just all` runs: `db-up → migrate → ingest-overture → ingest-dohmh → match → analyze → check`.

```mermaid
flowchart TD
    OV[(Overture Maps<br/>place · public S3 GeoParquet)]
    DH[(NYC DOHMH<br/>inspections · Socrata)]

    OV -->|overturemaps download<br/>5-borough bbox| OVDL[/data/overture_places_&lt;release&gt;.parquet/]
    DH -->|wide dba LIKE net<br/>+ beverage cuisines| DHDL[/data/dohmh_raw_last.parquet/]

    OVDL -->|category pre-filter<br/>→ OverturePlaceRecord pydantic<br/>→ overture_is_boba → drop non-NY| OVI[ingest/overture.py]
    DHDL -->|dohmh_frame_schema pandera<br/>→ normalize| DHI[ingest/dohmh.py]

    OVI --> OP[(overture_places<br/>~443, latest release only)]
    OVI --> OPS[(overture_place_snapshots<br/>slim row per release)]
    DHI --> DE[(dohmh_establishments<br/>~2803, 170 boba-name)]
    DHI --> DI[(dohmh_inspections<br/>~27.5k)]
    DI -. recompute first/last inspection,<br/>closed/reopened .-> DE

    OP --> M[match.py]
    DE --> M
    M -->|ST_DWithin 120 m<br/>+ rapidfuzz on stopword-stripped names<br/>+ label propagation| PM[(place_matches<br/>~170)]

    OP --> A[analyze.py]
    DE --> A
    PM --> A
    A -->|merged / Overture-only / DOHMH-only<br/>· opened+closed date blend<br/>· identified_by| BS[(boba_shops<br/>~523)]
    A --> SE[(status_events<br/>opened/closed/reopened)]
    A --> CSV[/data/boba_status.csv/]

    BS --> DASH[dashboard/app.py<br/>Streamlit + pydeck]
    SE --> DASH

    OVI -. manifest .-> IR[(ingest_runs<br/>volume · coverage · drift_warnings)]
    DHI -. manifest .-> IR
    M -. manifest .-> IR
    A -. manifest .-> IR

    BS --> CHK[checks.py<br/>13 SQL invariants · just check]
    PM --> CHK
    DE --> CHK
```

## The seams

| Stage | Contract / guard |
|---|---|
| Overture ingest | pydantic per record — fails on unknown `operating_status`, bad `confidence`, missing `categories`; aborts if >10% of candidates fail |
| DOHMH ingest | pandera on the frame — hard-fails on bad `camis`; warns on unknown `action` / out-of-NYC coords |
| every ingest | `ingest_runs` row + `drift_warnings()` vs the last good run |
| schema | `alembic check` — models ↔ migrations ↔ DB |
| post-run | `checks.py` — FK resolution, score/date ranges, status enums, the 1900 sentinel |

See [methodology.md](methodology.md) for the matching and date-blending logic,
[data-sources.md](data-sources.md) for why two sources, and
[decisions.md](decisions.md) for infrastructure choices.
