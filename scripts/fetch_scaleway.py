#!/usr/bin/env python3
"""
Scaleway Cloud Fetcher for CloudPriceFinder v3.

Uses the public Scaleway product catalog API (no authentication required):
    https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products

The catalog returns one product entry per (instance_type, zone). Products are
grouped by offer_id and prices are uniform across zones for the same type.

Schema notes (as of 2025):
    - service_category: "Compute"  (not "Instances")
    - product_category: "Instance"
    - price format: {units, nanos} (Google Money) — price = units + nanos/1e9
    - locality: {"zone": "fr-par-1"}  (object, not string)
    - hardware specs: properties.hardware.cpu/ram (present for modern instances)
    - instance range: properties.instance.range (maps to family)

Commitment-pricing note:
    Scaleway does not publish public commitment or reserved pricing.
    All prices are hourly on-demand rates in EUR, converted to USD.
    commitments[] is empty for all instances.

Usage:
    python scripts/fetch_scaleway.py [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Repo root import setup
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.currency_converter import convert_currency
from scripts.utils.data_validator import validate_instance_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_scaleway")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCALEWAY_CATALOG_URL = (
    "https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products"
)
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_BACKOFF = 5
PAGE_SIZE = 100
HOURS_PER_MONTH = 730.44

# ---------------------------------------------------------------------------
# Zone → location mapping
# ---------------------------------------------------------------------------

_ZONE_LOCATION: Dict[str, Dict[str, str]] = {
    "fr-par": {"city": "Paris",     "country": "France",       "countryCode": "FR"},
    "nl-ams": {"city": "Amsterdam", "country": "Netherlands",  "countryCode": "NL"},
    "pl-waw": {"city": "Warsaw",    "country": "Poland",       "countryCode": "PL"},
    "it-mil": {"city": "Milan",     "country": "Italy",        "countryCode": "IT"},
}


def _zone_to_location(zone: str) -> Dict[str, str]:
    """Derive city/country from a Scaleway zone code like 'fr-par-1'."""
    parts = zone.rsplit("-", 1)
    prefix = parts[0] if len(parts) > 1 and parts[1].isdigit() else zone
    loc = _ZONE_LOCATION.get(prefix)
    if loc:
        return {
            "code": zone,
            "city": loc["city"],
            "country": loc["country"],
            "countryCode": loc["countryCode"],
            "region": prefix,
        }
    return {
        "code": zone,
        "city": zone,
        "country": zone,
        "countryCode": zone.upper()[:2],
        "region": prefix,
    }


# ---------------------------------------------------------------------------
# Instance range → family mapping
# ---------------------------------------------------------------------------

_RANGE_TO_FAMILY: Dict[str, str] = {
    "General Purpose":  "general-purpose",
    "ARM Based":        "arm",
    "START1":           "start1",
    "X64":              "x64-legacy",
    "VC1":              "vc1-legacy",
    "Cost-Optimized":   "cost-optimized",
    "GPU":              "gpu",
}

# ---------------------------------------------------------------------------
# Fallback specs for instances that don't carry hardware properties.
# Used for AMP2 (retired ARM) and VC1 legacy instances.
# ---------------------------------------------------------------------------

_FALLBACK_SPECS: Dict[str, Dict[str, Any]] = {
    # AMP2 — Ampere Altra Q80-30 ARM (1:2 vCPU:GiB ratio)
    "AMP2-C1":  {"vcpu": 1,  "ram_gib": 2,   "arch": "arm64"},
    "AMP2-C2":  {"vcpu": 2,  "ram_gib": 4,   "arch": "arm64"},
    "AMP2-C4":  {"vcpu": 4,  "ram_gib": 8,   "arch": "arm64"},
    "AMP2-C8":  {"vcpu": 8,  "ram_gib": 16,  "arch": "arm64"},
    "AMP2-C12": {"vcpu": 12, "ram_gib": 24,  "arch": "arm64"},
    "AMP2-C24": {"vcpu": 24, "ram_gib": 48,  "arch": "arm64"},
    "AMP2-C48": {"vcpu": 48, "ram_gib": 96,  "arch": "arm64"},
    "AMP2-C60": {"vcpu": 60, "ram_gib": 120, "arch": "arm64"},
    # VC1 — legacy instances
    "VC1XS": {"vcpu": 1, "ram_gib": 1,  "arch": "x86_64"},
    "VC1XL": {"vcpu": 4, "ram_gib": 32, "arch": "x86_64"},
}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    })
    return session


def _get_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, str]] = None,
) -> Any:
    """Fetch URL with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            logger.warning(
                f"Attempt {attempt} failed for {url}: {exc}. "
                f"Retrying in {RETRY_BACKOFF}s..."
            )
            time.sleep(RETRY_BACKOFF)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


