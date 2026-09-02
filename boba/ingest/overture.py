"""Overture Maps `place` ingest.

download 5-borough extract -> category pre-filter -> validate each candidate via
the pydantic contract -> keep the ones that pass ``overture_is_boba`` -> upsert
``overture_places`` (+ a per-release row in ``overture_place_snapshots``).
Writes an ``ingest_runs`` manifest row and logs drift vs the last good run.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

import geopandas as gpd
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import text

from boba.config import BOBA_CATEGORIES, BOBA_FALLBACK_CATEGORIES, NYC_BBOX, data_dir
from boba.contracts import (
    ContractViolation,
    drift_warnings,
    ingest_run,
    last_successful_run,
    parse_overture_place,
)
from boba.db import SessionLocal, upsert
from boba.filters import overture_is_boba
from boba.log import get_logger, setup
from boba.models import OverturePlace, OverturePlaceSnapshot

log = get_logger("boba.ingest.overture")

# if more than this fraction of pre-filtered rows fail the contract, treat it as
# schema drift and abort rather than silently dropping shops.
_MAX_VIOLATION_RATE = 0.10

# The NYC bbox rectangle also covers a chunk of NJ (Jersey City, Hoboken, ...).
# `region` is fully populated in Overture, so drop anything explicitly out of NY.
_NY_REGIONS = {"NY", "New York", None}


def _overturemaps_cmd() -> list[str]:
    exe = shutil.which("overturemaps")
    return [exe] if exe else [sys.executable, "-m", "overturemaps"]


def _resolve_release(release: str | None) -> str | None:
    if release:
        return release
    try:
        out = subprocess.run(
            [*_overturemaps_cmd(), "releases", "latest"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, OSError):
        log.warning("could not resolve latest Overture release")
        return None


def _download(release: str | None, out, refresh: bool) -> None:
    if out.exists() and not refresh:
        log.info("using cached %s (%.1f MB)", out.name, out.stat().st_size / 1e6)
        return
    cmd = [
        *_overturemaps_cmd(),
        "download",
        "-t",
        "place",
        "-f",
        "geoparquet",
        "--bbox",
        ",".join(str(x) for x in NYC_BBOX),
        "-o",
        str(out),
    ]
    if release:
        cmd += ["-r", release]
    log.info("downloading Overture places: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    log.info("downloaded %.1f MB", out.stat().st_size / 1e6)


def _categories_of(v) -> list[str]:
    if not isinstance(v, dict):
        return []
    alt = v.get("alternate")
    alt = list(alt.tolist()) if hasattr(alt, "tolist") else (list(alt) if alt else [])
    return [c for c in [v.get("primary") or v.get("main"), *alt] if c]


def _prefilter(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    wanted = BOBA_CATEGORIES | BOBA_FALLBACK_CATEGORIES
    keep = gdf["categories"].map(lambda v: bool(set(_categories_of(v)) & wanted))
    return gdf[keep]


def _rows(records, release: str | None) -> tuple[list[dict], list[dict]]:
    places, snaps = [], []
    for r in records:
        geom = from_shape(Point(r.lon, r.lat), srid=4326)
        places.append(
            {
                "id": r.id,
                "name": r.name,
                "category_primary": r.category_primary,
                "categories": {"primary": r.category_primary, "all": r.categories_all},
                "brand": r.brand,
                "confidence": r.confidence,
                "operating_status": r.operating_status,
                "addr_freeform": r.addr_freeform,
                "locality": r.locality,
                "region": r.region,
                "postcode": r.postcode,
                "source_update_time": r.source_update_time,
                "overture_release": release,
                "first_seen_release": release,
                "last_seen_release": release,
                "geom": geom,
            }
        )
        snaps.append(
            {
                "release": release or "unknown",
                "place_id": r.id,
                "name": r.name,
                "category_primary": r.category_primary,
                "operating_status": r.operating_status,
                "confidence": r.confidence,
                "geom": geom,
            }
        )
    return places, snaps


def run(release: str | None = None, refresh: bool = False) -> None:
    setup()
    release = _resolve_release(release)
    out = data_dir() / f"overture_places_{release or 'latest'}.parquet"
    _download(release, out, refresh)

    gdf = gpd.read_parquet(out)
    total = len(gdf)
    cand = _prefilter(gdf)
    log.info("read %d places; %d after category pre-filter", total, len(cand))

    kept, violations, out_of_state = [], 0, 0
    for _, row in cand.iterrows():
        raw = row.drop(labels="geometry").to_dict()
        raw["lon"], raw["lat"] = row.geometry.x, row.geometry.y
        try:
            rec = parse_overture_place(raw)
        except ContractViolation as exc:
            violations += 1
            log.warning("contract: %s", str(exc).splitlines()[0][:200])
            continue
        if not overture_is_boba(rec):
            continue
        if rec.region not in _NY_REGIONS:
            out_of_state += 1
            continue
        kept.append(rec)

    if len(cand) and violations / len(cand) > _MAX_VIOLATION_RATE:
        raise ContractViolation(
            f"{violations}/{len(cand)} Overture records failed the contract "
            "-- looks like schema drift, not stray data"
        )
    log.info(
        "%d boba places kept (%d contract violations, %d dropped as out-of-NY)",
        len(kept),
        violations,
        out_of_state,
    )

    place_rows, snap_rows = _rows(kept, release)
    with SessionLocal() as s:
        prev = last_successful_run(s, "overture")
        with ingest_run(s, "overture", source_version=release) as manifest:
            upsert(
                s,
                OverturePlace,
                place_rows,
                index_elements=["id"],
                update_cols=[c for c in place_rows[0] if c not in ("id", "first_seen_release")]
                if place_rows
                else None,
            )
            if release and snap_rows:
                upsert(
                    s,
                    OverturePlaceSnapshot,
                    snap_rows,
                    index_elements=["release", "place_id"],
                )
            # mark & sweep: overture_places mirrors the current boba inventory.
            # Vanished places stay recoverable in overture_place_snapshots.
            # Guard: skip the sweep if this run is suspiciously thin vs the last.
            swept = 0
            kept_ids = [r.id for r in kept]
            thin = prev and prev.kept_count and len(kept_ids) < 0.5 * prev.kept_count
            if kept_ids and not thin:
                swept = s.execute(
                    text("delete from overture_places where not (id = any(:ids))"),
                    {"ids": kept_ids},
                ).rowcount
                if swept:
                    log.info("swept %d places no longer in the current set", swept)
            elif thin:
                log.warning(
                    "skipping sweep: only %d kept vs %d last run", len(kept_ids), prev.kept_count
                )
            manifest.row_count = total
            manifest.kept_count = len(kept)
            manifest.detail = {
                "candidates": int(len(cand)),
                "violations": violations,
                "dropped_out_of_ny": out_of_state,
                "swept": swept,
            }
        for w in drift_warnings(prev, manifest):
            log.warning("drift: %s", w)
    log.info("overture ingest complete")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default=None, help="Overture release (default: latest)")
    parser.add_argument("--refresh", action="store_true", help="re-download even if cached")
    args = parser.parse_args(argv)
    run(release=args.release, refresh=args.refresh)


if __name__ == "__main__":
    main()
