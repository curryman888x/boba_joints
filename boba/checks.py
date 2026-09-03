"""Post-pipeline invariants. Each query counts rows that VIOLATE the rule; 0 == pass."""

from __future__ import annotations

import sys

from sqlalchemy import text

from boba.db import engine
from boba.log import get_logger, setup

log = get_logger("boba.checks")

INVARIANTS: list[tuple[str, str]] = [
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
        "dohmh latest_grade is a real letter grade",
        "select count(*) from dohmh_establishments "
        "where latest_grade is not null and latest_grade not in ('A', 'B', 'C')",
    ),
    (
        "dohmh latest_score is non-negative",
        "select count(*) from dohmh_establishments where latest_score < 0",
    ),
    (
        "boba_shops.status in (open,closed,unknown)",
        "select count(*) from boba_shops where status not in ('open', 'closed', 'unknown')",
    ),
    (
        "boba_shops first_seen_date <= last_seen_date",
        "select count(*) from boba_shops where first_seen_date is not null "
        "and last_seen_date is not null and last_seen_date < first_seen_date",
    ),
    (
        "boba_shops has at least one source id",
        "select count(*) from boba_shops where camis is null and yelp_id is null",
    ),
    (
        "boba_shops closed_date only when status = closed",
        "select count(*) from boba_shops where closed_date is not null and status <> 'closed'",
    ),
    (
        "yelp_matches reference a yelp_business",
        "select count(*) from yelp_matches m "
        "left join yelp_businesses b on b.yelp_id = m.yelp_id where b.yelp_id is null",
    ),
    (
        "yelp_matches.camis resolves",
        "select count(*) from yelp_matches m "
        "left join dohmh_establishments e on e.camis = m.camis "
        "where m.camis is not null and e.camis is null",
    ),
    (
        "every ok ingest_run recorded a kept_count",
        "select count(*) from ingest_runs where status = 'ok' and kept_count is null",
    ),
]


def run(bind=None) -> int:
    failed = 0
    with (bind or engine).connect() as conn:
        for label, sql in INVARIANTS:
            n = conn.execute(text(sql)).scalar_one()
            if n:
                failed += 1
                log.error("FAIL  %s  (%d rows)", label, n)
            else:
                log.info("ok    %s", label)
    if failed:
        log.error("%d invariant(s) violated", failed)
    else:
        log.info("all invariants hold")
    return failed


def main() -> None:
    setup()
    sys.exit(1 if run() else 0)


if __name__ == "__main__":
    main()
