"""The 'is this a boba shop?' predicates, shared by the pipeline and notebooks."""

from __future__ import annotations

import re

from boba.config import (
    BOBA_CATEGORIES,
    BOBA_FALLBACK_CATEGORIES,
    BOBA_NAME_PATTERN,
)
from boba.contracts import OverturePlaceRecord

_NAME_RE = re.compile(BOBA_NAME_PATTERN)


def name_looks_like_boba(name: str | None) -> bool:
    return bool(name) and _NAME_RE.search(name) is not None


def overture_is_boba(rec: OverturePlaceRecord) -> bool:
    if any(c in BOBA_CATEGORIES for c in rec.categories_all):
        return True
    return rec.category_primary in BOBA_FALLBACK_CATEGORIES and name_looks_like_boba(rec.name)


def dohmh_is_boba(dba: str | None) -> bool:
    return name_looks_like_boba(dba)


# generic tokens that inflate fuzzy name matches between unrelated shops
NAME_STOPWORDS = frozenset(
    {
        "bubble",
        "tea",
        "boba",
        "milk",
        "cafe",
        "coffee",
        "the",
        "shop",
        "house",
        "and",
        "ny",
        "nyc",
        "llc",
        "inc",
        "co",
        "of",
        "at",
    }
)


def _alnum(s) -> str:
    if not isinstance(s, str):
        return ""
    return "".join(c if (c.isalnum() or c.isspace()) else " " for c in s.lower())


def name_key(s) -> str:
    """Lowercase, punctuation -> space, drop generic tokens. Used for fuzzy matching
    (match.py, ingest/yelp.py). Falls back to the plain form if all-stopwords."""
    toks = [t for t in _alnum(s).split() if t not in NAME_STOPWORDS]
    return " ".join(toks) or _alnum(s)
