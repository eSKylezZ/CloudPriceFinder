# scripts/fetch_azure.py
"""
Fetch Azure VM pricing and specs using the Azure Retail Prices API.
No authentication required — uses the public retail prices API.

Stage 3 extensions:
- Captures both Consumption and Reservation priceType rows.
- Skips DevTestConsumption rows (non-standard pricing).
- Groups by armSkuName + armRegionName (per-region) so Reservation rows
  can be merged as commitments onto the matching Consumption VM entry.
- Outputs data/providers/azure.raw.json in the v3 schema with commitments[].
"""

import json
import re
import sys
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))
from data_normalizer import normalize_commitments
from azure_regions import get_country_from_azure_region, create_location_detail
from azure_services import (
    is_dedicated_host_service,
)

# ---------------------------------------------------------------------------
# Azure Retail Prices API
# ---------------------------------------------------------------------------
RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
API_VERSION = "2023-01-01-preview"

# We only care about Virtual Machines for v3 (compute-only scope).
# Database and other service fetchers are deferred to v3.1.
VM_SERVICE_NAME = "Virtual Machines"

# Reservation term strings as returned by the API
TERM_MAP = {
    "1 Year": "1yr",
    "3 Years": "3yr",
}

# ---------------------------------------------------------------------------
# ARM-architecture detection
# ---------------------------------------------------------------------------
_ARM_SKU_RE = re.compile(r'_p[1-9]\d*[bmsv]*', re.IGNORECASE)  # Dpsv5, Epsv6 etc.
_ARM_KEYWORDS = ('ampere', 'arm', '_p')

def detect_architecture(sku_name: str) -> str:
    """Return 'arm64' for known ARM SKUs, else 'x86_64'."""
    if not sku_name:
        return 'x86_64'
    s = sku_name.lower()
    if 'dpds' in s or 'dpsv' in s or 'epsv' in s or 'dpls' in s or '_p' in s:
        return 'arm64'
    return 'x86_64'

# ---------------------------------------------------------------------------
# Family extraction
# ---------------------------------------------------------------------------
_FAMILY_RE = re.compile(r'^Standard_([A-Z]+)', re.IGNORECASE)

def extract_family(sku_name: str) -> str:
    """Extract normalised family id from Azure SKU name (e.g. 'Standard_D2s_v5' -> 'd')."""
    if not sku_name:
        return 'unknown'
    m = _FAMILY_RE.match(sku_name)
    if m:
        return m.group(1).lower()
    return sku_name.split('_')[0].lower() if '_' in sku_name else sku_name.lower()

