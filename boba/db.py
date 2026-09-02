"""SQLAlchemy engine / session factory + a bulk upsert helper."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from boba.config import DATABASE_URL

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, future=True, expire_on_commit=False)


def upsert(
    session: Session,
    model: Any,
    rows: Sequence[dict],
    *,
    index_elements: Sequence[str],
    update_cols: Iterable[str] | None = None,
    chunk_size: int = 1000,
) -> int:
    """INSERT ... ON CONFLICT DO UPDATE for a list of row dicts.

    `index_elements` is the conflict target (PK or a unique constraint's columns).
    `update_cols` defaults to every key in the first row that isn't a conflict key.
    Returns the number of rows sent.
    """
    rows = list(rows)
    if not rows:
        return 0
    cols = (
        list(update_cols)
        if update_cols is not None
        else [k for k in rows[0] if k not in set(index_elements)]
    )
    sent = 0
    for start in range(0, len(rows), chunk_size):
        batch = rows[start : start + chunk_size]
        stmt = pg_insert(model).values(batch)
        if cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=list(index_elements),
                set_={c: getattr(stmt.excluded, c) for c in cols},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=list(index_elements))
        session.execute(stmt)
        sent += len(batch)
    return sent
