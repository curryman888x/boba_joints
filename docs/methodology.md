# Methodology — how shops are identified and dated

## 1. Identifying boba shops

There is **no ground-truth list**, so recall is a lower bound. Two mechanisms:

**Overture** (`boba/filters.py::overture_is_boba`)
- primary: `categories.primary` or `alternate` == `bubble_tea` — Overture's own
  tag, not a regex. ~435 of ~445.
- fallback: category in a broader set (`cafe`, `tea_room`, `dessert_shop`, …)
  **and** the name matches `BOBA_NAME_PATTERN`. ~10 more.

**DOHMH** (`boba/filters.py::dohmh_is_boba`)
- **100% name regex** on `dba` (`BOBA_NAME_PATTERN` — chains + keyword fragments).
  DOHMH has no bubble-tea cuisine, so there is no category path. ~195 CAMIS.
- The pattern lists newer chains explicitly (HeyTea, Molly Tea, Auntea Jenny,
  Chun Yang, Sunright, Chagee, …) because their names carry no generic keyword.

**Label propagation** (`boba/match.py`)
- Any DOHMH establishment that matches an Overture `bubble_tea` place (below)
  counts as boba even if its `dba` never hit the regex. Recovers ~60
  establishments the regex alone misses.

### Provenance — `boba_shops.identified_by`

Every shop carries how it was identified, so counts can be reported by
confidence:

| value | meaning | count | precision-backed by |
|---|---|---|---|
| `both` | Overture `bubble_tea` **and** a name match | ~217 | strongest |
| `overture_category` | Overture `bubble_tea` tag, no independent name hit | ~197 | Overture's curated taxonomy |
| `name_pattern` | name regex only (Overture fallback name, or DOHMH `dba`) | ~108 | needs vetting |
| `propagated` | no category or name hit anywhere — pure spatial inference | ~1 | weakest |

So ~414 of 523 rest on Overture's tag; ~108 on the regex alone.

### Known gaps
- Independents with no keyword in the name ("Sunright Tea Studio", "O-CHA").
- Overture places filed under `restaurant` / `chinese_restaurant` (not in the
  fallback set).
- Regex false positives (e.g. `the alley` once matched "THE ALLEY PIZZA LOUNGE";
  the distance gate in matching removes most).
- Overture category false positives (e.g. "Poke Cafe" tagged `bubble_tea`).

### `notebooks/03_recall_precision.py`

A hand-labelling workbench: generates `data/recall_sample.csv`,
`data/match_review.csv`, `data/boba_set_review.csv`; you fill the blank column;
recompute cells produce recall (Wilson CI), match-tail precision, and
per-`identified_by` precision, and list concrete rejects.

Report counts as **"≥ N (name/category identification)"**, never a hard "N".
The recall universe is DOHMH `cuisine_description = 'Coffee/Tea'` (~2,220
establishments; the pipeline flags ~195).

## 2. Matching Overture ↔ DOHMH (`boba/match.py`)

1. **Candidates**: for each Overture boba place, every DOHMH establishment within
   `RADIUS_M = 120 m` (`ST_DWithin` on the `geography` cast → real metres).
2. **Name key**: lowercase, punctuation → space, then drop generic tokens
   (`bubble`, `tea`, `boba`, `cafe`, `the`, `llc`, …) so two unrelated shops
   don't score high just for both containing "bubble tea".
3. **Similarity**: `rapidfuzz.fuzz.token_set_ratio` on the two keys (0–100).
4. **Score**: `name_sim − min(dist/12, 25) + (8 if the DOHMH street's first token
   is in the Overture address)`.
5. **Keep** if `name_sim ≥ 72 and dist ≤ 60 m`, or `name_sim ≥ 55 and dist ≤ 35 m`.
6. **Up to 5 matches per place** — a shop that closed and re-permitted has a new
   CAMIS, and both matter for the timeline.

Idempotent (`truncate place_matches` + rebuild). `score` / `name_similarity` /
`method` are kept so a review pass can accept/reject the tail.

## 3. Borough + NYC filter (`boba/analyze.py`)

Overture `locality` is neighbourhood-level ("Woodside") and disagrees with DOHMH
`boro` on names. `boba/seed.py` loads NYC's official borough polygons into
`boroughs`; `analyze._assign_boroughs` does `ST_Contains` on each shop's point:

- in a borough → that borough's name (one of exactly five)
- outside all five → **dropped** (~14; the NYC bbox rectangle clips Nassau and
  Westchester — Great Neck, Manhasset, New Rochelle, …)
- no geometry but a CAMIS → DOHMH `boro` fallback (DOHMH is NYC-only)

## 4. Dates (`boba/analyze.py`) — evidence bounds, not lifecycle events

**DOHMH has no opening or closing field.** It records dated *health inspections*.
So the `boba_shops` date columns are named for what they actually are:

### `first_seen_date` / `first_seen_source`
| source | what it is | bias |
|---|---|---|
| `dohmh_first_inspection` | earliest DOHMH inspection — preferring the **"Pre-permit / Initial"** inspection (done as a new food business is licensed to open); 105 of 195 boba CAMIS have one | **late** — the inspection is on/after opening, typically weeks to a quarter |
| `overture_release` | earliest Overture release the place appears in — **only** once it has persisted across ≥ 2 of our ingests | quarter |

Read `first_seen_date` as **"operating by at least this date, ±1 quarter"** — not
"opened on". Overture `source_update_time` is deliberately unused (it clusters in
the current year). ~245 of 512 shops get one; the rest have no DOHMH match.

The year summary is **"first seen per year"**, a proxy for openings. The 2023
jump (15 → 92) is real; the split between adjacent years has ±1 quarter of
per-shop noise.

### `last_seen_date`
Latest evidence the shop existed — `max(DOHMH last inspection, Overture release)`.

### `status` / `status_basis`
`status` ∈ `open` / `closed` / `unknown`. **`open` requires a positive signal**;
with none, status is `unknown` (~63) — a stale record can't be told from a new
shop. `status_basis` records which signal, strongest first:

| basis | meaning | trust |
|---|---|---|
| `dohmh_closed_by_dohmh` | DOHMH "Establishment Closed" action, not reopened (health closure, ~4) | high |
| `yelp_closed` | Yelp `is_closed` — current; beats a stale inspection | high |
| `overture_permanently_closed` | Overture snapshot | low–med |
| `dohmh_inactive` | no inspection in > `INACTIVE_DAYS` (550) and not force-closed | low |
| `dohmh_active` | inspected within ~18 months | high (open) |
| `yelp_open` | Yelp says open | high (open) |
| `overture_open` | **only** Overture's `operating_status` — it lags real closures (~194) | **low** |
| `none` | no signal → `unknown` | — |

`first_seen > closed` contradictions drop the weaker `first_seen`.

### Output
`data/boba_status.csv` and the year summary. (No `status_events` table — it just
split these columns into rows.)
