"""NYC DOHMH Restaurant Inspection Results ingest.

Socrata is queried with a wide `dba` LIKE net + a few beverage cuisines, then the
frame is validated (``validate_dohmh_frame``) and tightened. We load:

* ``dohmh_inspections``    -- distinct (camis, inspection_date, violation_code, action) rows
* ``dohmh_establishments`` -- one row per CAMIS: identity fields from the latest
  inspection row, derived fields (first/last inspection, closed/reopened,
  boba_name_match) recomputed in SQL from whatever inspections are loaded.

Always a full pull: ``record_date`` is a per-publish dataset timestamp (~uniform
across rows) so it can't drive a row-level cursor; the candidate set is small
(~28k rows) and upserts dedupe.
"""

from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import text

from boba.config import BOBA_NAME_PATTERN, DOHMH_NULL_DATE, DOHMH_SOCRATA_BASE, data_dir
from boba.contracts import (
    ContractViolation,
    drift_warnings,
    ingest_run,
    last_successful_run,
    validate_dohmh_frame,
)
from boba.db import SessionLocal, upsert
from boba.log import get_logger, setup
from boba.models import DohmhEstablishment, DohmhInspection
from boba.net import socrata_session

log = get_logger("boba.ingest.dohmh")

_NAME_RE = re.compile(BOBA_NAME_PATTERN)
_PAGE = 50000

# Wide net for the Socrata query -- chains + keyword fragments + beverage cuisines.
_LIKE_TERMS = [
    "BOBA",
    "BUBBLE TEA",
    "BUBBLE T",
    "MILK TEA",
    "MILKTEA",
    "PEARL TEA",
    "TAPIOCA",
    "KUNG FU TEA",
    "GONG CHA",
    "CHATIME",
    "SHARETEA",
    "SHARE TEA",
    "TIGER SUGAR",
    "HAPPY LEMON",
    "YI FANG",
    "XING FU TANG",
    "MACHI MACHI",
    "THE ALLEY",
    "TEN REN",
    "VIVI",
    "MEET FRESH",
    "COCO FRESH",
    "QUICKLY",
    "COMEBUY",
    "TASTEA",
    "OMOMO",
    "BOBA GUYS",
    "TP TEA",
    "MOGE TEE",
    "POSSMEI",
    "WANPO",
    "TRUEDAN",
]
_CUISINES = [
    "Coffee/Tea",
    "Juice, Smoothies, Fruit Salads",
    "Bottled beverages, including water, sodas, juices, etc.",
]

_IDENTITY_COLS = [
    "dba",
    "boro",
    "building",
    "street",
    "zipcode",
    "phone",
    "cuisine_description",
    "latitude",
    "longitude",
    "geom",
    "boba_name_match",
]


def _where() -> str:
    clause = " OR ".join(f"upper(dba) like '%{t}%'" for t in _LIKE_TERMS)
    clause += " OR cuisine_description in(" + ",".join(f"'{c}'" for c in _CUISINES) + ")"
    return f"({clause})"


