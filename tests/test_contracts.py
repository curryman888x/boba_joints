from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from boba.contracts import (
    ContractViolation,
    IngestRun,
    drift_warnings,
    parse_yelp_business,
    validate_dohmh_frame,
)


def _biz(**over):
    base = {
        "id": "yelp-1",
        "name": "Gong Cha",
        "is_closed": False,
        "coordinates": {"longitude": -73.98, "latitude": 40.75},
        "location": {"address1": "1 Main St", "city": "New York", "zip_code": "10002"},
        "categories": [{"alias": "bubbletea", "title": "Bubble Tea"}],
        "url": "https://www.yelp.com/biz/gong-cha?adjust_creative=abc",
    }
    base.update(over)
    return base


# --- Yelp business record --------------------------------------------


def test_good_yelp_record_parses_and_flattens():
    rec = parse_yelp_business(_biz())
    assert rec.yelp_id == "yelp-1"
    assert rec.name == "Gong Cha"
    assert (rec.lon, rec.lat) == (-73.98, 40.75)
    assert rec.address == "1 Main St"
    assert rec.zip == "10002"
    assert rec.url == "https://www.yelp.com/biz/gong-cha"  # query string stripped


def test_yelp_missing_coordinates_rejected():
    raw = _biz()
    del raw["coordinates"]
    with pytest.raises(ContractViolation):
        parse_yelp_business(raw)


def test_yelp_categories_kept_as_dicts():
    rec = parse_yelp_business(
        _biz(categories=[{"alias": "coffee"}, "junk", {"alias": "bubbletea"}])
    )
    assert [c["alias"] for c in rec.categories] == ["coffee", "bubbletea"]


# --- DOHMH frame -----------------------------------------------------


def _dohmh_df(rows):
    return pd.DataFrame(rows)


def test_valid_dohmh_frame_passes():
    df = _dohmh_df(
        [
            {
                "camis": "40361618",
                "dba": "KUNG FU TEA",
                "boro": "Manhattan",
                "inspection_date": "2023-05-01",
                "action": "Violations were cited in the following area(s).",
                "latitude": 40.75,
                "longitude": -73.98,
            },
        ]
    )
    out = validate_dohmh_frame(df)
    assert len(out) == 1


def test_empty_frame_raises():
    with pytest.raises(ContractViolation, match="empty"):
        validate_dohmh_frame(pd.DataFrame())


def test_blank_camis_raises():
    df = _dohmh_df([{"camis": "not-a-number", "dba": "X"}])
    with pytest.raises(ContractViolation):
        validate_dohmh_frame(df)


def test_unknown_action_warns_but_passes():
    df = _dohmh_df(
        [
            {
                "camis": "1",
                "dba": "X",
                "action": "Establishment vaporised by aliens",
                "latitude": np.nan,
                "longitude": np.nan,
            }
        ]
    )
    with pytest.warns(Warning):
        out = validate_dohmh_frame(df)
    assert len(out) == 1


# --- manifest drift -------------------------------------------------


def test_drift_warns_on_big_volume_drop_and_earlier_coverage():
    prev = IngestRun(
        source="dohmh",
        status="ok",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        kept_count=200,
        min_date=date(2022, 1, 25),
        source_version="a",
    )
    curr = IngestRun(
        source="dohmh",
        status="ok",
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
        kept_count=120,
        min_date=date(2021, 3, 1),
        source_version="b",
    )
    msgs = " ".join(drift_warnings(prev, curr))
    assert "kept_count" in msgs
    assert "2021" in msgs
    assert "version" in msgs


def test_no_drift_on_first_run():
    curr = IngestRun(source="dohmh", status="ok", kept_count=200)
    assert drift_warnings(None, curr) == []
