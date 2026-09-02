# Methodology — how shops are identified and dated

## 1. Identifying boba shops

There is **no ground-truth list**, so recall is a lower bound. Three layers, each
only *adding* shops:

**Yelp** (`boba/ingest/yelp.py::discover`) — the primary source
- Yelp's `bubbletea` category is **hand-curated**. `discover()` enumerates it
  over an adaptive NYC grid and keeps a business only if `bubbletea` is its
  *primary* category (or the name matches `BOBA_NAME_PATTERN`) — dropping ~260
  restaurants that merely list boba 3rd. ~446 businesses.

**Overture** (`boba/filters.py::overture_is_boba`)
- primary: `categories.primary` or `alternate` == `bubble_tea` — Overture's own
  tag, not a regex. ~428 of ~449.
- fallback: category in a broader set (`cafe`, `tea_room`, `desserts`, …) **and**
  the name matches `BOBA_NAME_PATTERN`. ~20 more.

**DOHMH** (`boba/filters.py::dohmh_is_boba`)
- **100% name regex** on `dba` (`BOBA_NAME_PATTERN` — chains + keyword fragments).
  DOHMH has no bubble-tea cuisine, so there is no category path. ~195 CAMIS.
- The pattern lists newer chains explicitly (HeyTea, Molly Tea, Auntea Jenny,
  Chun Yang, Sunright, Chagee, …) because their names carry no generic keyword.

**Label propagation** (`boba/match.py`)
- Any DOHMH establishment that matches an Overture `bubble_tea` place counts as
  boba even if its `dba` never hit the regex.

### Provenance — `boba_shops.identified_by`

Every shop carries how it was identified, strongest first:

| value | meaning | count | precision-backed by |
|---|---|---|---|
| `yelp_category` | seeded from Yelp's curated `bubbletea` category | ~350 | Yelp's hand curation |
| `both` | Overture `bubble_tea` **and** a name match (no Yelp) | ~100 | strongest of the non-Yelp tail |
| `overture_category` | Overture `bubble_tea` tag only (no Yelp, no name hit) | ~135 | Overture's curated taxonomy |
| `name_pattern` | name regex only (Overture fallback name, or DOHMH `dba`) | ~65 | needs vetting |
| `propagated` | no category or name hit anywhere — pure spatial inference | ~0 | weakest |

So ~585 of ~655 rest on a curated category (Yelp or Overture); ~65 on the regex
alone.

### Known gaps
- Independents with no keyword in the name that neither Yelp nor Overture tags.
- Overture places filed under `restaurant` / `chinese_restaurant` (not in the
  fallback set).
- Regex false positives (e.g. `the alley` once matched "THE ALLEY PIZZA LOUNGE";
  the distance gate in matching removes most).
- Category false positives (e.g. "Poke Cafe" tagged `bubble_tea`).

### `notebooks/03_recall_precision.py`

A hand-labelling workbench: a source-overlap summary (who finds what), plus
`data/recall_sample.csv`, `data/match_review.csv`, `data/boba_set_review.csv` —
you fill the blank column; recompute cells produce recall (Wilson CI), match-tail
precision, and per-`identified_by` precision, and list concrete rejects.

Report counts as **"≥ N (Yelp/Overture/name identification)"**, never a hard "N".
The DOHMH recall universe is `cuisine_description = 'Coffee/Tea'` (~2,220
establishments; the pipeline flags ~195 of them by name).

## 2. Linking the shop to a brand and a timeline

Discovery gives a shop; linking attaches Overture's brand + geometry and DOHMH's
inspection dates for the *same physical shop*. All name comparisons run on a
**name key**: lowercase, punctuation → space, then drop generic tokens (`bubble`,
`tea`, `boba`, `cafe`, `the`, `llc`, …) so two unrelated shops don't score high
just for both containing "bubble tea".

### Yelp business → Overture id + CAMIS (`boba/ingest/yelp.py::link`)
For each Yelp business, every Overture place and every DOHMH establishment within
`160 m` (`ST_DWithin` on the `geography` cast). Score each side as
`name_sim − min(dist/20, 15)`; keep the best if `name_sim ≥ 60`. One row per Yelp
business in `yelp_matches` (its best Overture id, its best CAMIS). ~240 of ~446
link to at least one side.

### Overture place ↔ DOHMH establishment (`boba/match.py`)
1. **Candidates**: for each Overture boba place, every DOHMH establishment within
   `RADIUS_M = 120 m`.
