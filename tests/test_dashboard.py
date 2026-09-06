"""Smoke test: the Streamlit dashboard renders without raising.

It auto-deploys to a public URL on every push, with no other coverage --
a broken query or a schema change that outruns app.py would ship live.
Runs the script headless via AppTest against the migrated test DB.
"""

from __future__ import annotations

import os

import sqlalchemy as sa

_APP = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "app.py")

_SEED = [
    """insert into dohmh_establishments
         (camis, dba, boro, latitude, longitude, geom,
          first_inspection_date, last_inspection_date, latest_grade, latest_score)
       values
         ('40000000', 'Test Boba', 'Manhattan', 40.75, -73.98,
          ST_SetSRID(ST_MakePoint(-73.98, 40.75), 4326),
          '2022-01-01', '2025-06-01', 'A', 9)""",
    """insert into yelp_businesses
         (yelp_id, name, is_closed, rating, review_count, url, geom)
       values
         ('yelp-test-1', 'Test Boba', false, 4.5, 120,
          'https://yelp.com/biz/test-boba',
          ST_SetSRID(ST_MakePoint(-73.98, 40.75), 4326))""",
    """insert into boba_shops
         (name, camis, yelp_id, geom, borough, first_seen_date, first_seen_source,
          last_seen_date, status, status_basis, identified_by)
       values
         ('Test Boba', '40000000', 'yelp-test-1',
          ST_SetSRID(ST_MakePoint(-73.98, 40.75), 4326), 'Manhattan',
          '2022-01-01', 'dohmh_first_inspection', '2025-06-01',
          'open', 'yelp_open', 'yelp_category')""",
    """insert into ingest_runs
         (source, status, started_at, finished_at, row_count, kept_count)
       values ('yelp_discover', 'ok', now(), now(), 500, 460)""",
]

_CLEANUP = [
    "delete from boba_shops",
    "delete from ingest_runs",
    "delete from yelp_businesses",
    "delete from dohmh_establishments",
]


def _run_app(engine, monkeypatch):
    import boba.db

    monkeypatch.setattr(boba.db, "engine", engine)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(_APP)
    at.run(timeout=60)
    return at


def test_dashboard_renders_on_empty_db(migrated_engine, monkeypatch):
    # a fresh / failed pipeline leaves boba_shops empty -- the page should
    # still render (all zeros), not crash
    at = _run_app(migrated_engine, monkeypatch)
    assert not at.exception, at.exception


def test_dashboard_renders_with_data(migrated_engine, monkeypatch):
    try:
        with migrated_engine.begin() as c:
            for stmt in _SEED:
                c.execute(sa.text(stmt))
        at = _run_app(migrated_engine, monkeypatch)
        assert not at.exception, at.exception
    finally:
        with migrated_engine.begin() as c:
            for stmt in _CLEANUP:
                c.execute(sa.text(stmt))
