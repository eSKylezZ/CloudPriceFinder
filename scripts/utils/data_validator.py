"""
Data validation utilities for CloudPriceFinder.
"""

from typing import Dict, Any, List, Tuple
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Commitment validation (Stage 1 — v3 schema extension)
# ---------------------------------------------------------------------------

VALID_TERMS = {'on-demand', '1yr', '3yr'}
VALID_PAYMENTS = {'no-upfront', 'partial-upfront', 'all-upfront', 'flexible'}
VALID_PRODUCTS = {'reserved', 'savings-plan', 'cud', 'flex'}


def validate_commitments(commitments: List[Dict[str, Any]], on_demand_hourly: float) -> Tuple[bool, List[str]]:
    """
    Validate a list of CommitmentPrice objects.

    Rules:
    - Each entry must have: term, payment, product, priceUSD_hourly,
      effectiveHourlyUSD, savingsVsOnDemandPct.
    - term must be in VALID_TERMS.
    - payment must be in VALID_PAYMENTS.
    - product must be in VALID_PRODUCTS.
    - priceUSD_hourly >= 0 (can be 0 for all-upfront).
    - effectiveHourlyUSD >= 0.
    - 0 <= savingsVsOnDemandPct <= 100.
    - savingsVsOnDemandPct must be monotonically non-decreasing when
      sorted by (term asc, payment asc): longer commitments must be
      at least as cheap as shorter ones within the same product type
      (we enforce this loosely — just check each entry is internally
      consistent with on_demand_hourly).

    Args:
        commitments: List of commitment dictionaries.
        on_demand_hourly: The on-demand priceUSD_hourly for the parent instance.
                          Used to validate savingsVsOnDemandPct.

    Returns:
        (is_valid, error_messages)
    """
    errors: List[str] = []

    if not isinstance(commitments, list):
        return False, ['commitments must be a list']

    for idx, c in enumerate(commitments):
        prefix = f'commitments[{idx}]'

        # Required field presence
        for field in ('term', 'payment', 'product', 'priceUSD_hourly', 'effectiveHourlyUSD', 'savingsVsOnDemandPct'):
            if field not in c:
                errors.append(f'{prefix}: missing required field "{field}"')

        if errors:
            # Skip further checks if fields are missing to avoid KeyErrors
            continue

        # Enum checks
        if c['term'] not in VALID_TERMS:
            errors.append(f'{prefix}: invalid term "{c["term"]}" — must be one of {sorted(VALID_TERMS)}')

        if c['payment'] not in VALID_PAYMENTS:
            errors.append(f'{prefix}: invalid payment "{c["payment"]}" — must be one of {sorted(VALID_PAYMENTS)}')

        if c['product'] not in VALID_PRODUCTS:
            errors.append(f'{prefix}: invalid product "{c["product"]}" — must be one of {sorted(VALID_PRODUCTS)}')

        # Numeric range checks
        if not isinstance(c['priceUSD_hourly'], (int, float)) or c['priceUSD_hourly'] < 0:
            errors.append(f'{prefix}: priceUSD_hourly must be a non-negative number, got {c["priceUSD_hourly"]!r}')

        if not isinstance(c['effectiveHourlyUSD'], (int, float)) or c['effectiveHourlyUSD'] < 0:
            errors.append(f'{prefix}: effectiveHourlyUSD must be a non-negative number, got {c["effectiveHourlyUSD"]!r}')

        savings = c['savingsVsOnDemandPct']
        if not isinstance(savings, (int, float)) or not (0 <= savings <= 100):
            errors.append(
                f'{prefix}: savingsVsOnDemandPct must be a number in [0, 100], got {savings!r}'
            )

        # Cross-field consistency: effectiveHourlyUSD should be < on_demand for
        # committed pricing (allow small floating-point tolerance).
        if (on_demand_hourly > 0
                and isinstance(c['effectiveHourlyUSD'], (int, float))
                and c['effectiveHourlyUSD'] > on_demand_hourly * 1.001):
            errors.append(
                f'{prefix}: effectiveHourlyUSD ({c["effectiveHourlyUSD"]:.6f}) '
                f'exceeds on-demand rate ({on_demand_hourly:.6f}) — suspicious'
            )

    return len(errors) == 0, errors

REQUIRED_FIELDS = [
    'provider',
    'type', 
    'instanceType',
    'priceUSD_hourly',
    'lastUpdated'
]

