"""Validation contracts for the Overture and DOHMH sources, plus the ingest manifest."""

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
    field_validator,
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


# --- Overture place record ------------------------------------------------

KNOWN_OPERATING_STATUS = frozenset({"open", "permanently_closed", "closed_temporarily"})
_NYC_SANITY = (-74.30, 40.45, -73.65, 40.95)  # min_lon, min_lat, max_lon, max_lat


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


def _first_dict(seq: Any) -> dict:
    for item in _as_list(seq):
        if isinstance(item, Mapping):
            return dict(item)
    return {}


def _max_source_time(sources: Any) -> datetime | None:
    times = []
    for s in _as_list(sources):
        t = _as_dict(s).get("update_time")
        if t in (None, ""):
            continue
        try:
            ts = pd.Timestamp(t)
        except (ValueError, TypeError):
            continue
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        times.append(ts.to_pydatetime())
    return max(times) if times else None


class OverturePlaceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str | None = None
    category_primary: str | None = None
    categories_all: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    operating_status: str | None = None
    addr_freeform: str | None = None
    locality: str | None = None
    region: str | None = None
    postcode: str | None = None
    brand: str | None = None
    source_update_time: datetime | None = None
    lon: float
    lat: float

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        d = {k: (None if _is_missing(v) else v) for k, v in dict(data).items()}

        names = _as_dict(d.get("names"))
        d.setdefault("name", names.get("primary") or names.get("common"))

        if "categories" not in data:
            raise ContractViolation(
                f"Overture record {d.get('id')!r} is missing the `categories` field entirely"
            )
        cats = _as_dict(d.get("categories"))  # null value is fine: place with no category
        primary = cats.get("primary") or cats.get("main")
        d["category_primary"] = primary
        d["categories_all"] = [c for c in [primary, *_as_list(cats.get("alternate"))] if c]

        addr = _first_dict(d.get("addresses"))
        d["addr_freeform"] = addr.get("freeform")
        d["locality"] = addr.get("locality")
        d["region"] = addr.get("region")
        d["postcode"] = addr.get("postcode")

        brand = _as_dict(d.get("brand"))
        d["brand"] = _as_dict(brand.get("names")).get("primary") or brand.get("wikidata") or None

        d["source_update_time"] = _max_source_time(d.get("sources"))
        return d

    @field_validator("operating_status")
    @classmethod
    def _known_status(cls, v: str | None) -> str | None:
        if v is not None and v not in KNOWN_OPERATING_STATUS:
            raise ContractViolation(
                f"unknown Overture operating_status {v!r} (known: {sorted(KNOWN_OPERATING_STATUS)})"
            )
        return v

    @model_validator(mode="after")
    def _inside_nyc(self) -> OverturePlaceRecord:
        lo_lon, lo_lat, hi_lon, hi_lat = _NYC_SANITY
        if not (lo_lon <= self.lon <= hi_lon and lo_lat <= self.lat <= hi_lat):
            raise ContractViolation(
                f"place {self.id} at ({self.lon:.4f}, {self.lat:.4f}) is outside the NYC box"
            )
        return self


def parse_overture_place(raw: Mapping[str, Any]) -> OverturePlaceRecord:
    try:
        return OverturePlaceRecord.model_validate(raw)
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
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = datetime.now(UTC)
        session.commit()
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
