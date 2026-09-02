"""Build the canonical boba-shop list.

One ``boba_shops`` row per real-world shop, from two populations:

* a Yelp business (primary -- its ``bubbletea`` category is the best discovery
  signal), plus its linked DOHMH CAMIS for a first-seen date
* a boba-name DOHMH CAMIS that isn't in Yelp -- usually a shop that closed before
  Yelp would help

first_seen_date / last_seen_date are *evidence bounds* (DOHMH inspection dates),
not lifecycle events; closed_date is set only on a real closure signal.
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
from boba.log import get_logger, setup
from boba.models import BobaShop

log = get_logger("boba.analyze")

SINCE_YEAR = 2022  # DOHMH inspection history floor; nothing credible before this
INACTIVE_DAYS = 550  # no inspection in ~18 months => stale record, status can't be told

# CAMIS in play: boba-name, or linked from a Yelp business
_BOBA_CAMIS = """
    select camis from dohmh_establishments where boba_name_match
    union select camis from yelp_matches where camis is not null
"""

_YELP = """
    select y.yelp_id, y.name, y.is_closed as yelp_is_closed, y.rating, y.review_count,
           st_x(y.geom) lon, st_y(y.geom) lat,
           ym.camis
    from yelp_businesses y
    left join yelp_matches ym on ym.yelp_id = y.yelp_id
"""

_ESTABLISHMENTS = f"""
    select e.camis, e.dba, e.boro, e.boba_name_match,
           e.first_inspection_date, e.last_inspection_date,
           e.closed_flag, e.closed_date, e.reopened_date,
           st_x(e.geom) lon, st_y(e.geom) lat
    from dohmh_establishments e
    where e.camis in ({_BOBA_CAMIS})
"""

# DOHMH `boro` fallback, used only when a point sits just outside the polygons
# (e.g. a shoreline geocode). Borough otherwise comes from point-in-polygon.
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
    boroughs (Yelp's radius search also pulls in Nassau / Westchester / NJ)."""
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


def _first_seen(est) -> tuple[dt.date | None, str | None]:
    """Earliest *evidence* the shop existed -- NOT an opening date. DOHMH's first
    inspection (pre-permit where available) lags the true opening by the permit
    gap, so this is 'operating by at least this date'."""
    if est is not None and pd.notna(est.first_inspection_date):
        return est.first_inspection_date, "dohmh_first_inspection"
    return None, None


def _last_seen(est) -> dt.date | None:
    """Latest evidence the shop existed."""
    if est is not None and pd.notna(est.last_inspection_date):
        return est.last_inspection_date
    return None


def _closed(est, yb, today) -> tuple[dt.date | None, str | None, str, str]:
    """-> (closed_date, closed_source, status, status_basis). yb = Yelp row or None."""
    yelp = bool(yb.yelp_is_closed) if yb is not None and pd.notna(yb.yelp_is_closed) else None
    # --- closure signals, strongest first ---
    if est is not None and est.closed_flag and pd.notna(est.closed_date):
        reopened = pd.notna(est.reopened_date) and est.reopened_date >= est.closed_date
        if not reopened:
            return est.closed_date, "dohmh_closed_by_dohmh", "closed", "dohmh_closed_by_dohmh"
    if yelp is True:  # Yelp is current -- beats a stale inspection
        return None, "yelp", "closed", "yelp_closed"
    if est is not None and pd.notna(est.last_inspection_date):
        idle = (today - est.last_inspection_date).days
        if idle <= INACTIVE_DAYS:
            return None, None, "open", "dohmh_active"  # inspected within ~18 months
        if yelp is False:  # Yelp is current -- an inspection gap doesn't override it
            return None, None, "open", "yelp_open"
        if not est.closed_flag:  # 18+ months no inspection, nothing says open -> can't tell
            return None, None, "unknown", "dohmh_inactive"
    # --- positive "open" signal, else unknown ---
    if yelp is False:
        return None, None, "open", "yelp_open"
    return None, None, "unknown", "none"


def _identified_by(yb, est) -> str:
    if yb is not None:
        return "yelp_category"  # came from Yelp's curated bubbletea discovery
    return "name_pattern"  # a boba-name DOHMH CAMIS not in Yelp


def _dates(est, yb, today):
    first_d, first_src = _first_seen(est)
    last_d = _last_seen(est)
    closed_d, closed_src, status, basis = _closed(est, yb, today)
    if first_d and closed_d and first_d > closed_d:  # contradiction -> drop the weaker
        first_d, first_src = None, None
    return first_d, first_src, last_d, closed_d, closed_src, status, basis


def _coord(*vals):
    for v in vals:
        if v is not None and pd.notna(v):
            return float(v)
    return None


def _shop(*, est, yb, today) -> dict:
    first_d, first_src, last_d, c_d, c_s, status, basis = _dates(est, yb, today)
    name = (yb.name if yb is not None else None) or (est.dba if est is not None else None)
    return {
        "name": name,
        "camis": est.Index if est is not None else None,
        "yelp_id": yb.yelp_id if yb is not None else None,
        # geometry: Yelp point, then DOHMH
        "lon": _coord(
            yb.lon if yb is not None else None,
            est.lon if est is not None else None,
        ),
        "lat": _coord(
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
        "identified_by": _identified_by(yb, est),
    }


def _dedup(shops: list[dict]) -> list[dict]:
    """Collapse near-identical rows (same normalised name, within ~60 m) -- e.g. a
    Yelp business and a boba-name CAMIS for the same shop that didn't link. Keep
    the richest row and union the ids onto it."""
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
            for k in ("camis", "yelp_id"):
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
    today = dt.date.today()

    est_by_camis = {r.Index: r for r in est_df.itertuples(name="Est")}
    est_used: set[str] = set()
    shops: list[dict] = []
    n_yelp = n_dohmh = 0

    # 1. Yelp businesses (primary discovery)
    for yb in yelp_df.itertuples(index=False, name="Y"):
        est = est_by_camis.get(yb.camis) if isinstance(yb.camis, str) else None
        if est is not None:
            est_used.add(est.Index)
        shops.append(_shop(est=est, yb=yb, today=today))
        n_yelp += 1

    # 2. boba-name CAMIS not already seeded via Yelp
    for est in est_by_camis.values():
        if est.Index in est_used or not bool(est.boba_name_match):
            continue
        shops.append(_shop(est=est, yb=None, today=today))
        n_dohmh += 1

    shops = _assign_boroughs(shops)
    shops = _dedup(shops)

    log.info("identified_by: %s", dict(Counter(s["identified_by"] for s in shops)))
    log.info(
        "%d boba shops (seeds: %d Yelp, %d DOHMH-only)",
        len(shops),
        n_yelp,
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
    with_date = d["first_seen_date"].notna().sum()
    log.info(
        "currently: %d open, %d closed, %d unknown  (%d of %d have a first-seen date)",
        opn.sum(),
        (d["status"] == "closed").sum(),
        (d["status"] == "unknown").sum(),
        with_date,
        len(d),
    )
    log.info("by borough:\n%s", d.groupby("borough")["status"].value_counts().to_string())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since-year", type=int, default=SINCE_YEAR)
    args = parser.parse_args(argv)
    run(since_year=args.since_year)


if __name__ == "__main__":
    main()