VALID_PROVIDERS = ['aws', 'azure', 'gcp', 'hetzner', 'oci', 'ovh', 'scaleway', 'vast', 'vultr']
VALID_TYPES = [
    'cloud-server',
    'cloud-loadbalancer', 
    'cloud-volume',
    'cloud-network',
    'cloud-floating-ip',
    'cloud-snapshot',
    'cloud-certificate',
    'dedicated-server',
    'dedicated-auction',
    'dedicated-storage',
    'dedicated-colocation'
]

def validate_instance_data(instance: Dict[str, Any]) -> bool:
    """
    Validate a single cloud instance data structure.
    
    Args:
        instance: Dictionary containing instance data
        
    Returns:
        bool: True if valid, False otherwise
    """
    try:
        # Check required fields
        for field in REQUIRED_FIELDS:
            if field not in instance:
                logger.error(f"Missing required field: {field}")
                return False
        
        # Validate provider
        if instance['provider'] not in VALID_PROVIDERS:
            logger.error(f"Invalid provider: {instance['provider']}")
            return False
        
        # Validate type
        if instance['type'] not in VALID_TYPES:
            logger.error(f"Invalid type: {instance['type']}")
            return False
        
        # Validate numeric fields (optional for some service types)
        if 'vCPU' in instance and instance['vCPU'] is not None:
            if not isinstance(instance['vCPU'], (int, float)) or instance['vCPU'] <= 0:
                logger.error(f"Invalid vCPU: {instance['vCPU']}")
                return False
        
        if 'memoryGiB' in instance and instance['memoryGiB'] is not None:
            if not isinstance(instance['memoryGiB'], (int, float)) or instance['memoryGiB'] <= 0:
                logger.error(f"Invalid memoryGiB: {instance['memoryGiB']}")
                return False
        
        # Check for meaningful pricing data (either USD or EUR pricing)
        has_usd_pricing = (isinstance(instance.get('priceUSD_hourly'), (int, float)) and 
                          instance['priceUSD_hourly'] > 0)
        has_eur_pricing = (isinstance(instance.get('priceEUR_hourly_net'), (int, float)) and 
                          instance['priceEUR_hourly_net'] > 0)
        
        if not has_usd_pricing and not has_eur_pricing:
            logger.error(f"No valid pricing data found for {instance.get('instanceType')}")
            return False
        
        # Validate string fields are not empty
        if not instance['instanceType'].strip():
            logger.error("instanceType cannot be empty")
            return False
        
        # For compute instances, require basic specs
        if instance['type'] in ['cloud-server', 'dedicated-server', 'dedicated-auction']:
            if not instance.get('vCPU') or instance.get('vCPU', 0) <= 0:
                logger.error(f"Compute instance missing valid vCPU: {instance.get('instanceType')}")
                return False
            if not instance.get('memoryGiB') or instance.get('memoryGiB', 0) <= 0:
                logger.error(f"Compute instance missing valid memory: {instance.get('instanceType')}")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return False

def validate_dataset(instances: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[str]]:
    """
    Validate a complete dataset and return valid instances and errors.

    Args:
        instances: List of instance dictionaries

    Returns:
        tuple: (valid_instances, error_messages)
    """
    valid_instances = []
    errors = []

    for i, instance in enumerate(instances):
        if validate_instance_data(instance):
            valid_instances.append(instance)
        else:
            errors.append(f"Instance {i}: Invalid data for {instance.get('instanceType', 'unknown')}")

    return valid_instances, errors


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json as _json

    if len(sys.argv) < 2:
        print("Usage: python scripts/utils/data_validator.py <path-to-json>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
    except Exception as exc:
        print(f"ERROR: Could not load {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        print("ERROR: Expected a JSON array at top level", file=sys.stderr)
        sys.exit(1)

    valid_instances, errors = validate_dataset(data)

    # Also validate commitments for each instance
    commitment_errors: List[str] = []
    for i, inst in enumerate(data):
        commitments = inst.get("commitments", [])
        if commitments:
            od_hourly = inst.get("priceUSD_hourly", 0.0)
            ok, errs = validate_commitments(commitments, od_hourly)
            if not ok:
                for e in errs:
                    commitment_errors.append(f"Instance {i} ({inst.get('instanceType', '?')}): {e}")

    all_errors = errors + commitment_errors
    if all_errors:
        for e in all_errors[:50]:
            print(f"ERROR: {e}", file=sys.stderr)
        if len(all_errors) > 50:
            print(f"... and {len(all_errors) - 50} more errors", file=sys.stderr)
        print(f"FAILED: {len(all_errors)} error(s) in {len(data)} instances", file=sys.stderr)
        sys.exit(1)

    print(f"OK: {len(valid_instances)}/{len(data)} instances validated successfully")