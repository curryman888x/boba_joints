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
# # 02 · NYC DOHMH inspections — can we actually see boba shops opening / closing?
#
# DOHMH has **no "bubble tea" cuisine**, and the dataset is widely said to only
# keep a rolling ~3 years of inspections. Before trusting it as the 2020+
# timeline, confirm:
#
# 1. The Socrata pull works and returns rows.
# 2. Name-matching `dba` surfaces a real set of boba shops (how many CAMIS?).
# 3. `action` really contains closure signals.
# 4. **How far back do inspection dates actually go?** Can we see 2020 openings?
# 5. lat/long coverage is good enough for spatial matching.
#
# Nothing here writes to the database.

# %%
from __future__ import annotations

import time

import pandas as pd
import requests

from boba.config import BOBA_NAME_PATTERN, DOHMH_NULL_DATE, DOHMH_SOCRATA_BASE, data_dir

DATA_DIR = data_dir()

pd.set_option("display.max_columns", 40)
pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 70)

CACHE = DATA_DIR / "dohmh_boba_candidates.parquet"

# %% [markdown]
# ## Pull candidate rows from Socrata
#
# SoQL `like` is limited (no regex), so cast a wide net here on `dba` + a few
# cuisines, then tighten with the real regex in pandas.

# %%
LIKE_TERMS = [
    "BOBA",
    "BUBBLE TEA",
    "BUBBLE T",
    "MILK TEA",
    "MILKTEA",
    "PEARL TEA",
    "TAPIOCA",
    "KUNG FU TEA",
    "GONG CHA",
    "CHATIME",
    "SHARETEA",
    "SHARE TEA",
    "TIGER SUGAR",
    "HAPPY LEMON",
    "YI FANG",
    "XING FU TANG",
    "MACHI MACHI",
    "THE ALLEY",
    "TEN REN",
    "VIVI",
    "MEET FRESH",
    "COCO FRESH",
    "QUICKLY",
    "COMEBUY",
    "TASTEA",
    "OMOMO",
    "BOBA GUYS",
    "TP TEA",
    "CHA ",
    "MOGE TEE",
    "POSSMEI",
    "WANPO",
    "TRUEDAN",
]
CUISINES = [
    "Coffee/Tea",
    "Juice, Smoothies, Fruit Salads",
    "Bottled beverages, including water, sodas, juices, etc.",
]

where = " OR ".join([f"upper(dba) like '%{t}%'" for t in LIKE_TERMS])
where += " OR cuisine_description in(" + ",".join(f"'{c}'" for c in CUISINES) + ")"


def fetch_all(where_clause: str, page: int = 50000) -> pd.DataFrame:
    frames, offset = [], 0
    while True:
        params = {"$where": where_clause, "$limit": page, "$offset": offset, "$order": ":id"}
        r = requests.get(DOHMH_SOCRATA_BASE, params=params, timeout=120)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        frames.append(pd.DataFrame(batch))
        offset += page
        print(f"  fetched {offset:>7,} rows...")
        if len(batch) < page:
            break
        time.sleep(0.3)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


if CACHE.exists():
    raw = pd.read_parquet(CACHE)
    print(f"loaded cache: {len(raw):,} rows")
else:
    raw = fetch_all(where)
    raw.to_parquet(CACHE)
    print(f"fetched + cached: {len(raw):,} rows")

# %%
print("columns:", list(raw.columns))
print("\ndtypes:\n", raw.dtypes)
raw.head(3)

# %% [markdown]
# ## Type the columns

# %%
df = raw.copy()
for col in ["inspection_date", "record_date", "grade_date"]:
    if col in df:
        df[col] = pd.to_datetime(df[col], errors="coerce")
for col in ["score", "latitude", "longitude"]:
    if col in df:
        df[col] = pd.to_numeric(df[col], errors="coerce")

# 1900-01-01 == "never inspected" sentinel
null_date = pd.Timestamp(DOHMH_NULL_DATE)
df.loc[df["inspection_date"] == null_date, "inspection_date"] = pd.NaT

