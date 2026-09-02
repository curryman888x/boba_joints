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
# Discovery is Yelp's `bubbletea` category first, then a name regex on the DOHMH
# `dba` for shops Yelp doesn't list. **Section 0** shows how much each source
# contributes; sections 1-2 probe recall and precision.
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
# source ids it resolved to. This is the live "recall" picture: how many shops
# would we lose without each source.

# %%
overlap = pd.read_sql(
    text(
        """
        select identified_by,
               count(*)                                             as shops,
               count(*) filter (where yelp_id is not null)          as has_yelp,
               count(*) filter (where camis   is not null)          as has_dohmh,
               count(*) filter (where first_seen_date is not null)  as has_date
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
    "Yelp-only (no DOHMH match):",
    pd.read_sql(
        text("select count(*) from boba_shops where yelp_id is not null and camis is null"),
        engine,
    ).iat[0, 0],
)
print(
    "DOHMH-only (not in Yelp):",
    pd.read_sql(
        text("select count(*) from boba_shops where camis is not null and yelp_id is null"),
        engine,
    ).iat[0, 0],
)

# %% [markdown]
# ## 1. Recall — do we catch the boba shops in DOHMH's Coffee/Tea universe?
#
# DOHMH `cuisine_description = 'Coffee/Tea'` is the bounded universe a boba shop
# would sit in. Sample it, label which are really boba, and see what fraction our
# pipeline (name regex OR linked to a Yelp `bubbletea` business) captured.

# %%
coffee_tea = pd.read_sql(
    text(
        """
        select distinct e.camis, e.dba, e.boro, e.building, e.street, e.zipcode,
               e.boba_name_match,
               exists (select 1 from yelp_matches m where m.camis = e.camis) as yelp_linked
        from dohmh_establishments e
        where e.cuisine_description = 'Coffee/Tea'
        """
    ),
    engine,
)
coffee_tea["our_hit"] = coffee_tea["boba_name_match"] | coffee_tea["yelp_linked"]
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
# ## 2. Precision of the `name_pattern` tail
#
# `yelp_category` shops rest on Yelp's hand curation (treated as the baseline).
# The risky rows are `name_pattern` — a boba-named DOHMH permit with no Yelp
# listing; the regex can misfire ("THE ALLEY PIZZA LOUNGE"). Fill `is_boba` (y/n).

# %%
tail = pd.read_sql(
    text(
        """
        select id, name, borough, status, status_basis
        from boba_shops
        where identified_by = 'name_pattern'
        """
    ),
    engine,
)
set_path = DATA / "name_pattern_review.csv"
if set_path.exists():
    review = pd.read_csv(set_path)
    print(f"loaded {set_path.name} ({review['is_boba'].notna().sum()} labelled)")
else:
    review = tail.sample(min(40, len(tail)), random_state=RANDOM_STATE).copy()
    review["is_boba"] = review["name"].map(
        lambda n: "n" if _NOT_FOOD.search(n or "") else ("y" if _NAME_RE.search(n or "") else "")
    )
    review.to_csv(set_path, index=False)
    print(f"wrote {set_path} -- fill `is_boba` (y/n)")
review

# %%
r = review[review["is_boba"].isin(["y", "n"])]
if len(r):
    print(f"name_pattern tail: {len(review)} rows, {len(r)} labelled")
    print(f"precision = {r['is_boba'].eq('y').mean():.0%}   (n={len(r)})")
    if (r["is_boba"] == "n").any():
        print("\nNOT boba:")
        print(r.loc[r["is_boba"] == "n", ["name", "status"]].to_string(index=False))
else:
    print("nothing labelled yet")

# %% [markdown]
# ## Findings
#
# Paste the confirmed numbers into `docs/methodology.md`:
#
# - source overlap: Yelp-only ___, DOHMH-only ___
# - recall (Coffee/Tea universe): ___%  (CI ___)
# - name_pattern precision: ___%
# - concrete rejects to drop: ___
