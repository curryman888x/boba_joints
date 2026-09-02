# Data sources — Yelp discovers, Overture + DOHMH enrich

## The question

"Which NYC boba shops opened or closed, and roughly when?"

Answering it needs: **a boba label**, **a location**, **a rough timeline**, and
**a current-status check**. No single free source has all of it — but Yelp
covers the first, second and fourth well enough to be the spine.

## What each source has

| Need | Yelp Fusion (`bubbletea`) | Overture Maps Places | NYC DOHMH inspections |
|---|---|---|---|
| Is it a boba shop? | ✅ **hand-curated `bubbletea` category** — the primary discovery signal | ✅ `bubble_tea` category — curated, but misses ~200 Yelp has | ⚠️ no such cuisine; name-guess on `dba` |
| Location | ✅ point + address | ✅ precise point, address | ⚠️ lat/long (~99% filled) |
| Brand | ⚠️ name only | ✅ brand + Wikidata id | ❌ none |
| **Opening / closing date** | ❌ none | ❌ none | ⚠️ **no such field** — first *inspection* ≈ "operating by" (permit lag); an explicit "Closed by DOHMH" action is dated, but plain silence is not a closure |
| Currently open? | ✅ `is_closed` — current, purpose-built, free with discovery | ⚠️ `operating_status` — lags real closures | ⚠️ trailing (inspected within ~18 mo) |
| History before mid-2023 | ⚠️ keeps closed listings, no dates | ❌ first release July 2023 | ✅ inspections to ~2016 (~2022 for boba names) |

**Yelp** is the shop list. `discover()` searches the `bubbletea` category over an
adaptive NYC grid (subdividing any tile that hits Yelp's 240-result ceiling),
keeps a business only if `bubbletea` is its *primary* category — dropping ~260
restaurants that merely serve boba — and gets `is_closed` for free. ~446 NYC
businesses.

**Overture** adds brand + a more precise geometry when a Yelp business links to
one, and contributes ~200 shops Yelp doesn't list (its own `bubble_tea` tag, or a
name match). No time axis; `operating_status` is unreliable (it said "open" for
Come Buy, which is closed).

**DOHMH** is the only timeline. First inspection lags the true opening by the
permit gap; closure is inferred from ~18 months of silence. Directionally right,
±1 quarter. It's also the only source that sees shops which closed before Yelp or
Overture would help.

## Why we still match

Yelp gives "this is a boba shop, here, open/closed". Matching glues on Overture's
brand and DOHMH's "first inspected 2023‑04, last 2025‑01" for the *same physical
shop* — `ingest/yelp.py::link` by name + distance, `match.py` for Overture↔DOHMH.
See [methodology.md](methodology.md).

## How `boba_shops` is seeded (~655 total, after dedup)

`analyze.py` walks three populations in order; each only *adds* rows:

| Seed | Count | What it means |
|---|---|---|
| **Yelp business** | ~446 | the primary list. ~240 link to an Overture place and/or a CAMIS; ~206 are Yelp-only (too new / not in the others) |
| **Overture-only** | ~275 → adds the ones not already seeded via Yelp | an Overture `bubble_tea` (or name-match) place with no Yelp business — Yelp's grid missed it, or the name/category disagree |
| **DOHMH-only** | ~50 | a boba-name (or spatially-propagated) CAMIS with no Yelp or Overture entity — often a shop that **closed before the others existed** |

Borough is a point-in-polygon against NYC's official boundaries; shops outside
the five boroughs (the Yelp lat/lon search and the Overture bbox both pull in
Nassau / Westchester / NJ) are dropped, ~100. Duplicate rows for one shop
(same normalised name within 60 m) collapse, keeping the richest.

## Coverage: 2022–2026

DOHMH's boba‑name inspection records start 2022‑01‑25 and Overture's first
release is July 2023; Yelp has no dates at all. So the timeline covers
**2022 → 2026** and doesn't reach earlier.
