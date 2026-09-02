# Methodology — how shops are identified and dated

## 1. Identifying boba shops

There is **no ground-truth list**, so recall is a lower bound. Two mechanisms:

**Overture** (`boba/filters.py::overture_is_boba`)
- primary: `categories.primary` or `alternate` == `bubble_tea` — Overture's own
  tag, not a regex. ~435 of 443.
- fallback: category in a broader set (`cafe`, `tea_room`, `dessert_shop`, …)
  **and** the name matches `BOBA_NAME_PATTERN`. ~8 more.

**DOHMH** (`boba/filters.py::dohmh_is_boba`)
- **100% name regex** on `dba` (`BOBA_NAME_PATTERN` — chains + keyword fragments).
  DOHMH has no bubble-tea cuisine, so there is no category path.

**Label propagation** (`boba/match.py`)
- Any DOHMH establishment that matches an Overture `bubble_tea` place (below)
  counts as boba even if its `dba` never hit the regex. Recovers ~71
  establishments the regex alone misses.

### Known gaps
- Independents with no keyword in the name ("Sunright Tea Studio", "O-CHA").
- Overture places filed under `restaurant` / `chinese_restaurant` (not in the
  fallback set).
- Regex false positives (e.g. `the alley` once matched "THE ALLEY PIZZA LOUNGE";
  the distance gate in matching removes most).
- Overture category false positives (a burger place tagged `bubble_tea`).

Planned: `notebooks/03_recall_precision.py` — sample and hand-label ~50 DOHMH
Coffee/Tea establishments for a recall estimate, and spot-check the match tail.
Report counts as **"≥ N (name/category identification)"**, never a hard "N".

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

## 3. Deriving opened / closed (`boba/analyze.py`)

### opened_date (first match wins)
| signal | precision | note |
|---|---|---|
| DOHMH first inspection date | month | strongest — a new permit is inspected within weeks |
| Overture `first_seen_release` | quarter | **only** once a place has persisted across ≥ 2 of our ingests; meaningless on the first run |
| — | — | Overture `source_update_time` is **deliberately not used** — it's when Overture last touched the record, clusters in the current year, and is not an opening |

~231 of 523 shops get a real opened date; the other ~292 have none (no DOHMH
match — the coverage gap, left explicit rather than faked).

### closed_date / status
| signal | confidence | note |
|---|---|---|
| DOHMH `Establishment Closed by DOHMH` action, not reopened | high | health-department closure — rare (~4) |
| Overture `operating_status == permanently_closed` | low–medium | current snapshot; date ≈ `source_update_time` |
| No inspection in > `INACTIVE_DAYS` (550) and not force-closed | low | "went silent" ≈ likely closed |

`opened > closed` contradictions drop the weaker opened proxy.

### Output
`status_events` (opened / closed / reopened, each with source + confidence),
`data/boba_status.csv`, and a 2022–2026 openings/closings/net summary by year and
borough.
