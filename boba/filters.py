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
