"""Project configuration: paths, DB URL, and the NYC / boba filter constants."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://boba:boba@localhost:5433/boba"
)

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# Bounding box covering all five NYC boroughs, as (min_lon, min_lat, max_lon, max_lat).
NYC_BBOX = (-74.2591, 40.4774, -73.7002, 40.9162)

# Overture place category values that are unambiguously boba / bubble-tea shops.
BOBA_CATEGORIES = {"bubble_tea"}

# Broader Overture categories to sweep with a name filter (a lot of boba shops are
# tagged as generic cafes / dessert shops / juice bars).
BOBA_FALLBACK_CATEGORIES = {
    "cafe",
    "coffee_shop",
    "tea_room",
    "dessert_shop",
    "juice_bar",
    "shopping",
    "food",
}

# Name-based signal for boba shops, applied to both Overture names and DOHMH `dba`.
# Chain names are included because DOHMH cuisine has no "bubble tea" value.
BOBA_NAME_PATTERN = (
    r"(?i)("
    r"\bboba\b|bubble\s*tea|milk\s*tea|pearl\s*(milk\s*)?tea|tapioca|\bbbt\b|"
    r"kung\s*fu\s*tea|gong\s*cha|chatime|coco\s*fresh|\bcoco\b\s*(tea|bubble)|"
    r"vivi\s*bubble|xing\s*fu\s*tang|tiger\s*sugar|happy\s*lemon|sharetea|share\s*tea|"
    r"yi\s*fang|moge\s*tee|m[o0]ge\s*tee|machi\s*machi|the\s*alley|tp\s*tea|ten\s*ren|"
    r"quickly|possmei|wanpo|truedan|meet\s*fresh|no[.\s]*1\s*bubble|gong\s*cha|"
    r"tea\s*(&|and)\s*milk|milktea|teado|boba\s*guys|omomo|smoodee|tastea|tea\s*more|"
    r"i[- ]?tea|it'?s\s*boba|boba\s*tea|bubble\s*house|tea\s*station|comebuy|come\s*buy"
    r")"
)

# CAMIS rows in DOHMH use this sentinel date for "never inspected".
DOHMH_NULL_DATE = "1900-01-01"

# NYC DOHMH Restaurant Inspection Results (Socrata dataset).
DOHMH_DATASET_ID = "43nn-pn8j"
DOHMH_SOCRATA_BASE = f"https://data.cityofnewyork.us/resource/{DOHMH_DATASET_ID}.json"

# Optional Socrata app token -> much higher rate limits for the DOHMH pull.
# Register one free at https://evergreen.data.socrata.com/profile/app_tokens
SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN") or None