def _fetch(where: str) -> pd.DataFrame:
    sess = socrata_session()
    frames, offset = [], 0
    while True:
        params = {"$where": where, "$limit": _PAGE, "$offset": offset, "$order": ":id"}
        r = sess.get(DOHMH_SOCRATA_BASE, params=params, timeout=120)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        frames.append(pd.DataFrame(batch))
        offset += _PAGE
        log.info("  fetched %d rows", offset)
        if len(batch) < _PAGE:
            break
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("inspection_date", "record_date", "grade_date"):
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ("score", "latitude", "longitude"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df.loc[df["inspection_date"] == pd.Timestamp(DOHMH_NULL_DATE), "inspection_date"] = pd.NaT
    for col in ("latitude", "longitude"):
        df[col] = df[col].replace(0.0, np.nan)
    df["dba"] = df["dba"].fillna("").str.strip()
    return df


def _inspection_rows(df: pd.DataFrame) -> list[dict]:
    cols = [
        "camis",
        "inspection_date",
        "inspection_type",
        "action",
        "critical_flag",
        "score",
        "grade",
        "grade_date",
        "record_date",
        "violation_code",
    ]
    sub = df[[c for c in cols if c in df.columns]].copy()
    sub = sub.drop_duplicates(subset=["camis", "inspection_date", "violation_code", "action"])
    out = []
    for rec in sub.to_dict("records"):
        out.append(
            {
                k: (None if (isinstance(v, float) and pd.isna(v)) or v is pd.NaT else v)
                for k, v in rec.items()
            }
        )
    return out


def _establishment_identity_rows(df: pd.DataFrame) -> list[dict]:
    # newest inspection row per CAMIS wins for the identity fields
    latest = (
        df.sort_values("inspection_date").drop_duplicates("camis", keep="last").set_index("camis")
    )
    rows = []
    for camis, r in latest.iterrows():
        lat, lon = r.get("latitude"), r.get("longitude")
        geom = (
            from_shape(Point(float(lon), float(lat)), srid=4326)
            if pd.notna(lat) and pd.notna(lon)
            else None
        )
        rows.append(
            {
                "camis": str(camis),
                "dba": r["dba"] or None,
                "boro": r.get("boro"),
                "building": r.get("building"),
                "street": r.get("street"),
                "zipcode": r.get("zipcode"),
                "phone": r.get("phone"),
                "cuisine_description": r.get("cuisine_description"),
                "latitude": float(lat) if pd.notna(lat) else None,
                "longitude": float(lon) if pd.notna(lon) else None,
                "geom": geom,
                "boba_name_match": bool(_NAME_RE.search(r["dba"] or "")),
                "closed_flag": False,  # placeholder for INSERT; recomputed in SQL below
            }
        )
    return rows


_RECOMPUTE_SQL = text(
    """
    update dohmh_establishments e set
        first_inspection_date = s.first_insp,
        last_inspection_date  = s.last_insp,
        latest_grade          = s.latest_grade,
        latest_score          = s.latest_score,
        closed_flag           = s.ever_closed,
        closed_date           = s.closed_date,
        reopened_date         = s.reopened_date
    from (
        select
            camis,
            -- prefer the pre-permit / initial inspection (done right as a new shop
            -- opens); fall back to the earliest inspection of any type
            coalesce(
                min(inspection_date) filter (
                    where inspection_date is not null
                      and inspection_type ilike 'pre-permit%'
                ),
                min(inspection_date) filter (where inspection_date is not null)
            ) as first_insp,
            max(inspection_date) as last_insp,
            -- letter grade / score from the newest row that has one (grades: A/B/C only;
            -- 'P'/'Z'/'N' = pending / not yet graded -> left null)
            (array_agg(grade order by coalesce(grade_date, inspection_date) desc)
                filter (where grade in ('A', 'B', 'C')))[1] as latest_grade,
            (array_agg(score order by inspection_date desc)
                filter (where score is not null))[1] as latest_score,
            coalesce(bool_or(action ilike 'Establishment Closed by DOHMH%'), false) as ever_closed,
            min(inspection_date) filter (where action ilike 'Establishment Closed by DOHMH%')
                as closed_date,
            max(inspection_date) filter (where action ilike '%re-opened by DOHMH%')
                as reopened_date
        from dohmh_inspections
        group by camis
    ) s
    where e.camis = s.camis
    """
)


def run() -> None:
    setup()
    with SessionLocal() as s:
        prev = last_successful_run(s, "dohmh")

    raw = _fetch(_where())
    if raw.empty:
        raise ContractViolation("DOHMH Socrata pull returned nothing")

    raw.to_parquet(data_dir() / "dohmh_raw_last.parquet")
    # null the 0.0 coord sentinels before validation so the contract doesn't
    # warn on thousands of known placeholders
    for col in ("latitude", "longitude"):
        if col in raw:
            raw[col] = pd.to_numeric(raw[col], errors="coerce").replace(0.0, np.nan)
    df = _normalize(validate_dohmh_frame(raw))
    df = df[df["camis"].str.strip().str.match(r"^\d+$")]

    insp_rows = _inspection_rows(df)
    ident_rows = _establishment_identity_rows(df)
    boba_camis = {r["camis"] for r in ident_rows if r["boba_name_match"]}

    min_d = df["inspection_date"].min()
    max_rec = df["record_date"].max()
    log.info(
        "%d rows -> %d inspections, %d establishments (%d boba-name); inspection dates %s..%s",
        len(df),
        len(insp_rows),
        len(ident_rows),
        len(boba_camis),
        None if pd.isna(min_d) else min_d.date(),
        None if pd.isna(df["inspection_date"].max()) else df["inspection_date"].max().date(),
    )

    with SessionLocal() as s:
        with ingest_run(s, "dohmh", source_version="full") as m:
            upsert(
                s,
                DohmhEstablishment,
                ident_rows,
                index_elements=["camis"],
                update_cols=_IDENTITY_COLS,
            )
            upsert(
                s,
                DohmhInspection,
                insp_rows,
                index_elements=["camis", "inspection_date", "violation_code", "action"],
            )
            s.execute(_RECOMPUTE_SQL)
            m.row_count = len(df)
            m.kept_count = len(ident_rows)
            m.min_date = None if pd.isna(min_d) else min_d.date()
            m.max_date = None if pd.isna(max_rec) else max_rec.date()
            m.detail = {"boba_name_camis": len(boba_camis)}
        for w in drift_warnings(prev, m):
            log.warning("drift: %s", w)
    log.info("dohmh ingest complete")


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    run()


if __name__ == "__main__":
    main()
