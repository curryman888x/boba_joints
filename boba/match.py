"""Link Overture boba places to DOHMH establishments (spatial + fuzzy name).

For every Overture boba place, find DOHMH establishments within ~120 m and score
each on name similarity, distance, and street agreement. Rows above threshold go
to ``place_matches`` -- multiple per place are kept on purpose, since a shop that
closed and reopened gets a fresh CAMIS.

Label propagation: a DOHMH establishment that matches here is a boba shop even if
its ``dba`` never hit ``BOBA_NAME_PATTERN``. ``analyze.py`` unions the name-matched
CAMIS with the matched ones.
"""

from __future__ import annotations

import argparse

import pandas as pd
from rapidfuzz import fuzz
from sqlalchemy import text

from boba.contracts import ingest_run
from boba.db import SessionLocal, engine, upsert
from boba.log import get_logger, setup
from boba.models import PlaceMatch

log = get_logger("boba.match")

RADIUS_M = 120.0
NAME_MIN = 72.0  # keep on name alone at/above this...
NAME_ALONE_M = 60.0  # ...but only within this distance
CLOSE_M = 35.0  # closer than this, accept a weaker name...
NAME_MIN_CLOSE = 55.0  # ...down to here
MAX_PER_PLACE = 5

# generic words that inflate token_set_ratio between unrelated boba shops
_STOP = {
    "bubble",
    "tea",
    "boba",
    "milk",
    "cafe",
    "coffee",
    "the",
    "shop",
    "house",
    "and",
    "ny",
    "nyc",
    "llc",
    "inc",
    "co",
    "of",
    "at",
}

_CANDIDATES_SQL = text(
    """
    select o.id as overture_id, o.name as o_name, o.addr_freeform as o_addr,
           e.camis, e.dba, e.street, e.boba_name_match,
           st_distance(o.geom::geography, e.geom::geography) as dist_m
    from overture_places o
    join dohmh_establishments e
      on e.geom is not null
     and st_dwithin(o.geom::geography, e.geom::geography, :radius)
    """
)


def _norm(s) -> str:
    if not isinstance(s, str):
        return ""
    return "".join(c if (c.isalnum() or c.isspace()) else " " for c in s.lower())


def _name_key(s) -> str:
    toks = [t for t in _norm(s).split() if t not in _STOP]
    return " ".join(toks) or _norm(s)  # fall back if the name was all stopwords


def _score(o_name, dba, o_addr, street, dist_m) -> tuple[float, float, str]:
    name_sim = fuzz.token_set_ratio(_name_key(o_name), _name_key(dba))
    street_tokens = _norm(street).split()
    addr_hit = bool(street_tokens) and street_tokens[0] in _norm(o_addr)
    score = name_sim - min(dist_m / 12.0, 25.0) + (8.0 if addr_hit else 0.0)
    return name_sim, score, ("name_addr" if addr_hit else "name_dist")


def run(radius: float = RADIUS_M) -> None:
    setup()
    cand = pd.read_sql(_CANDIDATES_SQL, engine, params={"radius": radius})
    log.info("%d Overture x DOHMH candidate pairs within %dm", len(cand), int(radius))
    if cand.empty:
        return

    rows: list[dict] = []
    for oid, grp in cand.groupby("overture_id"):
        scored = []
        for r in grp.itertuples():
            name_sim, score, method = _score(r.o_name, r.dba, r.o_addr, r.street, r.dist_m)
            keep = (name_sim >= NAME_MIN and r.dist_m <= NAME_ALONE_M) or (
                name_sim >= NAME_MIN_CLOSE and r.dist_m <= CLOSE_M
            )
            if keep:
                scored.append((score, name_sim, method, r.camis, r.dist_m))
        for score, name_sim, method, camis, dist_m in sorted(scored, reverse=True)[:MAX_PER_PLACE]:
            rows.append(
                {
                    "overture_id": oid,
                    "camis": camis,
                    "score": max(0.0, min(100.0, score)),
                    "name_similarity": float(name_sim),
                    "distance_m": float(dist_m),
                    "method": method,
                }
            )

    matched_camis = {r["camis"] for r in rows}
    name_camis = set(cand.loc[cand["boba_name_match"], "camis"])
    propagated = matched_camis - name_camis
    log.info(
        "%d matches across %d Overture places; %d DOHMH establishments newly labelled boba",
        len(rows),
        len({r["overture_id"] for r in rows}),
        len(propagated),
    )

    with SessionLocal() as s, ingest_run(s, "match") as m:
        s.execute(text("truncate place_matches"))
        upsert(s, PlaceMatch, rows, index_elements=["overture_id", "camis"])
        m.row_count = len(cand)
        m.kept_count = len(rows)
        m.detail = {
            "places_matched": len({r["overture_id"] for r in rows}),
            "camis_matched": len(matched_camis),
            "label_propagated": len(propagated),
        }
    log.info("match complete")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radius", type=float, default=RADIUS_M, help="metres")
    args = parser.parse_args(argv)
    run(radius=args.radius)


if __name__ == "__main__":
    main()