# ---------------------------------------------------------------------------
# Price extraction
# ---------------------------------------------------------------------------


def _extract_price_eur(product: Dict[str, Any]) -> float:
    """Extract EUR hourly price from the Google Money format."""
    rp = product.get("price", {}).get("retail_price", {})
    units = rp.get("units", 0)
    nanos = rp.get("nanos", 0)
    return units + nanos / 1_000_000_000


# ---------------------------------------------------------------------------
# Catalog fetching (paginated)
# ---------------------------------------------------------------------------


def _fetch_all_products(session: requests.Session) -> List[Dict[str, Any]]:
    """Fetch all products from the Scaleway public catalog, following pagination."""
    all_products: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    page_num = 0

    while True:
        page_num += 1
        params: Dict[str, str] = {"page_size": str(PAGE_SIZE)}
        if page_token:
            params["page_token"] = page_token

        logger.info(f"Fetching catalog page {page_num} (token={page_token!r})...")
        data = _get_json(session, SCALEWAY_CATALOG_URL, params)

        products = data.get("products", [])
        all_products.extend(products)
        logger.info(
            f"  Page {page_num}: {len(products)} products "
            f"(running total: {len(all_products)})"
        )

        page_token = data.get("next_page_token") or None
        if not page_token or not products:
            break

    logger.info(f"Fetched {len(all_products)} total products across {page_num} pages")
    return all_products


# ---------------------------------------------------------------------------
# Hardware spec extraction
# ---------------------------------------------------------------------------


def _extract_specs(
    offer_id: str,
    product: Dict[str, Any],
) -> Optional[Tuple[int, float, str]]:
    """
    Extract (vcpu, ram_gib, arch) from a product.

    Tries properties.hardware first; falls back to _FALLBACK_SPECS for
    legacy instances that don't include hardware metadata.
    Returns None if specs cannot be determined.
    """
    hw = product.get("properties", {}).get("hardware", {})
    if hw:
        vcpu: int = hw.get("cpu", {}).get("virtual", {}).get("count", 0)
        ram_bytes: int = hw.get("ram", {}).get("size", 0)
        ram_gib = ram_bytes / (1024 ** 3)
        api_arch: str = hw.get("cpu", {}).get("arch", "x64")
        arch = "arm64" if "arm" in api_arch.lower() else "x86_64"
        if vcpu > 0 and ram_gib > 0:
            return vcpu, ram_gib, arch

    # Fallback for instances without hardware metadata
    fallback = _FALLBACK_SPECS.get(offer_id)
    if fallback:
        return fallback["vcpu"], float(fallback["ram_gib"]), fallback["arch"]

    return None


# ---------------------------------------------------------------------------
# Instance record builder
# ---------------------------------------------------------------------------


def _build_instance(
    offer_id: str,
    zones: List[str],
    price_eur_hourly: float,
    vcpu: int,
    ram_gib: float,
    arch: str,
    family: str,
    timestamp: str,
) -> Dict[str, Any]:
    """Build a v3 CloudInstance record for a Scaleway offer."""
    price_usd_hourly = round(convert_currency(price_eur_hourly, "EUR", "USD"), 6)
    price_usd_monthly = round(price_usd_hourly * HOURS_PER_MONTH, 4)
    price_eur_monthly = round(price_eur_hourly * HOURS_PER_MONTH, 4)

    sorted_zones = sorted(zones)
    location_details = [_zone_to_location(z) for z in sorted_zones]

    return {
        "provider": "scaleway",
        "type": "cloud-server",
        "instanceType": offer_id,
        "vCPU": vcpu,
        "memoryGiB": round(ram_gib, 3),
        "architecture": arch,
        "family": family,
        "priceUSD_hourly": price_usd_hourly,
        "priceUSD_monthly": price_usd_monthly,
        "originalPrice": {
            "hourly": round(price_eur_hourly, 9),
            "monthly": price_eur_monthly,
            "currency": "EUR",
        },
        "regions": sorted_zones,
        "locationDetails": location_details,
        "commitments": [],
        "source": "scaleway_public_catalog_api",
        "description": (
            f"Scaleway {offer_id} — {vcpu} vCPU, {ram_gib:.0f} GiB RAM"
        ),
        "lastUpdated": timestamp,
        "raw": {
            "offer_id": offer_id,
            "price_eur_hourly": round(price_eur_hourly, 9),
            "zones": sorted_zones,
        },
    }


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------


