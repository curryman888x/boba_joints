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


def ov(**kw):
    base = dict(
        operating_status=None,
        source_update_time=None,
        first_seen_release=None,
        last_seen_release=None,
        is_bubble_tea=False,
        name=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def yb(**kw):
    base = dict(name=None, yelp_is_closed=None)
    base.update(kw)
    return SimpleNamespace(**base)


# --- _first_seen ----------------------------------------------------


def test_first_seen_prefers_dohmh_first_inspection():
    d = dt.date(2023, 4, 1)
    assert _first_seen(est(first_inspection_date=d), ov()) == (d, "dohmh_first_inspection")


def test_first_seen_uses_overture_release_only_across_two_ingests():
    o1 = ov(first_seen_release="2026-08-19.0", last_seen_release="2026-08-19.0")
    assert _first_seen(None, o1) == (None, None)  # single ingest -> no signal
    o2 = ov(first_seen_release="2026-07-22.0", last_seen_release="2026-08-19.0")
    assert _first_seen(None, o2) == (dt.date(2026, 7, 22), "overture_release")


# --- _closed / status_basis ---------------------------------------


def test_closed_dohmh_forced_closure():
    d = dt.date(2025, 2, 3)
    assert _closed(est(closed_flag=True, closed_date=d), ov(), None, TODAY) == (
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
    assert _closed(e, ov(), None, TODAY)[2] == "open"


def test_recent_inspection_is_open_dohmh_active():
    e = est(last_inspection_date=dt.date(2026, 6, 1))
    assert _closed(e, ov(), None, TODAY) == (None, None, "open", "dohmh_active")


def test_long_silence_is_closed_inactive():
    e = est(last_inspection_date=dt.date(2024, 1, 1))
    d, src, status, basis = _closed(e, ov(), None, TODAY)
    assert (status, basis, src) == ("closed", "dohmh_inactive", "dohmh_inactive")


def test_overture_open_is_low_trust_basis():
    assert _closed(None, ov(operating_status="open"), None, TODAY) == (
        None,
        None,
        "open",
        "overture_open",
    )


def test_no_signal_is_unknown_not_open():
    assert _closed(None, ov(operating_status=None), None, TODAY) == (None, None, "unknown", "none")


def test_overture_permanently_closed():
    o = ov(operating_status="permanently_closed", source_update_time=dt.datetime(2026, 8, 14))
    assert _closed(None, o, None, TODAY)[2:] == ("closed", "overture_permanently_closed")


def test_yelp_closed_beats_a_recent_inspection():
    e = est(last_inspection_date=dt.date(2026, 6, 1))
    assert _closed(e, ov(), yb(yelp_is_closed=True), TODAY)[2:] == ("closed", "yelp_closed")


def test_yelp_open_outranks_overture_open():
    o = ov(operating_status="open")
    assert _closed(None, o, yb(yelp_is_closed=False), TODAY)[2:] == ("open", "yelp_open")


# --- _identified_by ----------------------------------------------


def test_identified_by_precedence():
    assert (
        _identified_by(None, ov(is_bubble_tea=True, name="Gong Cha"), est(boba_name_match=True))
        == "both"
    )
    assert (
        _identified_by(None, ov(is_bubble_tea=True, name="Random Deli"), None)
        == "overture_category"
    )
    assert _identified_by(None, ov(is_bubble_tea=False, name="Kung Fu Tea"), None) == "name_pattern"
    assert _identified_by(None, None, est(boba_name_match=True)) == "name_pattern"
    assert (
        _identified_by(None, ov(is_bubble_tea=False, name="Joe's"), est(boba_name_match=False))
        == "propagated"
    )


# --- _dedup + haversine -----------------------------------------


def _shop(name, lon, lat, camis=None, status="open", first_seen=None, oid="x"):
    return {
        "name": name,
        "lon": lon,
        "lat": lat,
        "camis": camis,
        "status": status,
        "first_seen_date": first_seen,
        "overture_id": oid,
        "yelp_id": None,
    }


def test_haversine_metres_roughly_right():
    # ~111 m per 0.001 deg latitude
    assert 100 < _haversine_m(-73.98, 40.75, -73.98, 40.751) < 120


def test_dedup_merges_same_name_within_60m_keeps_richest():
    a = _shop("Gong Cha", -73.9800, 40.7500, oid="a")
    b = _shop("GONG CHA", -73.98005, 40.75002, camis="123", first_seen=dt.date(2023, 1, 1), oid="b")
    out = _dedup([a, b])
    assert len(out) == 1
    assert out[0]["camis"] == "123"  # richer row won
    assert out[0]["overture_id"] in {"a", "b"}


def test_dedup_keeps_distinct_locations():
    a = _shop("Come Buy", -73.997, 40.7376, oid="a")
    b = _shop("Come Buy", -73.987, 40.7448, oid="b")  # ~1.2 km away
    assert len(_dedup([a, b])) == 2
