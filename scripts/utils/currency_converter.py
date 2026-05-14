"""
Currency conversion utilities for CloudPriceFinder.
Converts prices from various currencies to USD.
"""

import requests
import logging
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta
import json
import os
import tempfile

logger = logging.getLogger(__name__)

# Simple in-process cache for exchange rates
_rate_cache = {}
_cache_expiry = None
CACHE_DURATION = timedelta(hours=1)

# File-based cache shared across parallel fetcher processes
_FILE_CACHE_PATH = os.path.join(tempfile.gettempdir(), "cloudpricefinder_rates.json")
_FILE_CACHE_MAX_AGE = timedelta(hours=6)

# Fallback exchange rates (mid-2026 approximations)
FALLBACK_RATES = {
    'EUR': 1.09,
    'GBP': 1.27,
    'JPY': 0.0065,
    'CAD': 0.73,
    'AUD': 0.64,
    'CHF': 1.12,
    'USD': 1.0
}


def _load_file_cache() -> Optional[Dict[str, float]]:
    """Return cached rates from disk if the file exists and is < 6 hours old."""
    try:
        with open(_FILE_CACHE_PATH, "r") as f:
            data = json.load(f)
        timestamp = datetime.fromisoformat(data["timestamp"])
        if datetime.now(timezone.utc) - timestamp < _FILE_CACHE_MAX_AGE:
            logger.info("Loaded exchange rates from file cache")
            return data["rates"]
    except Exception:
        pass
    return None


def _save_file_cache(rates: Dict[str, float]) -> None:
    """Persist rates to disk with a UTC timestamp."""
    try:
        with open(_FILE_CACHE_PATH, "w") as f:
            json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), "rates": rates}, f)
    except Exception as e:
        logger.warning(f"Could not write exchange rate file cache: {e}")


def get_exchange_rates() -> Dict[str, float]:
    """
    Get current exchange rates to USD.
    Uses a free API with fallback to hardcoded rates.
    """
    global _rate_cache, _cache_expiry

    # Check in-process cache
    if _cache_expiry and datetime.now(timezone.utc) < _cache_expiry and _rate_cache:
        return _rate_cache

    # Check file cache (shared across parallel processes)
    file_rates = _load_file_cache()
    if file_rates:
        _rate_cache = file_rates
        _cache_expiry = datetime.now(timezone.utc) + CACHE_DURATION
        return _rate_cache

    try:
        response = requests.get(
            "https://api.exchangerate-api.com/v4/latest/USD",
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            # Convert to rates TO USD (inverse of FROM USD)
            rates = {}
            for currency, rate in data.get('rates', {}).items():
                if rate > 0:
                    rates[currency] = 1.0 / rate

            rates['USD'] = 1.0

            _rate_cache = rates
            _cache_expiry = datetime.now(timezone.utc) + CACHE_DURATION
            _save_file_cache(rates)

            logger.info(f"Updated exchange rates for {len(rates)} currencies")
            return rates

    except Exception as e:
        logger.warning(f"Failed to fetch exchange rates: {e}")

    # Use fallback rates
    logger.info("Using fallback exchange rates")
    _rate_cache = FALLBACK_RATES.copy()
    _cache_expiry = datetime.now(timezone.utc) + CACHE_DURATION

    return _rate_cache


def convert_currency(amount: float, from_currency: str, to_currency: str = 'USD') -> float:
    """
    Convert currency amount to USD.

    Args:
        amount: Amount to convert
        from_currency: Source currency code (EUR, GBP, etc.)
        to_currency: Target currency code (default: USD)

    Returns:
        Converted amount in target currency
    """
    if from_currency == to_currency:
        return amount

    try:
        rates = get_exchange_rates()

        if from_currency not in rates:
            logger.warning(f"Currency {from_currency} not found, using fallback rate")
            rate = FALLBACK_RATES.get(from_currency, 1.0)
        else:
            rate = rates[from_currency]

        converted = amount * rate
        logger.debug(f"Converted {amount} {from_currency} to {converted:.6f} {to_currency}")

        return converted

    except Exception as e:
        logger.error(f"Currency conversion failed: {e}")
        return amount
