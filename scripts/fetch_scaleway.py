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
from scripts.utils.http_client import HOURS_PER_MONTH, get_json, make_session

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
PAGE_SIZE = 1000

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
# Deprecated / EOL instance ranges — filtered out before processing
# ---------------------------------------------------------------------------

_DEPRECATED_RANGES: set[str] = {
    "START1",    # deprecated 2021, replaced by PLAY2
    "X64",       # legacy bare-metal style, not currently bookable
    "VC1",       # legacy VPS-style, replaced by PLAY2/POP2
    "DEV1",      # deprecated development tier
    "GP1",       # legacy general purpose
    "STARDUST1", # free tier dev instance, not generally available
}

# ---------------------------------------------------------------------------
# Instance range → family mapping
# ---------------------------------------------------------------------------

_RANGE_TO_FAMILY: Dict[str, str] = {
    "General Purpose":  "general-purpose",
    "ARM Based":        "arm",
    "Cost-Optimized":   "cost-optimized",
    "GPU":              "gpu",
    "PLAY2":            "play2",
    "POP2":             "pop2",
    "POP2-HM":          "pop2-hm",
    "POP2-HC":          "pop2-hc",
    "COPARM1":          "arm",
    "H100":             "gpu",
    "RENDER":           "gpu",
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
# Bare-metal / Dedibox constants
# ---------------------------------------------------------------------------

# Category name used by the public catalog API for Elastic Metal bare-metal servers.
# Note: classic "Dedibox" dedicated servers are NOT in the public catalog API;
# those are handled separately via the _DEDIBOX_START_SPECS static table.
_ELASTIC_METAL_CATEGORY: str = "Elastic Metal"

# Maps commitment duration in months → CommitmentPrice.term (schema)
_COMMITMENT_MONTHS_TO_TERM: Dict[int, str] = {
    12: "1yr",
    24: "2yr",
    36: "3yr",
}

# Fallback hardware specs for Dedibox Start plans.
# Key: lowercase offer_id as returned by the API (hyphens, no underscores).
# vcpu = logical CPU threads (what the OS sees, including HyperThreading).
# Combined spec + pricing table for Dedibox Start plans.
# Dedibox is not in the Scaleway public catalog API; this table is the source of truth.
# commitment_prices: {months: eur_monthly} — fill in from scaleway.com/en/dedibox/
# vcpu = logical CPU threads (what the OS sees, including HyperThreading)
_DEDIBOX_START_SPECS: Dict[str, Dict[str, Any]] = {
    # offer_id                   vcpu  ram   arch     disk     disk_gb  price/mo  commitments {mo: eur}
    "start-2-s-sata": {"vcpu": 2,  "ram_gib": 4.0,  "arch": "x86_64", "disk_type": "HDD",  "disk_size_gb": 1000.0, "price_eur": 4.99,  "commitment_prices": {}},
    "start-2-s-ssd":  {"vcpu": 2,  "ram_gib": 4.0,  "arch": "x86_64", "disk_type": "SSD",  "disk_size_gb": 120.0,  "price_eur": 4.99,  "commitment_prices": {}},
    "start-3-s-ssd":  {"vcpu": 2,  "ram_gib": 4.0,  "arch": "x86_64", "disk_type": "SSD",  "disk_size_gb": 250.0,  "price_eur": 4.99,  "commitment_prices": {}},
    "start-1-m-sata": {"vcpu": 8,  "ram_gib": 8.0,  "arch": "x86_64", "disk_type": "HDD",  "disk_size_gb": 1000.0, "price_eur": 13.99, "commitment_prices": {}},
    "start-2-m-sata": {"vcpu": 8,  "ram_gib": 16.0, "arch": "x86_64", "disk_type": "HDD",  "disk_size_gb": 1000.0, "price_eur": 15.99, "commitment_prices": {}},
    "start-2-m-ssd":  {"vcpu": 8,  "ram_gib": 16.0, "arch": "x86_64", "disk_type": "SSD",  "disk_size_gb": 250.0,  "price_eur": 15.99, "commitment_prices": {}},
    "start-1-l":      {"vcpu": 4,  "ram_gib": 16.0, "arch": "x86_64", "disk_type": "HDD",  "disk_size_gb": 2000.0, "price_eur": 19.99, "commitment_prices": {}},
    "start-2-l":      {"vcpu": 12, "ram_gib": 32.0, "arch": "x86_64", "disk_type": "SSD",  "disk_size_gb": 500.0,  "price_eur": 24.99, "commitment_prices": {}},
    "start-3-l":      {"vcpu": 12, "ram_gib": 32.0, "arch": "x86_64", "disk_type": "SSD",  "disk_size_gb": 1000.0, "price_eur": 34.99, "commitment_prices": {}},
    "start-9-m":      {"vcpu": 12, "ram_gib": 32.0, "arch": "x86_64", "disk_type": "NVMe", "disk_size_gb": 2000.0, "price_eur": 39.99, "commitment_prices": {}},
}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------




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
    """Fetch all products from the Scaleway public catalog, following pagination.

    The API uses page-number pagination (?page=N), not token-based.
    """
    all_products: List[Dict[str, Any]] = []
    page = 1

    while True:
        params: Dict[str, str] = {"page_size": str(PAGE_SIZE), "page": str(page)}
        logger.info(f"Fetching catalog page {page}...")
        data = get_json(session, SCALEWAY_CATALOG_URL, params)

        products = data.get("products", [])
        all_products.extend(products)
        logger.info(
            f"  Page {page}: {len(products)} products "
            f"(running total: {len(all_products)})"
        )

        if len(products) < PAGE_SIZE:
            break
        page += 1

    logger.info(f"Fetched {len(all_products)} total products across {page} pages")
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
# Dedibox spec resolver
# ---------------------------------------------------------------------------


def _get_dedibox_specs(
    offer_id: str,
    product: Dict[str, Any],
) -> Optional[Tuple[int, float, str]]:
    """Return (vcpu, ram_gib, arch) for a Dedibox/Elastic Metal offer.

    Tries API-provided hardware properties first (cpu.threads for total logical
    threads); falls back to the Start-series static table for legacy offers.
    Returns None when specs cannot be determined.
    """
    props: Dict[str, Any] = product.get("properties") or {}
    hw: Dict[str, Any] = props.get("hardware") or {}
    if hw:
        cpu_info: Dict[str, Any] = hw.get("cpu") or {}
        virtual_info: Dict[str, Any] = cpu_info.get("virtual") or {}
        physical_info: Dict[str, Any] = cpu_info.get("physical") or {}
        vcpu: int = int(
            virtual_info.get("count")
            or physical_info.get("threads")
            or cpu_info.get("threads")
            or 0
        )
        ram_info: Dict[str, Any] = hw.get("ram") or {}
        ram_bytes: int = int(ram_info.get("size") or 0)
        ram_gib: float = ram_bytes / (1024 ** 3)
        api_arch: str = str(cpu_info.get("arch") or "x64")
        arch: str = "arm64" if "arm" in api_arch.lower() else "x86_64"
        if vcpu > 0 and ram_gib > 0:
            return vcpu, ram_gib, arch

    fallback = _DEDIBOX_START_SPECS.get(offer_id)
    if fallback:
        return int(fallback["vcpu"]), float(fallback["ram_gib"]), str(fallback["arch"])
    return None


# ---------------------------------------------------------------------------
# Dedibox product processor
# ---------------------------------------------------------------------------


def _process_elastic_metal_products(
    all_products: List[Dict[str, Any]],
    timestamp: str,
) -> List[Dict[str, Any]]:
    """Process Elastic Metal bare-metal servers from the product catalog (hourly billing).

    Groups catalog entries by instance type (derived from product name) to
    collect all zones for each server model. Specs come from properties.hardware;
    vcpu is read from cpu.threads (total logical threads across all sockets).
    """
    em_products = [
        p for p in all_products
        if p.get("product_category") == _ELASTIC_METAL_CATEGORY
        and p.get("unit_of_measure", {}).get("unit") == "hour"
        and _extract_price_eur(p) > 0
        and p.get("status") != "end_of_sale"
        and (p.get("locality") or {}).get("zone")
    ]

    if not em_products:
        logger.info("No Elastic Metal products found in catalog")
        return []

    logger.info(f"Found {len(em_products)} Elastic Metal catalog entries")

    # Group by instanceType → collect zones; price is uniform per type
    grouped: Dict[str, Dict[str, Any]] = {}

    for p in em_products:
        props: Dict[str, Any] = p.get("properties") or {}
        em_props: Dict[str, Any] = props.get("elastic_metal") or {}

        # Strip the "Elastic Metal " prefix to get the short model name
        instance_type = str(p.get("product", "")).replace("Elastic Metal ", "").strip()
        if not instance_type:
            continue

        zone = p["locality"]["zone"]
        price_eur = _extract_price_eur(p)
        em_range: str = str(em_props.get("range") or "")

        if instance_type not in grouped:
            grouped[instance_type] = {
                "zones": [],
                "price_eur": price_eur,
                "range": em_range,
                "product": p,
            }
        grouped[instance_type]["zones"].append(zone)

    logger.info(f"Grouped into {len(grouped)} unique Elastic Metal types")

    instances: List[Dict[str, Any]] = []
    skipped: List[str] = []

    for instance_type, group in sorted(grouped.items()):
        specs = _get_dedibox_specs(instance_type.lower(), group["product"])
        if specs is None:
            logger.warning(f"Cannot determine specs for Elastic Metal {instance_type!r} — skipping")
            skipped.append(instance_type)
            continue

        vcpu, ram_gib, arch = specs
        price_eur_hourly = group["price_eur"]

        # Sanity check: dedicated servers should cost at least €0.03/hr regardless of size.
        # Prices below this indicate stale or malformed API data (e.g. HC-M at €0.003/hr).
        if price_eur_hourly < 0.03:
            logger.warning(
                f"Skipping {instance_type!r}: price {price_eur_hourly:.4f} EUR/hr is "
                f"implausibly low for a dedicated server ({vcpu} vCPU, {ram_gib:.0f} GiB)"
            )
            skipped.append(instance_type)
            continue

        zones = sorted(group["zones"])

        em_range = group["range"]
        family = (
            f"elastic-metal-{em_range.lower().replace(' ', '-')}"
            if em_range else "elastic-metal"
        )
        location_details = [_zone_to_location(z) for z in zones]

        price_usd_hourly = round(convert_currency(price_eur_hourly, "EUR", "USD"), 6)
        price_usd_monthly = round(price_usd_hourly * HOURS_PER_MONTH, 4)
        price_eur_monthly = round(price_eur_hourly * HOURS_PER_MONTH, 4)

        instances.append({
            "provider": "scaleway",
            "type": "dedicated-server",
            "instanceType": instance_type,
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
            "regions": zones,
            "locationDetails": location_details,
            "commitments": [],
            "source": "scaleway_public_catalog_api",
            "description": (
                f"Scaleway Elastic Metal {instance_type} — {vcpu} vCPU, {ram_gib:.0f} GiB RAM"
            ),
            "lastUpdated": timestamp,
            "raw": {
                "offer_id": instance_type,
                "price_eur_hourly": round(price_eur_hourly, 9),
                "range": em_range,
                "zones": zones,
            },
        })

    if skipped:
        logger.info(f"Skipped {len(skipped)} Elastic Metal type(s) with unknown specs: {skipped}")
    logger.info(f"Built {len(instances)} Elastic Metal instance records")
    return instances


def _process_dedibox_products(
    all_products: List[Dict[str, Any]],
    timestamp: str,
) -> List[Dict[str, Any]]:
    """Process Dedibox dedicated-server plans from the product catalog (monthly billing).

    Groups catalog entries by product name so that multiple datacenter entries
    collapse into a single record with all locations. Monthly EUR prices are
    divided by HOURS_PER_MONTH to produce the hourly equivalents the schema
    requires. Specs come from properties.hardware; cpu.threads is the total
    logical thread count (vcpu). Region is extracted from the SKU path.
    """
    dedibox_products = [
        p for p in all_products
        if p.get("product_category") == "Dedibox"
        and p.get("unit_of_measure", {}).get("unit") == "month"
        and _extract_price_eur(p) > 0
        and p.get("status") != "end_of_sale"
    ]

    if not dedibox_products:
        logger.info("No Dedibox products found in catalog")
        return []

    logger.info(f"Found {len(dedibox_products)} Dedibox catalog entries")

    # Group by product name → collect datacenters and pick lowest price
    grouped: Dict[str, Dict[str, Any]] = {}

    for p in dedibox_products:
        product_name = str(p.get("product", "")).strip()
        if not product_name:
            continue

        price_eur = _extract_price_eur(p)
        locality: Dict[str, Any] = p.get("locality") or {}
        datacenter = str(locality.get("datacenter") or "")

        # Extract region from SKU: "/dedibox/offer/monthly/fr-par/dc/dc2" → "fr-par"
        sku = str(p.get("sku") or "")
        sku_parts = sku.strip("/").split("/")
        region = sku_parts[3] if len(sku_parts) > 3 else "fr-par"

        if product_name not in grouped:
            grouped[product_name] = {
                "price_eur": price_eur,
                "datacenters": set(),
                "regions": set(),
                "product": p,
            }
        if datacenter:
            grouped[product_name]["datacenters"].add(datacenter)
        if region:
            grouped[product_name]["regions"].add(region)

    logger.info(f"Grouped into {len(grouped)} unique Dedibox products")

    instances: List[Dict[str, Any]] = []
    skipped: List[str] = []

    for product_name, group in sorted(grouped.items()):
        # Derive instanceType slug from product name: "Dedibox Core-10-L" → "core-10-l"
        instance_type = product_name.replace("Dedibox ", "").strip().lower()

        product_entry: Dict[str, Any] = group["product"]
        specs = _get_dedibox_specs(instance_type, product_entry)
        if specs is None:
            logger.warning(f"Cannot determine specs for Dedibox {product_name!r} — skipping")
            skipped.append(product_name)
            continue

        vcpu, ram_gib, arch = specs

        price_eur_monthly = group["price_eur"]
        price_eur_hourly = price_eur_monthly / HOURS_PER_MONTH
        price_usd_hourly = round(convert_currency(price_eur_hourly, "EUR", "USD"), 6)
        price_usd_monthly = round(convert_currency(price_eur_monthly, "EUR", "USD"), 4)

        # Map regions to zone codes (Dedibox uses region-level availability)
        regions: List[str] = sorted(group["regions"])
        location_details = [_zone_to_location(r) for r in regions]

        # Family from Dedibox range: "Core-10-L" → "dedibox-core"
        dedibox_props: Dict[str, Any] = (
            group["product"].get("properties") or {}
        ).get("dedibox") or {}
        dedibox_range: str = str(dedibox_props.get("range") or "")
        family = (
            f"dedibox-{dedibox_range.lower().replace(' ', '-')}"
            if dedibox_range else f"dedibox-{instance_type.split('-')[0]}"
        )

        instances.append({
            "provider": "scaleway",
            "type": "dedicated-server",
            "instanceType": instance_type,
            "vCPU": vcpu,
            "memoryGiB": round(ram_gib, 3),
            "architecture": arch,
            "family": family,
            "priceUSD_hourly": price_usd_hourly,
            "priceUSD_monthly": price_usd_monthly,
            "originalPrice": {
                "hourly": round(price_eur_hourly, 9),
                "monthly": round(price_eur_monthly, 4),
                "currency": "EUR",
            },
            "regions": regions,
            "locationDetails": location_details,
            "commitments": [],
            "source": "scaleway_public_catalog_api",
            "description": (
                f"Scaleway Dedibox {instance_type} — {vcpu} vCPU, {ram_gib:.0f} GiB RAM"
            ),
            "lastUpdated": timestamp,
            "raw": {
                "offer_id": instance_type,
                "price_eur_monthly": round(price_eur_monthly, 4),
                "regions": regions,
                "datacenters": sorted(group["datacenters"]),
            },
        })

    if skipped:
        logger.info(f"Skipped {len(skipped)} Dedibox product(s) with unknown specs: {skipped}")
    logger.info(f"Built {len(instances)} Dedibox instance records")
    return instances


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
        self.session = make_session()

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
        skipped_deprecated: Dict[str, set[str]] = {}  # range → set of offer_ids
        for p in instance_products:
            inst_props = p.get("properties", {}).get("instance", {})
            offer_id = inst_props.get("offer_id") or p.get("product", "")
            if not offer_id:
                continue

            zone = p["locality"]["zone"]
            price_eur = _extract_price_eur(p)
            prange = inst_props.get("range", "")

            if prange in _DEPRECATED_RANGES:
                logger.debug(
                    f"Skipping deprecated range {prange!r} for offer {offer_id!r}"
                )
                skipped_deprecated.setdefault(prange, set()).add(offer_id)
                continue

            if offer_id not in grouped:
                grouped[offer_id] = {
                    "zones": [],
                    "price_eur": price_eur,
                    "range": prange,
                    "product": p,  # keep first product for hw spec extraction
                }
            grouped[offer_id]["zones"].append(zone)

        if skipped_deprecated:
            for dep_range, dep_ids in sorted(skipped_deprecated.items()):
                logger.info(
                    f"Skipped deprecated range {dep_range!r}: "
                    f"{len(dep_ids)} offer(s) — {sorted(dep_ids)}"
                )
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

        # Elastic Metal bare-metal (catalog, hourly billing)
        elastic_metal = _process_elastic_metal_products(products, timestamp)
        instances.extend(elastic_metal)

        # Dedibox dedicated servers (catalog, monthly billing)
        dedibox = _process_dedibox_products(products, timestamp)
        instances.extend(dedibox)

        logger.info(
            f"Total Scaleway records: {len(instances)} "
            f"({len(instances) - len(elastic_metal) - len(dedibox)} cloud + "
            f"{len(elastic_metal)} Elastic Metal + {len(dedibox)} Dedibox)"
        )
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
