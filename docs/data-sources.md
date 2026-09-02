# Data sources — why three, and why matching

## The question

"Which NYC boba shops opened or closed, and roughly when?"

Answering it needs: **a boba label**, **a location**, **a rough timeline**, and
**a current-status check**. No single free source has all of it.

## What each source has

| Need | Overture Maps Places | NYC DOHMH inspections | Yelp Fusion |
|---|---|---|---|
| Is it a boba shop? | ✅ `bubble_tea` category — a real, curated tag | ⚠️ no such cuisine; name-guess `dba` | ✅ `bubbletea` category (used only to confirm a match) |
| Location / brand | ✅ precise point, address, brand + Wikidata | ⚠️ lat/long (≈99% filled), no brand | ✅ address, coords |
| **Opening / closing date** | ❌ none | ⚠️ **no such field** — first *inspection* ≈ "operating by" (permit lag), silence ≈ closed | ❌ none |
| Currently open? | ⚠️ `operating_status` — lags real closures | ⚠️ trailing (inspected within ~18 mo) | ✅ `is_closed` — current, purpose-built |
| History before mid-2023 | ❌ first release July 2023 | ✅ inspections to ~2016 (≈2022 for boba names) | ✅ keeps closed listings |

**Overture** is a snapshot: it labels and locates shops but has no time axis, and
its `operating_status` is unreliable (it said "open" for Come Buy, which is
closed).

**DOHMH** is the closest thing to a timeline — but it's *inspection* data. First
inspection lags the true opening by the permit gap; closure is inferred from
18 months of silence. Directionally right, ±1 quarter. It's also the only source
that sees shops which closed before Overture existed.

**Yelp** answers "is it open *now*" for the ~194 shops resting only on Overture's
word — matched by name + address (`/businesses/matches`).

## Why we match

Matching glues Overture's "this is a boba shop, here, this brand" onto DOHMH's
"first inspected 2023‑04, last 2025‑01" and Yelp's "still open", for the *same
physical shop*. See [methodology.md](methodology.md).

## The three populations of `boba_shops`

| Population | Count | What it means |
|---|---|---|
| **merged** | ~169 | in both sources — best case: clean label + a first-seen date |
| **Overture-only** | ~266 | Overture has it, no DOHMH match — too new to have been inspected, name too different, or outside our DOHMH net. Known to exist; no first-seen date. |
| **DOHMH-only** | ~77 | a boba CAMIS with no Overture point — often a shop that **closed before Overture existed**. Why DOHMH is load-bearing, not a mere date lookup. |

(~512 total, after dedup. Borough is a point-in-polygon against NYC's official
boundaries; shops outside the five boroughs — the bbox rectangle clips Nassau /
Westchester — are dropped, ~14. Duplicate Overture GERS ids for one shop
collapse if same-name within 60 m, ~12.)

## The hard constraint: 2022–2026

Neither source reaches 2020–2021. Overture starts July 2023; DOHMH's
boba‑name records start 2022‑01‑25. The deliverable is scoped to **2022 → 2026**;
earlier openings/closings are an acknowledged gap (would need a paid source like
SafeGraph/Advan, or a curated historical list).
