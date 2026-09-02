"""HTTP session with retry/backoff, used for the Socrata pulls."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from boba.config import SOCRATA_APP_TOKEN


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
