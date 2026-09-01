# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # boba_joints — project recap
#
# **Question:** which NYC boba shops have opened / closed, and when?
# Originally "since 2020" — see the constraint below.
#
# This notebook is a living status page: what's built, what the data actually
# looks like, and what's next. Re-run it any time.

# %% [markdown]
# ## TL;DR
#
# * **Stack chosen:** `uv` + PostGIS (Docker) + SQLAlchemy/GeoAlchemy2 + Alembic,
#   pipeline steps as plain modules, `just` task runner, jupytext notebooks.
# * **Two data sources validated with live pulls:**
#   * **Overture Maps `place`** — current inventory. `bubble_tea` category is real
#     and populated (~180 in a Manhattan-sized box). Has a current-only
#     `operating_status` (`open` / `permanently_closed`). No open/close dates.
#     Data starts **July 2023**.
#   * **NYC DOHMH restaurant inspections** — the time dimension. Name-matching
#     `dba` cleanly finds boba shops (172 CAMIS citywide). First inspection ≈
#     "opened by". Data starts **Jan 2022** (rolling ~3-year window is real).
# * **Hard constraint:** neither free source sees **2020–2021**.
# * **Decision:** scope the deliverable to **2022 → 2026**, framed as the NYC
#   boba boom, with explicit confidence bands. 2020–2021 = acknowledged gap.

# %% [markdown]
# ## What's scaffolded
#
# ```
# docker-compose.yml        postgis/postgis:16-3.5 -> localhost:5433   (not started/migrated yet)
# .env / .env.example       DATABASE_URL
# alembic.ini, migrations/  wired to boba.models.Base + GeoAlchemy2    (no migration generated yet)
# justfile                  db-up / migrate / ingest-* / match / analyze / all
# pyproject.toml            deps + [dependency-groups] notebooks
# boba/
#   config.py               NYC bbox, BOBA_CATEGORIES, BOBA_NAME_PATTERN, DOHMH dataset id
#   db.py                   engine + SessionLocal
#   models.py               9 tables (below)
#   ingest/overture.py      STUB (NotImplementedError)
#   ingest/dohmh.py         STUB
#   match.py                STUB
#   analyze.py              STUB
# notebooks/
#   00_recap.py             this file
#   01_overture_places.py   Overture exploration  (runs, validated)
#   02_dohmh_inspections.py DOHMH exploration     (runs, validated)
# ```

# %% [markdown]
# ## Schema (boba/models.py) — 3 layers, 9 tables
#
# | layer | table | purpose |
# |---|---|---|
# | raw | `overture_places` | latest-release Overture boba candidates in NYC bbox |
# | raw | `overture_place_snapshots` | slim row per release, for diffing releases |
# | raw | `dohmh_establishments` | one row per CAMIS + derived first/last inspection, closed flags |
# | raw | `dohmh_inspections` | distinct inspection/violation rows |
# | link | `place_matches` | Overture place ↔ CAMIS candidate links (name + distance score) |
# | derived | `boba_shops` | canonical merged shop w/ best-estimate opened/closed |
# | derived | `status_events` | opened / closed / reopened timeline |
#
# `boba_shops` + `status_events` are recomputed by `analyze.py`; raw tables are truth.

# %% [markdown]
# ## Live numbers from the exploration caches
#
# (Populated by `notebooks/01_*` and `02_*`. If a cache is missing, run those first.)

# %%
from __future__ import annotations

import geopandas as gpd
import pandas as pd

from boba.config import BOBA_NAME_PATTERN, DATA_DIR

pd.set_option("display.width", 140)
pd.set_option("display.max_colwidth", 50)

# %% [markdown]
# ### Overture (Manhattan smoke box)

# %%
ov_path = DATA_DIR / "overture_places_nyc.parquet"
if not ov_path.exists():
    ov_path = DATA_DIR / "overture_places_smoke.parquet"