print("distinct CAMIS in wide net:", df["camis"].nunique())
print("cuisine_description of the wide net:")
print(df.drop_duplicates("camis")["cuisine_description"].value_counts().head(20))

# %% [markdown]
# ## Q2 — tighten to a confident boba set with the real regex

# %%
df["dba"] = df["dba"].fillna("").str.strip()
df["is_boba_name"] = df["dba"].str.contains(BOBA_NAME_PATTERN, regex=True)

boba = df[df["is_boba_name"]].copy()
print("rows:", len(boba), " | distinct CAMIS:", boba["camis"].nunique())
print("\ntop boba dba names by CAMIS count:")
print(boba.drop_duplicates("camis")["dba"].str.upper().value_counts().head(40))

# %%
# Eyeball: are these really boba shops? Any obvious false positives to exclude?
sample = boba.drop_duplicates("camis")[
    ["camis", "dba", "cuisine_description", "boro", "building", "street", "zipcode"]
]
sample.sample(min(30, len(sample)), random_state=0)

# %% [markdown]
# ## Q3 — does `action` contain closure signals?

# %%
print("all action values in the boba set:")
print(boba["action"].value_counts(dropna=False))

closed = boba[boba["action"].str.contains("Closed", case=False, na=False)]
reopened = boba[boba["action"].str.contains("re-opened", case=False, na=False)]
print("\nrows with a 'Closed' action:", len(closed), "| distinct CAMIS:", closed["camis"].nunique())
print(
    "rows with a 're-opened' action:",
    len(reopened),
    "| distinct CAMIS:",
    reopened["camis"].nunique(),
)
closed[["camis", "dba", "inspection_date", "action"]].sort_values("inspection_date").head(20)

# %% [markdown]
# ## Q4 — HOW FAR BACK do dates go? Can we capture 2020 openings?
#
# This is the assumption most likely to break the whole "since 2020" idea.

# %%
print("inspection_date range:", boba["inspection_date"].min(), "→", boba["inspection_date"].max())
print("record_date range:    ", boba["record_date"].min(), "→", boba["record_date"].max())

per_camis = boba.groupby("camis").agg(
    dba=("dba", "first"),
    boro=("boro", "first"),
    first_insp=("inspection_date", "min"),
    last_insp=("inspection_date", "max"),
    n_insp=("inspection_date", "count"),
    ever_closed=("action", lambda s: s.str.contains("Closed", case=False, na=False).any()),
    lat=("latitude", "first"),
    lon=("longitude", "first"),
)
per_camis["first_insp_year"] = per_camis["first_insp"].dt.year

print("\ndistinct boba CAMIS:", len(per_camis))
print("\nfirst-inspection year distribution (proxy for 'opened'):")
print(per_camis["first_insp_year"].value_counts().sort_index())

# %%
print(
    "boba CAMIS whose FIRST inspection is 2020 or later:",
    (per_camis["first_insp_year"] >= 2020).sum(),
)
print("boba CAMIS ever flagged Closed:", per_camis["ever_closed"].sum())

CUTOFF = pd.Timestamp.today() - pd.Timedelta(days=550)
silent = per_camis[(per_camis["last_insp"] < CUTOFF) & (~per_camis["ever_closed"])]
print(f"boba CAMIS with no inspection since {CUTOFF.date()} (silent → maybe closed):", len(silent))

# %% [markdown]
# ## Q5 — lat/long coverage for spatial matching

# %%
geo_ok = per_camis[["lat", "lon"]].notna().all(axis=1).mean()
print(f"CAMIS with usable lat/lon: {geo_ok:.1%}")
print("lat range:", per_camis["lat"].min(), per_camis["lon"].max())

# %% [markdown]
# ## Verdict
#
# Fill in after running:
#
# - confident boba CAMIS: ___
# - earliest inspection_date seen: ___  (if it's ~2022+, the 3-year window is real
#   and pre-2022 openings/closings are NOT visible here — need another source)
# - CAMIS with 'Closed' action: ___
# - first-inspection-year 2020/2021/2022 counts: ___
# - geocode coverage: ___
