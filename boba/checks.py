"""Post-pipeline invariants. Each query counts rows that VIOLATE the rule; 0 == pass."""
from __future__ import annotations

import sys

from sqlalchemy import text

from boba.db import engine

INVARIANTS: list[tuple[str, str]] = [
    (
        "overture_places.geom is never null",
        "select count(*) from overture_places where geom is null",
    ),
    (
        "overture_places.confidence in [0,1]",
        "select count(*) from overture_places "
        "where confidence is not null and (confidence < 0 or confidence > 1)",
    ),
    (
        "place_matches.overture_id resolves",
        "select count(*) from place_matches m "
        "left join overture_places p on p.id = m.overture_id where p.id is null",
    ),
    (
        "place_matches.camis resolves",
        "select count(*) from place_matches m "
        "left join dohmh_establishments e on e.camis = m.camis where e.camis is null",
    ),
    (
        "place_matches.score in [0,100]",
        "select count(*) from place_matches "
        "where score is not null and (score < 0 or score > 100)",
    ),
    (
        "dohmh closed_flag implies closed_date",
        "select count(*) from dohmh_establishments where closed_flag and closed_date is null",
    ),
    (
        "dohmh first_inspection_date is not the 1900 sentinel",
        "select count(*) from dohmh_establishments "
        "where first_inspection_date is not null and first_inspection_date < date '2000-01-01'",
    ),
    (
        "dohmh first_inspection_date <= last_inspection_date",
        "select count(*) from dohmh_establishments "
        "where first_inspection_date is not null and last_inspection_date is not null "
        "and first_inspection_date > last_inspection_date",
    ),
    (
        "boba_shops.status in (open,closed,unknown)",
        "select count(*) from boba_shops where status not in ('open', 'closed', 'unknown')",
    ),
    (
        "boba_shops opened_date <= closed_date",
        "select count(*) from boba_shops "
        "where opened_date is not null and closed_date is not null and closed_date < opened_date",
    ),
    (
        "boba_shops has at least one source id",
        "select count(*) from boba_shops where overture_id is null and camis is null",
    ),
    (
        "status_events reference a boba_shop",
        "select count(*) from status_events e "
        "left join boba_shops s on s.id = e.boba_shop_id where s.id is null",
    ),
    (
        "every ok ingest_run recorded a row_count",
        "select count(*) from ingest_runs where status = 'ok' and row_count is null",
    ),
]


def run() -> int:
    failed = 0
    with engine.connect() as conn:
        for label, sql in INVARIANTS:
            n = conn.execute(text(sql)).scalar_one()
            mark = "ok  " if n == 0 else "FAIL"
            if n:
                failed += 1
            print(f"  [{mark}] {label}" + (f"  ({n} rows)" if n else ""))
    if failed:
        print(f"\n{failed} invariant(s) violated")
    else:
        print("\nall invariants hold")
    return failed


def main() -> None:
    sys.exit(1 if run() else 0)


if __name__ == "__main__":
    main()
