# Data sources — Yelp discovers, DOHMH dates

## The question

"Which NYC boba shops opened or closed, and roughly when?"

Answering it needs: **a boba label**, **a location**, **a rough timeline**, and
**a current-status check**. Yelp covers the first, second and fourth; DOHMH is
the only timeline.

## What each source has

| Need | Yelp Fusion (`bubbletea`) | NYC DOHMH inspections |
|---|---|---|
| Is it a boba shop? | ✅ **hand-curated `bubbletea` category** — the primary discovery signal | ⚠️ no such cuisine; name-guess on `dba` |
| Location | ✅ point + address | ⚠️ lat/long (~99% filled) |
| **Opening / closing date** | ❌ none | ⚠️ **no such field** — first *inspection* ≈ "operating by" (permit lag); an explicit "Closed by DOHMH" action is dated, but plain silence is not a closure |
| Currently open? | ✅ `is_closed` — current, purpose-built, free with discovery | ⚠️ trailing (inspected within ~18 mo) |
| History before mid-2023 | ⚠️ keeps closed listings, no dates | ✅ inspections to ~2016 (~2022 for boba names) |

**Yelp** is the shop list. `discover()` searches the `bubbletea` category over an
adaptive NYC grid (subdividing any tile that hits Yelp's 240-result ceiling),
keeps a business only if `bubbletea` is its *primary* category — dropping ~260
restaurants that merely serve boba — and gets `is_closed` for free. ~446 NYC
businesses. The raw sweep is cached to `data/yelp_raw_last.json` (the free tier is
~500 calls/day; a later rate-limited run rebuilds from the cache).

**DOHMH** is the only timeline. First inspection lags the true opening by the
permit gap; an explicit "Establishment Closed by DOHMH" action is a dated event,
but plain inspection silence is *not* treated as a closure. The name regex on
`dba` also finds a tail of boba-named permits Yelp doesn't list — mostly shops
that closed before Yelp would help.

## Why we still link

Yelp gives "this is a boba shop, here, open/closed". Linking attaches DOHMH's
"first inspected 2023‑04, last 2025‑01" for the *same physical shop* —
`ingest/yelp.py::link` scores each Yelp business against DOHMH points within
160 m on name + distance. ~185 of ~446 link to a CAMIS. See
[methodology.md](methodology.md).

## How `boba_shops` is seeded (~450 total, after dedup)

`analyze.py` walks two populations in order; each only *adds* rows:

| Seed | Count | What it means |
|---|---|---|
| **Yelp business** | ~446 | the primary list. ~185 link to a CAMIS for a first-seen date; the rest are Yelp-only (too new / not name-matched in DOHMH) |
| **boba-name CAMIS** | ~50 → adds the ones not already seeded via Yelp | a DOHMH `dba` that matched `BOBA_NAME_PATTERN` but has no Yelp business — usually a shop that **closed before Yelp would help** |

Borough is a point-in-polygon against NYC's official boundaries; shops outside the
five boroughs (Yelp's radius search pulls in Nassau / Westchester / NJ) are
dropped, ~100. Duplicate rows for one shop (same normalised name within 60 m)
collapse, keeping the richest.

## Coverage: 2022–2026

DOHMH's boba‑name inspection records start 2022‑01‑25; Yelp has no dates at all.
So the timeline covers **2022 → 2026** and doesn't reach earlier.
