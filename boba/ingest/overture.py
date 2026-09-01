"""Overture Maps ingest.

Plan:
  1. `overturemaps download -t place -f geoparquet --bbox <NYC_BBOX>` -> data/
     (optionally per historical release for churn signal).
  2. Read the GeoParquet (pyarrow / geopandas).
  3. Keep a row when:
       - its primary category is in config.BOBA_CATEGORIES, OR
       - its primary category is in config.BOBA_FALLBACK_CATEGORIES and the
         name matches config.BOBA_NAME_PATTERN.
  4. Upsert survivors into `overture_places`; write a slim row per release into
     `overture_place_snapshots` so releases can be diffed later.

Not implemented yet.
"""
from __future__ import annotations

import argparse


def run(release: str | None = None, refresh: bool = False) -> None:
    raise NotImplementedError("boba.ingest.overture.run")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default=None, help="Overture release (default: latest)")
    parser.add_argument("--refresh", action="store_true", help="re-download even if cached")
    args = parser.parse_args(argv)
    run(release=args.release, refresh=args.refresh)


if __name__ == "__main__":
    main()
