"""Yelp Fusion ingest -- current open/closed for shops DOHMH can't verify.

For every Overture boba place and every boba-name DOHMH establishment with no
Overture match, search Yelp near its point, fuzzy-match by name, and record
``is_closed`` in ``yelp_status`` (keyed by overture_id or camis). ``analyze.py``
folds this in as a status signal above Overture's `operating_status`.

Yelp's free tier is ~500 calls/day, so this is capped (``--limit``) and cached:
a target checked within ``--max-age-days`` is skipped. Run it across a few days
for full coverage, then weekly to refresh.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt

from rapidfuzz import fuzz
from sqlalchemy import text

from boba.config import YELP_API_KEY, YELP_SEARCH_URL
from boba.contracts import ingest_run
from boba.db import SessionLocal, engine, upsert
from boba.filters import name_key
from boba.log import get_logger, setup
from boba.models import YelpStatus
from boba.net import yelp_session

log = get_logger("boba.ingest.yelp")

_RADIUS_M = 150
_NAME_MIN = 68.0
_CATEGORIES = "bubbletea,coffee,cafes,juicebars"

_TARGETS_SQL = text(
    """
    select 'overture' as src, o.id as key, o.name,
           st_x(o.geom) as lon, st_y(o.geom) as lat
    from overture_places o
    union all
    select 'dohmh', e.camis, e.dba, st_x(e.geom), st_y(e.geom)
    from dohmh_establishments e
    where e.boba_name_match and e.geom is not null
      and not exists (select 1 from place_matches m where m.camis = e.camis)
    """
)


def _haversine_m(lon1, lat1, lon2, lat2) -> float:
    dlon, dlat = radians(lon2 - lon1), radians(lat2 - lat1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(h))


def _best_match(name: str, lat: float, lon: float, businesses: list[dict]) -> dict | None:
    key = name_key(name)
    best, best_score = None, 0.0
    for b in businesses:
        coords = b.get("coordinates") or {}
        blat, blon = coords.get("latitude"), coords.get("longitude")
        if blat is None or blon is None:
            continue
        dist = _haversine_m(lon, lat, blon, blat)
        if dist > _RADIUS_M:
            continue
        sim = fuzz.token_set_ratio(key, name_key(b.get("name", "")))
        score = sim - min(dist / 15.0, 20.0)
        if sim >= _NAME_MIN and score > best_score:
            best, best_score = b, score
    if best is None:
        return None
    return {"biz": best, "score": round(best_score, 1)}


def _row(src: str, key: str, hit: dict | None) -> dict:
    base = {
        "overture_id": key if src == "overture" else None,
        "camis": key if src == "dohmh" else None,
    }
    if hit is None:
        return {
            **base,
            "yelp_id": None,
            "yelp_name": None,
            "is_closed": None,
            "rating": None,
            "review_count": None,
            "url": None,
            "match_score": None,
        }
    b = hit["biz"]
    return {
        **base,
        "yelp_id": b.get("id"),
        "yelp_name": b.get("name"),
        "is_closed": bool(b.get("is_closed")),
        "rating": b.get("rating"),
        "review_count": b.get("review_count"),
        "url": (b.get("url") or "").split("?")[0] or None,
        "match_score": hit["score"],
    }


def run(limit: int = 480, max_age_days: int = 30) -> None:
    setup()
    if not YELP_API_KEY:
        log.warning("YELP_API_KEY not set -- skipping Yelp ingest")
        return
    fresh = set()
    with engine.connect() as conn:
        targets = conn.execute(_TARGETS_SQL).all()
        for oid, camis in conn.execute(
            text(
                "select overture_id, camis from yelp_status "
                "where checked_at > now() - make_interval(days => :d)"
            ),
            {"d": max_age_days},
        ):
            fresh.add(oid or camis)

    todo = [t for t in targets if t.key not in fresh][:limit]
    log.info(
        "%d targets, %d already fresh, checking %d (cap %d)",
        len(targets),
        len(fresh),
        len(todo),
        limit,
    )
    if not todo:
        return

    sess = yelp_session()
    rows, matched, closed, errors = [], 0, 0, 0
    for i, t in enumerate(todo, 1):
        if t.lat is None or t.lon is None:
            continue
        try:
            r = sess.get(
                YELP_SEARCH_URL,
                params={
                    "latitude": t.lat,
                    "longitude": t.lon,
                    "radius": _RADIUS_M,
                    "term": t.name or "bubble tea",
                    "categories": _CATEGORIES,
                    "limit": 10,
                    "sort_by": "distance",
                },
                timeout=30,
            )
            if r.status_code == 429:
                log.warning("Yelp rate limit hit after %d calls -- stopping early", i - 1)
                break
            r.raise_for_status()
            hit = _best_match(t.name or "", t.lat, t.lon, r.json().get("businesses", []))
        except Exception as exc:  # noqa: BLE001 -- record & continue
            errors += 1
            log.warning("%s %s: %s", t.src, t.key, exc)
            continue
        row = _row(t.src, t.key, hit)
        rows.append(row)
        if row["yelp_id"]:
            matched += 1
            closed += bool(row["is_closed"])
        if i % 100 == 0:
            log.info("  %d/%d", i, len(todo))

    log.info(
        "checked %d: %d matched to a Yelp business, %d of those flagged closed, %d errors",
        len(rows),
        matched,
        closed,
        errors,
    )
    now = datetime.now(UTC)
    for r in rows:
        r["checked_at"] = now
    with SessionLocal() as s, ingest_run(s, "yelp") as m:
        upsert(s, YelpStatus, rows, index_elements=["overture_id", "camis"])
        m.row_count = len(rows)
        m.kept_count = matched
        m.detail = {"closed": closed, "errors": errors, "fresh_skipped": len(fresh)}
    log.info("yelp ingest complete")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=480, help="max Yelp calls this run")
    parser.add_argument(
        "--max-age-days", type=int, default=30, help="re-check targets older than this"
    )
    args = parser.parse_args(argv)
    run(limit=args.limit, max_age_days=args.max_age_days)


if __name__ == "__main__":
    main()
