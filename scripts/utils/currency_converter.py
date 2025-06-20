"""
Currency conversion utilities for CloudPriceFinder.
Converts prices from various currencies to USD.
"""

import requests
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta
import json
import os

logger = logging.getLogger(__name__)

# Simple cache for exchange rates
_rate_cache = {}
_cache_expiry = None
CACHE_DURATION = timedelta(hours=1)

# Fallback exchange rates (updated manually as needed)
FALLBACK_RATES = {
    'EUR': 1.10,
    'GBP': 1.25,
    'JPY': 0.0067,
    'CAD': 0.74,
    'AUD': 0.65,
    'CHF': 1.05,
    'USD': 1.0
}

def get_exchange_rates() -> Dict[str, float]:
    """
    Get current exchange rates to USD.
    Uses a free API with fallback to hardcoded rates.
    """
    global _rate_cache, _cache_expiry
    
    # Check cache
    if _cache_expiry and datetime.now() < _cache_expiry and _rate_cache:
        return _rate_cache
    
    try:
        # Try to fetch from free API (exchangerate-api.com or similar)
        # Note: In production, use a paid service for better reliability
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
            
            rates['USD'] = 1.0  # USD to USD is always 1
            
            # Update cache
            _rate_cache = rates
            _cache_expiry = datetime.now() + CACHE_DURATION
            
            logger.info(f"Updated exchange rates for {len(rates)} currencies")
            return rates
            
    except Exception as e:
        logger.warning(f"Failed to fetch exchange rates: {e}")
    
    # Use fallback rates
    logger.info("Using fallback exchange rates")
    _rate_cache = FALLBACK_RATES.copy()
    _cache_expiry = datetime.now() + CACHE_DURATION
    
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
        # Return original amount as fallback
        return amount

def convert_to_usd(amount: float, currency: str) -> float:
    """
    Simplified function to convert any currency to USD.
    
    Args:
        amount: Amount in source currency
        currency: Currency code
        
    Returns:
        Amount in USD
    """
    return convert_currency(amount, currency, 'USD')