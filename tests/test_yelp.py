from __future__ import annotations

from boba.ingest.yelp import (
    _best,
    _haversine_m,
    _in_bbox,
    _is_boba_business,
    _quadrants,
)

_NYC = (-74.2591, 40.4774, -73.7002, 40.9162)


# --- geometry helpers --------------------------------------------


def test_haversine_metres_roughly_right():
    # ~111 m per 0.001 deg latitude
    assert 100 < _haversine_m(-73.98, 40.75, -73.98, 40.751) < 120


def test_in_bbox():
    assert _in_bbox(-73.98, 40.75, _NYC)  # midtown Manhattan
    assert not _in_bbox(-74.9, 40.75, _NYC)  # NJ, west of the box


def test_quadrants_tile_the_parent():
    qs = _quadrants((0.0, 0.0, 2.0, 2.0))
    assert len(qs) == 4
    assert (0.0, 0.0, 1.0, 1.0) in qs  # SW
    assert (1.0, 1.0, 2.0, 2.0) in qs  # NE
    assert min(q[0] for q in qs) == 0.0 and max(q[2] for q in qs) == 2.0


# --- off-topic filter ------------------------------------------


def test_is_boba_business_primary_category_kept():
    assert _is_boba_business("Anything", [{"alias": "bubbletea", "title": "Bubble Tea"}])


def test_is_boba_business_secondary_category_dropped():
    # a ramen shop that also lists bubbletea 2nd is not a boba shop
    assert not _is_boba_business("Ippudo", [{"alias": "ramen"}, {"alias": "bubbletea"}])


def test_is_boba_business_rescued_by_name():
    assert _is_boba_business("Kung Fu Tea", [{"alias": "coffee"}])
    assert not _is_boba_business("Blue Bottle", [{"alias": "coffee"}])


# --- link scorer ---------------------------------------------


def test_best_prefers_high_similarity_candidate():
    cands = [
        ("overture", "ov-1", "Gong Cha", 10.0),
        ("overture", "ov-2", "Joe's Pizza", 5.0),
    ]
    cid, score = _best(cands, "Gong Cha")
    assert cid == "ov-1"
    assert score and score > 80


def test_best_rejects_below_name_threshold():
    cands = [("dohmh", "123", "Totally Different Deli", 5.0)]
    assert _best(cands, "Gong Cha") == (None, None)


def test_best_distance_penalty_changes_score():
    near = [("overture", "near", "Boba Guys", 5.0)]
    far = [("overture", "far", "Boba Guys", 250.0)]
    assert _best(near, "Boba Guys")[1] > _best(far, "Boba Guys")[1]


def test_best_empty_candidates():
    assert _best([], "Gong Cha") == (None, None)
