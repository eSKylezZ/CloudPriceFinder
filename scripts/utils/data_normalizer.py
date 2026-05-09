"""
Data normalization utilities for CloudPriceFinder.
Standardizes data formats across different cloud providers.
"""

from typing import Dict, Any, List
import logging
from datetime import datetime

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

def normalize_instance_data(raw_data: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """
    Normalize raw provider data to standard CloudPriceFinder format.
    
    Args:
        raw_data: Raw data from provider API
        provider: Provider name (aws, azure, etc.)
        
    Returns:
        Dict containing normalized instance data
    """
    try:
        if provider == 'hetzner':
            return _normalize_hetzner(raw_data)
        elif provider == 'aws':
            return _normalize_aws(raw_data)
        elif provider == 'azure':
            return _normalize_azure(raw_data)
        elif provider == 'gcp':
            return _normalize_gcp(raw_data)
        elif provider == 'oci':
            return _normalize_oci(raw_data)
        elif provider == 'ovh':
            return _normalize_ovh(raw_data)
        else:
            raise ValueError(f"Unknown provider: {provider}")
            
    except Exception as e:
        logger.error(f"Normalization failed for {provider}: {e}")
        raise

def _normalize_hetzner(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Hetzner data."""
    # Convert EUR to USD (simplified - should use real exchange rates)
    eur_hourly = data.get('priceEUR_hourly_net', 0)
    eur_monthly = data.get('priceEUR_monthly_net', 0)
    
    # Simple conversion rate - in production, use real-time rates
    usd_hourly = eur_hourly * 1.1 if eur_hourly else 0
    usd_monthly = eur_monthly * 1.1 if eur_monthly else 0
    
    return {
        'provider': 'hetzner',
        'type': data.get('type', 'cloud-server'),
        'instanceType': data.get('instanceType', ''),
        'vCPU': data.get('vCPU', 0),
        'memoryGiB': data.get('memoryGiB', 0),
        'diskType': data.get('diskType'),
        'diskSizeGB': data.get('diskSizeGB'),
        'priceUSD_hourly': round(usd_hourly, 6),
        'priceUSD_monthly': round(usd_monthly, 2),
        'originalPrice': {
            'hourly': eur_hourly,
            'monthly': eur_monthly,
            'currency': 'EUR'
        },
        'regions': data.get('locations', []),
        'deprecated': data.get('deprecated', False),
        'source': data.get('source', 'hetzner_api'),
        'description': data.get('description', ''),
        'lastUpdated': datetime.now().isoformat(),
        'raw': data
    }

def _normalize_aws(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize AWS data - placeholder."""
    return {
        'provider': 'aws',
        'type': 'cloud-server',
        'instanceType': data.get('instanceType', ''),
        'vCPU': data.get('vCPU', 0),
        'memoryGiB': data.get('memoryGiB', 0),
        'priceUSD_hourly': data.get('priceUSD', 0),
        'priceUSD_monthly': data.get('priceUSD', 0) * 24 * 30,
        'regions': data.get('regions', []),
        'lastUpdated': datetime.now().isoformat(),
        'raw': data
    }

def _normalize_azure(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Azure data - placeholder."""
    return {
        'provider': 'azure',
        'type': 'cloud-server',
        'instanceType': data.get('instanceType', ''),
        'vCPU': data.get('vCPU', 0),
        'memoryGiB': data.get('memoryGiB', 0),
        'priceUSD_hourly': data.get('priceUSD', 0),
        'priceUSD_monthly': data.get('priceUSD', 0) * 24 * 30,
        'regions': data.get('regions', []),
        'lastUpdated': datetime.now().isoformat(),
        'raw': data
    }

def _normalize_gcp(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize GCP data - placeholder."""
    return {
        'provider': 'gcp',
        'type': 'cloud-server',
        'instanceType': data.get('instanceType', ''),
        'vCPU': data.get('vCPU', 0),
        'memoryGiB': data.get('memoryGiB', 0),
        'priceUSD_hourly': data.get('priceUSD', 0),
        'priceUSD_monthly': data.get('priceUSD', 0) * 24 * 30,
        'regions': data.get('regions', []),
        'lastUpdated': datetime.now().isoformat(),
        'raw': data
    }

def _normalize_oci(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize OCI data - placeholder."""
    return {
        'provider': 'oci',
        'type': 'cloud-server',
        'instanceType': data.get('instanceType', ''),
        'vCPU': data.get('vCPU', 0),
        'memoryGiB': data.get('memoryGiB', 0),
        'priceUSD_hourly': data.get('priceUSD', 0),
        'priceUSD_monthly': data.get('priceUSD', 0) * 24 * 30,
        'regions': data.get('regions', []),
        'lastUpdated': datetime.now().isoformat(),
        'raw': data
    }

def _normalize_ovh(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize OVH data - placeholder."""
    return {
        'provider': 'ovh',
        'type': 'cloud-server',
        'instanceType': data.get('instanceType', ''),
        'vCPU': data.get('vCPU', 0),
        'memoryGiB': data.get('memoryGiB', 0),
        'priceUSD_hourly': data.get('priceUSD', 0),
        'priceUSD_monthly': data.get('priceUSD', 0) * 24 * 30,
        'regions': data.get('regions', []),
        'lastUpdated': datetime.now().isoformat(),
        'raw': data
    }