#!/usr/bin/env python3
"""
AWS EC2 pricing fetcher for CloudPriceFinder v3.

Uses the AWS Pricing API (public, no auth required) with streaming JSON
parsing via ijson to keep memory usage bounded even for the large (100+ MB)
per-region EC2 pricing files.

Side output: aws.storage.raw.json (EBS volume-type pricing) is written
alongside aws.raw.json — zero extra downloads, extracted from the same
per-region EC2 pricing files.

Out of scope for v1 (deferred to v3.1+):
- Spot / capacity-block / dedicated-host SKUs
- China (cn-north-1, cn-northwest-1) and GovCloud (us-gov-*) regions
  (these use a separate pricing endpoint and are not standard commercial)

Usage:
    python scripts/fetch_aws.py [--regions us-east-1 [us-west-2 ...]] [--output PATH]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import ijson
import requests

# ---------------------------------------------------------------------------
# Ensure repo root is importable so 'scripts.utils' works whether the script
# is run as `python scripts/fetch_aws.py` or `python -m scripts.fetch_aws`.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.data_normalizer import normalize_commitments
from scripts.utils.data_validator import validate_commitments, validate_instance_data
from scripts.utils.http_client import HOURS_PER_MONTH, get_json, make_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_aws")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRICING_BASE = "https://pricing.us-east-1.amazonaws.com"
OFFER_INDEX_URL = f"{PRICING_BASE}/offers/v1.0/aws/index.json"
EC2_REGION_INDEX_PATH = "/offers/v1.0/aws/AmazonEC2/current/region_index.json"

# Regions excluded from v1 — separate pricing API endpoints.
# Kept here for documentation; discovery is now fully dynamic from the live
# EC2 region index. Any region code starting with these prefixes is skipped.
_EXCLUDED_REGION_PREFIXES = ("cn-", "us-gov-")

# OS filter — Linux, Windows, RHEL, and macOS on-demand/reserved
INCLUDED_OS = {"Linux", "Windows", "RHEL", "macOS"}

# Maps operating system name → Savings Plan operation code
OS_SP_OPERATIONS = {
    "Linux": "RunInstances",
    "Windows": "RunInstances:0002",
    "RHEL": "RunInstances:0010",
}

# Reverse map: Savings Plan operation code → OS name
_SP_OP_TO_OS: Dict[str, str] = {v: k for k, v in OS_SP_OPERATIONS.items()}

# Term labels used in the AWS Pricing API
TERM_ON_DEMAND = "OnDemand"
TERM_RESERVED = "Reserved"

# Savings Plans index path (from top-level offer index)
SP_BASE = "https://pricing.us-east-1.amazonaws.com"
SP_INDEX_URL = f"{SP_BASE}/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/current/region_index.json"

# HTTP session config
REQUEST_TIMEOUT = 120  # seconds per HTTP request
MAX_RETRIES = 3
RETRY_BACKOFF = 5  # seconds

# Known GPU model details keyed by GPU type string from AWS (lowercase)
_GPU_MODELS: Dict[str, Dict[str, Any]] = {
    "nvidia tesla k80": {"type": "NVIDIA Tesla K80", "memoryGiB": 12},
    "nvidia tesla m60": {"type": "NVIDIA Tesla M60", "memoryGiB": 8},
    "nvidia tesla v100": {"type": "NVIDIA Tesla V100", "memoryGiB": 16},
    "nvidia tesla t4": {"type": "NVIDIA Tesla T4", "memoryGiB": 16},
    "nvidia a10g": {"type": "NVIDIA A10G", "memoryGiB": 24},
    "nvidia a100": {"type": "NVIDIA A100", "memoryGiB": 80},
    "nvidia h100": {"type": "NVIDIA H100", "memoryGiB": 80},
    "nvidia l4": {"type": "NVIDIA L4", "memoryGiB": 24},
    "nvidia l40s": {"type": "NVIDIA L40S", "memoryGiB": 48},
    "amd radeon pro v520": {"type": "AMD Radeon Pro V520", "memoryGiB": 8},
    "inferentia": {"type": "AWS Inferentia", "memoryGiB": 8},
    "trainium": {"type": "AWS Trainium", "memoryGiB": 32},
    "gaudi2": {"type": "Intel Gaudi2", "memoryGiB": 96},
    "nvidia h200": {"type": "NVIDIA H200", "memoryGiB": 141},
    "nvidia b200": {"type": "NVIDIA B200", "memoryGiB": 192},
}

# AWS 'physicalProcessor' strings that indicate ARM64
_ARM64_HINTS = ("graviton", "arm", "neoverse", "apple")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _stream_response(session: requests.Session, url: str) -> requests.Response:
    """Open a streaming GET request with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, stream=True, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            logger.warning(f"Attempt {attempt} failed for {url}: {exc}. Retrying in {RETRY_BACKOFF}s…")
            time.sleep(RETRY_BACKOFF)
    raise RuntimeError(f"Failed to stream {url} after {MAX_RETRIES} attempts")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_memory_gib(mem_str: str) -> float:
    """Parse AWS memory strings like '16 GiB', '1,952 GiB', '0.5 GiB'."""
    if not mem_str or mem_str.lower() in ("na", "n/a", ""):
        return 0.0
    cleaned = mem_str.replace(",", "").lower().replace("gib", "").replace("gb", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_vcpu(vcpu_str: str) -> int:
    """Parse AWS vCPU string like '4' or '4 vCPUs'."""
    if not vcpu_str:
        return 0
    m = re.search(r"(\d+)", vcpu_str)
    return int(m.group(1)) if m else 0


def _parse_gpu_count(gpu_str: str) -> int:
    if not gpu_str or gpu_str.lower() in ("na", "n/a", "0", ""):
        return 0
    m = re.search(r"(\d+)", gpu_str)
    return int(m.group(1)) if m else 0


def _detect_architecture(processor_str: str) -> str:
    """Return 'arm64' if the processor string indicates Graviton/ARM, else 'x86_64'."""
    lower = (processor_str or "").lower()
    for hint in _ARM64_HINTS:
        if hint in lower:
            return "arm64"
    return "x86_64"


def _extract_family_generation(instance_type: str) -> Tuple[str, Optional[str]]:
    """
    Split 'c7g.xlarge' → family='c7g', generation='7'.
    Split 'm5.large' → family='m5', generation='5'.
    Split 't3a.micro' → family='t3a', generation='3'.
    Returns (family, generation_or_None).
    """
    parts = instance_type.split(".")
    if not parts:
        return instance_type, None
    family = parts[0]
    m = re.search(r"(\d+)", family)
    generation = m.group(1) if m else None
    return family, generation


def _parse_gpu_info(
    gpu_count_str: str, gpu_type_str: str
) -> Optional[Dict[str, Any]]:
    """Return a gpu dict or None if the instance has no GPU."""
    count = _parse_gpu_count(gpu_count_str)
    if count == 0:
        return None
    model_key = (gpu_type_str or "").lower().strip()
    model_info = _GPU_MODELS.get(model_key, {"type": gpu_type_str or "Unknown", "memoryGiB": 0})
    return {
        "count": count,
        "type": model_info["type"],
        "memoryGiB": model_info["memoryGiB"],
    }


# ---------------------------------------------------------------------------
# AWS Pricing API streaming parser
# ---------------------------------------------------------------------------

def _download_region_bytes(session: requests.Session, url: str) -> bytes:
    """Download a per-region pricing file with retry; return raw bytes."""
    logger.info(f"  Downloading pricing data from {url} …")
    t0 = time.time()
    resp = _stream_response(session, url)
    raw_bytes = resp.content
    elapsed = time.time() - t0
    size_mb = len(raw_bytes) / (1024 * 1024)
    logger.info(f"  Downloaded {size_mb:.1f} MB in {elapsed:.1f}s")
    return raw_bytes


def _parse_ec2_skus(raw_bytes: bytes) -> Generator[Dict[str, Any], None, None]:
    """
    Stream-parse a per-region EC2 pricing JSON (already in memory) and yield
    one dict per compute SKU that has on-demand pricing.

    The per-region JSON structure is:
    {
      "products": { "<sku>": { "sku": "...", "attributes": {...} }, ... },
      "terms": {
        "OnDemand": { "<sku>": { "<offerTermCode>": { "priceDimensions": {...} } } },
        "Reserved":  { ... }
      }
    }

    Three-pass ijson parse (the file is already in memory as raw_bytes):
      Pass 1: collect all compute products (specs) into a dict keyed by SKU.
      Pass 2: read on-demand pricing.
      Pass 3: read reserved term pricing.

    Yields one dict per (SKU, operating-system, instance-type) combination with:
      - All product attributes
      - on_demand_hourly: float
      - raw_reservations: list of raw term dicts for reserved pricing
    """
    # --- Pass 1: harvest product attributes ---
    # The `products` key in the EC2 pricing JSON is a dict (object), not an array.
    # ijson.kvitems(stream, "products") iterates over its (sku, value) pairs directly.
    #
    # Mac dedicated host pricing model (discovered from API):
    #   - Bare-metal instance entries (mac1.metal, mac2.metal, mac-m4.metal, …)
    #     have productFamily="Compute Instance (bare metal)", tenancy="Host",
    #     operatingSystem="Linux" (placeholder), and on-demand price $0.00.
    #   - The REAL price is on the corresponding Dedicated Host entries (mac1,
    #     mac2, mac-m4, …) which have no dot and are normally skipped.
    # Strategy: track host-entry SKUs in pass 1, collect their on-demand prices
    # in pass 2, then inject those prices into the bare-metal instance records.
    products: Dict[str, Dict[str, Any]] = {}
    # mac host type (e.g. "mac1") -> SKU of its Dedicated Host product entry
    _mac_host_sku: Dict[str, str] = {}

    stream1 = io.BytesIO(raw_bytes)
    for sku, attrs in ijson.kvitems(stream1, "products"):
        attributes = attrs.get("attributes", {})
        instance_type = attributes.get("instanceType", "")
        if not instance_type:
            continue

        if "." not in instance_type:
            # Track Mac Dedicated Host entries even though they have no dot.
            if (instance_type.startswith("mac")
                    and attrs.get("productFamily", "") == "Dedicated Host"):
                _mac_host_sku[instance_type] = sku
            continue

        # Skip Spot capacity
        if attributes.get("marketoption", "").lower() in ("spot",):
            continue

        os_name = attributes.get("operatingSystem", "")

        # Mac bare-metal instances always run macOS on a dedicated host.
        # The API sets operatingSystem="Linux" as a placeholder — override it.
        if instance_type.startswith("mac"):
            attributes["operatingSystem"] = "macOS"
            attributes["tenancy"] = "Host"
            os_name = "macOS"

        # Skip OS not in INCLUDED_OS (SUSE, SQL Server, etc.)
        if os_name not in INCLUDED_OS:
            continue

        products[sku] = attributes

    logger.info(f"  Collected {len(products)} product SKUs")
    logger.info(f"  Tracking {len(_mac_host_sku)} Mac dedicated host SKUs for pricing")

    # --- Pass 2: harvest on-demand pricing ---
    # Also collect prices for Mac Dedicated Host SKUs so we can inject them into
    # the bare-metal instance records (which have $0 on-demand price).
    _mac_host_skus_set = set(_mac_host_sku.values())
    on_demand_prices: Dict[str, float] = {}  # sku -> hourly USD
    stream2 = io.BytesIO(raw_bytes)
    for sku, offer_terms in ijson.kvitems(stream2, f"terms.{TERM_ON_DEMAND}"):
        if sku not in products and sku not in _mac_host_skus_set:
            continue
        for _term_code, term in offer_terms.items():
            for _dim_code, dim in term.get("priceDimensions", {}).items():
                usd_str = dim.get("pricePerUnit", {}).get("USD", "0")
                try:
                    price = float(usd_str)
                except (ValueError, TypeError):
                    price = 0.0
                if price > 0:
                    on_demand_prices[sku] = price
                    break

    # Build instance-type -> host on-demand price lookup.
    # "mac1" host at $1.083/hr -> "mac1.metal" instance price = $1.083/hr.
    _mac_instance_price: Dict[str, float] = {
        f"{host_type}.metal": on_demand_prices[host_sku]
        for host_type, host_sku in _mac_host_sku.items()
        if host_sku in on_demand_prices
    }
    logger.info(
        f"  Collected {len(on_demand_prices)} on-demand price points "
        f"({len(_mac_instance_price)} Mac host prices resolved)"
    )

    # --- Pass 3: harvest reserved pricing ---
    reserved_terms: Dict[str, List[Dict[str, Any]]] = {}  # sku -> list of term dicts
    stream3 = io.BytesIO(raw_bytes)
    for sku, offer_terms in ijson.kvitems(stream3, f"terms.{TERM_RESERVED}"):
        if sku not in products:
            continue
        terms_list = []
        for _term_code, term in offer_terms.items():
            term_attrs = term.get("termAttributes", {})
            lease_contract_length = term_attrs.get("LeaseContractLength", "")  # "1yr" or "3yr"
            purchase_option = term_attrs.get("PurchaseOption", "")  # "No Upfront", "Partial Upfront", "All Upfront"
            offering_class = term_attrs.get("OfferingClass", "standard")  # "standard" or "convertible"

            # Map to our schema
            if "1" in lease_contract_length:
                schema_term = "1yr"
            elif "3" in lease_contract_length:
                schema_term = "3yr"
            else:
                continue  # unknown term — skip

            purchase_lower = purchase_option.lower()
            if "no" in purchase_lower:
                schema_payment = "no-upfront"
            elif "partial" in purchase_lower:
                schema_payment = "partial-upfront"
            elif "all" in purchase_lower:
                schema_payment = "all-upfront"
            else:
                continue

            # Extract hourly and upfront amounts from priceDimensions
            hourly_usd = 0.0
            upfront_usd = 0.0
            for _dim_code, dim in term.get("priceDimensions", {}).items():
                desc = dim.get("description", "").lower()
                usd_str = dim.get("pricePerUnit", {}).get("USD", "0")
                try:
                    amount = float(usd_str)
                except (ValueError, TypeError):
                    amount = 0.0
                if "upfront" in desc or dim.get("unit", "").lower() == "quantity":
                    upfront_usd += amount
                else:
                    hourly_usd += amount

            terms_list.append({
                "term": schema_term,
                "payment": schema_payment,
                "product": "reserved",
                "offering_class": offering_class,
                "priceUSD_hourly": hourly_usd,
                "upfront_usd": upfront_usd,
            })
        if terms_list:
            reserved_terms[sku] = terms_list

    logger.info(f"  Collected reserved terms for {len(reserved_terms)} SKUs")

    # --- Yield merged records ---
    # Only yield SKUs that have on-demand pricing (i.e. real RunInstances costs).
    for sku, attrs in products.items():
        instance_type = attrs.get("instanceType", "")
        od_price = on_demand_prices.get(sku)

        # Mac bare-metal instances carry $0 list price — substitute the host price.
        if (od_price is None or od_price <= 0) and instance_type.startswith("mac"):
            od_price = _mac_instance_price.get(instance_type)

        if od_price is None or od_price <= 0:
            continue
        yield {
            "sku": sku,
            "attributes": attrs,
            "on_demand_hourly": od_price,
            "raw_reservations": reserved_terms.get(sku, []),
        }


# ---------------------------------------------------------------------------
# EBS storage pricing extractor
# ---------------------------------------------------------------------------

def _parse_int_str(val: str) -> Optional[int]:
    """Extract the first integer from strings like '16000 IOPS', '1,000 MiB/s'."""
    if not val or val.lower().strip() in ("na", "n/a", ""):
        return None
    m = re.search(r"(\d[\d,]*)", val)
    return int(m.group(1).replace(",", "")) if m else None


_EBS_SKIP_USAGE = ("IOPS", "Throughput", "SnapshotUsage")
_EBS_VOLUME_FAMILY_PREFIXES = ("gp", "io", "sc", "st")
_EBS_MONTH_HOURS = 730.0


def _ebs_volume_family(vol_type: str) -> str:
    name = vol_type.lower()
    for prefix in _EBS_VOLUME_FAMILY_PREFIXES:
        if name.startswith(prefix):
            return prefix
    return name


def _extract_ebs_records(
    raw_bytes: bytes,
    region: str,
    now_iso: str,
) -> Dict[str, Dict[str, Any]]:
    """
    Two-pass ijson parse of an EC2 per-region pricing file.
    Returns a dict mapping vol_type -> partial record dict, each containing:
      instanceType, family, priceUSD_monthly, priceUSD_hourly,
      maxIops, maxThroughputMBps, storageMedia, regionPricing={region: price}.
    Only Storage productFamily SKUs with a volumeApiName and a positive
    on-demand USD price are included.
    """
    # Pass 1: collect EBS Storage products
    products: Dict[str, Dict[str, Any]] = {}
    stream1 = io.BytesIO(raw_bytes)
    for sku, prod in ijson.kvitems(stream1, "products"):
        attrs = prod.get("attributes", {})
        if prod.get("productFamily") != "Storage":
            continue
        vol_type = attrs.get("volumeApiName", "")
        if not vol_type:
            continue
        usage_type = attrs.get("usagetype", "")
        if any(kw in usage_type for kw in _EBS_SKIP_USAGE):
            continue
        products[sku] = attrs

    # Pass 2: extract on-demand (per-GB-month) prices
    result: Dict[str, Dict[str, Any]] = {}
    stream2 = io.BytesIO(raw_bytes)
    for sku, offer_terms in ijson.kvitems(stream2, "terms.OnDemand"):
        if sku not in products:
            continue
        attrs = products[sku]
        vol_type = attrs["volumeApiName"]

        for _, term in offer_terms.items():
            for _, dim in term.get("priceDimensions", {}).items():
                try:
                    price = float(dim.get("pricePerUnit", {}).get("USD", "0"))
                except (ValueError, TypeError):
                    price = 0.0
                if price <= 0:
                    continue

                existing = result.get(vol_type)
                if existing is None or price < existing["regionPricing"][region]:
                    result[vol_type] = {
                        "instanceType": vol_type,
                        "family": _ebs_volume_family(vol_type),
                        "priceUSD_monthly": round(price, 8),
                        "priceUSD_hourly": round(price / _EBS_MONTH_HOURS, 8),
                        "maxIops": _parse_int_str(attrs.get("maxIopsvolume", "")),
                        "maxThroughputMBps": _parse_int_str(attrs.get("maxThroughputvolume", "")),
                        "storageMedia": attrs.get("storageMedia") or None,
                        "regionPricing": {region: price},
                    }
                break

    return result


# ---------------------------------------------------------------------------
# Savings Plans streaming parser
# ---------------------------------------------------------------------------

def _fetch_savings_plans_for_region(
    session: requests.Session, region: str
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """
    Fetch Compute Savings Plan commitments for a given region.

    AWS Savings Plans are hosted at a separate pricing endpoint:
      https://pricing.us-east-1.amazonaws.com/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/

    The region index is a list (not dict) of entries like:
      { "regionCode": "us-east-1", "versionUrl": "/savingsPlan/v1.0/aws/.../us-east-1/index.json" }

    Each region's file has terms.savingsPlan[] where each entry has:
      - leaseContractLength: { duration: 1|3, unit: "year" }
      - description: "1 year No Upfront Compute Savings Plan" (used to extract payment option)
      - rates[]: list of { discountedInstanceType, discountedRate.price, discountedUsageType }

    Returns a dict mapping (instanceType, operatingSystem) -> list of raw commitment dicts.
    """
    try:
        region_index_data = get_json(session, SP_INDEX_URL, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        logger.warning(f"  Could not fetch Savings Plan region index: {exc}. Skipping SP for {region}.")
        return {}

    # The regions field is a list of dicts, not a dict keyed by region code.
    regions_list = region_index_data.get("regions", [])
    region_url: Optional[str] = None
    for entry in regions_list:
        if entry.get("regionCode") == region:
            version_url = entry.get("versionUrl", "")
            if version_url:
                region_url = SP_BASE + version_url
            break

    if not region_url:
        logger.debug(f"  No Savings Plan URL found for region {region}")
        return {}

    try:
        logger.info(f"  Downloading Savings Plan data for {region} …")
        resp = _stream_response(session, region_url)
        raw_bytes = resp.content
    except Exception as exc:
        logger.warning(f"  Failed to download SP data for {region}: {exc}. Skipping.")
        return {}

    result: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    try:
        data = json.loads(raw_bytes)
        terms = data.get("terms", {}).get("savingsPlan", [])

        for term_entry in terms:
            # Parse term length from leaseContractLength.duration
            lc = term_entry.get("leaseContractLength", {})
            duration = lc.get("duration", 0)
            if duration == 1:
                schema_term = "1yr"
            elif duration == 3:
                schema_term = "3yr"
            else:
                continue

            # Parse payment option from description string
            desc_lower = (term_entry.get("description", "")).lower()
            if "no upfront" in desc_lower:
                schema_payment = "no-upfront"
            elif "partial upfront" in desc_lower:
                schema_payment = "partial-upfront"
            elif "all upfront" in desc_lower:
                schema_payment = "all-upfront"
            else:
                # Unknown payment option — skip
                continue

            for rate in term_entry.get("rates", []):
                # Only standard shared-tenancy BoxUsage rates for known OS types.
                usage_type = rate.get("discountedUsageType", "")
                operation = rate.get("discountedOperation", "")

                # Skip anything that isn't standard shared-tenancy BoxUsage
                if "BoxUsage" not in usage_type:
                    continue
                # Only accept known OS operation codes (Linux, Windows, RHEL)
                if operation not in _SP_OP_TO_OS:
                    continue
                os_name = _SP_OP_TO_OS[operation]

                # Prefer the explicit discountedInstanceType field
                instance_type = rate.get("discountedInstanceType", "")
                if not instance_type:
                    # Fall back to parsing from usageType (e.g. "BoxUsage:m5.xlarge")
                    m = re.search(r"BoxUsage:(.+)", usage_type)
                    instance_type = m.group(1).strip() if m else ""
                if not instance_type or "." not in instance_type:
                    continue

                price_str = rate.get("discountedRate", {}).get("price", "0")
                try:
                    price = float(price_str)
                except (ValueError, TypeError):
                    price = 0.0
                if price <= 0:
                    continue

                entry = {
                    "term": schema_term,
                    "payment": schema_payment,
                    "product": "savings-plan",
                    "priceUSD_hourly": price,
                    "upfront_usd": 0.0,
                    "operatingSystem": os_name,
                }
                result.setdefault((instance_type, os_name), []).append(entry)

    except Exception as exc:
        logger.warning(f"  Error parsing SP data for {region}: {exc}")
        return {}

    logger.info(f"  Savings Plans: found data for {len(result)} instance types in {region}")
    return result


# ---------------------------------------------------------------------------
# Per-region processing
# ---------------------------------------------------------------------------

def _process_region(
    session: requests.Session,
    region: str,
    region_url: str,
    savings_plans: Dict[Tuple[str, str], List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Download, stream-parse, and normalise EC2 pricing for one region.

    Returns (compute_instances, ebs_vol_map).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    raw_bytes = _download_region_bytes(session, region_url)
    ebs_vol_map = _extract_ebs_records(raw_bytes, region, now_iso)

    instances_by_type: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for raw_sku in _parse_ec2_skus(raw_bytes):
        attrs = raw_sku["attributes"]
        on_demand_hourly = raw_sku["on_demand_hourly"]
        raw_reservations = raw_sku["raw_reservations"]
        instance_type = attrs.get("instanceType", "")
        os_name = attrs.get("operatingSystem", "Linux")

        # Normalise tenancy: "Shared" → shared, "Dedicated" → dedicated, "Host" → host
        raw_tenancy = attrs.get("tenancy", "Shared").lower()
        tenancy = raw_tenancy if raw_tenancy in ("shared", "dedicated", "host") else "shared"

        key = (instance_type, os_name, tenancy)
        if key in instances_by_type:
            # Merge additional reservation terms (same instance in another SKU variant)
            instances_by_type[key]["_raw_reservations"].extend(raw_reservations)
            continue

        # Extract specs
        vcpu = _parse_vcpu(attrs.get("vcpu", "0"))
        memory_gib = _parse_memory_gib(attrs.get("memory", "0 GiB"))
        processor = attrs.get("physicalProcessor", "")
        architecture = _detect_architecture(processor)
        gpu_info = _parse_gpu_info(attrs.get("gpu", "0"), attrs.get("gpuMemory", ""))
        family, generation = _extract_family_generation(instance_type)

        instances_by_type[key] = {
            "instance_type": instance_type,
            "operatingSystem": os_name,
            "tenancy": tenancy,
            "vcpu": vcpu,
            "memory_gib": memory_gib,
            "architecture": architecture,
            "family": family,
            "generation": generation,
            "gpu": gpu_info,
            "on_demand_hourly": on_demand_hourly,
            "_raw_reservations": list(raw_reservations),
            "_attrs": attrs,
        }

    # Build final normalised records
    results: List[Dict[str, Any]] = []

    for (instance_type, os_name, _), data in instances_by_type.items():
        on_demand_hourly = data["on_demand_hourly"]

        # Deduplicate reservations by (term, payment).
        # When both standard and convertible exist, prefer standard (lower price).
        # Sort so 'standard' comes before 'convertible' when prices are equal.
        reservations_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in data["_raw_reservations"]:
            key = (r["term"], r["payment"])
            existing = reservations_by_key.get(key)
            if existing is None:
                reservations_by_key[key] = r
            else:
                # Keep the entry with lower effectiveHourlyUSD (standard < convertible)
                # Use priceUSD_hourly + upfront as proxy for now (normalize_commitments
                # will compute effectiveHourlyUSD later)
                existing_cost = existing.get("priceUSD_hourly", 0) + existing.get("upfront_usd", 0) / 8760
                new_cost = r.get("priceUSD_hourly", 0) + r.get("upfront_usd", 0) / 8760
                if new_cost < existing_cost:
                    reservations_by_key[key] = r
        unique_reservations = list(reservations_by_key.values())

        # Normalise reservations via shared utility
        normalised_reservations = normalize_commitments(unique_reservations, on_demand_hourly)

        # Merge savings plan commitments for this instance type.
        # Deduplicate by (term, payment): keep the entry with the lowest price.
        sp_raw_list = savings_plans.get((instance_type, os_name), [])
        if sp_raw_list:
            sp_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for sp_entry in sp_raw_list:
                key = (sp_entry["term"], sp_entry["payment"])
                existing = sp_by_key.get(key)
                if existing is None or sp_entry.get("priceUSD_hourly", 1e9) < existing.get("priceUSD_hourly", 1e9):
                    sp_by_key[key] = sp_entry
            normalised_sp = normalize_commitments(list(sp_by_key.values()), on_demand_hourly)

            # Only add SP entries not already covered by reserved (avoid duplicating
            # if somehow both products cover the same term+payment).
            existing_commitment_keys = {
                (c["term"], c["payment"]) for c in normalised_reservations
                if c["product"] == "savings-plan"
            }
            for c in normalised_sp:
                if (c["term"], c["payment"]) not in existing_commitment_keys:
                    normalised_reservations.append(c)
                    existing_commitment_keys.add((c["term"], c["payment"]))

        record: Dict[str, Any] = {
            "provider": "aws",
            "type": "cloud-server",
            "instanceType": instance_type,
            "operatingSystem": os_name,
            "tenancy": data["tenancy"],
            "family": data["family"],
            "architecture": data["architecture"],
            "vCPU": data["vcpu"],
            "memoryGiB": data["memory_gib"],
            "priceUSD_hourly": round(on_demand_hourly, 6),
            "priceUSD_monthly": round(on_demand_hourly * HOURS_PER_MONTH, 4),
            "commitments": normalised_reservations,
            "regions": [region],
            "source": "aws_pricing_api",
            "lastUpdated": now_iso,
        }

        if data["generation"]:
            record["generation"] = data["generation"]

        if data["gpu"]:
            record["gpu"] = data["gpu"]

        if os_name == "macOS":
            record["minimumBillingHours"] = 24

        results.append(record)

    logger.info(f"  Region {region}: {len(results)} instance types")
    return results, ebs_vol_map


# ---------------------------------------------------------------------------
# Region merging
# ---------------------------------------------------------------------------

def _merge_regions(
    per_region: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Merge per-region records into a single list.

    For the same (instanceType, operatingSystem) combination appearing in multiple regions:
    - Union the `regions` list
    - Keep the on-demand price from the first region encountered (prices vary
      by region; we store the primary record per instance type and tag all
      regions; the aggregator (Stage 6) will split by region later)
    - Union the `commitments` list (deduped by term+payment+product)

    We keep one record per (instanceType, operatingSystem, tenancy) across all regions,
    with the `regions` list populated. The per-region price variation is captured in a
    `regionPricing` dict if prices differ.
    """
    # Master dict: (instanceType, operatingSystem, tenancy) -> merged record
    merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for region, instances in per_region.items():
        for inst in instances:
            itype = inst["instanceType"]
            os_name = inst.get("operatingSystem", "Linux")
            tenancy = inst.get("tenancy", "shared")
            od = inst["priceUSD_hourly"]
            merge_key = (itype, os_name, tenancy)

            if merge_key not in merged:
                record = dict(inst)
                record["regions"] = [region]
                record["regionPricing"] = {region: od}
                merged[merge_key] = record
            else:
                existing = merged[merge_key]
                if region not in existing["regions"]:
                    existing["regions"].append(region)
                existing.setdefault("regionPricing", {})[region] = od

                # Merge commitments (dedup by term+payment+product)
                existing_keys = {
                    (c["term"], c["payment"], c["product"])
                    for c in existing.get("commitments", [])
                }
                for c in inst.get("commitments", []):
                    key = (c["term"], c["payment"], c["product"])
                    if key not in existing_keys:
                        existing.setdefault("commitments", []).append(c)
                        existing_keys.add(key)

    # Normalize priceUSD_hourly / priceUSD_monthly to the primary region so all
    # OS variants use the same reference price.  Without this, each OS variant
    # inherits the price from whichever region was processed first (alphabetical
    # order), which can vary wildly across OS variants and makes cross-OS
    # comparison in the UI meaningless (e.g. Linux $0.71 vs Windows $0.07).
    _PRIMARY_PRICE_REGIONS = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1"]
    for record in merged.values():
        rp = record.get("regionPricing", {})
        for candidate in _PRIMARY_PRICE_REGIONS:
            if candidate in rp:
                price = float(rp[candidate])
                if price > 0:
                    record["priceUSD_hourly"] = round(price, 6)
                    record["priceUSD_monthly"] = round(price * HOURS_PER_MONTH, 4)
                    break

    return list(merged.values())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_aws_data(
    regions: Optional[List[str]] = None,
    output_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch AWS EC2 pricing for the given regions (or all STANDARD_REGIONS).

    Args:
        regions: Optional list of AWS region codes to fetch. Defaults to STANDARD_REGIONS.
        output_path: Where to write aws.raw.json. Defaults to data/providers/aws.raw.json.

    Returns:
        List of normalised instance dicts.
    """
    # Explicit regions from --regions flag; None means discover dynamically.
    explicit_regions = regions

    session = make_session()

    # Step 1: Fetch top-level offer index (small JSON)
    logger.info("Fetching AWS top-level offer index …")
    try:
        offer_index = get_json(session, OFFER_INDEX_URL, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        logger.error(f"Failed to fetch offer index: {exc}")
        raise

    # Step 2: Fetch EC2 region index
    logger.info("Fetching EC2 region index …")
    ec2_region_index_url = PRICING_BASE + EC2_REGION_INDEX_PATH
    try:
        region_index = get_json(session, ec2_region_index_url, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        logger.error(f"Failed to fetch EC2 region index: {exc}")
        raise

    # Build region code → pricing URL map
    region_url_map: Dict[str, str] = {}
    for _display_name, region_data in region_index.get("regions", {}).items():
        region_code = region_data.get("regionCode", "")
        current_version_url = region_data.get("currentVersionUrl", "")
        if region_code and current_version_url:
            # currentVersionUrl is relative, e.g. "/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.json"
            region_url_map[region_code] = PRICING_BASE + current_version_url

    logger.info(f"EC2 region index has {len(region_url_map)} regions")

    # Determine which regions to process — discover dynamically when no --regions flag given.
    if explicit_regions is None:
        regions = sorted(
            code for code in region_url_map
            if not any(code.startswith(pfx) for pfx in _EXCLUDED_REGION_PREFIXES)
        )
        logger.info(f"Dynamically discovered {len(regions)} standard commercial regions from EC2 region index")
    else:
        regions = []
        for r in explicit_regions:
            if any(r.startswith(pfx) for pfx in _EXCLUDED_REGION_PREFIXES):
                logger.warning(
                    f"Skipping {r}: China and GovCloud regions use a separate pricing API "
                    "and are excluded from v1 (see PROJECT_TODO.md 'Out of scope')."
                )
            else:
                regions.append(r)

    # Step 3: Process each requested region
    per_region: Dict[str, List[Dict[str, Any]]] = {}

    for region in regions:
        region_pricing_url = region_url_map.get(region)
        if not region_pricing_url:
            logger.warning(f"No pricing URL found for region {region} — skipping")
            continue

        logger.info(f"Processing region {region} …")

        # Fetch savings plans for this region
        savings_plans = _fetch_savings_plans_for_region(session, region)

        try:
            region_instances, ebs_vol_map = _process_region(session, region, region_pricing_url, savings_plans)
            per_region[region] = (region_instances, ebs_vol_map)
        except Exception as exc:
            logger.error(f"Failed to process region {region}: {exc}")
            # Continue with other regions — don't fail the entire run

    if not per_region:
        raise RuntimeError("No regions were successfully processed. Aborting.")

    # Accumulate EBS pricing across all processed regions
    _EBS_CANONICAL = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1"]
    ebs_pricing: Dict[str, Dict[str, float]] = {}
    ebs_attrs: Dict[str, Dict[str, Any]] = {}
    for region, (_, ebs_vol_map) in per_region.items():
        for vol_type, rec in ebs_vol_map.items():
            price = rec["regionPricing"][region]
            ebs_pricing.setdefault(vol_type, {})[region] = price
            ebs_attrs.setdefault(vol_type, rec)

    now_iso = datetime.now(timezone.utc).isoformat()
    ebs_records: List[Dict[str, Any]] = []
    for vol_type, region_prices in sorted(ebs_pricing.items()):
        canonical_price = next(
            (region_prices[r] for r in _EBS_CANONICAL if r in region_prices),
            next(iter(region_prices.values())),
        )
        attrs = ebs_attrs[vol_type]
        ebs_records.append({
            "provider":          "aws",
            "type":              "cloud-volume",
            "instanceType":      vol_type,
            "family":            _ebs_volume_family(vol_type),
            "priceUSD_monthly":  round(canonical_price, 8),
            "priceUSD_hourly":   round(canonical_price / _EBS_MONTH_HOURS, 8),
            "storageGiB":        None,
            "maxIops":           attrs.get("maxIops"),
            "maxThroughputMBps": attrs.get("maxThroughputMBps"),
            "storageMedia":      attrs.get("storageMedia"),
            "regions":           sorted(region_prices.keys()),
            "regionPricing":     region_prices,
            "source":            "aws_pricing_api",
            "lastUpdated":       now_iso,
            "pricingModel":      "on-demand",
        })
    logger.info(f"EBS: {len(ebs_records)} volume types across {len(ebs_pricing)} regions")

    # Step 4: Merge across regions
    logger.info("Merging across regions …")
    compute_per_region = {r: instances for r, (instances, _) in per_region.items()}
    merged = _merge_regions(compute_per_region)
    logger.info(f"Total unique instance types: {len(merged)}")

    # Step 5: Validate a sample
    valid_count = 0
    invalid_count = 0
    for inst in merged:
        if validate_instance_data(inst):
            valid_count += 1
        else:
            invalid_count += 1
    logger.info(f"Validation: {valid_count} valid, {invalid_count} invalid records")

    # Step 6: Write output
    if output_path is None:
        output_path = _REPO_ROOT / "data" / "providers" / "aws.raw.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Writing {len(merged)} instances to {output_path} …")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"Output file size: {size_mb:.1f} MB")

    ebs_output_path = output_path.parent / "aws.storage.raw.json"
    with open(ebs_output_path, "w", encoding="utf-8") as f:
        json.dump(ebs_records, f, indent=2, ensure_ascii=False)
    logger.info(f"Wrote {len(ebs_records)} EBS volume types to {ebs_output_path}")

    return merged


# ---------------------------------------------------------------------------
# CLI validation entry point  (used by verification commands in PROJECT_TODO)
# ---------------------------------------------------------------------------

def _cli_validate(path: Path) -> None:
    """
    Validate an existing aws.raw.json file.
    Invoked by: python scripts/utils/data_validator.py data/providers/aws.raw.json
    This function is not the primary validator invocation; the validator module
    has its own __main__ path. This is here for completeness.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    errors: List[str] = []
    for i, inst in enumerate(data):
        if not validate_instance_data(inst):
            errors.append(f"Instance {i} ({inst.get('instanceType', '?')}): failed validate_instance_data")
        ok, errs = validate_commitments(inst.get("commitments", []), inst.get("priceUSD_hourly", 0))
        if not ok:
            errors.extend([f"Instance {i} ({inst.get('instanceType', '?')}): {e}" for e in errs])
    if errors:
        for e in errors[:50]:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"Total errors: {len(errors)}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"OK: {len(data)} instances validated successfully")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch AWS EC2 on-demand + reserved + savings-plan pricing."
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        metavar="REGION",
        help="AWS region code(s) to fetch. Defaults to all standard commercial regions.",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Output file path. Defaults to data/providers/aws.raw.json.",
    )
    parser.add_argument(
        "--validate-only",
        metavar="PATH",
        help="Validate an existing aws.raw.json file and exit.",
    )
    args = parser.parse_args()

    if args.validate_only:
        _cli_validate(Path(args.validate_only))
        sys.exit(0)

    output = Path(args.output) if args.output else None
    result = fetch_aws_data(
        regions=args.regions if args.regions else None,
        output_path=output,
    )
    print(f"Fetched {len(result)} instance types.")