# ---------------------------------------------------------------------------
# SKU spec lookup table (vCPU, memoryGiB)
# ---------------------------------------------------------------------------
# Comprehensive exact-match table; pattern fallback below if not found.
EXACT_SKU_SPECS: Dict[str, Tuple[int, float]] = {
    # A-series
    'Standard_A0': (1, 0.768), 'Standard_A1': (1, 1.75), 'Standard_A2': (2, 3.5),
    'Standard_A3': (4, 7), 'Standard_A4': (8, 14), 'Standard_A5': (2, 14),
    'Standard_A6': (4, 28), 'Standard_A7': (8, 56),
    # Av2
    'Standard_A1_v2': (1, 2), 'Standard_A2_v2': (2, 4), 'Standard_A4_v2': (4, 8),
    'Standard_A8_v2': (8, 16), 'Standard_A2m_v2': (2, 16), 'Standard_A4m_v2': (4, 32),
    'Standard_A8m_v2': (8, 64),
    # B-series (burstable)
    'Standard_B1ls': (1, 0.5), 'Standard_B1s': (1, 1), 'Standard_B1ms': (1, 2),
    'Standard_B2s': (2, 4), 'Standard_B2ms': (2, 8), 'Standard_B4ms': (4, 16),
    'Standard_B8ms': (8, 32), 'Standard_B12ms': (12, 48), 'Standard_B16ms': (16, 64),
    'Standard_B20ms': (20, 80), 'Standard_B32ms': (32, 128), 'Standard_B48ms': (48, 192),
    'Standard_B64ms': (64, 256), 'Standard_B80ms': (80, 320),
    # D-series v5
    'Standard_D2s_v5': (2, 8), 'Standard_D4s_v5': (4, 16), 'Standard_D8s_v5': (8, 32),
    'Standard_D16s_v5': (16, 64), 'Standard_D32s_v5': (32, 128),
    'Standard_D48s_v5': (48, 192), 'Standard_D64s_v5': (64, 256),
    'Standard_D96s_v5': (96, 384),
    # D-series v4
    'Standard_D2s_v4': (2, 8), 'Standard_D4s_v4': (4, 16), 'Standard_D8s_v4': (8, 32),
    'Standard_D16s_v4': (16, 64), 'Standard_D32s_v4': (32, 128),
    'Standard_D48s_v4': (48, 192), 'Standard_D64s_v4': (64, 256),
    # D-series v3
    'Standard_D2s_v3': (2, 8), 'Standard_D4s_v3': (4, 16), 'Standard_D8s_v3': (8, 32),
    'Standard_D16s_v3': (16, 64), 'Standard_D32s_v3': (32, 128),
    'Standard_D48s_v3': (48, 192), 'Standard_D64s_v3': (64, 256),
    # E-series v5
    'Standard_E2s_v5': (2, 16), 'Standard_E4s_v5': (4, 32), 'Standard_E8s_v5': (8, 64),
    'Standard_E16s_v5': (16, 128), 'Standard_E20s_v5': (20, 160),
    'Standard_E32s_v5': (32, 256), 'Standard_E48s_v5': (48, 384),
    'Standard_E64s_v5': (64, 512), 'Standard_E96s_v5': (96, 672),
    'Standard_E104is_v5': (104, 672),
    # E-series v4
    'Standard_E2s_v4': (2, 16), 'Standard_E4s_v4': (4, 32), 'Standard_E8s_v4': (8, 64),
    'Standard_E16s_v4': (16, 128), 'Standard_E20s_v4': (20, 160),
    'Standard_E32s_v4': (32, 256), 'Standard_E48s_v4': (48, 384),
    'Standard_E64s_v4': (64, 512),
    # F-series
    'Standard_F1': (1, 2), 'Standard_F2': (2, 4), 'Standard_F4': (4, 8),
    'Standard_F8': (8, 16), 'Standard_F16': (16, 32),
    # Fsv2
    'Standard_F2s_v2': (2, 4), 'Standard_F4s_v2': (4, 8), 'Standard_F8s_v2': (8, 16),
    'Standard_F16s_v2': (16, 32), 'Standard_F32s_v2': (32, 64),
    'Standard_F48s_v2': (48, 96), 'Standard_F64s_v2': (64, 128),
    'Standard_F72s_v2': (72, 144),
    # M-series
    'Standard_M8ms': (8, 218.75), 'Standard_M16ms': (16, 437.5),
    'Standard_M32ts': (32, 192), 'Standard_M32ls': (32, 256),
    'Standard_M32ms': (32, 875), 'Standard_M64s': (64, 1024),
    'Standard_M64ls': (64, 512), 'Standard_M64ms': (64, 1792),
    'Standard_M128s': (128, 2048), 'Standard_M128ms': (128, 3892),
    # NC-series (GPU)
    'Standard_NC6': (6, 56), 'Standard_NC12': (12, 112), 'Standard_NC24': (24, 224),
    'Standard_NC6s_v3': (6, 112), 'Standard_NC12s_v3': (12, 224),
    'Standard_NC24s_v3': (24, 448),
    # NV-series (GPU)
    'Standard_NV6': (6, 56), 'Standard_NV12': (12, 112), 'Standard_NV24': (24, 224),
    # L-series
    'Standard_L4s': (4, 32), 'Standard_L8s': (8, 64), 'Standard_L16s': (16, 128),
    'Standard_L32s': (32, 256), 'Standard_L8s_v2': (8, 64), 'Standard_L16s_v2': (16, 128),
    'Standard_L32s_v2': (32, 256), 'Standard_L48s_v2': (48, 384),
    'Standard_L64s_v2': (64, 512), 'Standard_L80s_v2': (80, 640),
}

