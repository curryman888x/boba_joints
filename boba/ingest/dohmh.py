"""NYC DOHMH Restaurant Inspection Results ingest.

Dataset: config.DOHMH_DATASET_ID (Socrata).  One source row per violation per
inspection; we care about the (camis, inspection_date, action) shape.

Plan:
  1. Page the Socrata API (or bulk CSV) for all rows.  Optionally pre-filter to
     plausible boba candidates: cuisine in {Coffee/Tea, Juice/Smoothies, ...}
     OR dba matches config.BOBA_NAME_PATTERN.  (Full pull is ~a few hundred MB;
     candidate pull is small.)
  2. Upsert one `dohmh_establishments` row per CAMIS.
  3. Insert distinct `dohmh_inspections` rows.
  4. Derive per-establishment fields:
       first_inspection_date = min(inspection_date) excluding config.DOHMH_NULL_DATE
                               -> "opened by" proxy
       last_inspection_date  = max(inspection_date)
       closed_flag / closed_date  from action ILIKE '%Establishment Closed%'
       reopened_date              from action ILIKE '%re-opened%'
       boba_name_match            = dba ~* BOBA_NAME_PATTERN

Not implemented yet.
"""

from __future__ import annotations

import argparse


def run(candidates_only: bool = True, refresh: bool = False) -> None:
    raise NotImplementedError("boba.ingest.dohmh.run")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        dest="candidates_only",
        action="store_false",
        help="ingest every establishment, not just boba candidates",
    )
    parser.add_argument("--refresh", action="store_true", help="re-download even if cached")
    args = parser.parse_args(argv)
    run(candidates_only=args.candidates_only, refresh=args.refresh)


if __name__ == "__main__":
    main()
