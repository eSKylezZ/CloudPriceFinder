#!/usr/bin/env python3
"""
AWS EC2 pricing fetcher for CloudPriceFinder v3.

Uses the AWS Pricing API (public, no auth required) with streaming JSON
parsing via ijson to keep memory usage bounded even for the large (100+ MB)
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

# Standard commercial AWS regions we include in v1.
# Excludes: cn-north-1, cn-northwest-1 (China), us-gov-* (GovCloud).
STANDARD_REGIONS: List[str] = [
    "af-south-1",
    "ap-east-1",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-northeast-3",
    "ap-south-1",
    "ap-south-2",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-southeast-3",
    "ap-southeast-4",
    "ap-southeast-5",
    "ca-central-1",
    "ca-west-1",
    "eu-central-1",
    "eu-central-2",
    "eu-north-1",
    "eu-south-1",
    "eu-south-2",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "il-central-1",
    "me-central-1",
    "me-south-1",
    "mx-central-1",
    "sa-east-1",
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
]

# OS filter — only Linux on-demand/reserved (RunInstances) for v1
INCLUDED_OS = {"Linux"}

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
}

# AWS 'physicalProcessor' strings that indicate ARM64
_ARM64_HINTS = ("graviton", "arm", "neoverse")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "CloudPriceFinder/3.0 (pricing-data-collector)"})
    return session


def _get_json(session: requests.Session, url: str) -> Dict[str, Any]:
    """Fetch a URL and parse as JSON with retry logic. For small payloads."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            logger.warning(f"Attempt {attempt} failed for {url}: {exc}. Retrying in {RETRY_BACKOFF}s…")
            time.sleep(RETRY_BACKOFF)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


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

def _stream_ec2_skus(
    session: requests.Session, url: str
) -> Generator[Dict[str, Any], None, None]:
    """
    Stream an EC2 per-region pricing JSON and yield individual SKU product+term dicts.

    The per-region JSON structure is:
    {
      "products": { "<sku>": { "sku": "...", "attributes": {...} }, ... },
      "terms": {
        "OnDemand": { "<sku>": { "<offerTermCode>": { "priceDimensions": {...} } } },
        "Reserved":  { ... }
      }
    }

    We parse this in two passes using ijson to avoid loading the full file into memory:
      Pass 1: collect all products (specs) into a dict keyed by SKU.
      Pass 2: read on-demand and reserved term pricing, merging with product specs.

    Because ijson streams from the network, we download the content once into an
    in-memory buffer (bytes), then parse it twice. This keeps memory under 1 GB for
    the largest regions (~120 MB raw JSON) since we only materialise the final
    normalised dicts.

    Yields one dict per (SKU, operating-system, instance-type) combination with:
      - All product attributes
      - on_demand_hourly: float
      - raw_reservations: list of raw term dicts for reserved pricing
    """
    logger.info(f"  Downloading pricing data from {url} …")
    t0 = time.time()

    resp = _stream_response(session, url)
    raw_bytes = resp.content
    elapsed = time.time() - t0
    size_mb = len(raw_bytes) / (1024 * 1024)
    logger.info(f"  Downloaded {size_mb:.1f} MB in {elapsed:.1f}s")

    # --- Pass 1: harvest product attributes ---
    # The `products` key in the EC2 pricing JSON is a dict (object), not an array.
    # ijson.kvitems(stream, "products") iterates over its (sku, value) pairs directly.
    products: Dict[str, Dict[str, Any]] = {}
    stream1 = io.BytesIO(raw_bytes)
    for sku, attrs in ijson.kvitems(stream1, "products"):
        attributes = attrs.get("attributes", {})
        instance_type = attributes.get("instanceType", "")
        if not instance_type:
            continue
        # Skip bare family name entries (e.g. "r8ib", "m5", "c7g") — these are
        # spurious AWS pricing API entries without a size qualifier.  All real
        # instance types have a dot separator (e.g. "m5.xlarge", "c7g.2xlarge").
        if "." not in instance_type:
            continue
        # Skip non-Linux OS (Windows, RHEL, SUSE, SQL, etc.) — we only want Linux on-demand
        if attributes.get("operatingSystem", "") not in INCLUDED_OS:
            continue
        # Skip Spot, DedicatedHost, CapacityBlock, etc. — only RunInstances and Host
        if attributes.get("marketoption", "").lower() in ("spot",):
            continue
        products[sku] = attributes

    logger.info(f"  Collected {len(products)} Linux product SKUs")

    # --- Pass 2: harvest on-demand pricing ---
    # terms.OnDemand is also a dict keyed by SKU.
    on_demand_prices: Dict[str, float] = {}  # sku -> hourly USD
    stream2 = io.BytesIO(raw_bytes)
    for sku, offer_terms in ijson.kvitems(stream2, f"terms.{TERM_ON_DEMAND}"):
        if sku not in products:
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

    logger.info(f"  Collected {len(on_demand_prices)} on-demand price points")

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
        od_price = on_demand_prices.get(sku)
        if od_price is None or od_price <= 0:
            continue
        yield {
            "sku": sku,
            "attributes": attrs,
            "on_demand_hourly": od_price,
            "raw_reservations": reserved_terms.get(sku, []),
        }


# ---------------------------------------------------------------------------
# Savings Plans streaming parser
# ---------------------------------------------------------------------------

