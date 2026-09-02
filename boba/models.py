"""Database schema.

  * reference       -- boroughs (seeded), ingest_runs (manifest)
  * raw ingest      -- overture_places, dohmh_establishments, dohmh_inspections
  * linking         -- place_matches, yelp_status
  * derived         -- boba_shops

`boba_shops` is truncated and rebuilt by boba/analyze.py from the raw + linking
tables; those are the source of truth.
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


class Borough(Base):
    """The five NYC borough polygons (seeded from NYC Open Data). analyze.py does
    a point-in-polygon against these to assign a borough and drop non-NYC points."""

    __tablename__ = "boroughs"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    geom: Mapped[object] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=True)
    )


class IngestRun(Base):
    """One row per ingest invocation -- the manifest that lets a later run detect
    that a source changed shape (schema, enum values, date coverage, volume)."""

    __tablename__ = "ingest_runs"
    __table_args__ = (CheckConstraint("status in ('running', 'ok', 'failed')", name="status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, index=True)  # overture | dohmh
    status: Mapped[str] = mapped_column(String, server_default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
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
    boba_name_match: Mapped[bool] = mapped_column(Boolean, server_default="false", index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    inspections: Mapped[list[DohmhInspection]] = relationship(
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
            # so re-runs don't re-insert rows whose violation_code / action is NULL
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camis: Mapped[str] = mapped_column(
        ForeignKey("dohmh_establishments.camis", ondelete="CASCADE"), index=True
    )
    inspection_date: Mapped[date | None] = mapped_column(Date, index=True)
    inspection_type: Mapped[str | None] = mapped_column(String)  # "Pre-permit ... / Initial" etc.
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BobaShop(Base):
    """Canonical merged boba shop. Dates are *evidence bounds*, not lifecycle
    events: first_seen_date = earliest DOHMH inspection (or Overture release);
    last_seen_date = latest such evidence; closed_date is set only on a real
    closure signal. See docs/methodology.md."""

    __tablename__ = "boba_shops"
    __table_args__ = (
        CheckConstraint("status in ('open', 'closed', 'unknown')", name="status"),
        CheckConstraint(
            "first_seen_date is null or last_seen_date is null "
            "or last_seen_date >= first_seen_date",
            name="date_order",
        ),
        CheckConstraint(
            "identified_by in ('overture_category', 'name_pattern', 'both', 'propagated')",
            name="identified_by",
        ),
        CheckConstraint(
            "status_basis in ('dohmh_active', 'yelp_open', 'overture_open', "
            "'yelp_closed', 'dohmh_closed_by_dohmh', 'dohmh_inactive', "
            "'overture_permanently_closed', 'none')",
            name="status_basis",
        ),
        CheckConstraint(
            "first_seen_source is null or first_seen_source in "
            "('dohmh_prepermit_inspection', 'dohmh_first_inspection', 'overture_release')",
            name="first_seen_source",
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
    # earliest / latest evidence the shop existed (mostly DOHMH inspection dates)
    first_seen_date: Mapped[date | None] = mapped_column(Date, index=True)
    first_seen_source: Mapped[str | None] = mapped_column(String)
    last_seen_date: Mapped[date | None] = mapped_column(Date)
    # set only on a real closure signal (rare); NULL otherwise
    closed_date: Mapped[date | None] = mapped_column(Date)
    closed_source: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String, index=True)  # open | closed | unknown
    status_basis: Mapped[str | None] = mapped_column(String, index=True)
    identified_by: Mapped[str | None] = mapped_column(String, index=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class YelpStatus(Base):
    """Current open/closed for a shop from Yelp Fusion -- the corroboration DOHMH
    can't give for Overture-only shops. Keyed by whichever id identifies the shop
    (overture_id or camis, exactly one set). analyze.py left-joins on both."""

    __tablename__ = "yelp_status"
    __table_args__ = (
        UniqueConstraint(
            "overture_id", "camis", name="uq_yelp_status_key", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    overture_id: Mapped[str | None] = mapped_column(
        ForeignKey("overture_places.id", ondelete="CASCADE"), index=True
    )
    camis: Mapped[str | None] = mapped_column(
        ForeignKey("dohmh_establishments.camis", ondelete="CASCADE"), index=True
    )
    yelp_id: Mapped[str | None] = mapped_column(String)  # null == searched, no match
    yelp_name: Mapped[str | None] = mapped_column(String)
    is_closed: Mapped[bool | None] = mapped_column(Boolean)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String)
    match_score: Mapped[float | None] = mapped_column(Float)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
