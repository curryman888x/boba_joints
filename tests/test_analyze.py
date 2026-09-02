from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from boba.analyze import _closed, _dedup, _first_seen, _haversine_m, _identified_by

TODAY = dt.date(2026, 9, 1)


def est(**kw):
    base = dict(
        first_inspection_date=None,
        last_inspection_date=None,
        closed_flag=False,
        closed_date=None,
        reopened_date=None,
        boba_name_match=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def yb(**kw):
    base = dict(name=None, yelp_is_closed=None)
    base.update(kw)
    return SimpleNamespace(**base)


# --- _first_seen ----------------------------------------------------


def test_first_seen_is_dohmh_first_inspection():
    d = dt.date(2023, 4, 1)
    assert _first_seen(est(first_inspection_date=d)) == (d, "dohmh_first_inspection")


def test_first_seen_none_without_a_camis():
    assert _first_seen(None) == (None, None)


# --- _closed / status_basis ---------------------------------------


def test_closed_dohmh_forced_closure():
    d = dt.date(2025, 2, 3)
    assert _closed(est(closed_flag=True, closed_date=d), None, TODAY) == (
        d,
        "dohmh_closed_by_dohmh",
        "closed",
        "dohmh_closed_by_dohmh",
    )


def test_closed_reopened_is_not_closed():
    e = est(
        closed_flag=True,
        closed_date=dt.date(2024, 1, 1),
        reopened_date=dt.date(2024, 3, 1),
        last_inspection_date=dt.date(2026, 6, 1),
    )
    assert _closed(e, None, TODAY)[2] == "open"


def test_recent_inspection_is_open_dohmh_active():
    e = est(last_inspection_date=dt.date(2026, 6, 1))
    assert _closed(e, None, TODAY) == (None, None, "open", "dohmh_active")


def test_long_silence_is_unknown_not_closed():
    # 18+ months without an inspection and nothing else to go on -> can't tell
    e = est(last_inspection_date=dt.date(2024, 1, 1))
    assert _closed(e, None, TODAY) == (None, None, "unknown", "dohmh_inactive")


def test_no_signal_is_unknown_not_open():
    assert _closed(None, None, TODAY) == (None, None, "unknown", "none")


def test_yelp_closed_beats_a_recent_inspection():
    e = est(last_inspection_date=dt.date(2026, 6, 1))
    assert _closed(e, yb(yelp_is_closed=True), TODAY)[2:] == ("closed", "yelp_closed")


def test_yelp_open_when_no_dohmh():
    assert _closed(None, yb(yelp_is_closed=False), TODAY) == (None, None, "open", "yelp_open")


def test_yelp_open_beats_dohmh_inactive():
    # long inspection silence, but Yelp is current and says open -> not "closed"
    e = est(last_inspection_date=dt.date(2024, 1, 1))
    assert _closed(e, yb(yelp_is_closed=False), TODAY) == (None, None, "open", "yelp_open")


def test_recent_inspection_outranks_yelp_open_for_the_basis():
    e = est(last_inspection_date=dt.date(2026, 6, 1))
    assert _closed(e, yb(yelp_is_closed=False), TODAY) == (None, None, "open", "dohmh_active")


# --- _identified_by ----------------------------------------------


def test_identified_by_yelp_vs_name():
    assert _identified_by(yb(name="Gong Cha"), None) == "yelp_category"
    assert _identified_by(yb(name="Gong Cha"), est(boba_name_match=True)) == "yelp_category"
    assert _identified_by(None, est(boba_name_match=True)) == "name_pattern"


# --- _dedup + haversine -----------------------------------------


def _shop(name, lon, lat, camis=None, status="open", first_seen=None, yelp_id=None):
    return {
        "name": name,
        "lon": lon,
        "lat": lat,
        "camis": camis,
        "status": status,
        "first_seen_date": first_seen,
        "yelp_id": yelp_id,
    }


def test_haversine_metres_roughly_right():
    # ~111 m per 0.001 deg latitude
    assert 100 < _haversine_m(-73.98, 40.75, -73.98, 40.751) < 120


def test_dedup_merges_same_name_within_60m_keeps_richest_and_unions_ids():
    a = _shop("Gong Cha", -73.9800, 40.7500, yelp_id="y-1")
    b = _shop("GONG CHA", -73.98005, 40.75002, camis="123", first_seen=dt.date(2023, 1, 1))
    out = _dedup([a, b])
    assert len(out) == 1
    assert out[0]["camis"] == "123"  # richer row (has a CAMIS + date) won
    assert out[0]["yelp_id"] == "y-1"  # ...but the Yelp id was merged onto it


def test_dedup_keeps_distinct_locations():
    a = _shop("Come Buy", -73.997, 40.7376)
    b = _shop("Come Buy", -73.987, 40.7448)  # ~1.2 km away
    assert len(_dedup([a, b])) == 2
