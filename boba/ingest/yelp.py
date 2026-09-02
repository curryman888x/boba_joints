"""Yelp Fusion ingest -- the primary boba discovery source.

``discover``: enumerate NYC ``bubbletea`` businesses via ``/businesses/search``,
adaptively subdividing the bbox wherever a tile hits Yelp's 240-result ceiling.
Each result carries ``is_closed`` (free current status). -> ``yelp_businesses``.

``link``: match each Yelp business to a DOHMH CAMIS (for a first-seen date) by
name + distance. -> ``yelp_matches``.

Capped by ``--limit`` calls; ``discover`` is skipped if the last run was within
``--max-age-days``. The raw sweep is cached to ``data/yelp_raw_last.json`` so a
later rate-limited run rebuilds ``yelp_businesses`` instead of wiping it.
No-ops without ``YELP_API_KEY``.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt

from rapidfuzz import fuzz
from sqlalchemy import text

from boba.config import NYC_BBOX, YELP_API_KEY, YELP_SEARCH_URL, data_dir
from boba.contracts import ContractViolation, ingest_run, last_successful_run, parse_yelp_business
from boba.db import SessionLocal, engine, upsert
from boba.filters import name_key, name_looks_like_boba
from boba.log import get_logger, setup
from boba.models import YelpBusiness, YelpMatch
from boba.net import yelp_session

log = get_logger("boba.ingest.yelp")

_PAGE = 50
_YELP_MAX_OFFSET = 240  # Yelp returns at most 240 results per search
_MAX_DEPTH = 5
_LINK_RADIUS_M = 160.0
_LINK_NAME_MIN = 60.0
# the raw sweep, cached so a later rate-limited run (Yelp free tier is ~500
# calls/day) can still rebuild yelp_businesses instead of wiping it
_RAW_CACHE = "yelp_raw_last.json"


def _haversine_m(lon1, lat1, lon2, lat2) -> float:
    dlon, dlat = radians(lon2 - lon1), radians(lat2 - lat1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(h))


def _in_bbox(lon, lat, bbox) -> bool:
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def _is_boba_business(name: str | None, categories: list[dict]) -> bool:
    """Yelp over-tags -- a ramen shop can carry ``bubbletea`` as its 3rd category.
    Keep a result only if ``bubbletea`` is its *primary* category, or the name
    itself reads as boba."""
    aliases = [c.get("alias") for c in categories]
    return aliases[:1] == ["bubbletea"] or name_looks_like_boba(name)


def _search_tile(sess, bbox, budget: list[int]) -> list[dict]:
    """All bubbletea businesses for one bbox tile (paged, <= 240)."""
    lo_lon, lo_lat, hi_lon, hi_lat = bbox
    clat, clon = (lo_lat + hi_lat) / 2, (lo_lon + hi_lon) / 2
    radius = min(40000, int(_haversine_m(lo_lon, lo_lat, hi_lon, hi_lat) / 2) + 200)
    out: list[dict] = []
    for offset in range(0, _YELP_MAX_OFFSET, _PAGE):
        if budget[0] <= 0:
            break
        lim = min(_PAGE, _YELP_MAX_OFFSET - offset)  # Yelp requires offset + limit <= 240
        r = sess.get(
            YELP_SEARCH_URL,
            params={
                "categories": "bubbletea",
                "latitude": clat,
                "longitude": clon,
                "radius": radius,
                "limit": lim,
                "offset": offset,
                "sort_by": "distance",
            },
            timeout=30,
        )
        budget[0] -= 1
        if r.status_code == 429:
            log.warning("Yelp rate limit -- stopping discovery")
            budget[0] = 0
            break
        r.raise_for_status()
        batch = r.json().get("businesses", [])
        out.extend(batch)
        if len(batch) < lim:
            break
    return out


def _quadrants(bbox):
    lo_lon, lo_lat, hi_lon, hi_lat = bbox
    mlon, mlat = (lo_lon + hi_lon) / 2, (lo_lat + hi_lat) / 2
    return [
        (lo_lon, lo_lat, mlon, mlat),
        (mlon, lo_lat, hi_lon, mlat),
        (lo_lon, mlat, mlon, hi_lat),
        (mlon, mlat, hi_lon, hi_lat),
    ]


def _sweep(sess, bbox, seen: dict, budget: list[int], depth: int = 0) -> None:
    if budget[0] <= 0:
        return
    batch = _search_tile(sess, bbox, budget)
    for b in batch:
        if b.get("id"):
            seen[b["id"]] = b
    if len(batch) >= _YELP_MAX_OFFSET and depth < _MAX_DEPTH:
        log.info("tile saturated at depth %d -- subdividing", depth)
        for q in _quadrants(bbox):
            _sweep(sess, q, seen, budget, depth + 1)


def discover(limit: int) -> int:
    sess = yelp_session()
    seen: dict[str, dict] = {}
    budget = [limit]
    _sweep(sess, NYC_BBOX, seen, budget)
    log.info("discovery: %d unique Yelp businesses (%d calls used)", len(seen), limit - budget[0])

    cache = data_dir() / _RAW_CACHE
    if len(seen) < 200 and cache.exists():
        # the sweep was starved (rate limit / outage) -- rebuild from the last good one
        cached = json.loads(cache.read_text())
        log.warning(
            "thin sweep (%d) -- falling back to %d cached businesses", len(seen), len(cached)
        )
        seen = {b["id"]: b for b in cached if b.get("id")}
    elif len(seen) >= 200:
        cache.write_text(json.dumps(list(seen.values())))

    rows, violations, off_topic = [], 0, 0
    for raw in seen.values():
        try:
            rec = parse_yelp_business(raw)
        except ContractViolation as exc:
            violations += 1
            log.warning("contract: %s", str(exc).splitlines()[0][:160])
            continue
        if not _in_bbox(rec.lon, rec.lat, NYC_BBOX):
            continue
        if not _is_boba_business(rec.name, rec.categories):
            off_topic += 1
            continue
        rows.append(
            {
                "yelp_id": rec.yelp_id,
                "name": rec.name,
                "is_closed": rec.is_closed,
                "rating": rec.rating,
                "review_count": rec.review_count,
                "price": rec.price,
                "phone": rec.phone,
                "url": rec.url,
                "categories": rec.categories,
                "address": rec.address,
                "city": rec.city,
                "zip": rec.zip,
                "geom": f"SRID=4326;POINT({rec.lon} {rec.lat})",
                "checked_at": datetime.now(UTC),
            }
        )

    with SessionLocal() as s, ingest_run(s, "yelp_discover") as m:
        upsert(s, YelpBusiness, rows, index_elements=["yelp_id"])
        swept = 0
        ids = [r["yelp_id"] for r in rows]
        if ids and len(ids) > 200:  # guard against a thin sweep on a rate-limited run
            swept = s.execute(
                text("delete from yelp_businesses where not (yelp_id = any(:ids))"),
                {"ids": ids},
            ).rowcount
        m.row_count = len(seen)
        m.kept_count = len(rows)
        m.detail = {
            "violations": violations,
            "off_topic": off_topic,
            "swept": swept,
            "calls": limit - budget[0],
        }
    log.info(
        "discover: %d NYC boba businesses (%d off-topic dropped, %d contract)",
        len(rows),
        off_topic,
        violations,
    )
    return len(rows)


_CAND_SQL = text(
    """
    select y.yelp_id, y.name as y_name,
           e.camis as cand_id, e.dba as cand_name,
           st_distance(y.geom::geography, e.geom::geography) as dist_m
    from yelp_businesses y
    join dohmh_establishments e
      on e.geom is not null and st_dwithin(e.geom::geography, y.geom::geography, :r)
    """
)


def _best(cands: list, y_name: str) -> tuple[str | None, float | None]:
    """cands: list of (id, name, dist). -> (id, score) for the best name+distance match."""
    key = name_key(y_name)
    best_id, best = None, 0.0
    for cid, cname, dist in cands:
        sim = fuzz.token_set_ratio(key, name_key(cname))
        score = sim - min(dist / 20.0, 15.0)
        if sim >= _LINK_NAME_MIN and score > best:
            best_id, best = cid, round(score, 1)
    return best_id, (best or None)


def link() -> None:
    setup()
    with engine.connect() as conn:
        rows_raw = conn.execute(_CAND_SQL, {"r": _LINK_RADIUS_M}).all()

    by_yelp: dict[str, dict] = {}
    for r in rows_raw:
        d = by_yelp.setdefault(r.yelp_id, {"name": r.y_name, "cands": []})
        d["cands"].append((r.cand_id, r.cand_name, r.dist_m))

    rows = []
    for yid, d in by_yelp.items():
        camis, c_score = _best(d["cands"], d["name"])
        if camis:
            rows.append({"yelp_id": yid, "camis": camis, "camis_score": c_score})

    with SessionLocal() as s, ingest_run(s, "yelp_link") as m:
        s.execute(text("truncate yelp_matches"))
        upsert(s, YelpMatch, rows, index_elements=["yelp_id"])
        m.row_count = len(by_yelp)
        m.kept_count = len(rows)
        m.detail = {"to_dohmh": len(rows)}
    log.info("link: %d Yelp businesses linked to a DOHMH CAMIS", len(rows))


def run(limit: int = 240, max_age_days: int = 14, rediscover: bool = False) -> None:
    setup()
    if not YELP_API_KEY:
        log.warning("YELP_API_KEY not set -- skipping Yelp ingest")
        return
    with SessionLocal() as s:
        prev = last_successful_run(s, "yelp_discover")
    stale = (
        rediscover
        or prev is None
        or (prev.finished_at and (datetime.now(UTC) - prev.finished_at).days >= max_age_days)
    )
    if stale:
        discover(limit)
    else:
        log.info("discovery is fresh (last run %s) -- skipping", prev.finished_at)
    link()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=240, help="max search calls for discovery")
    parser.add_argument("--max-age-days", type=int, default=14)
    parser.add_argument("--rediscover", action="store_true", help="force a fresh discovery sweep")
    args = parser.parse_args(argv)
    run(limit=args.limit, max_age_days=args.max_age_days, rediscover=args.rediscover)


if __name__ == "__main__":
    main()
