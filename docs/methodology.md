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

## 4. Deriving opened / closed (`boba/analyze.py`)

### opened_date (first match wins)
| signal | precision | note |
|---|---|---|
| DOHMH first inspection date | month | strongest — a new permit is inspected within weeks |
| Overture `first_seen_release` | quarter | **only** once a place has persisted across ≥ 2 of our ingests; meaningless on the first run |
| — | — | Overture `source_update_time` is **deliberately not used** — it's when Overture last touched the record, clusters in the current year, and is not an opening |

~244 of 523 shops get a real opened date; the other ~279 have none (no DOHMH
match — the coverage gap, left explicit rather than faked).

### closed_date / status

`status` is one of `open` / `closed` / `unknown`. **`open` requires a positive
signal** — a DOHMH inspection within `INACTIVE_DAYS` (550), or Overture
`operating_status = 'open'`. With no signal either way (Overture-only shop,
`operating_status` NULL, no DOHMH record) the status is **`unknown`**, not `open`
— a stale record can't be distinguished from a genuinely-new shop. (~70 of 523.)

| closed signal | confidence | note |
|---|---|---|
| DOHMH `Establishment Closed by DOHMH` action, not reopened | high | health-department closure — rare (~4) |
| Overture `operating_status == permanently_closed` | low–medium | current snapshot; date ≈ `source_update_time` |
| No inspection in > `INACTIVE_DAYS` (550) and not force-closed | low | "went silent" ≈ likely closed |

`opened > closed` contradictions drop the weaker opened proxy.

### Output
`status_events` (opened / closed / reopened, each with source + confidence),
`data/boba_status.csv`, and a 2022–2026 openings/closings/net summary by year and
borough.
