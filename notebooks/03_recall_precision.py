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
# # 03 · Recall & precision of the boba set
#
# There's no ground-truth list, so this notebook estimates how good the set is by
# **sampling and hand-labelling**. Each section: generate a CSV, you fill the
# blank column, re-run the recompute cell.
#
# Discovery is Yelp's `bubbletea` category first, then Overture's `bubble_tea`
# tag, then a name regex on Overture / DOHMH. **Section 0** shows how much each
# source contributes; sections 1-3 probe recall and precision of the tail.
#
# The provisional numbers below use a rough auto-label so there's something to
# look at immediately -- **replace them by editing `data/*.csv` and re-running.**

# %%
from __future__ import annotations

import re

import pandas as pd
from sqlalchemy import text

from boba.config import BOBA_NAME_PATTERN, data_dir
from boba.db import engine

DATA = data_dir()
pd.set_option("display.max_colwidth", 60)
pd.set_option("display.width", 160)

_STRICT = re.compile(
    r"(?i)\b(boba|bubble\s*tea|milk\s*tea|tapioca|gong\s*cha|kung\s*fu\s*tea|"
    r"chatime|sharetea|tiger\s*sugar|vivi|coco\s*fresh|xing\s*fu\s*tang|"
    r"machi\s*machi|yi\s*fang|moge\s*tee|meet\s*fresh|happy\s*lemon)\b"
)
_NOT_FOOD = re.compile(
    r"(?i)\b(burger|poke|pizza|grill|deli|pharmacy|laundr|bagel|taco|sushi|"
    r"ramen|donut|bakery|hot\s*pot|bbq|noodle|dumpling|kava)\b"
)
_NAME_RE = re.compile(BOBA_NAME_PATTERN)

SAMPLE_N = 60
RANDOM_STATE = 7

# %% [markdown]
# ## 0. Source overlap — who finds what
#
# Each `boba_shops` row carries `identified_by` (how it entered the set) and which
# source ids it resolved to. This is the live "recall" picture now that Yelp is
# primary: how many shops would we lose without each source.

# %%
overlap = pd.read_sql(
    text(
        """
        select identified_by,
               count(*)                                              as shops,
               count(*) filter (where yelp_id  is not null)          as has_yelp,
               count(*) filter (where overture_id is not null)       as has_overture,
               count(*) filter (where camis    is not null)          as has_dohmh,
               count(*) filter (where first_seen_date is not null)   as has_date
        from boba_shops
        group by identified_by
        order by shops desc
        """
    ),
    engine,
)
print(overlap.to_string(index=False))
print(f"\ntotal shops: {overlap['shops'].sum()}")
print(
    "Yelp-only (no Overture, no CAMIS):",
    pd.read_sql(
        text(
            "select count(*) from boba_shops "
            "where yelp_id is not null and overture_id is null and camis is null"
        ),
        engine,
    ).iat[0, 0],
)

# %% [markdown]
# ## 1. Recall — do we catch the boba shops in DOHMH's Coffee/Tea universe?
#
# DOHMH `cuisine_description = 'Coffee/Tea'` is the bounded universe a boba shop
# would sit in. Sample it, label which are really boba, and see what fraction our
# pipeline (name regex OR a spatial match) captured.

# %%
coffee_tea = pd.read_sql(
    text(
        """
        select distinct e.camis, e.dba, e.boro, e.building, e.street, e.zipcode,
               e.boba_name_match,
               exists (select 1 from place_matches m where m.camis = e.camis) as matched
        from dohmh_establishments e
        where e.cuisine_description = 'Coffee/Tea'
        """
    ),
    engine,
)
coffee_tea["our_hit"] = coffee_tea["boba_name_match"] | coffee_tea["matched"]
print(f"Coffee/Tea establishments loaded: {len(coffee_tea)}")
print(f"  our pipeline flags {int(coffee_tea['our_hit'].sum())} as boba")

sample_path = DATA / "recall_sample.csv"
if sample_path.exists():
    sample = pd.read_csv(sample_path, dtype={"camis": str})
    print(f"loaded existing {sample_path.name} ({sample['true_label'].notna().sum()} labelled)")
else:
    sample = coffee_tea.sample(min(SAMPLE_N, len(coffee_tea)), random_state=RANDOM_STATE).copy()

    # provisional auto-label: strict keyword => yes, obvious non-food word => no, else blank
    def _auto(dba: str) -> str:
        if _STRICT.search(dba or ""):
            return "y"
        if _NOT_FOOD.search(dba or ""):
            return "n"
        return ""

    sample["true_label"] = sample["dba"].map(_auto)
    sample = sample[["camis", "dba", "boro", "street", "our_hit", "true_label"]]
    sample.to_csv(sample_path, index=False)
    print(f"wrote {sample_path} -- fill the `true_label` column (y/n) and re-run")

sample.head(20)

# %%
labelled = sample[sample["true_label"].isin(["y", "n"])].copy()
truth = labelled["true_label"].eq("y")
hit = labelled["our_hit"].astype(bool)

