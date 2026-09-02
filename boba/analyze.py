"""Build the canonical boba-shop list.

One ``boba_shops`` row per real-world shop, seeded from three populations
(Yelp first -- its ``bubbletea`` category is the best discovery signal):

* a Yelp business, + its linked Overture place (brand/geom) and CAMIS (dates)
* an Overture ``bubble_tea`` place not in Yelp, + its matched CAMIS
* a boba-name CAMIS not in Yelp or Overture

first_seen_date / last_seen_date are *evidence bounds* (mostly DOHMH inspection
dates), not lifecycle events; closed_date is set only on a real closure signal.
status + status_basis capture open/closed/unknown and why. Prints a "first seen
per year" summary and writes data/boba_status.csv.
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections import Counter

import pandas as pd
from sqlalchemy import text

from boba.config import data_dir
from boba.contracts import ingest_run
from boba.db import SessionLocal, engine
from boba.filters import name_looks_like_boba
from boba.log import get_logger, setup
from boba.models import BobaShop

log = get_logger("boba.analyze")

SINCE_YEAR = 2022  # DOHMH inspection history floor; nothing credible before this
INACTIVE_DAYS = 550  # no inspection in ~18 months + not force-closed => likely closed

# CAMIS in play: name-matched, or linked to an Overture / Yelp boba entity
_BOBA_CAMIS = """
    select camis from dohmh_establishments where boba_name_match
    union select camis from place_matches
    union select camis from yelp_matches where camis is not null
"""

_YELP = """
    select y.yelp_id, y.name, y.is_closed as yelp_is_closed, y.rating, y.review_count,
           st_x(y.geom) lon, st_y(y.geom) lat,
           ym.overture_id, ym.camis
    from yelp_businesses y
    left join yelp_matches ym on ym.yelp_id = y.yelp_id
"""

_ESTABLISHMENTS = f"""
    select e.camis, e.dba, e.boro, e.boba_name_match,
           e.first_inspection_date, e.last_inspection_date,
           e.closed_flag, e.closed_date, e.reopened_date,
           st_x(e.geom) lon, st_y(e.geom) lat,
           m.overture_id, m.score
    from dohmh_establishments e
    left join lateral (
        select overture_id, score from place_matches pm
        where pm.camis = e.camis order by score desc limit 1
    ) m on true
    where e.camis in ({_BOBA_CAMIS})
"""

_OVERTURE = """
    select o.id as overture_id, o.name, o.locality, o.operating_status,
           o.source_update_time, o.first_seen_release, o.last_seen_release,
           jsonb_exists(o.categories -> 'all', 'bubble_tea') as is_bubble_tea,
           st_x(o.geom) lon, st_y(o.geom) lat,
           m.camis
    from overture_places o
    left join lateral (
        select camis, score from place_matches pm
        where pm.overture_id = o.id order by score desc limit 1
    ) m on true