# Pattern-based fallback (vCPU, memory ratio)
_SKU_PATTERNS = [
    (re.compile(r'^Standard_D(\d+)s?_v\d+$', re.I), lambda n: (n, n * 4)),
    (re.compile(r'^Standard_E(\d+)i?s?_v\d+$', re.I), lambda n: (n, n * 8)),
    (re.compile(r'^Standard_F(\d+)s?_v\d+$', re.I), lambda n: (n, n * 2)),
    (re.compile(r'^Standard_M(\d+)m?s?$', re.I), lambda n: (n, n * 14)),
    (re.compile(r'^Standard_NC(\d+)r?s?_v\d+$', re.I), lambda n: (n, n * 14)),
    (re.compile(r'^Standard_H(\d+)r?s?$', re.I), lambda n: (n, n * 7)),
    (re.compile(r'^Standard_D(\d+)$', re.I), lambda n: (n, n * 3.5)),
    (re.compile(r'^Standard_E(\d+)$', re.I), lambda n: (n, n * 8)),
    (re.compile(r'^Standard_F(\d+)$', re.I), lambda n: (n, n * 2)),
]


def parse_sku_specs(sku_name: str) -> Tuple[Optional[int], Optional[float]]:
    """Return (vCPU, memoryGiB) for the given Azure SKU, or (None, None)."""
    if not sku_name:
        return None, None

    if sku_name in EXACT_SKU_SPECS:
        return EXACT_SKU_SPECS[sku_name]

    for pattern, extractor in _SKU_PATTERNS:
        m = pattern.match(sku_name)
        if m:
            n = int(m.group(1))
            vcpu, mem = extractor(n)
            return int(vcpu), float(mem)

    # Last-resort: extract first number
    m = re.search(r'[A-Z](\d+)', sku_name)
    if m:
        n = int(m.group(1))
        s = sku_name.upper()
        if 'E' in s:
            return n, float(n * 8)
        if 'F' in s:
            return n, float(n * 2)
        if 'M' in s:
            return n, float(n * 14)
        return n, float(n * 4)

    return None, None



# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

