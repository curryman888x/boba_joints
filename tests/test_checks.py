from __future__ import annotations

import sqlalchemy as sa

from boba import checks


def test_invariants_hold_on_empty_schema(migrated_engine):
    assert checks.run(bind=migrated_engine) == 0


def test_invariants_catch_a_bad_sentinel_date(migrated_engine):
    with migrated_engine.begin() as c:
        c.execute(
            sa.text(
                "insert into dohmh_establishments (camis, first_inspection_date) "
                "values ('999999', date '1999-01-01')"
            )
        )
    try:
        assert checks.run(bind=migrated_engine) >= 1
    finally:
        with migrated_engine.begin() as c:
            c.execute(sa.text("delete from dohmh_establishments where camis = '999999'"))

    assert checks.run(bind=migrated_engine) == 0