"""

# DOHMH `boro` fallback (used only when a point sits just outside the polygons,
# e.g. a shoreline geocode); Overture localities are neighbourhood-level and are
# NOT trusted -- borough comes from point-in-polygon against `boroughs`.
_DOHMH_BORO = {
    "1": "Manhattan",
    "2": "Bronx",
    "3": "Brooklyn",
    "4": "Queens",
    "5": "Staten Island",
    "manhattan": "Manhattan",
    "bronx": "Bronx",
    "brooklyn": "Brooklyn",
    "queens": "Queens",
    "staten island": "Staten Island",
}


def _assign_boroughs(shops: list[dict]) -> list[dict]:
    """Set `borough` by point-in-polygon; drop shops that fall outside all five
    boroughs (the NYC bbox rectangle also covers Nassau / Westchester edges)."""
    pts = [
        (i, s["lon"], s["lat"])
        for i, s in enumerate(shops)
        if s["lon"] is not None and s["lat"] is not None
    ]
    in_boro: dict[int, str] = {}
    if pts:
        idx, lons, lats = (list(x) for x in zip(*pts, strict=True))
        with engine.connect() as conn:
            if conn.execute(text("select count(*) from boroughs")).scalar_one() == 0:
                raise RuntimeError("boroughs table is empty -- run `just seed` first")
            rows = conn.execute(
                text(
                    """
                    select t.i, b.name
                    from unnest(cast(:i as int[]), cast(:lon as float8[]),
                                cast(:lat as float8[])) as t(i, lon, lat)
                    left join boroughs b
                      on st_contains(b.geom, st_setsrid(st_makepoint(t.lon, t.lat), 4326))
                    """
                ),
                {"i": idx, "lon": lons, "lat": lats},
            )
            in_boro = {int(i): name for i, name in rows if name}

    kept, dropped = [], 0
    for i, sh in enumerate(shops):
        boro = in_boro.get(i)
        if not boro:
            dboro = sh.get("_dohmh_boro")
            if sh["camis"] and isinstance(dboro, str) and dboro.strip():
                boro = _DOHMH_BORO.get(dboro.strip().lower(), dboro.strip().title())
            else:
                dropped += 1
                continue
        sh["borough"] = boro
        kept.append(sh)
    if dropped:
        log.info("dropped %d shops outside the five boroughs", dropped)
    return kept


def _release_date(rel: str | None) -> dt.date | None:
    if not rel:
        return None
    try:
        return dt.date.fromisoformat(rel.split(".")[0])
    except ValueError:
        return None


def _first_seen(est, ov) -> tuple[dt.date | None, str | None]:
    """Earliest *evidence* the shop existed -- NOT an opening date. DOHMH's first
    inspection (pre-permit where available) lags the true opening by the permit
    gap, so this is 'operating by at least this date'."""
    if est is not None and pd.notna(est.first_inspection_date):
        return est.first_inspection_date, "dohmh_first_inspection"
    # only meaningful once a place persists across >= 2 of our ingests
    if ov is not None and ov.first_seen_release and ov.first_seen_release != ov.last_seen_release:
        rel = _release_date(ov.first_seen_release)
        if rel:
            return rel, "overture_release"
    return None, None


def _last_seen(est, ov) -> dt.date | None:
    """Latest evidence the shop existed."""
    cands = []
    if est is not None and pd.notna(est.last_inspection_date):
        cands.append(est.last_inspection_date)
    if ov is not None and ov.last_seen_release:
        rel = _release_date(ov.last_seen_release)
        if rel:
            cands.append(rel)
    return max(cands) if cands else None


def _closed(est, ov, yb, today) -> tuple[dt.date | None, str | None, str, str]:
    """-> (closed_date, closed_source, status, status_basis). yb = Yelp row or None."""
    yelp = bool(yb.yelp_is_closed) if yb is not None and pd.notna(yb.yelp_is_closed) else None
    # --- closure signals, strongest first ---
    if est is not None and est.closed_flag and pd.notna(est.closed_date):
        reopened = pd.notna(est.reopened_date) and est.reopened_date >= est.closed_date
        if not reopened:
            return est.closed_date, "dohmh_closed_by_dohmh", "closed", "dohmh_closed_by_dohmh"
    if yelp is True:  # Yelp is current -- beats a stale inspection
        return None, "yelp", "closed", "yelp_closed"
    if ov is not None and ov.operating_status == "permanently_closed":
        d = ov.source_update_time.date() if pd.notna(ov.source_update_time) else None
        return d, "overture_permanently_closed", "closed", "overture_permanently_closed"
    if est is not None and pd.notna(est.last_inspection_date):
        idle = (today - est.last_inspection_date).days
        if idle > INACTIVE_DAYS and not est.closed_flag:
            return est.last_inspection_date, "dohmh_inactive", "closed", "dohmh_inactive"
        return None, None, "open", "dohmh_active"  # inspected within ~18 months
    # --- positive "open" signals, most trustworthy first, else unknown ---
    if yelp is False:
        return None, None, "open", "yelp_open"
    if ov is not None and ov.operating_status == "open":
        return None, None, "open", "overture_open"  # Overture's word only -- unreliable
    return None, None, "unknown", "none"


def _identified_by(yb, ov, est) -> str:
    if yb is not None:
        return "yelp_category"  # Yelp's curated bubbletea category -- our best signal
    has_cat = ov is not None and bool(ov.is_bubble_tea)
    has_name = (ov is not None and name_looks_like_boba(ov.name)) or (
        est is not None and bool(est.boba_name_match)
    )
    if has_cat and has_name:
        return "both"
    if has_cat:
        return "overture_category"
    if has_name:
        return "name_pattern"
    return "propagated"  # DOHMH establishment labelled boba only via a spatial match


def _dates(est, ov, yb, today):
    first_d, first_src = _first_seen(est, ov)
    last_d = _last_seen(est, ov)
    closed_d, closed_src, status, basis = _closed(est, ov, yb, today)
    if first_d and closed_d and first_d > closed_d:  # contradiction -> drop the weaker
        first_d, first_src = None, None
    return first_d, first_src, last_d, closed_d, closed_src, status, basis


def _coord(*vals):
    for v in vals:
        if v is not None and pd.notna(v):
            return float(v)
    return None


def _shop(*, est, ov, yb, today) -> dict:
    first_d, first_src, last_d, c_d, c_s, status, basis = _dates(est, ov, yb, today)
    name = (
        (yb.name if yb is not None else None)
        or (ov.name if ov is not None else None)
        or (est.dba if est is not None else None)
    )
    return {
        "name": name,
        "overture_id": ov.Index if ov is not None else None,
        "camis": est.Index if est is not None else None,
        "yelp_id": yb.yelp_id if yb is not None else None,
        # geometry: Overture is most precise, then Yelp, then DOHMH
        "lon": _coord(
            ov.lon if ov is not None else None,
            yb.lon if yb is not None else None,
            est.lon if est is not None else None,
        ),
        "lat": _coord(
            ov.lat if ov is not None else None,
            yb.lat if yb is not None else None,
            est.lat if est is not None else None,
        ),
        "borough": None,  # set by _assign_boroughs
        "_dohmh_boro": est.boro if est is not None else None,
        "first_seen_date": first_d,
        "first_seen_source": first_src,
        "last_seen_date": last_d,
        "closed_date": c_d,
        "closed_source": c_s,
        "status": status,
        "status_basis": basis,
        "identified_by": _identified_by(yb, ov, est),
    }


def _dedup(shops: list[dict]) -> list[dict]:
    """Collapse near-identical rows (same normalised name, within ~60 m) -- Overture
    carries duplicate GERS ids for the same shop. Keep the richest row."""
    by_name: dict[str, list[dict]] = {}
    for sh in shops:
        key = "".join(c for c in (sh["name"] or "").lower() if c.isalnum())
        by_name.setdefault(key, []).append(sh)

    def richness(s: dict) -> tuple:
        return (
            s["camis"] is not None,
            s["yelp_id"] is not None,
            s["status"] != "unknown",
            s["first_seen_date"] is not None,
            s["overture_id"] is not None,
        )

    kept, dropped = [], 0
    for group in by_name.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        used = [False] * len(group)
        for i, a in enumerate(group):
            if used[i]:
                continue
            cluster = [a]
            used[i] = True
            for j in range(i + 1, len(group)):
                b = group[j]
                if used[j] or None in (a["lon"], a["lat"], b["lon"], b["lat"]):
                    continue
                if _haversine_m(a["lon"], a["lat"], b["lon"], b["lat"]) <= 60:
                    cluster.append(b)
                    used[j] = True
            best = max(cluster, key=richness)
            for k in ("overture_id", "camis", "yelp_id"):
                best[k] = best[k] or next((c[k] for c in cluster if c[k]), None)
            kept.append(best)
            dropped += len(cluster) - 1
    if dropped:
        log.info("dedup: merged %d duplicate rows", dropped)
    return kept


def _haversine_m(lon1, lat1, lon2, lat2) -> float:
    from math import asin, cos, radians, sin, sqrt

    dlon, dlat = radians(lon2 - lon1), radians(lat2 - lat1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(h))


def run(since_year: int = SINCE_YEAR) -> None:
    setup()
    yelp_df = pd.read_sql(text(_YELP), engine)
    est_df = pd.read_sql(text(_ESTABLISHMENTS), engine).set_index("camis")
    ov_df = pd.read_sql(text(_OVERTURE), engine).set_index("overture_id")
    today = dt.date.today()

    ov_by_id = {r.Index: r for r in ov_df.itertuples(name="Ov")}
    est_by_camis = {r.Index: r for r in est_df.itertuples(name="Est")}
    ov_used: set[str] = set()
    est_used: set[str] = set()
    shops: list[dict] = []
    n_yelp = n_ov = n_dohmh = 0

    # 1. Yelp businesses (primary discovery)
    for yb in yelp_df.itertuples(index=False, name="Y"):
        ov = ov_by_id.get(yb.overture_id) if isinstance(yb.overture_id, str) else None
        est = est_by_camis.get(yb.camis) if isinstance(yb.camis, str) else None
        if ov is not None:
            ov_used.add(ov.Index)
        if est is not None:
            est_used.add(est.Index)
        shops.append(_shop(est=est, ov=ov, yb=yb, today=today))
        n_yelp += 1

    # 2. Overture bubble_tea / name places not already seeded via Yelp
    for ov in ov_by_id.values():
        if ov.Index in ov_used:
            continue
        if not (bool(ov.is_bubble_tea) or name_looks_like_boba(ov.name)):
            continue
        est = est_by_camis.get(ov.camis) if isinstance(ov.camis, str) else None
        if est is not None:
            est_used.add(est.Index)
        shops.append(_shop(est=est, ov=ov, yb=None, today=today))
        n_ov += 1

    # 3. boba-name CAMIS not already seeded
    for est in est_by_camis.values():
        if est.Index in est_used or not bool(est.boba_name_match):
            continue
        shops.append(_shop(est=est, ov=None, yb=None, today=today))
        n_dohmh += 1

    shops = _assign_boroughs(shops)
    shops = _dedup(shops)

    log.info("identified_by: %s", dict(Counter(s["identified_by"] for s in shops)))
    log.info(
        "%d boba shops (seeds: %d Yelp, %d Overture-only, %d DOHMH-only)",
        len(shops),
        n_yelp,
        n_ov,
        n_dohmh,
    )
    _write(shops, since_year, today)


def _write(shops: list[dict], since_year: int, today: dt.date) -> None:
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point

    with SessionLocal() as s, ingest_run(s, "analyze") as m:
        s.execute(text("truncate boba_shops restart identity cascade"))
        for r in shops:
            lon, lat = r["lon"], r["lat"]
            geom = (
                from_shape(Point(lon, lat), srid=4326)
                if lon is not None and lat is not None
                else None
            )
            s.add(
                BobaShop(
                    name=r["name"],
                    overture_id=r["overture_id"],
                    camis=r["camis"],
                    yelp_id=r["yelp_id"],
                    geom=geom,
                    borough=r["borough"],
                    first_seen_date=r["first_seen_date"],
                    first_seen_source=r["first_seen_source"],
                    last_seen_date=r["last_seen_date"],
                    closed_date=r["closed_date"],
                    closed_source=r["closed_source"],
                    status=r["status"],
                    status_basis=r["status_basis"],
                    identified_by=r["identified_by"],
                )
            )
        m.row_count = len(shops)
        m.kept_count = len(shops)
        m.detail = {
            "open": sum(1 for r in shops if r["status"] == "open"),
            "closed": sum(1 for r in shops if r["status"] == "closed"),
        }

    df = pd.DataFrame(shops)
    out = data_dir() / "boba_status.csv"
    df.to_csv(out, index=False)
    log.info("wrote %s", out)
    _summary(df, since_year, today)


def _summary(df: pd.DataFrame, since_year: int, today: dt.date) -> None:
    d = df.copy()
    d["first_seen_year"] = pd.to_datetime(d["first_seen_date"], errors="coerce").dt.year
    d["closed_year"] = pd.to_datetime(d["closed_date"], errors="coerce").dt.year
    years = list(range(since_year, today.year + 1))
    fs = d["first_seen_year"].value_counts().reindex(years, fill_value=0)
    cl = d[d["status"] == "closed"]["closed_year"].value_counts().reindex(years, fill_value=0)

    log.info("--- NYC boba shops (first seen = first DOHMH inspection, a proxy) ---")
    log.info("year   first-seen  closed")
    for y in years:
        log.info("%d   %9d  %6d", y, fs[y], cl[y])
    opn = d["status"] == "open"
    verified = opn & d["status_basis"].isin(["dohmh_active", "yelp_open"])
    log.info(
        "currently: %d open (%d verified by DOHMH/Yelp, %d Overture's word only), "
        "%d closed, %d unknown",
        opn.sum(),
        verified.sum(),
        (opn & (d["status_basis"] == "overture_open")).sum(),
        (d["status"] == "closed").sum(),
        (d["status"] == "unknown").sum(),
    )
    log.info("by borough:\n%s", d.groupby("borough")["status"].value_counts().to_string())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since-year", type=int, default=SINCE_YEAR)
    args = parser.parse_args(argv)
    run(since_year=args.since_year)


if __name__ == "__main__":
    main()
