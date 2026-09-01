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
# # 01 · Overture places — does NYC actually have boba shops we can use?
#
# Goal: **stop assuming the happy path.** Before building the pipeline, confirm:
#
# 1. The Overture `place` download for the NYC bbox actually works.
# 2. There *is* a `bubble_tea` category and it's populated in NYC (how many?).
# 3. `operating_status` is filled in for NYC (or still mostly null).
# 4. Names / addresses look usable for matching against DOHMH.
#
# Nothing here writes to the database.

# %%
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections import Counter

import geopandas as gpd
import pandas as pd

from boba.config import BOBA_FALLBACK_CATEGORIES, BOBA_NAME_PATTERN, DATA_DIR, NYC_BBOX

pd.set_option("display.max_columns", 40)
pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 60)

# Tiny Manhattan box for a fast smoke test before the full pull.
# In a notebook: set SMOKE_TEST = True.  As a script: SMOKE_TEST=1 uv run python ...
SMOKE_TEST = os.environ.get("SMOKE_TEST", "0") == "1"
MANHATTAN_BBOX = (-74.02, 40.70, -73.93, 40.82)
BBOX = MANHATTAN_BBOX if SMOKE_TEST else NYC_BBOX
OUT = DATA_DIR / ("overture_places_smoke.parquet" if SMOKE_TEST else "overture_places_nyc.parquet")
print("bbox:", BBOX)
print("target file:", OUT)

# %% [markdown]
# ## Download the extract (cached on disk)

# %%
def download_places(bbox: tuple[float, float, float, float], out) -> None:
    if out.exists():
        print(f"already have {out} ({out.stat().st_size / 1e6:.1f} MB) — skipping download")
        return
    bbox_str = ",".join(str(x) for x in bbox)
    exe = shutil.which("overturemaps")
    cmd = [exe] if exe else [sys.executable, "-m", "overturemaps"]
    cmd += ["download", "-t", "place", "-f", "geoparquet", "--bbox", bbox_str, "-o", str(out)]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"done: {out.stat().st_size / 1e6:.1f} MB")


download_places(BBOX, OUT)

# %%
gdf = gpd.read_parquet(OUT)
print("rows:", len(gdf))
print("crs:", gdf.crs)
print("columns:", list(gdf.columns))
gdf.head(3)

# %% [markdown]
# ## Unpack the nested Overture fields
#
# `names`, `categories`, `addresses`, `brand` come back as dicts / lists of dicts.

# %%
def _as_list(x):
    """pyarrow list fields come back as numpy arrays -> normalise to a plain list."""
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    if hasattr(x, "tolist"):
        return list(x.tolist())
    return [x]


def _name(v):
    if isinstance(v, dict):
        return v.get("primary") or v.get("common")
    return None


def _cat_primary(v):
    if isinstance(v, dict):
        return v.get("primary") or v.get("main")
    return None


def _cat_all(v):
    if not isinstance(v, dict):
        return []
    prim = v.get("primary") or v.get("main")
    return [c for c in ([prim, *_as_list(v.get("alternate"))]) if c]


def _first_addr(v):
    seq = _as_list(v)
    if seq and isinstance(seq[0], dict):
        return seq[0]
    if isinstance(v, dict):
        return v
    return {}


flat = pd.DataFrame(
    {
        "id": gdf["id"],
        "name": gdf["names"].map(_name),
        "cat_primary": gdf["categories"].map(_cat_primary),
        "cat_all": gdf["categories"].map(_cat_all),
        "basic_category": gdf["basic_category"] if "basic_category" in gdf else None,
        "confidence": gdf.get("confidence"),
        "operating_status": gdf["operating_status"] if "operating_status" in gdf else None,
    }
)
addr = gdf["addresses"].map(_first_addr)
flat["addr_freeform"] = addr.map(lambda d: d.get("freeform"))
flat["locality"] = addr.map(lambda d: d.get("locality"))
flat["region"] = addr.map(lambda d: d.get("region"))
flat["postcode"] = addr.map(lambda d: d.get("postcode"))
flat["geometry"] = gdf.geometry
flat = gpd.GeoDataFrame(flat, geometry="geometry", crs=gdf.crs)
flat.head(3)

