from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from boba.contracts import (
    ContractViolation,
    IngestRun,
    drift_warnings,
    parse_overture_place,
    validate_dohmh_frame,
)
from boba.filters import overture_is_boba


def _place(**over):
    base = {
        "id": "gers-1",
        "categories": {"primary": "bubble_tea", "alternate": ["cafe"]},
        "confidence": 0.93,
        "names": {"primary": "Gong Cha"},
        "lon": -73.98,
        "lat": 40.75,
    }
    base.update(over)
    return base


# --- Overture record ---------------------------------------------------


def test_good_record_parses_and_is_boba():
    rec = parse_overture_place(_place())
    assert rec.name == "Gong Cha"
    assert rec.category_primary == "bubble_tea"
    assert "cafe" in rec.categories_all
    assert overture_is_boba(rec)


def test_null_confidence_and_status_are_allowed():
    rec = parse_overture_place(
        _place(confidence=None, operating_status=float("nan"), categories={"primary": None})
    )
    assert rec.confidence is None
    assert rec.operating_status is None
    assert rec.categories_all == []


def test_missing_categories_field_is_drift():
    raw = _place()
    del raw["categories"]
    with pytest.raises(ContractViolation, match="categories"):
        parse_overture_place(raw)


def test_confidence_out_of_range_rejected():
    with pytest.raises(ContractViolation):
        parse_overture_place(_place(confidence=1.4))


def test_unknown_operating_status_is_drift():
    with pytest.raises(ContractViolation, match="operating_status"):
        parse_overture_place(_place(operating_status="franchise_paused"))


def test_point_outside_nyc_rejected():
    with pytest.raises(ContractViolation, match="NYC"):
        parse_overture_place(_place(lon=-118.24, lat=34.05))


def test_source_update_time_takes_the_max_across_sources():
    rec = parse_overture_place(
        _place(
            sources=[
                {"update_time": "2024-01-01T00:00:00Z"},
                {"update_time": "2025-06-15"},
            ]
        )
    )
    assert rec.source_update_time.year == 2025


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