if ov_path.exists():
    g = gpd.read_parquet(ov_path)

    def cat_all(v):
        if not isinstance(v, dict):
            return []
        alt = v.get("alternate")
        alt = list(alt.tolist()) if hasattr(alt, "tolist") else (list(alt) if alt else [])
        return [c for c in ([v.get("primary") or v.get("main"), *alt]) if c]

    is_bt = g["categories"].map(lambda v: "bubble_tea" in cat_all(v))
    bt = g[is_bt]
    print(f"source file            : {ov_path.name}  ({len(g):,} total places)")
    print(f"bubble_tea places      : {len(bt):,}")
    if "operating_status" in bt:
        print("operating_status       :")
        print(bt["operating_status"].value_counts(dropna=False).to_string().replace("\n", "\n  "))
else:
    print("no Overture cache yet — run notebooks/01_overture_places.py")

# %% [markdown]
# ### DOHMH (citywide boba candidates)

# %%
d_path = DATA_DIR / "dohmh_boba_candidates.parquet"
if d_path.exists():
    raw = pd.read_parquet(d_path)
    raw["inspection_date"] = pd.to_datetime(raw["inspection_date"], errors="coerce")
    raw.loc[raw["inspection_date"] == pd.Timestamp("1900-01-01"), "inspection_date"] = pd.NaT
    raw["dba"] = raw["dba"].fillna("").str.strip()
    boba = raw[raw["dba"].str.contains(BOBA_NAME_PATTERN, regex=True)]

    per_camis = boba.groupby("camis").agg(
        first_insp=("inspection_date", "min"),
        last_insp=("inspection_date", "max"),
        ever_closed=("action", lambda s: s.str.contains("Closed", case=False, na=False).any()),
    )
    print(f"confident boba CAMIS   : {boba['camis'].nunique()}")
    print(f"earliest inspection    : {boba['inspection_date'].min().date()}")
    print(f"CAMIS ever force-closed : {int(per_camis['ever_closed'].sum())}")
    cut = pd.Timestamp.today() - pd.Timedelta(days=550)
    print(f"silent >18mo (proxy)   : {int(((per_camis['last_insp'] < cut) & ~per_camis['ever_closed']).sum())}")
    print("\nfirst-inspection year (opening proxy):")
    print(per_camis["first_insp"].dt.year.value_counts().sort_index().to_string())
else:
    print("no DOHMH cache yet — run notebooks/02_dohmh_inspections.py")

# %% [markdown]
# ## How opened / closed will be inferred (2022–2026 scope)
#
# | signal | means | confidence |
# |---|---|---|
# | DOHMH first inspection date | opened on/before this date | **high** (month precision) |
# | Overture: first release the place appears in | opened on/before that release | medium (quarter) |
# | Overture `source_update_time` only | rough opened year | low |
# | DOHMH `action` = "Establishment Closed by DOHMH" | closed (health) — rare, ~3 shops | high but narrow |
# | DOHMH: no inspection in >18mo, not force-closed | probably closed | low–medium |
# | Overture `operating_status` = `permanently_closed` | closed | medium (current snapshot) |
# | in old Overture snapshot, gone from latest, no recent DOHMH | closed | low |
#
# `analyze.py` blends these per shop and emits `status_events`, plus a summary:
# openings & closings per year 2022–2026, net, still-open count, by borough,
# and `data/boba_status.csv`.

# %% [markdown]
# ## Next steps
#
# 1. **DB up + first migration** — `just db-up && just revision "initial schema" && just migrate`
#    (Docker is running; add `CREATE EXTENSION IF NOT EXISTS postgis` to the migration).
# 2. **Implement `boba/ingest/overture.py`** — full 5-borough bbox pull, filter to
#    `bubble_tea` ∪ name-matched fallback, upsert `overture_places`.
# 3. **Implement `boba/ingest/dohmh.py`** — Socrata pull, `dohmh_establishments` +
#    `dohmh_inspections`, derive first/last/closed fields.
# 4. **Implement `boba/match.py`** — PostGIS `ST_DWithin` (~75 m) + `rapidfuzz`
#    name score → `place_matches`.
# 5. **Implement `boba/analyze.py`** — the blend above → `boba_shops`,
#    `status_events`, summary + CSV.
#
# ### Open question for step 5
# How aggressive should the "silently closed" inference be (the >18-month rule)?
# It's the only closure signal with real volume, but it's noisy.
