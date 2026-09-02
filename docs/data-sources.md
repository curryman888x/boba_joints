# Data sources — why two, and why matching

## The question

"Which NYC boba shops opened or closed, and when?"

Answering it needs three things: **a boba label**, **a location**, and **a timeline**.
No single free source has all three.

## What each source has

| Need | Overture Maps Places | NYC DOHMH inspections |
|---|---|---|
| Is it a boba shop? | ✅ `bubble_tea` category — a real, curated tag | ⚠️ no such cuisine; only name-guessing `dba` |
| Location / brand | ✅ precise point, address, brand + Wikidata | ⚠️ lat/long (≈99% filled), no brand |
| Open/closed **right now** | ✅ `operating_status` (`open` / `permanently_closed`), US-populated since the June 2026 release | ❌ no field |
| **When it opened** | ❌ nothing | ✅ first inspection date ≈ opening (a new permit gets inspected within weeks) |
| **When it closed** | ❌ nothing (only current status) | ⚠️ `Establishment Closed by DOHMH` action (health only, ~4 shops), or the permit goes silent |
| History before mid-2023 | ❌ Overture's first release was July 2023 | ✅ inspections back to ~2016 (≈2022 for the boba-name subset) |

**Overture is a snapshot with no time axis.** It can say "here are 443 boba
shops that exist today, 11 marked permanently closed" — but not *when* any opened,
and a shop that existed in 2022 and closed in 2023 simply isn't in it.

**DOHMH is the time axis.** Every NYC food business needs a dated, public health
permit (a CAMIS). First inspection ≈ opening; the permit going quiet ≈ closing.

## Why we match

Matching glues Overture's clean "this is a boba shop, here, this brand" onto
DOHMH's "first seen 2023‑04, last seen 2025‑01" for the *same physical shop*.
See [methodology.md](methodology.md) for the matching logic.

## The three populations of `boba_shops`

| Population | Count | What it means |
|---|---|---|
| **merged** | ~171 | in both sources — best case: clean label + real timeline |
| **Overture-only** | ~267 | Overture has it, no DOHMH match — usually too new to have been inspected, name too different to match, or outside our DOHMH net. Known to exist; opening date unknown. |
| **DOHMH-only** | ~85 | a boba CAMIS with no Overture point — often a shop that **closed before Overture existed**. This is the churn the project is about, and the reason DOHMH is load‑bearing rather than a mere date lookup. |

(~523 total. Borough comes from a point-in-polygon against NYC's official
boundaries; shops outside the five boroughs — the bbox rectangle clips Nassau /
Westchester — are dropped, ~14.)

## The hard constraint: 2022–2026

Neither source reaches 2020–2021. Overture starts July 2023; DOHMH's
boba‑name records start 2022‑01‑25. The deliverable is scoped to **2022 → 2026**;
earlier openings/closings are an acknowledged gap (would need a paid source like
SafeGraph/Advan, or a curated historical list).