tp = int((truth & hit).sum())
fn = int((truth & ~hit).sum())
fp = int((~truth & hit).sum())
recall = tp / (tp + fn) if (tp + fn) else float("nan")
precision = tp / (tp + fp) if (tp + fp) else float("nan")


def _wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z**2 / n
    c = p + z**2 / (2 * n)
    hw = z * ((p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5)
    return ((c - hw) / d, (c + hw) / d)


lo, hi = _wilson(tp, tp + fn)
print(f"labelled: {len(labelled)} / {len(sample)}   (unlabelled rows are skipped)")
print(f"true boba in sample: {int(truth.sum())}")
print(f"recall    = {recall:.0%}   (95% CI {lo:.0%}-{hi:.0%})   [missed {fn}]")
print(f"precision = {precision:.0%}   [false positives {fp}]")
if fn:
    print("\nMISSED (true boba, our pipeline didn't flag):")
    print(labelled.loc[truth & ~hit, ["camis", "dba", "street"]].to_string(index=False))

# %% [markdown]
# ## 2. Precision of `place_matches` — the uncertain tail
#
# Matches with a weak name score or a distance-only method. Fill `ok` (y/n).

# %%
tail = pd.read_sql(
    text(
        """
        select m.overture_id, m.camis, o.name as overture_name, e.dba,
               round(m.score::numeric, 0) as score,
               round(m.name_similarity::numeric, 0) as name_sim,
               round(m.distance_m::numeric, 0) as dist_m, m.method
        from place_matches m
        join overture_places o on o.id = m.overture_id
        join dohmh_establishments e on e.camis = m.camis
        where m.name_similarity < 82 or m.method = 'name_dist'
        order by m.name_similarity
        """
    ),
    engine,
)
review_path = DATA / "match_review.csv"
if review_path.exists():
    review = pd.read_csv(review_path, dtype={"camis": str})
    print(f"loaded {review_path.name} ({review['ok'].notna().sum()} labelled)")
else:
    review = tail.copy()
    review["ok"] = (review["name_sim"] >= 72) | (review["dist_m"] <= 20)  # provisional
    review["ok"] = review["ok"].map({True: "y", False: "n"})
    review.to_csv(review_path, index=False)
    print(f"wrote {review_path} -- verify the `ok` column")
review

# %%
r = review[review["ok"].isin(["y", "n"])]
good = int(r["ok"].eq("y").sum())
print(f"match tail: {len(review)} rows, {len(r)} labelled")
print(f"tail precision = {good / len(r):.0%}" if len(r) else "nothing labelled")
print(f"total matches: kept {len(review)} of the tail; rejects would be {len(r) - good}")
if (r["ok"] == "n").any():
    print("\nREJECTS (feed back as overrides later):")
    print(r.loc[r["ok"] == "n", ["overture_name", "dba", "score", "dist_m"]].to_string(index=False))

# %% [markdown]
# ## 3. Precision of the boba set itself
#
# The non-Yelp tail is the risky part: Overture-category shops can be mis-tagged
# (a burger place tagged `bubble_tea`); name-pattern shops can be regex false
# positives. `yelp_category` is excluded here -- Yelp's curation is the baseline
# we're comparing against. Fill `is_boba` (y/n).

# %%
shops = pd.read_sql(
    text(
        """
        select id, name, borough, identified_by, status
        from boba_shops
        where identified_by in ('overture_category', 'both', 'name_pattern', 'propagated')
        """
    ),
    engine,
)
set_path = DATA / "boba_set_review.csv"
if set_path.exists():
    review3 = pd.read_csv(set_path)
    print(f"loaded {set_path.name} ({review3['is_boba'].notna().sum()} labelled)")
else:
    review3 = pd.concat(
        [
            g.sample(min(30, len(g)), random_state=RANDOM_STATE)
            for _, g in shops.groupby("identified_by")
        ],
        ignore_index=True,
    )
    review3["is_boba"] = review3["name"].map(
        lambda n: "n" if _NOT_FOOD.search(n or "") else ("y" if _NAME_RE.search(n or "") else "")
    )
    review3.to_csv(set_path, index=False)
    print(f"wrote {set_path} -- fill `is_boba` (y/n)")
review3

# %%
r3 = review3[review3["is_boba"].isin(["y", "n"])]
print("precision by identified_by (labelled rows only):")
for k, g in r3.groupby("identified_by"):
    print(f"  {k:18s} {g['is_boba'].eq('y').mean():.0%}   (n={len(g)})")
if (r3["is_boba"] == "n").any():
    print("\nNOT boba:")
    print(r3.loc[r3["is_boba"] == "n", ["name", "identified_by"]].to_string(index=False))

# %% [markdown]
# ## Findings
#
# Paste the confirmed numbers into `docs/methodology.md`:
#
# - source overlap: Yelp-only ___, Overture-only ___, DOHMH-only ___
# - recall (Coffee/Tea universe): ___%  (CI ___)
# - identification precision: overture_category ___%, name_pattern ___%
# - match-tail precision: ___%
# - concrete rejects to override: ___
