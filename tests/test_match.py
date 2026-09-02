from __future__ import annotations

from boba.filters import name_key
from boba.match import _score


def test_name_key_strips_generic_tokens():
    assert name_key("CoCo Bubble Tea") == "coco"
    assert name_key("Kung Fu Tea") == "kung fu"
    assert name_key("The Alley LLC") == "alley"


def test_name_key_falls_back_when_all_generic():
    assert name_key("Bubble Tea") == "bubble tea"  # don't return ""


def test_score_exact_name_close_distance():
    name_sim, score, method = _score(
        "Kung Fu Tea", "KUNG FU TEA", "50 Bayard St", "Bayard St", 15.0
    )
    assert name_sim == 100
    assert method == "name_addr"  # street token matched the address
    assert score > 95


def test_score_generic_overlap_does_not_inflate():
    # "CoCo Bubble Tea" vs "ViVi Bubble Tea" -> only "bubble tea" shared, stripped
    name_sim, _, _ = _score("CoCo Bubble Tea", "VIVI BUBBLE TEA", "1 Main St", "Other Ave", 40.0)
    assert name_sim < 50


def test_score_distance_penalty():
    near = _score("Sweetea", "SWEETEA", "1 A St", "A St", 5.0)[1]
    far = _score("Sweetea", "SWEETEA", "1 A St", "A St", 110.0)[1]
    assert near > far


def test_score_handles_missing_address_and_street():
    name_sim, score, method = _score("Tea Pulse", "TEAPULSE", None, None, 20.0)
    assert method == "name_dist"
    assert name_sim > 60
