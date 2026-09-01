"""Link Overture places to DOHMH establishments.

Plan:
  For each Overture boba place, find DOHMH establishments within ~75 m
  (PostGIS ST_DWithin on geography) and score each candidate:
      name_similarity = rapidfuzz.token_set_ratio(overture.name, dohmh.dba)
      + address agreement (house number + street) bonus
      - distance penalty
  Keep the best candidate above a threshold as method='name_dist' (or
  'name_addr' when the address also agrees).  Write rows to `place_matches`.

  DOHMH establishments with boba_name_match=True and no Overture match are kept
  as DOHMH-only boba shops (these are mostly shops that closed before Overture
  existed) -- handled in analyze.py.

Not implemented yet.
"""
from __future__ import annotations

import argparse

NAME_SCORE_MIN = 70.0
MAX_DISTANCE_M = 75.0


def run(distance_m: float = MAX_DISTANCE_M, score_min: float = NAME_SCORE_MIN) -> None:
    raise NotImplementedError("boba.match.run")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance-m", type=float, default=MAX_DISTANCE_M)
    parser.add_argument("--score-min", type=float, default=NAME_SCORE_MIN)
    args = parser.parse_args(argv)
    run(distance_m=args.distance_m, score_min=args.score_min)


if __name__ == "__main__":
    main()
