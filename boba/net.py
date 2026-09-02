"""HTTP session with retry/backoff, used for the Socrata and Yelp pulls."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from boba.config import SOCRATA_APP_TOKEN, YELP_API_KEY


def session_with_retries(
    *,
    total: int = 5,
    backoff_factor: float = 1.0,
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Session:
    retry = Retry(
        total=total,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def socrata_session() -> requests.Session:
    s = session_with_retries()
    if SOCRATA_APP_TOKEN:
        s.headers["X-App-Token"] = SOCRATA_APP_TOKEN
    return s


def yelp_session() -> requests.Session:
    if not YELP_API_KEY:
        raise RuntimeError("YELP_API_KEY is not set (add it to .env)")
    s = session_with_retries()
    s.headers["Authorization"] = f"Bearer {YELP_API_KEY}"
    return s