class ScalewayFetcher:
    def __init__(self) -> None:
        self.session = _make_session()

    def fetch_all(self) -> List[Dict[str, Any]]:
        """Fetch all Scaleway compute instances from the public catalog."""
        logger.info("Fetching Scaleway product catalog...")
        products = _fetch_all_products(self.session)

        # Filter: Instance category, billed by the hour, non-zero price
        instance_products = [
            p for p in products
            if p.get("product_category") == "Instance"
            and p.get("unit_of_measure", {}).get("unit") == "hour"
            and _extract_price_eur(p) > 0
            and p.get("locality", {}).get("zone")  # skip global entries
        ]
        logger.info(
            f"Filtered to {len(instance_products)} hourly instance products "
            f"with non-zero price (from {len(products)} total)"
        )

        # Group by offer_id: collect zones, record first-seen price (uniform per type)
        grouped: Dict[str, Dict[str, Any]] = {}
        for p in instance_products:
            inst_props = p.get("properties", {}).get("instance", {})
            offer_id = inst_props.get("offer_id") or p.get("product", "")
            if not offer_id:
                continue

            zone = p["locality"]["zone"]
            price_eur = _extract_price_eur(p)
            prange = inst_props.get("range", "")

            if offer_id not in grouped:
                grouped[offer_id] = {
                    "zones": [],
                    "price_eur": price_eur,
                    "range": prange,
                    "product": p,  # keep first product for hw spec extraction
                }
            grouped[offer_id]["zones"].append(zone)

        logger.info(f"Grouped into {len(grouped)} unique instance types")

        # Build CloudInstance records
        timestamp = datetime.now(timezone.utc).isoformat()
        instances: List[Dict[str, Any]] = []
        skipped: List[str] = []

        for offer_id, group in sorted(grouped.items()):
            specs = _extract_specs(offer_id, group["product"])
            if specs is None:
                logger.warning(f"Cannot determine specs for {offer_id!r} — skipping")
                skipped.append(offer_id)
                continue

            vcpu, ram_gib, arch = specs
            prange = group["range"]
            family = _RANGE_TO_FAMILY.get(prange, prange.lower().replace(" ", "-") or "general-purpose")

            instances.append(
                _build_instance(
                    offer_id=offer_id,
                    zones=group["zones"],
                    price_eur_hourly=group["price_eur"],
                    vcpu=vcpu,
                    ram_gib=ram_gib,
                    arch=arch,
                    family=family,
                    timestamp=timestamp,
                )
            )

        if skipped:
            logger.info(f"Skipped {len(skipped)} instance(s) with unknown specs: {skipped}")

        logger.info(f"Built {len(instances)} Scaleway instance records")
        return instances


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_output(instances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid = []
    for inst in instances:
        if validate_instance_data(inst):
            valid.append(inst)
        else:
            logger.warning(f"Dropping invalid instance: {inst.get('instanceType', 'unknown')}")
    return valid


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scaleway cloud pricing fetcher (CloudPriceFinder v3)"
    )
    parser.add_argument(
        "--output",
        default="data/providers/scaleway.raw.json",
        help="Output JSON file path (default: data/providers/scaleway.raw.json)",
    )
    args = parser.parse_args(argv)

    logger.info("=== Scaleway Fetcher (CloudPriceFinder v3) ===")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    fetcher = ScalewayFetcher()
    instances = fetcher.fetch_all()

    if not instances:
        logger.error("No instances collected — aborting")
        return 1

    valid = _validate_output(instances)
    logger.info(f"Valid instances: {len(valid)}/{len(instances)}")

    if len(valid) < 10:
        logger.error(f"Too few valid instances ({len(valid)}), expected >=10")
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(valid, f, indent=2, ensure_ascii=False)

    logger.info(f"Wrote {len(valid)} instances to {out_path}")

    by_family: Dict[str, int] = {}
    by_region_prefix: Dict[str, int] = {}
    for inst in valid:
        fam = inst.get("family", "unknown")
        by_family[fam] = by_family.get(fam, 0) + 1
        for zone in inst.get("regions", []):
            parts = zone.rsplit("-", 1)
            prefix = parts[0] if len(parts) > 1 and parts[1].isdigit() else zone
            by_region_prefix[prefix] = by_region_prefix.get(prefix, 0) + 1

    logger.info("Summary by family:")
    for k, v in sorted(by_family.items()):
        logger.info(f"  {k}: {v}")
    logger.info("Summary by region prefix:")
    for k, v in sorted(by_region_prefix.items()):
        logger.info(f"  {k}: {v}")

    prices = [i["priceUSD_hourly"] for i in valid]
    logger.info(
        f"Price range: ${min(prices):.4f} – ${max(prices):.4f}/hr "
        f"(median: ${sorted(prices)[len(prices) // 2]:.4f}/hr)"
    )

    return 0


# ---------------------------------------------------------------------------
# Orchestrator compatibility
# ---------------------------------------------------------------------------


def fetch_scaleway_data() -> List[Dict[str, Any]]:
    """Entry point for the orchestrator."""
    fetcher = ScalewayFetcher()
    instances = fetcher.fetch_all()
    return _validate_output(instances)


if __name__ == "__main__":
    sys.exit(main())