# %% [markdown]
# ## Q2 — is `bubble_tea` a real, populated category here?

# %%
cat_counts = flat["cat_primary"].value_counts(dropna=False)
print("distinct primary categories:", cat_counts.size)
print("\ntop 30 primary categories:")
print(cat_counts.head(30))

tea_like = [c for c in cat_counts.index if isinstance(c, str) and ("tea" in c or "boba" in c)]
print("\ncategories containing 'tea'/'boba':", tea_like)
print("counts:\n", cat_counts.reindex(tea_like))

# The new taxonomy field: is `basic_category` a cleaner filter?
if flat["basic_category"].notna().any():
    print("\nbasic_category value counts (top 30):")
    print(flat["basic_category"].value_counts(dropna=False).head(30))
    bt = [c for c in flat["basic_category"].dropna().unique() if "tea" in c or "boba" in c]
    print("\nbasic_category tea/boba values:", bt)

# %%
all_cat_counter = Counter(c for lst in flat["cat_all"] for c in lst)
print("bubble_tea appears (primary OR alternate):", all_cat_counter.get("bubble_tea", 0))
print("\nany category with tea/boba (primary or alternate):")
for c, n in sorted(all_cat_counter.items(), key=lambda kv: -kv[1]):
    if "tea" in c or "boba" in c:
        print(f"  {c:30s} {n}")

# %%
bubble_tea = flat[flat["cat_all"].map(lambda lst: "bubble_tea" in lst)].copy()
print("bubble_tea rows:", len(bubble_tea))
bubble_tea[["name", "cat_primary", "cat_all", "confidence", "operating_status", "addr_freeform", "locality"]].head(40)

# %% [markdown]
# ## Q3 — is `operating_status` populated for NYC?

# %%
if "operating_status" in gdf:
    print("operating_status over ALL NYC places:")
    print(gdf["operating_status"].value_counts(dropna=False))
    print("\nnon-null fraction: {:.1%}".format(gdf["operating_status"].notna().mean()))
    print("\nover bubble_tea rows only:")
    print(bubble_tea["operating_status"].value_counts(dropna=False))
else:
    print("no operating_status column in this release")

# %% [markdown]
# ## Q4 — name-pattern sweep of cafes / dessert shops (boba shops mis-tagged as generic)

# %%
fallback = flat[flat["cat_primary"].isin(BOBA_FALLBACK_CATEGORIES)].copy()
fallback["name_hit"] = fallback["name"].fillna("").str.contains(BOBA_NAME_PATTERN, regex=True)
name_hits = fallback[fallback["name_hit"]]
print("fallback-category rows:", len(fallback))
print("of those, name matches boba pattern:", len(name_hits))
print("...that are NOT already bubble_tea:", (~name_hits["id"].isin(bubble_tea["id"])).sum())
name_hits[["name", "cat_primary", "confidence", "operating_status", "addr_freeform"]].head(40)

# %% [markdown]
# ## Candidate set = bubble_tea ∪ name-matched fallback

# %%
candidates = pd.concat([bubble_tea, name_hits.drop(columns=["name_hit"])]).drop_duplicates("id")
print("TOTAL Overture boba candidates in NYC:", len(candidates))
print("\nby borough/locality:")
print(candidates["locality"].value_counts().head(15))
print("\nby operating_status:")
print(candidates.get("operating_status").value_counts(dropna=False) if "operating_status" in candidates else "n/a")
print("\nconfidence describe:")
print(candidates["confidence"].describe())

# %%
# Address shape we'll rely on for DOHMH matching — eyeball a sample.
candidates[["name", "addr_freeform", "locality", "region", "postcode"]].sample(min(25, len(candidates)), random_state=0)

# %% [markdown]
# ## Verdict
#
# Fill in after running:
#
# - bubble_tea count: ___
# - extra from name sweep: ___
# - operating_status usable? ___
# - addresses usable for matching? ___
