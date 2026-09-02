"""Yelp Fusion ingest -- current open/closed for shops DOHMH can't verify.

For every Overture boba place and every boba-name DOHMH establishment with no
Overture match, resolve it to a Yelp business by **name + address**
(``/businesses/matches``), then read ``is_closed`` from ``/businesses/{id}``.
Result lands in ``yelp_status`` (keyed by overture_id or camis); ``analyze.py``
folds it in above Overture's ``operating_status``.

Two calls per shop, so this is capped (``--limit``) and cached: a target checked
within ``--max-age-days`` is skipped. Run across a few days for full coverage,
then monthly to refresh.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from rapidfuzz import fuzz
from sqlalchemy import text

from boba.config import (
    YELP_API_KEY,
    YELP_BUSINESS_URL,
    YELP_MATCH_URL,
)
from boba.contracts import ingest_run
from boba.db import SessionLocal, engine, upsert
from boba.filters import name_key
from boba.log import get_logger, setup
from boba.models import YelpStatus
from boba.net import yelp_session

log = get_logger("boba.ingest.yelp")

_NAME_MIN = 70.0  # reject a /matches result whose name is this different

_TARGETS_SQL = text(
    """
    select 'overture' as src, o.id as key, o.name,
           o.addr_freeform as addr, null as city, o.postcode as zip
    from overture_places o
    union all
    select 'dohmh', e.camis, e.dba,
           nullif(trim(concat_ws(' ', e.building, e.street)), ''), e.boro, e.zipcode
    from dohmh_establishments e
    where e.boba_name_match and e.geom is not null
      and not exists (select 1 from place_matches m where m.camis = e.camis)
    """
)


def _match(sess, t) -> str | None:
    """/businesses/matches -> the best-matching Yelp business id, or None."""
    if not t.addr:
        return None
    params = {
        "name": (t.name or "")[:64],
        "address1": t.addr[:64],
        "city": t.city or "New York",
        "state": "NY",
        "country": "US",
        "match_threshold": "default",
    }
    if t.zip:
        params["zip_code"] = str(t.zip)[:5]
    r = sess.get(YELP_MATCH_URL, params=params, timeout=30)
    r.raise_for_status()
    for b in r.json().get("businesses", []):
        if fuzz.token_set_ratio(name_key(t.name), name_key(b.get("name", ""))) >= _NAME_MIN:
            return b.get("id")
    return None


def _detail(sess, yelp_id: str) -> dict:
    r = sess.get(f"{YELP_BUSINESS_URL}/{yelp_id}", timeout=30)
    r.raise_for_status()
    b = r.json()
    return {
        "yelp_id": b.get("id"),
        "yelp_name": b.get("name"),
        "is_closed": bool(b.get("is_closed")),
        "rating": b.get("rating"),
        "review_count": b.get("review_count"),
        "url": (b.get("url") or "").split("?")[0] or None,
    }


_EMPTY = {
    "yelp_id": None,
    "yelp_name": None,
    "is_closed": None,
    "rating": None,
    "review_count": None,
    "url": None,
    "match_score": None,
}


def run(limit: int = 480, max_age_days: int = 30) -> None:
    setup()
    if not YELP_API_KEY:
        log.warning("YELP_API_KEY not set -- skipping Yelp ingest")
        return

    with engine.connect() as conn:
        targets = conn.execute(_TARGETS_SQL).all()
        fresh = {
            oid or camis
            for oid, camis in conn.execute(
                text(
                    "select overture_id, camis from yelp_status "
                    "where checked_at > now() - make_interval(days => :d)"
                ),
                {"d": max_age_days},
            )
        }

    todo = [t for t in targets if t.key not in fresh][:limit]
    log.info(
        "%d targets, %d fresh, checking %d (cap %d, ~%d calls)",
        len(targets),
        len(fresh),
        len(todo),
        limit,
        2 * len(todo),
    )
    if not todo:
        return

    sess = yelp_session()
    rows, matched, closed, errors = [], 0, 0, 0
    for i, t in enumerate(todo, 1):
        base = {
            "overture_id": t.key if t.src == "overture" else None,
            "camis": t.key if t.src == "dohmh" else None,
        }
        try:
            yid = _match(sess, t)
            detail = _detail(sess, yid) if yid else dict(_EMPTY)
        except Exception as exc:  # noqa: BLE001 -- record & continue
            errors += 1
            log.warning("%s %s: %s", t.src, t.key, exc)
            continue
        rows.append({**base, **detail, "match_score": None})
        if detail["yelp_id"]:
            matched += 1
            closed += bool(detail["is_closed"])
        if i % 100 == 0:
            log.info("  %d/%d", i, len(todo))

    log.info(
        "checked %d: %d matched, %d flagged closed, %d errors", len(rows), matched, closed, errors
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
    parser.add_argument("--limit", type=int, default=480, help="max targets (2 calls each)")
    parser.add_argument(
        "--max-age-days", type=int, default=30, help="re-check targets older than this"
    )
    args = parser.parse_args(argv)
    run(limit=args.limit, max_age_days=args.max_age_days)


if __name__ == "__main__":
    main()
