"""Validation contracts for the Yelp and DOHMH sources, plus the ingest manifest."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from boba.models import IngestRun

try:
    import pandera.pandas as pa
    from pandera.pandas import Check, Column, DataFrameSchema
except ImportError:  # older pandera
    import pandera as pa
    from pandera import Check, Column, DataFrameSchema


class ContractViolation(ValueError):
    """A source record or frame broke an assumption the pipeline relies on."""


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (ValueError, TypeError):  # arrays / nested
        return False


def _as_dict(v: Any) -> dict:
    return dict(v) if isinstance(v, Mapping) else {}


def _as_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, (str, bytes, Mapping)):
        return [v]
    if hasattr(v, "tolist"):  # numpy array from pyarrow
        return list(v.tolist())
    if isinstance(v, Sequence):
        return list(v)
    return [v]


# --- Yelp business record ---------------------------------------------


class YelpBusinessRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    yelp_id: str
    name: str | None = None
    is_closed: bool | None = None
    rating: float | None = None
    review_count: int | None = None
    price: str | None = None
    phone: str | None = None
    url: str | None = None
    categories: list[dict] = Field(default_factory=list)
    address: str | None = None
    city: str | None = None
    zip: str | None = None
    lon: float
    lat: float

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        d = {k: (None if _is_missing(v) else v) for k, v in dict(data).items()}
        d["yelp_id"] = d.get("id")
        coords = _as_dict(d.get("coordinates"))
        d["lon"], d["lat"] = coords.get("longitude"), coords.get("latitude")
        loc = _as_dict(d.get("location"))
        d["address"] = loc.get("address1")
        d["city"] = loc.get("city")
        d["zip"] = loc.get("zip_code")
        cats = _as_list(d.get("categories"))
        d["categories"] = [c for c in cats if isinstance(c, Mapping)]
        if d.get("url"):
            d["url"] = str(d["url"]).split("?")[0]
        return d


def parse_yelp_business(raw: Mapping[str, Any]) -> YelpBusinessRecord:
    try:
        return YelpBusinessRecord.model_validate(raw)
    except ValidationError as exc:
        raise ContractViolation(str(exc)) from exc


# --- DOHMH inspection frame ---------------------------------------------

KNOWN_DOHMH_ACTIONS = frozenset(
    {
        "Violations were cited in the following area(s).",
        "No violations were recorded at the time of this inspection.",
        "Establishment Closed by DOHMH. Violations were cited in the following area(s) "
        "and those requiring immediate action were addressed.",
        "Establishment re-opened by DOHMH.",
        "Establishment re-closed by DOHMH.",
    }
)

KNOWN_BOROUGHS = frozenset(
    {"Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "0", "Missing"}
)

dohmh_frame_schema = DataFrameSchema(
    {
        "camis": Column(str, nullable=False, coerce=True, checks=Check.str_matches(r"^\s*\d+\s*$")),
        "dba": Column(str, nullable=True, coerce=True, required=False),
        "boro": Column(
            str,
            nullable=True,
            coerce=True,
            required=False,
            checks=Check.isin(KNOWN_BOROUGHS, raise_warning=True, name="known_borough"),
        ),
        "cuisine_description": Column(str, nullable=True, coerce=True, required=False),
        "inspection_date": Column("datetime64[ns]", nullable=True, coerce=True, required=False),
        "record_date": Column("datetime64[ns]", nullable=True, coerce=True, required=False),
        "action": Column(
            str,
            nullable=True,
            coerce=True,
            required=False,
            checks=Check.isin(KNOWN_DOHMH_ACTIONS, raise_warning=True, name="known_action"),
        ),
        "latitude": Column(
            float,
            nullable=True,
            coerce=True,
            required=False,
            checks=Check.in_range(40.3, 41.0, raise_warning=True, name="nyc_lat"),
        ),
        "longitude": Column(
            float,
            nullable=True,
            coerce=True,
            required=False,
            checks=Check.in_range(-74.35, -73.6, raise_warning=True, name="nyc_lon"),
        ),
    },
    strict=False,
    coerce=True,
    name="dohmh_inspection_frame",
)


def validate_dohmh_frame(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) == 0:
        raise ContractViolation("DOHMH frame is empty -- the Socrata pull returned nothing")
    try:
        return dohmh_frame_schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:  # type: ignore[attr-defined]
        raise ContractViolation(
            f"DOHMH frame failed contract:\n{exc.failure_cases.to_string(max_rows=30)}"
        ) from exc


# --- Ingest manifest ---------------------------------------------------


@contextmanager
def ingest_run(session: Session, source: str, *, source_version: str | None = None):
    run = IngestRun(source=source, source_version=source_version, status="running")
    session.add(run)
    session.commit()
    try:
        yield run
    except BaseException as exc:
        # the work transaction may be aborted; clear it before recording the failure
        session.rollback()
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = datetime.now(UTC)
        try:
            session.add(run)
            session.commit()
        except Exception:  # don't mask the original error
            session.rollback()
        raise
    run.status = "ok"
    run.finished_at = datetime.now(UTC)
    session.commit()


def last_successful_run(session: Session, source: str) -> IngestRun | None:
    return session.scalars(
        select(IngestRun)
        .where(IngestRun.source == source, IngestRun.status == "ok")
        .order_by(IngestRun.started_at.desc())
        .limit(1)
    ).first()


def drift_warnings(prev: IngestRun | None, curr: IngestRun) -> list[str]:
    if prev is None:
        return []
    out: list[str] = []
    if prev.kept_count and curr.kept_count is not None:
        change = (curr.kept_count - prev.kept_count) / prev.kept_count
        if abs(change) >= 0.25:
            out.append(
                f"kept_count {prev.kept_count} -> {curr.kept_count} ({change:+.0%}) "
                f"since {prev.started_at:%Y-%m-%d}"
            )
    if prev.min_date and curr.min_date and curr.min_date < prev.min_date:
        out.append(f"coverage now reaches back to {curr.min_date} (was {prev.min_date})")
    if prev.source_version and curr.source_version and prev.source_version != curr.source_version:
        out.append(f"source version {prev.source_version} -> {curr.source_version}")
    return out