2. **Score**: `name_sim − min(dist/12, 25) + (8 if the DOHMH street's first token
   is in the Overture address)`.
3. **Keep** if `name_sim ≥ 72 and dist ≤ 60 m`, or `name_sim ≥ 55 and dist ≤ 35 m`.
4. **Up to 5 matches per place** — a shop that closed and re-permitted has a new
   CAMIS, and both matter for the timeline.

Both are idempotent (`truncate` + rebuild). `place_matches` keeps `score` /
`name_similarity` / `method` so a review pass can accept/reject the tail.

## 3. Borough + NYC filter (`boba/analyze.py`)

Overture `locality` is neighbourhood-level ("Woodside") and disagrees with DOHMH
`boro` on names. `boba/seed.py` loads NYC's official borough polygons into
`boroughs`; `analyze._assign_boroughs` does `ST_Contains` on each shop's point:

- in a borough → that borough's name (one of exactly five)
- outside all five → **dropped** (~100; Yelp's lat/lon search and the Overture
  bbox both pull in Nassau, Westchester and NJ — Great Neck, Manhasset, Newark, …)
- no geometry but a CAMIS → DOHMH `boro` fallback (DOHMH is NYC-only)

## 4. Dates (`boba/analyze.py`) — evidence bounds, not lifecycle events

**DOHMH has no opening or closing field.** It records dated *health inspections*.
So the `boba_shops` date columns are named for what they actually are:

### `first_seen_date` / `first_seen_source`
| source | what it is | bias |
|---|---|---|
| `dohmh_first_inspection` | earliest DOHMH inspection — preferring the **"Pre-permit / Initial"** inspection (done as a new food business is licensed to open) | **late** — the inspection is on/after opening, typically weeks to a quarter |
| `overture_release` | earliest Overture release the place appears in — **only** once it has persisted across ≥ 2 of our ingests | quarter |

Read `first_seen_date` as **"operating by at least this date, ±1 quarter"** — not
"opened on". Overture `source_update_time` is deliberately unused (it clusters in
the current year). **Only ~269 of ~655 shops get a date at all** — the rest are
Yelp/Overture entities with no DOHMH match (many too new to have been inspected).

The year summary is **"first seen per year"**, a proxy for openings. The 2023
jump (15 → 99) is real; the split between adjacent years has ±1 quarter of
per-shop noise; recent years are undercounted (permit gap + no DOHMH match yet).

### `last_seen_date`
Latest evidence the shop existed — `max(DOHMH last inspection, Overture release)`.

### `status` / `status_basis`
`status` ∈ `open` / `closed` / `unknown`. **`open` requires a positive signal**;
with none, status is `unknown` (~53) — a stale record can't be told from a new
shop. `status_basis` records which signal, strongest first (the order in
`analyze._closed`):

| basis | meaning | trust |
|---|---|---|
| `dohmh_closed_by_dohmh` | `status=closed` — DOHMH "Establishment Closed" action, not reopened (~3) | high |
| `yelp_closed` | `status=closed` — Yelp `is_closed`; beats a stale inspection | high |
| `overture_permanently_closed` | `status=closed` — Overture `operating_status` snapshot (~10) | low–med |
| `dohmh_active` | `status=open` — inspected within ~18 months (~255) | high |
| `yelp_open` | `status=open` — Yelp says open, no DOHMH inspection to corroborate (~179) | high |
| `overture_open` | `status=open` — **only** Overture's `operating_status`, which lags real closures (~151) | **low** |
| `dohmh_inactive` | `status=unknown` — no inspection in > `INACTIVE_DAYS` (550) and nothing else says open. A stale record, **not** a closure (~4) | — |
| `none` | `status=unknown` — no signal at all (~53) | — |

Order of precedence in `analyze._closed`: force-closed → `yelp_closed` →
`overture_permanently_closed` → recent inspection (`dohmh_active`) → `yelp_open` →
inspection gap (`dohmh_inactive`, now `unknown`) → `overture_open` → `none`. A
recent DOHMH inspection outranks Yelp for the *open* basis (an inspection is a
hard "was operating on date X"); Yelp `is_closed` still outranks a stale
inspection for the *closed* basis. **Inspection silence is never a closure** —
DOHMH is trusted for dates and its own explicit closure actions, nothing more.

`first_seen > closed` contradictions drop the weaker `first_seen`.

### Output
`data/boba_status.csv` and the year summary. (No `status_events` table — it just
split these columns into rows.)
