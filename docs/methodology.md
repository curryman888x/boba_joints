# Methodology — how shops are identified and dated

## 1. Identifying boba shops

There is **no ground-truth list**, so recall is a lower bound. Two layers, each
only *adding* shops:

**Yelp** (`boba/ingest/yelp.py::discover`) — the primary source
- Yelp's `bubbletea` category is **hand-curated**. `discover()` enumerates it
  over an adaptive NYC grid and keeps a business only if `bubbletea` is its
  *primary* category (or the name matches `BOBA_NAME_PATTERN`) — dropping ~260
  restaurants that merely list boba 3rd. ~446 businesses.

**DOHMH** (`boba/filters.py::dohmh_is_boba`)
- **100% name regex** on `dba` (`BOBA_NAME_PATTERN` — chains + keyword fragments).
  DOHMH has no bubble-tea cuisine, so there is no category path. ~195 CAMIS, of
  which ~50 aren't in Yelp (usually older closures).
- The pattern lists newer chains explicitly (HeyTea, Molly Tea, Auntea Jenny,
  Chun Yang, Sunright, Chagee, …) because their names carry no generic keyword.

### Provenance — `boba_shops.identified_by`

| value | meaning | count | precision-backed by |
|---|---|---|---|
| `yelp_category` | seeded from Yelp's curated `bubbletea` discovery | ~400 | Yelp's hand curation |
| `name_pattern` | a boba-name DOHMH `dba` with no Yelp listing | ~50 | needs vetting (regex can misfire) |

### Known gaps
- Independents Yelp doesn't file under `bubbletea` and whose name carries no
  keyword ("O-CHA", some single-location tea studios).
- Regex false positives in the `name_pattern` tail (e.g. `the alley` once matched
  "THE ALLEY PIZZA LOUNGE").
- Anything that opened and closed entirely between DOHMH inspection cycles and
  never got a Yelp page.

### `notebooks/03_recall_precision.py`

A hand-labelling workbench: a source-overlap summary (who finds what), a recall
sample against the DOHMH `Coffee/Tea` universe (`data/recall_sample.csv`), and a
`name_pattern` precision sample (`data/name_pattern_review.csv`). Fill the blank
column; recompute cells produce recall (Wilson CI) and tail precision.

Report counts as **"≥ N (Yelp / name identification)"**, never a hard "N". The
DOHMH recall universe is `cuisine_description = 'Coffee/Tea'` (~2,220
establishments; the pipeline flags ~195 of them by name).

## 2. Linking the shop to a first-observed date (`boba/ingest/yelp.py::link`)

Discovery gives a shop; linking attaches DOHMH's inspection dates for the *same
physical shop*. Name comparisons run on a **name key**: lowercase, punctuation →
space, then drop generic tokens (`bubble`, `tea`, `boba`, `cafe`, `the`, `llc`,
…) so two unrelated shops don't score high just for both containing "bubble tea".

For each Yelp business, every DOHMH establishment within `160 m` (`ST_DWithin` on
the `geography` cast → real metres). Score each candidate as
`name_sim − min(dist/20, 15)`; keep the best if `name_sim ≥ 60`. One row per
linked Yelp business in `yelp_matches` (its best CAMIS + score). ~185 of ~446
link. Idempotent (`truncate yelp_matches` + rebuild).

## 3. Borough + NYC filter (`boba/analyze.py`)

`boba/seed.py` loads NYC's official borough polygons into `boroughs`;
`analyze._assign_boroughs` does `ST_Contains` on each shop's point:

- in a borough → that borough's name (one of exactly five)
- outside all five → **dropped** (~100; Yelp's lat/lon radius search pulls in
  Nassau, Westchester and NJ — Great Neck, Manhasset, Newark, …)
- no geometry but a CAMIS → DOHMH `boro` fallback (DOHMH is NYC-only)

## 4. Dates (`boba/analyze.py`) — evidence bounds, not lifecycle events

**DOHMH has no opening or closing field.** It records dated *health inspections*.
So the `boba_shops` date columns are named for what they actually are.

### `first_seen_date` / `first_seen_source`
Only one source: `dohmh_first_inspection` — the earliest DOHMH inspection,
preferring the **"Pre-permit / Initial"** inspection (done as a new food business
is licensed to open).

Read it as **"operating by at least this date, ±1 quarter"** — not "opened on";
the inspection is on/after opening, typically weeks to a quarter late. **Only
shops with a DOHMH match get a date** — Yelp-only shops (no match, often too new
to have been inspected) have none.

The year summary is **"first observed per year"** — when shops entered the DOHMH
record, not openings. Recent years are undercounted (permit gap + no DOHMH match
yet); it's a floor, and not a closings series (there isn't one).

### `last_seen_date`
Latest DOHMH inspection for the shop, or null.

### `status` / `status_basis`
`status` ∈ `open` / `closed` / `unknown`. **`open` requires a positive signal**;
with none, status is `unknown` — a stale record can't be told from a live one.
`status_basis` records which signal, in the order `analyze._closed` checks them:

| basis | meaning | trust |
|---|---|---|
| `dohmh_closed_by_dohmh` | `status=closed` — DOHMH "Establishment Closed" action, not reopened | high |
| `yelp_closed` | `status=closed` — Yelp `is_closed`; beats a stale inspection | high |
| `dohmh_active` | `status=open` — inspected within ~18 months | high |
| `yelp_open` | `status=open` — Yelp says open (no recent inspection to corroborate) | high |
| `dohmh_inactive` | `status=unknown` — no inspection in > `INACTIVE_DAYS` (550) and nothing else says open. A stale record, **not** a closure | — |
| `none` | `status=unknown` — no signal at all | — |

Precedence: force-closed → `yelp_closed` → recent inspection (`dohmh_active`) →
`yelp_open` → inspection gap (`dohmh_inactive` → `unknown`) → `none`. A recent
DOHMH inspection outranks Yelp for the *open* basis (an inspection is a hard "was
operating on date X"); Yelp `is_closed` still outranks a stale inspection for the
*closed* basis. **Inspection silence is never a closure** — DOHMH is trusted for
dates and its own explicit closure actions, nothing more.

`first_seen > closed` contradictions drop the weaker `first_seen`.

### Health grade / score
`dohmh_establishments.latest_grade` (A/B/C, from the newest graded inspection) and
`latest_score` (violation points at the newest scored inspection, **lower is
cleaner** — A ≈ 0–13) are recomputed in `ingest/dohmh.py::_RECOMPUTE_SQL`. Only
shops with a DOHMH match carry them (~55%). Surfaced in the dashboard's Shops
table, the by-chain rollup, and a grade-vs-Yelp-rating scatter — a hygiene axis,
independent of the census logic.

### Output
`data/boba_status.csv` and the year summary.