def _fetch_savings_plans_for_region(
    session: requests.Session, region: str
) -> Dict[str, List[Dict[str, Any]]]:
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

    Returns a dict mapping instanceType -> list of raw commitment dicts.
    """
    try:
        region_index_data = _get_json(session, SP_INDEX_URL)
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

    result: Dict[str, List[Dict[str, Any]]] = {}

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
                # Only standard Linux on-demand shared-tenancy BoxUsage rates.
                # operation == 'RunInstances' with no suffix = Linux/Unix Shared tenancy.
                usage_type = rate.get("discountedUsageType", "")
                operation = rate.get("discountedOperation", "")

                # Skip anything that isn't standard shared-tenancy BoxUsage
                if "BoxUsage" not in usage_type:
                    continue
                # Only plain 'RunInstances' operation = Linux on-demand (no suffix = Linux/Unix)
                if operation != "RunInstances":
                    continue

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
                }
                result.setdefault(instance_type, []).append(entry)

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
    savings_plans: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    Download, stream-parse, and normalise EC2 pricing for one region.

    Returns a list of normalised instance dicts ready for output.
    """
    instances_by_type: Dict[str, Dict[str, Any]] = {}

    for raw_sku in _stream_ec2_skus(session, region_url):
        attrs = raw_sku["attributes"]
        on_demand_hourly = raw_sku["on_demand_hourly"]
        raw_reservations = raw_sku["raw_reservations"]
        instance_type = attrs.get("instanceType", "")

        if instance_type in instances_by_type:
            # Merge additional reservation terms (same instance in another SKU variant)
            instances_by_type[instance_type]["_raw_reservations"].extend(raw_reservations)
            continue

        # Extract specs
        vcpu = _parse_vcpu(attrs.get("vcpu", "0"))
        memory_gib = _parse_memory_gib(attrs.get("memory", "0 GiB"))
        processor = attrs.get("physicalProcessor", "")
        architecture = _detect_architecture(processor)
        gpu_info = _parse_gpu_info(attrs.get("gpu", "0"), attrs.get("gpuMemory", ""))
        family, generation = _extract_family_generation(instance_type)

        instances_by_type[instance_type] = {
            "instance_type": instance_type,
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
    now_iso = datetime.now(timezone.utc).isoformat()

    for instance_type, data in instances_by_type.items():
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
        sp_raw_list = savings_plans.get(instance_type, [])
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
            "family": data["family"],
            "architecture": data["architecture"],
            "vCPU": data["vcpu"],
            "memoryGiB": data["memory_gib"],
            "priceUSD_hourly": round(on_demand_hourly, 6),
            "priceUSD_monthly": round(on_demand_hourly * 730, 4),
            "commitments": normalised_reservations,
            "regions": [region],
            "source": "aws_pricing_api",
            "lastUpdated": now_iso,
        }

        if data["generation"]:
            record["generation"] = data["generation"]

        if data["gpu"]:
            record["gpu"] = data["gpu"]

        results.append(record)

    logger.info(f"  Region {region}: {len(results)} instance types")
    return results


# ---------------------------------------------------------------------------
# Region merging
# ---------------------------------------------------------------------------

def _merge_regions(
    per_region: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Merge per-region records into a single list.

    For the same instanceType appearing in multiple regions:
    - Union the `regions` list
    - Keep the on-demand price from the first region encountered (prices vary
      by region; we store the primary record per instance type and tag all
      regions; the aggregator (Stage 6) will split by region later)
    - Union the `commitments` list (deduped by term+payment+product)

    For v1, we keep one record per instanceType across all regions, with the
    `regions` list populated. The per-region price variation is captured in a
    `regionPricing` dict if prices differ.
    """
    # Master dict: instanceType -> merged record
    merged: Dict[str, Dict[str, Any]] = {}

    for region, instances in per_region.items():
        for inst in instances:
            itype = inst["instanceType"]
            od = inst["priceUSD_hourly"]

            if itype not in merged:
                record = dict(inst)
                record["regions"] = [region]
                record["regionPricing"] = {region: od}
                merged[itype] = record
            else:
                existing = merged[itype]
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
    if regions is None:
        regions = STANDARD_REGIONS
    else:
        # Filter out China/GovCloud — document exclusion
        filtered = []
        for r in regions:
            if r.startswith("cn-") or r.startswith("us-gov-"):
                logger.warning(
                    f"Skipping {r}: China and GovCloud regions use a separate pricing API "
                    "and are excluded from v1 (see PROJECT_TODO.md 'Out of scope')."
                )
            else:
                filtered.append(r)
        regions = filtered

    session = _make_session()

    # Step 1: Fetch top-level offer index (small JSON)
    logger.info("Fetching AWS top-level offer index …")
    try:
        offer_index = _get_json(session, OFFER_INDEX_URL)
    except Exception as exc:
        logger.error(f"Failed to fetch offer index: {exc}")
        raise

    # Step 2: Fetch EC2 region index
    logger.info("Fetching EC2 region index …")
    ec2_region_index_url = PRICING_BASE + EC2_REGION_INDEX_PATH
    try:
        region_index = _get_json(session, ec2_region_index_url)
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
            region_instances = _process_region(session, region, region_pricing_url, savings_plans)
            per_region[region] = region_instances
        except Exception as exc:
            logger.error(f"Failed to process region {region}: {exc}")
            # Continue with other regions — don't fail the entire run

    if not per_region:
        raise RuntimeError("No regions were successfully processed. Aborting.")

    # Step 4: Merge across regions
    logger.info("Merging across regions …")
    merged = _merge_regions(per_region)
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
