"""
Data normalization utilities for CloudPriceFinder.
Standardizes data formats across different cloud providers.
"""

from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Commitment normalisation (Stage 1 — v3 schema extension)
# ---------------------------------------------------------------------------

def normalize_commitments(raw_commitments: List[Dict[str, Any]], on_demand_hourly: float) -> List[Dict[str, Any]]:
    """
    Normalise a list of raw commitment entries into the standard CommitmentPrice shape.

    Each input dict must contain at minimum:
    - term         : 'on-demand' | '1yr' | '3yr'
    - payment      : 'no-upfront' | 'partial-upfront' | 'all-upfront' | 'flexible'
    - product      : 'reserved' | 'savings-plan' | 'cud' | 'flex'
    - priceUSD_hourly : raw hourly charge (may be 0 for all-upfront products).

    Optional:
    - upfront_usd  : total upfront fee (used to compute effectiveHourlyUSD for
                     partial- and all-upfront reservations).
    - term_hours   : commitment duration in hours (defaults: 1yr=8760, 3yr=26280).

    The helper computes:
    - effectiveHourlyUSD   = priceUSD_hourly + (upfront_usd / term_hours)
    - savingsVsOnDemandPct = max(0, (1 - effectiveHourlyUSD / on_demand_hourly) * 100)
                             clamped to [0, 100]; 0 if on_demand_hourly == 0.

    Args:
        raw_commitments: List of dicts from provider-specific fetchers.
        on_demand_hourly: The on-demand priceUSD_hourly for the parent instance.

    Returns:
        List of normalised CommitmentPrice dicts.
    """
    _TERM_HOURS = {'1yr': 8760, '3yr': 26280}
    normalised: List[Dict[str, Any]] = []

    for raw in raw_commitments:
        try:
            term = raw.get('term', 'on-demand')
            payment = raw.get('payment', 'flexible')
            product = raw.get('product', 'reserved')
            price_hourly = float(raw.get('priceUSD_hourly', 0) or 0)
            upfront = float(raw.get('upfront_usd', 0) or 0)
            term_hours = float(raw.get('term_hours') or _TERM_HOURS.get(term, 8760))

            amortised_upfront = upfront / term_hours if term_hours > 0 else 0.0
            effective = price_hourly + amortised_upfront

            if on_demand_hourly > 0:
                savings_pct = max(0.0, min(100.0, (1.0 - effective / on_demand_hourly) * 100.0))
            else:
                savings_pct = 0.0

            normalised.append({
                'term': term,
                'payment': payment,
                'product': product,
                'priceUSD_hourly': round(price_hourly, 6),
                'effectiveHourlyUSD': round(effective, 6),
                'savingsVsOnDemandPct': round(savings_pct, 2),
            })
        except Exception as exc:
            logger.warning(f'normalize_commitments: skipping malformed entry {raw!r}: {exc}')

    return normalised