def _page_items(price_type_filter: str) -> List[Dict[str, Any]]:
    """
    Fetch all pages from the Azure Retail Prices API for Virtual Machines
    with the given priceType filter, returning the combined list of items.
    """
    params = {
        "$filter": (
            f"serviceName eq '{VM_SERVICE_NAME}' and priceType eq '{price_type_filter}'"
        ),
        "api-version": API_VERSION,
    }
    items: List[Dict[str, Any]] = []
    url: Optional[str] = RETAIL_PRICES_URL
    page = 0

    while url:
        page += 1
        try:
            if page == 1:
                resp = requests.get(url, params=params, timeout=60)
            else:
                resp = requests.get(url, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [page {page}] Request error: {exc}", flush=True)
            break

        body = resp.json()
        batch = body.get('Items', []) if isinstance(body, dict) else body
        items.extend(batch)
        url = body.get('NextPageLink') if isinstance(body, dict) else None
        if page % 5 == 0:
            print(f"  ... fetched {len(items)} {price_type_filter} records so far "
                  f"(page {page})", flush=True)

    print(f"  Total {price_type_filter} records fetched: {len(items)}", flush=True)
    return items


def fetch_azure_data() -> List[Dict[str, Any]]:
    """
    Fetch Azure VM pricing (Consumption + Reservation) from the public
    Retail Prices API and return a list of normalised instance dicts
    in v3 schema format with commitments[].
    """
    now = datetime.utcnow().isoformat()

    # ------------------------------------------------------------------
    # 1. Fetch Consumption rows (on-demand pricing)
    # ------------------------------------------------------------------
    print("Fetching Consumption (on-demand) rows…", flush=True)
    consumption_items = _page_items('Consumption')

    # ------------------------------------------------------------------
    # 2. Fetch Reservation rows (1yr / 3yr committed pricing)
    # ------------------------------------------------------------------
    print("Fetching Reservation rows…", flush=True)
    reservation_items = _page_items('Reservation')

    # ------------------------------------------------------------------
    # 3. Build consumption groups keyed by (armSkuName, armRegionName)
    #
    # Key choice: armSkuName + armRegionName gives us per-region per-SKU
    # entries so that reservation rows (which also carry armRegionName)
    # can be merged onto the correct base record.
    # ------------------------------------------------------------------
    # consumption_groups: key -> instance dict (will grow commitments[])
    consumption_groups: Dict[Tuple[str, str], Dict[str, Any]] = {}

    skipped_windows_sql = 0
    skipped_devtest = 0
    skipped_no_sku = 0
    skipped_no_specs = 0

    for item in consumption_items:
        sku_name: str = item.get('armSkuName', '') or ''
        region: str = item.get('armRegionName', '') or ''
        service_name: str = item.get('serviceName', '')
        meter_name: str = item.get('meterName', '') or ''
        price_type: str = item.get('priceType', '')

        # Only Virtual Machines service
        if service_name != VM_SERVICE_NAME:
            continue

        # Skip DevTestConsumption — not standard pricing
        if price_type == 'DevTestConsumption':
            skipped_devtest += 1
            continue

        # Skip Windows and SQL variants (prefer Linux on-demand)
        if 'Windows' in meter_name or 'SQL' in meter_name:
            skipped_windows_sql += 1
            continue

        # Skip Spot and Low Priority pricing — those are not standard on-demand.
        sku_display: str = item.get('skuName', '') or ''
        if 'Spot' in sku_display or 'Low Priority' in sku_display:
            continue

        # Skip dedicated host (scope: compute VMs only)
        if is_dedicated_host_service(service_name, meter_name):
            continue

        if not sku_name:
            skipped_no_sku += 1
            continue

        vcpu, mem_gib = parse_sku_specs(sku_name)
        if vcpu is None or mem_gib is None:
            skipped_no_specs += 1
            continue

        # Determine region → country
        country = get_country_from_azure_region(region)
        loc_detail = create_location_detail(region, country)

        hourly = float(item.get('unitPrice', 0) or 0)
        monthly = round(hourly * 730.44, 4)

        key = (sku_name, region)
        if key not in consumption_groups:
            consumption_groups[key] = {
                'provider': 'azure',
                'type': 'cloud-server',
                'instanceType': sku_name,
                'vCPU': vcpu,
                'memoryGiB': float(mem_gib),
                'priceUSD_hourly': hourly,
                'priceUSD_monthly': monthly,
                'architecture': detect_architecture(sku_name),
                'family': extract_family(sku_name),
                'commitments': [],
                'regions': [country] if country else [],
                'locationDetails': [loc_detail],
                'source': 'Azure Retail Prices API',
                'lastUpdated': now,
                'marketSegment': 'global',
                'operatedBy': 'Microsoft',
                'raw': {
                    'serviceName': service_name,
                    'meterName': meter_name,
                    'armSkuName': sku_name,
                    'armRegionName': region,
                    'location': item.get('location', ''),
                    'currencyCode': item.get('currencyCode', 'USD'),
                },
            }
        else:
            # If same SKU+region appears again with a lower price, keep lower
            existing = consumption_groups[key]
            if hourly > 0 and (existing['priceUSD_hourly'] == 0 or hourly < existing['priceUSD_hourly']):
                existing['priceUSD_hourly'] = hourly
                existing['priceUSD_monthly'] = monthly

    unique_regions_seen = len({region for (_, region) in consumption_groups})
    print(f"Consumption groups built: {len(consumption_groups)} (across {unique_regions_seen} regions — no region filter applied, Azure API is global)", flush=True)
    print(f"  Skipped — Windows/SQL: {skipped_windows_sql}, DevTest: {skipped_devtest}, "
          f"no SKU: {skipped_no_sku}, no specs: {skipped_no_specs}", flush=True)

    # ------------------------------------------------------------------
    # 4. Merge Reservation rows as commitments
    # ------------------------------------------------------------------
    reservation_matched = 0
    reservation_unmatched = 0
    skipped_res_windows_sql = 0

    for item in reservation_items:
        sku_name: str = item.get('armSkuName', '') or ''
        region: str = item.get('armRegionName', '') or ''
        meter_name: str = item.get('meterName', '') or ''
        reservation_term: str = item.get('reservationTerm', '') or ''

        # Skip Windows/SQL reservations
        if 'Windows' in meter_name or 'SQL' in meter_name:
            skipped_res_windows_sql += 1
            continue

        if not sku_name or not region:
            reservation_unmatched += 1
            continue

        term_key = TERM_MAP.get(reservation_term)
        if term_key is None:
            # Unknown term — skip
            reservation_unmatched += 1
            continue

        key = (sku_name, region)
        base = consumption_groups.get(key)
        if base is None:
            # No matching consumption entry; create a bare base so the
            # reservation is still represented (on-demand price = 0).
            vcpu, mem_gib = parse_sku_specs(sku_name)
            if vcpu is None:
                reservation_unmatched += 1
                continue

            if region.lower() in china_region_map:
                country = china_region_map[region.lower()]
            else:
                country = get_country_from_azure_region(region)
            loc_detail = create_location_detail(region, country)

            base = {
                'provider': 'azure',
                'type': 'cloud-server',
                'instanceType': sku_name,
                'vCPU': int(vcpu),
                'memoryGiB': float(mem_gib),
                'priceUSD_hourly': 0.0,
                'priceUSD_monthly': 0.0,
                'architecture': detect_architecture(sku_name),
                'family': extract_family(sku_name),
                'commitments': [],
                'regions': [country] if country else [],
                'locationDetails': [loc_detail],
                'source': 'Azure Retail Prices API (reservation-only)',
                'lastUpdated': now,
                'marketSegment': 'global',
                'operatedBy': 'Microsoft',
                'raw': {
                    'serviceName': VM_SERVICE_NAME,
                    'armSkuName': sku_name,
                    'armRegionName': region,
                },
            }
            consumption_groups[key] = base

        # Azure Retail Prices API returns the *total* reservation cost for the
        # term (despite unitOfMeasure saying "1 Hour").  We must amortise it.
        # e.g. Standard_D2s_v5 1yr = $496 total → $496/8760 ≈ $0.0566/hr
        term_hours = 8760 if term_key == '1yr' else 26280
        total_cost = float(item.get('unitPrice', 0) or 0)
        amortised_hourly = round(total_cost / term_hours, 8) if term_hours > 0 else 0.0

        raw_commitment = {
            'term': term_key,
            'payment': 'all-upfront',  # Azure retail exposes upfront-equivalent amortised hourly
            'product': 'reserved',
            'priceUSD_hourly': amortised_hourly,
            # upfront_usd omitted because effectiveHourlyUSD == amortised_hourly already
        }
        normalised = normalize_commitments([raw_commitment], base['priceUSD_hourly'])
        if normalised:
            # Avoid duplicate term entries
            existing_terms = {(c['term'], c['payment']) for c in base['commitments']}
            for nc in normalised:
                if (nc['term'], nc['payment']) not in existing_terms:
                    base['commitments'].append(nc)
                    existing_terms.add((nc['term'], nc['payment']))
            reservation_matched += 1
        else:
            reservation_unmatched += 1

    print(f"Reservations merged: {reservation_matched} matched, "
          f"{reservation_unmatched} unmatched/skipped, "
          f"{skipped_res_windows_sql} Windows/SQL skipped.", flush=True)

    # ------------------------------------------------------------------
    # 5. Collapse per-region groups into per-SKU instances
    #
    # Following the AWS fetcher pattern (Stage 2): one record per SKU across
    # all regions.  We use eastus as the canonical pricing reference region —
    # it is the primary Azure region where most standard pricing is anchored.
    # If eastus is absent for a SKU, fall back to the first encountered region.
    #
    # Commitments are kept from the canonical region only; duplicates (same
    # term+payment) from other regions are discarded.  savingsVsOnDemandPct is
    # computed against the canonical region's on-demand price so the validator
    # cross-field check is always consistent.
    # ------------------------------------------------------------------
    CANONICAL_REGION = 'eastus'

    # First pass: group per-SKU with a preference for the canonical region.
    # We store all per-region data first, then select the canonical.
    sku_region_map: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for (sku_name, region), inst in consumption_groups.items():
        sku_region_map.setdefault(sku_name, {})[region] = inst

    per_sku: Dict[str, Dict[str, Any]] = {}
    for sku_name, region_data in sku_region_map.items():
        # Pick canonical region
        if CANONICAL_REGION in region_data:
            canonical_inst = region_data[CANONICAL_REGION]
        else:
            canonical_inst = next(iter(region_data.values()))

        canonical_od = canonical_inst['priceUSD_hourly']

        # Build the merged per-SKU record from the canonical instance
        agg = {
            **canonical_inst,
            'regions': [],
            'locationDetails': [],
            'commitments': list(canonical_inst.get('commitments', [])),
            'regionPricing': {},
        }

        # Populate regions + regionPricing from all regions
        seen_locs: set = set()
        seen_countries: set = set()
        for region, inst in region_data.items():
            od = inst['priceUSD_hourly']
            if od > 0:
                agg['regionPricing'][region] = od
            for country in inst.get('regions', []):
                if country not in seen_countries:
                    agg['regions'].append(country)
                    seen_countries.add(country)
            for ld in inst.get('locationDetails', []):
                code = ld['code']
                if code not in seen_locs:
                    agg['locationDetails'].append(ld)
                    seen_locs.add(code)

        # Re-normalise commitments against canonical on-demand
        normalised_commitments = []
        seen_term_payment: set = set()
        for c in agg['commitments']:
            key = (c['term'], c['payment'])
            if key in seen_term_payment:
                continue
            seen_term_payment.add(key)
            effective = c.get('effectiveHourlyUSD', c.get('priceUSD_hourly', 0))
            if canonical_od > 0:
                savings = max(0.0, min(100.0,
                    (1.0 - effective / canonical_od) * 100.0))
            else:
                savings = 0.0
            normalised_commitments.append({
                'term': c['term'],
                'payment': c['payment'],
                'product': c['product'],
                'priceUSD_hourly': c['priceUSD_hourly'],
                'effectiveHourlyUSD': round(effective, 8),
                'savingsVsOnDemandPct': round(savings, 2),
            })
        agg['commitments'] = normalised_commitments

        per_sku[sku_name] = agg

    instances = list(per_sku.values())
    print(f"Final unique SKU count (global): {len(instances)}", flush=True)

    # ------------------------------------------------------------------
    # 6. Basic validation: drop instances with no price and no specs
    # ------------------------------------------------------------------
    valid: List[Dict[str, Any]] = []
    dropped = 0
    for inst in instances:
        if inst['vCPU'] and inst['memoryGiB'] and (inst['priceUSD_hourly'] > 0 or inst['commitments']):
            valid.append(inst)
        else:
            dropped += 1

    print(f"After filtering: {len(valid)} valid instances, {dropped} dropped.", flush=True)
    return valid


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.makedirs("data/providers", exist_ok=True)
    output_path = "data/providers/azure.raw.json"

    print("=== Azure fetcher ===", flush=True)

    instances = fetch_azure_data()

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(instances, f, indent=2)

    with_commitments = sum(1 for i in instances if i.get('commitments'))

    print(f"\nSaved {len(instances)} instances to {output_path}")
    print(f"  - Instances with commitments[]: {with_commitments}")
