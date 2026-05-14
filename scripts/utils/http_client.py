"""Shared HTTP utilities for CloudPriceFinder fetch scripts."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

HOURS_PER_MONTH = 730.44  # 365.25 * 24 / 12

USER_AGENT = "CloudPriceFinder/3.0 (cloudpricefinder.com)"


def make_session(user_agent: str = USER_AGENT) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
    return s


def get_json(
    session: requests.Session,
    url: str,
    params: dict | None = None,
    max_retries: int = 3,
    backoff: float = 5.0,
    timeout: int = 60,
) -> Any:
    for attempt in range(1, max_retries + 1):
        try:
            r = session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise
            wait = backoff * (2 ** (attempt - 1))
            logging.getLogger(__name__).warning(
                f"Attempt {attempt}/{max_retries} failed for {url}: {exc}. "
                f"Retrying in {wait:.1f}s..."
            )
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts")
