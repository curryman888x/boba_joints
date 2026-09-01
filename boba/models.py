"""Database schema.

Three layers:
  * raw ingest       -- overture_places, dohmh_establishments, dohmh_inspections
  * linking          -- place_matches
  * derived analysis -- boba_shops, status_events

`boba_shops` + `status_events` are recomputed by boba/analyze.py from the raw
tables; the raw tables are the source of truth.
"""
from __future__ import annotations

from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Deterministic constraint/index names so autogenerate output is stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _point(nullable: bool = True) -> Geometry:
    """A fresh POINT/4326 geometry type per column (GeoAlchemy2 keeps per-column
    state for spatial-index creation, so instances must not be shared)."""
    return Geometry(geometry_type="POINT", srid=4326, spatial_index=True, nullable=nullable)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IngestRun(Base):
    """One row per ingest invocation -- the manifest that lets a later run detect
    that a source changed shape (schema, enum values, date coverage, volume)."""

    __tablename__ = "ingest_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'ok', 'failed')", name="status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, index=True)  # overture | dohmh
    status: Mapped[str] = mapped_column(String, server_default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[str | None] = mapped_column(String)  # e.g. Overture release id
    row_count: Mapped[int | None] = mapped_column(Integer)  # rows pulled from source
    kept_count: Mapped[int | None] = mapped_column(Integer)  # rows after the boba filter
    min_date: Mapped[date | None] = mapped_column(Date)  # source coverage floor
    max_date: Mapped[date | None] = mapped_column(Date)  # source coverage ceiling
    detail: Mapped[dict | None] = mapped_column(JSONB)  # free-form manifest extras
    error: Mapped[str | None] = mapped_column(String)


class OverturePlace(Base):
    """Latest-release Overture `place` records inside the NYC bbox that look like boba shops."""

    __tablename__ = "overture_places"
    __table_args__ = (
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)  # GERS id
    name: Mapped[str | None] = mapped_column(String)
    category_primary: Mapped[str | None] = mapped_column(String, index=True)
    categories: Mapped[dict | None] = mapped_column(JSONB)
    brand: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[float | None] = mapped_column(Float)
    operating_status: Mapped[str | None] = mapped_column(String, index=True)
    addr_freeform: Mapped[str | None] = mapped_column(String)
    locality: Mapped[str | None] = mapped_column(String)
    region: Mapped[str | None] = mapped_column(String)
    postcode: Mapped[str | None] = mapped_column(String)
    website: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    source_update_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overture_release: Mapped[str | None] = mapped_column(String, index=True)
    first_seen_release: Mapped[str | None] = mapped_column(String)
    last_seen_release: Mapped[str | None] = mapped_column(String)
    geom: Mapped[object] = mapped_column(_point(nullable=False))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OverturePlaceSnapshot(Base):
    """Per-release slim snapshot, so we can diff Overture releases for churn signal."""

    __tablename__ = "overture_place_snapshots"

    release: Mapped[str] = mapped_column(String, primary_key=True)
    place_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    category_primary: Mapped[str | None] = mapped_column(String)
    operating_status: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[object | None] = mapped_column(_point())


class DohmhEstablishment(Base):
    """One row per CAMIS (DOHMH permitted food establishment)."""

    __tablename__ = "dohmh_establishments"
    __table_args__ = (
        CheckConstraint(
            "closed_flag is false or closed_date is not null",
            name="closed_date",
        ),
    )

    camis: Mapped[str] = mapped_column(String, primary_key=True)
    dba: Mapped[str | None] = mapped_column(String, index=True)
    boro: Mapped[str | None] = mapped_column(String)
    building: Mapped[str | None] = mapped_column(String)
    street: Mapped[str | None] = mapped_column(String)
    zipcode: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    cuisine_description: Mapped[str | None] = mapped_column(String, index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[object | None] = mapped_column(_point())
    # Derived from the inspection history (see analyze.py / ingest/dohmh.py):
    first_inspection_date: Mapped[date | None] = mapped_column(Date)  # opened-by proxy
    last_inspection_date: Mapped[date | None] = mapped_column(Date)
    last_record_date: Mapped[date | None] = mapped_column(Date)
    closed_flag: Mapped[bool] = mapped_column(Boolean, server_default="false", index=True)
    closed_date: Mapped[date | None] = mapped_column(Date)
    reopened_date: Mapped[date | None] = mapped_column(Date)
    boba_name_match: Mapped[bool] = mapped_column(
        Boolean, server_default="false", index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    inspections: Mapped[list["DohmhInspection"]] = relationship(
        back_populates="establishment", cascade="all, delete-orphan"
    )


class DohmhInspection(Base):
    """Distinct inspection/violation rows for an establishment."""

    __tablename__ = "dohmh_inspections"
    __table_args__ = (
        UniqueConstraint(
            "camis",
            "inspection_date",
            "violation_code",
            "action",
            name="uq_dohmh_inspection_row",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camis: Mapped[str] = mapped_column(
        ForeignKey("dohmh_establishments.camis", ondelete="CASCADE"), index=True
    )
    inspection_date: Mapped[date | None] = mapped_column(Date, index=True)
    action: Mapped[str | None] = mapped_column(String)
    critical_flag: Mapped[str | None] = mapped_column(String)
    score: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String)
    grade_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    violation_code: Mapped[str | None] = mapped_column(String)

    establishment: Mapped[DohmhEstablishment] = relationship(back_populates="inspections")


class PlaceMatch(Base):
    """Candidate link between an Overture place and a DOHMH CAMIS."""

    __tablename__ = "place_matches"
    __table_args__ = (
        UniqueConstraint("overture_id", "camis", name="uq_place_match"),
        CheckConstraint(
            "score is null or (score >= 0 and score <= 100)",
            name="score",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    overture_id: Mapped[str] = mapped_column(
        ForeignKey("overture_places.id", ondelete="CASCADE"), index=True
    )
    camis: Mapped[str] = mapped_column(
        ForeignKey("dohmh_establishments.camis", ondelete="CASCADE"), index=True
    )
    score: Mapped[float | None] = mapped_column(Float)  # 0-100 blended
    method: Mapped[str | None] = mapped_column(String)  # name_addr | name_dist | manual
    distance_m: Mapped[float | None] = mapped_column(Float)
    name_similarity: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BobaShop(Base):
    """Canonical merged boba shop with best-estimate opened / closed dates."""

    __tablename__ = "boba_shops"
    __table_args__ = (
        CheckConstraint(
            "status in ('open', 'closed', 'unknown')", name="status"
        ),
        CheckConstraint(
            "opened_precision is null or opened_precision in ('month', 'quarter', 'year')",
            name="opened_precision",
        ),
        CheckConstraint(
            "opened_date is null or closed_date is null or closed_date >= opened_date",
            name="date_order",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String)
    overture_id: Mapped[str | None] = mapped_column(
        ForeignKey("overture_places.id", ondelete="SET NULL"), index=True
    )
    camis: Mapped[str | None] = mapped_column(
        ForeignKey("dohmh_establishments.camis", ondelete="SET NULL"), index=True
    )
    geom: Mapped[object | None] = mapped_column(_point())
    borough: Mapped[str | None] = mapped_column(String, index=True)
    opened_date: Mapped[date | None] = mapped_column(Date)
    opened_source: Mapped[str | None] = mapped_column(String)
    opened_precision: Mapped[str | None] = mapped_column(String)  # month | quarter | year
    closed_date: Mapped[date | None] = mapped_column(Date)
    closed_source: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String, index=True)  # open | closed | unknown
    notes: Mapped[str | None] = mapped_column(String)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    events: Mapped[list["StatusEvent"]] = relationship(
        back_populates="boba_shop", cascade="all, delete-orphan"
    )


class StatusEvent(Base):
    """Timeline of opened / closed / reopened events for a boba shop."""

    __tablename__ = "status_events"
    __table_args__ = (
        CheckConstraint(
            "event_type in ('opened', 'closed', 'reopened')",
            name="event_type",
        ),
        CheckConstraint(
            "confidence is null or confidence in ('high', 'proxy', 'low')",
            name="confidence",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    boba_shop_id: Mapped[int] = mapped_column(
        ForeignKey("boba_shops.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String)  # opened | closed | reopened
    event_date: Mapped[date | None] = mapped_column(Date, index=True)
    source: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[str | None] = mapped_column(String)  # high | proxy | low
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
