"""Build the canonical boba-shop list and the opened/closed timeline.

One ``boba_shops`` row per real-world shop, from three populations:

* Overture boba place + its matched CAMIS  (merged)
* Overture boba place with no CAMIS         (Overture-only)
* boba CAMIS (name-matched OR label-propagated) with no Overture match (DOHMH-only)

opened_date / closed_date are blended from the signals available, each tagged
with a source and precision. ``status_events`` gets an opened / closed / reopened
row per shop. Also prints a 2022-2026 summary and writes data/boba_status.csv.
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
from boba.models import BobaShop, StatusEvent

log = get_logger("boba.analyze")

SINCE_YEAR = 2022  # DOHMH inspection history floor; nothing credible before this
INACTIVE_DAYS = 550  # no inspection in ~18 months + not force-closed => likely closed

# CAMIS that are boba: name-matched OR linked to an Overture boba place (propagation)
_BOBA_CAMIS = """
    select camis from dohmh_establishments where boba_name_match
    union
    select camis from place_matches
"""

# highest-scoring match per CAMIS, for the DOHMH-only / merged join
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


def _opened(est, ov) -> tuple[dt.date | None, str | None, str | None]:
    if est is not None and pd.notna(est.first_inspection_date):
        return est.first_inspection_date, "dohmh_first_inspection", "month"
    # first_seen_release is only meaningful once a place has persisted across
    # >= 2 of our ingests (otherwise it's just "when we started collecting").
    # `source_update_time` is deliberately NOT used -- it's when Overture last
    # touched the record, which clusters in the current year and is not an opening.
    if ov is not None and ov.first_seen_release and ov.first_seen_release != ov.last_seen_release:
        rel = _release_date(ov.first_seen_release)
        if rel:
            return rel, "overture_first_release", "quarter"
    return None, None, None


def _closed(est, ov, today) -> tuple[dt.date | None, str | None, str]:
    # --- explicit closure signals ---
    if est is not None and est.closed_flag and pd.notna(est.closed_date):
        reopened = pd.notna(est.reopened_date) and est.reopened_date >= est.closed_date
        if not reopened:
            return est.closed_date, "dohmh_closed_by_dohmh", "closed"
    if ov is not None and ov.operating_status == "permanently_closed":
        d = ov.source_update_time.date() if pd.notna(ov.source_update_time) else None
        return d, "overture_permanently_closed", "closed"
    if est is not None and pd.notna(est.last_inspection_date):
        idle = (today - est.last_inspection_date).days
        if idle > INACTIVE_DAYS and not est.closed_flag:
            return est.last_inspection_date, "dohmh_inactive", "closed"
        return None, None, "open"  # inspected within ~18 months -> operating
    # --- positive "open" signal, else we genuinely don't know ---
    if ov is not None and ov.operating_status == "open":
        return None, None, "open"
    return None, None, "unknown"


def _identified_by(ov, est) -> str:
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


def _dates(est, ov, today):
    opened_d, opened_src, prec = _opened(est, ov)
    closed_d, closed_src, status = _closed(est, ov, today)
    # a weak opened proxy that postdates a real closure is noise -- drop it
    if opened_d and closed_d and opened_d > closed_d:
        opened_d, opened_src, prec = None, None, None
    return opened_d, opened_src, prec, closed_d, closed_src, status


def _coord(*vals):
    for v in vals:
        if v is not None and pd.notna(v):
            return float(v)
    return None


def _shop(*, name, oid, camis, lon, lat, dohmh_boro, est, ov, today) -> dict:
    o_d, o_s, prec, c_d, c_s, status = _dates(est, ov, today)
    return {
        "name": name,
        "overture_id": oid,
        "camis": camis,
        "lon": lon,
        "lat": lat,
        "borough": None,  # set by _assign_boroughs
        "_dohmh_boro": dohmh_boro,
        "opened_date": o_d,
        "opened_source": o_s,
        "opened_precision": prec,
        "closed_date": c_d,
        "closed_source": c_s,
        "status": status,
        "identified_by": _identified_by(ov, est),
    }


def run(since_year: int = SINCE_YEAR) -> None:
    setup()
    est_df = pd.read_sql(text(_ESTABLISHMENTS), engine).set_index("camis")
    ov_df = pd.read_sql(text(_OVERTURE), engine).set_index("overture_id")
    today = dt.date.today()

    est_used: set[str] = set()
    shops: list[dict] = []

    # merged (Overture place + matched CAMIS) and Overture-only
    for oid, ov in zip(ov_df.index, ov_df.itertuples(index=False, name="Ov"), strict=True):
        est = est_df.loc[ov.camis] if ov.camis in est_df.index else None
        if est is not None:
            est_used.add(ov.camis)
        shops.append(
            _shop(
                name=ov.name or (est.dba if est is not None else None),
                oid=oid,
                camis=ov.camis if isinstance(ov.camis, str) else None,
                lon=_coord(ov.lon, est.lon if est is not None else None),
                lat=_coord(ov.lat, est.lat if est is not None else None),
                dohmh_boro=est.boro if est is not None else None,
                est=est,
                ov=ov,
                today=today,
            )
        )

    # DOHMH-only boba CAMIS with no Overture match
    for camis, est in zip(est_df.index, est_df.itertuples(index=False, name="Est"), strict=True):
        if camis in est_used:
            continue
        shops.append(
            _shop(
                name=est.dba,
                oid=est.overture_id if isinstance(est.overture_id, str) else None,
                camis=camis,
                lon=_coord(est.lon),
                lat=_coord(est.lat),
                dohmh_boro=est.boro,
                est=est,
                ov=None,
                today=today,
            )
        )

    shops = _assign_boroughs(shops)

    merged = sum(1 for s in shops if s["overture_id"] and s["camis"])
    ov_only = sum(1 for s in shops if s["overture_id"] and not s["camis"])
    dohmh_only = sum(1 for s in shops if s["camis"] and not s["overture_id"])
    by_id = Counter(s["identified_by"] for s in shops)
    log.info("identified_by: %s", dict(by_id))
    log.info(
        "%d boba shops (%d merged, %d Overture-only, %d DOHMH-only)",
        len(shops),
        merged,
        ov_only,
        dohmh_only,
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
            shop = BobaShop(
                name=r["name"],
                overture_id=r["overture_id"],
                camis=r["camis"],
                geom=geom,
                borough=r["borough"],
                opened_date=r["opened_date"],
                opened_source=r["opened_source"],
                opened_precision=r["opened_precision"],
                closed_date=r["closed_date"],
                closed_source=r["closed_source"],
                status=r["status"],
                identified_by=r["identified_by"],
            )
            s.add(shop)
            s.flush()
            if r["opened_date"]:
                s.add(
                    StatusEvent(
                        boba_shop_id=shop.id,
                        event_type="opened",
                        event_date=r["opened_date"],
                        source=r["opened_source"],
                        confidence="high" if r["opened_precision"] == "month" else "proxy",
                    )
                )
            if r["closed_date"] and r["status"] == "closed":
                conf = "high" if r["closed_source"] == "dohmh_closed_by_dohmh" else "low"
                s.add(
                    StatusEvent(
                        boba_shop_id=shop.id,
                        event_type="closed",
                        event_date=r["closed_date"],
                        source=r["closed_source"],
                        confidence=conf,
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
    d["opened_year"] = pd.to_datetime(d["opened_date"], errors="coerce").dt.year
    d["closed_year"] = pd.to_datetime(d["closed_date"], errors="coerce").dt.year
    years = list(range(since_year, today.year + 1))
    op = d["opened_year"].value_counts().reindex(years, fill_value=0)
    cl = d["closed_year"].value_counts().reindex(years, fill_value=0)

    log.info("--- NYC boba shops, %d-%d ---", since_year, today.year)
    log.info("year   opened  closed   net")
    for y in years:
        log.info("%d   %6d  %6d  %+4d", y, op[y], cl[y], op[y] - cl[y])
    log.info(
        "currently: %d open, %d closed, %d unknown",
        (d["status"] == "open").sum(),
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
