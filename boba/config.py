"""Project configuration: paths, DB URL, and the NYC / boba filter constants."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg2://boba:boba@localhost:5433/boba")

DATA_DIR = PROJECT_ROOT / "data"


def data_dir() -> Path:
    """Return the data dir, creating it on first use (no import-time side effect)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


# Bounding box covering all five NYC boroughs, as (min_lon, min_lat, max_lon, max_lat).
# Used by the Yelp adaptive-grid discovery sweep.
NYC_BBOX = (-74.2591, 40.4774, -73.7002, 40.9162)

# Name-based signal for boba shops, applied to Yelp names and DOHMH `dba`.
# Yelp's `bubbletea` category is the primary discovery source; this pattern is the
# secondary net -- it rescues Yelp results where `bubbletea` isn't the primary
# category, and finds DOHMH-only shops (DOHMH cuisine has no "bubble tea" value).
# Chain names are listed explicitly for that reason.
# All groups non-capturing so pandas .str.contains(regex=True) doesn't warn.
BOBA_NAME_PATTERN = (
    r"(?i)(?:"
    r"\bboba\b|bubble\s*tea|milk\s*tea|pearl\s*(?:milk\s*)?tea|tapioca|\bbbt\b|"
    r"kung\s*fu\s*tea|gong\s*cha|chatime|coco\s*fresh|\bcoco\b\s*(?:tea|bubble)|"
    r"vivi\s*bubble|xing\s*fu\s*tang|tiger\s*sugar|happy\s*lemon|sharetea|share\s*tea|"
    r"yi\s*fang|moge\s*tee|m[o0]ge\s*tee|machi\s*machi|the\s*alley|tp\s*tea|ten\s*ren|"
    r"quickly|possmei|wanpo|truedan|meet\s*fresh|no[.\s]*1\s*bubble|"
    r"tea\s*(?:&|and)\s*milk|milktea|teado|boba\s*guys|omomo|smoodee|tastea|tea\s*more|"
    r"i[- ]?tea|it'?s\s*boba|boba\s*tea|bubble\s*house|tea\s*station|comebuy|come\s*buy|"
    # newer chains that carry no generic keyword:
    r"hey\s*tea|heytea|molly\s*tea|auntea|chun\s*yang|sunright|chagee|one\s*zo|"
    r"wushiland|bar\s*pa\s*tea|tsaocaa|ts[aâ]o\s*caa|dakasi|truwin|zhen\s*gu\s*li|"
    r"\bfeng\s*cha\b|chun\s*cui\s*he|xiao\s*mei|7\s*leaves|nine\s*leaves"
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

# Yelp Fusion API key -> primary boba discovery (its curated `bubbletea` category)
# plus current open/closed status for free.
# Free key at https://www.yelp.com/developers  (~500 calls/day).
YELP_API_KEY = os.environ.get("YELP_API_KEY") or None
YELP_SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
