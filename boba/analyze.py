"""Derive the canonical boba shop list and the opened/closed timeline.

Plan:
  1. Build one `boba_shops` row per real-world shop:
       - Overture place + its matched CAMIS  -> merged row
       - Overture place with no CAMIS        -> Overture-only row
       - boba_name_match CAMIS with no Overture match -> DOHMH-only row
  2. opened_date:
       - DOHMH first_inspection_date if present (precision: month), else
       - earliest Overture release the place appears in (precision: quarter), else
       - Overture source_update_time (precision: year, low confidence)
  3. closed_date / status:
       - DOHMH closed_date (high), else
       - Overture operating_status == 'permanently_closed' (proxy), else
       - present in an old Overture snapshot but absent from latest + no recent
         DOHMH inspection (low), else status='open'
  4. Emit `status_events` (opened / closed / reopened) and print a summary:
     openings and closings per year 2020..now, net change, still-open count,
     broken out by borough.  Also write data/boba_status.csv.

Not implemented yet.
"""

from __future__ import annotations

import argparse

SINCE_YEAR = 2020


def run(since_year: int = SINCE_YEAR) -> None:
    raise NotImplementedError("boba.analyze.run")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since-year", type=int, default=SINCE_YEAR)
    args = parser.parse_args(argv)
    run(since_year=args.since_year)


if __name__ == "__main__":
    main()
