"""Seed the `boroughs` table from NYC Open Data (Borough Boundaries, tqmj-j8zm)."""

from __future__ import annotations

from geoalchemy2.shape import from_shape
from shapely.geometry import shape
from sqlalchemy import text

from boba.db import SessionLocal
from boba.log import get_logger, setup
from boba.models import Borough
from boba.net import session_with_retries

log = get_logger("boba.seed")
# NYC Open Data "Borough Boundaries" (gthc-hcne), clipped to shoreline.
_URL = "https://data.cityofnewyork.us/resource/gthc-hcne.geojson"
_NAME_KEYS = ("boroname", "boro_name", "BoroName", "borough")


def run() -> None:
    setup()
    gj = session_with_retries().get(_URL, timeout=90).json()
    feats = gj.get("features", [])
    if len(feats) != 5:
        raise RuntimeError(f"expected 5 borough features, got {len(feats)}")

    with SessionLocal() as s:
        s.execute(text("truncate boroughs"))
        for feat in feats:
            props = feat["properties"]
            name = next((props[k] for k in _NAME_KEYS if props.get(k)), None)
            if not name:
                raise RuntimeError(f"no borough name in {props}")
            s.add(Borough(name=name.strip(), geom=from_shape(shape(feat["geometry"]), srid=4326)))
        s.commit()
    log.info("seeded 5 borough polygons")


if __name__ == "__main__":
    run()
