#!/usr/bin/env python3
"""
Oracle Cloud Infrastructure (OCI) Fetcher for CloudPriceFinder v3.

Uses the public cetools pricing API at:
    https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/

This API returns per-component pricing for OCI services (OCPU/hour,
GiB-RAM/hour, GPU/hour). Flexible shapes are represented by a set of
standard OCPU+memory configurations for comparison purposes.

Commitment-pricing note:
    OCI's public pricing API (cetools) exposes only PAY_AS_YOU_GO entries
    (no ANNUAL_FLEX / MONTHLY_FLEX models are returned per shape). OCI
    commitment discounts are handled through Universal Credits at account
    level and are not published per-shape in this endpoint. This fetcher
    ships on-demand-only pricing and sets a raw.note field documenting
    this caveat. See: https://docs.oracle.com/en-us/iaas/Content/Billing/Concepts/understanding_commit_pricing.htm

Out of scope for v1 (deferred to v3.1+):
    - OCI Government Cloud regions
    - OCI China regions
    - Bare Metal shapes (listed but not included in default output)

Usage:
    python scripts/fetch_oci.py [--output PATH]
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

from scripts.utils.data_normalizer import normalize_commitments
from scripts.utils.data_validator import validate_commitments, validate_instance_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_oci")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OCI_PRICING_URL = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/"
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_BACKOFF = 5

# Service categories for VM compute instances
VM_CATEGORIES = {
    "Compute - Virtual Machine",
    "Compute - Bare Metal",
    "Compute - GPU",
}

# OCI regions for v1 (standard commercial only)
OCI_REGIONS: List[Dict[str, str]] = [
    {"code": "us-ashburn-1",    "name": "US East (Ashburn)",              "country": "United States", "countryCode": "US"},
    {"code": "us-phoenix-1",    "name": "US West (Phoenix)",              "country": "United States", "countryCode": "US"},
    {"code": "us-sanjose-1",    "name": "US West (San Jose)",             "country": "United States", "countryCode": "US"},
    {"code": "us-chicago-1",    "name": "US Midwest (Chicago)",           "country": "United States", "countryCode": "US"},
    {"code": "ca-toronto-1",    "name": "Canada Southeast (Toronto)",     "country": "Canada",        "countryCode": "CA"},
    {"code": "ca-montreal-1",   "name": "Canada Southeast (Montreal)",    "country": "Canada",        "countryCode": "CA"},
    {"code": "eu-frankfurt-1",  "name": "Germany Central (Frankfurt)",    "country": "Germany",       "countryCode": "DE"},
    {"code": "eu-zurich-1",     "name": "Switzerland North (Zurich)",     "country": "Switzerland",   "countryCode": "CH"},
    {"code": "eu-amsterdam-1",  "name": "Netherlands Northwest (Amsterdam)", "country": "Netherlands", "countryCode": "NL"},
    {"code": "eu-london-1",     "name": "UK South (London)",              "country": "United Kingdom","countryCode": "GB"},
    {"code": "eu-stockholm-1",  "name": "Sweden Central (Stockholm)",     "country": "Sweden",        "countryCode": "SE"},
    {"code": "eu-milan-1",      "name": "Italy Northwest (Milan)",        "country": "Italy",         "countryCode": "IT"},
    {"code": "eu-paris-1",      "name": "France Central (Paris)",         "country": "France",        "countryCode": "FR"},
    {"code": "eu-madrid-1",     "name": "Spain Central (Madrid)",         "country": "Spain",         "countryCode": "ES"},
    {"code": "ap-mumbai-1",     "name": "India West (Mumbai)",            "country": "India",         "countryCode": "IN"},
    {"code": "ap-hyderabad-1",  "name": "India South (Hyderabad)",        "country": "India",         "countryCode": "IN"},
    {"code": "ap-seoul-1",      "name": "South Korea Central (Seoul)",    "country": "South Korea",   "countryCode": "KR"},
    {"code": "ap-chuncheon-1",  "name": "South Korea North (Chuncheon)",  "country": "South Korea",   "countryCode": "KR"},
    {"code": "ap-tokyo-1",      "name": "Japan East (Tokyo)",             "country": "Japan",         "countryCode": "JP"},
    {"code": "ap-osaka-1",      "name": "Japan Central (Osaka)",          "country": "Japan",         "countryCode": "JP"},
    {"code": "ap-sydney-1",     "name": "Australia East (Sydney)",        "country": "Australia",     "countryCode": "AU"},
    {"code": "ap-melbourne-1",  "name": "Australia Southeast (Melbourne)","country": "Australia",     "countryCode": "AU"},
    {"code": "ap-singapore-1",  "name": "Singapore (Singapore)",          "country": "Singapore",     "countryCode": "SG"},
    {"code": "sa-saopaulo-1",   "name": "Brazil East (Sao Paulo)",        "country": "Brazil",        "countryCode": "BR"},
    {"code": "sa-vinhedo-1",    "name": "Brazil Southeast (Vinhedo)",     "country": "Brazil",        "countryCode": "BR"},
    {"code": "me-jeddah-1",     "name": "Saudi Arabia West (Jeddah)",     "country": "Saudi Arabia",  "countryCode": "SA"},
    {"code": "me-dubai-1",      "name": "UAE East (Dubai)",               "country": "UAE",           "countryCode": "AE"},
    {"code": "af-johannesburg-1","name": "South Africa Central (Johannesburg)", "country": "South Africa", "countryCode": "ZA"},
    {"code": "il-jerusalem-1",  "name": "Israel Central (Jerusalem)",     "country": "Israel",        "countryCode": "IL"},
    {"code": "mx-queretaro-1",  "name": "Mexico Central (Queretaro)",     "country": "Mexico",        "countryCode": "MX"},
]

_REGION_CODES = [r["code"] for r in OCI_REGIONS]

# ---------------------------------------------------------------------------
# Flex shape configurations: (ocpu, memory_gib) pairs to enumerate
# These represent standard comparison points for flexible shapes.
# ---------------------------------------------------------------------------
_FLEX_CONFIGS: List[Tuple[int, int]] = [
    (1, 8),
    (2, 16),
    (4, 32),
    (8, 64),
    (16, 128),
    (32, 256),
    (64, 512),
]

_A1_FLEX_CONFIGS: List[Tuple[int, int]] = [
    (1, 6),
    (2, 12),
    (4, 24),
    (8, 48),
    (16, 96),
    (32, 192),
    (80, 512),
]

# GPU shape configurations: (gpu_count, ocpu, memory_gib)
_GPU_SHAPES: Dict[str, Dict[str, Any]] = {
    "BM.GPU3.8":   {"gpu_type": "NVIDIA V100", "gpu_count": 8, "ocpu": 52, "memory_gib": 768, "price_per_gpu": 1.275, "arch": "x86_64"},
    "VM.GPU3.1":   {"gpu_type": "NVIDIA V100", "gpu_count": 1, "ocpu": 6, "memory_gib": 90, "price_per_gpu": 1.275, "arch": "x86_64"},
    "VM.GPU3.2":   {"gpu_type": "NVIDIA V100", "gpu_count": 2, "ocpu": 12, "memory_gib": 180, "price_per_gpu": 1.275, "arch": "x86_64"},
    "VM.GPU3.4":   {"gpu_type": "NVIDIA V100", "gpu_count": 4, "ocpu": 24, "memory_gib": 360, "price_per_gpu": 1.275, "arch": "x86_64"},
    "BM.GPU4.8":   {"gpu_type": "NVIDIA A100 40GB", "gpu_count": 8, "ocpu": 64, "memory_gib": 2048, "price_per_gpu": 4.0, "arch": "x86_64"},
    "BM.GPU.A10.4":{"gpu_type": "NVIDIA A10", "gpu_count": 4, "ocpu": 64, "memory_gib": 1024, "price_per_gpu": 2.0, "arch": "x86_64"},
    "BM.GPU.H100.8":{"gpu_type": "NVIDIA H100", "gpu_count": 8, "ocpu": 112, "memory_gib": 2048, "price_per_gpu": 10.0, "arch": "x86_64"},
}

# Commitment pricing note (per Stage 4 requirements)
_COMMITMENT_NOTE = (
    "Commitment pricing on OCI is primarily handled via Universal Credits at account level; "
    "per-shape commitment data shown is the publicly listed annual-flex rate. "
    "The cetools public pricing API (apexapps.oracle.com) does not expose per-shape "
    "ANNUAL_FLEX or MONTHLY_FLEX pricing models — only PAY_AS_YOU_GO is returned. "
    "See: https://docs.oracle.com/en-us/iaas/Content/Billing/Concepts/understanding_commit_pricing.htm"
)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    })
    return session


def _get_json(session: requests.Session, url: str) -> Any:
    """Fetch URL with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            logger.warning(f"Attempt {attempt} failed for {url}: {exc}. Retrying in {RETRY_BACKOFF}s...")
            time.sleep(RETRY_BACKOFF)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


# ---------------------------------------------------------------------------
# Pricing API parsing
# ---------------------------------------------------------------------------

def _get_usd_price(item: Dict[str, Any], range_min: float = 0.0) -> Optional[float]:
    """Extract USD pay-as-you-go price from a cetools item, optionally at a price tier."""
    for loc in item.get("currencyCodeLocalizations", []):
        if loc["currencyCode"] == "USD":
            # Find price at or above range_min (for tiered items like A1 free tier)
            for p in loc.get("prices", []):
                if p.get("model") == "PAY_AS_YOU_GO":
                    if p.get("rangeMin", 0) >= range_min:
                        return float(p["value"])
            # Fallback: first PAY_AS_YOU_GO price
            for p in loc.get("prices", []):
                if p.get("model") == "PAY_AS_YOU_GO":
                    return float(p["value"])
    return None


def _extract_series_info(display_name: str) -> Tuple[str, str, str]:
    """
    Parse OCI display name into (series, generation, architecture).

    Examples:
      'Compute - Standard - E3 - OCPU' -> ('Standard', 'E3', 'x86_64')
      'Compute - Standard - A1 - OCPU' -> ('Standard', 'A1', 'arm64')
      'Compute - Dense I/O - E4 - OCPU' -> ('DenseIO', 'E4', 'x86_64')
      'Compute - Optimized - X9 - OCPU' -> ('Optimized', 'X9', 'x86_64')
    """
    name = display_name.upper()

    # Architecture
    if "A1" in name or "A2" in name or "A4" in name:
        arch = "arm64"
    else:
        arch = "x86_64"

    # Generation
    gen = "unknown"
    for candidate in ("E2", "E3", "E4", "E5", "E6", "X5", "X7", "X9", "X12", "B1", "A1", "A2", "A4", "V2"):
        if candidate in name:
            gen = candidate
            break

    # Series
    if "DENSE" in name or "DENSE I/O" in name:
        series = "DenseIO"
    elif "OPTIMIZED" in name:
        series = "Optimized"
    elif "HPC" in name:
        series = "HPC"
    elif "GPU" in name:
        series = "GPU"
    elif "STANDARD" in name:
        series = "Standard"
    else:
        series = "Standard"

    return series, gen, arch


def _build_flex_instance(
    shape_name: str,
    family: str,
    generation: str,
    architecture: str,
    ocpu: int,
    memory_gib: int,
    price_per_ocpu: float,
    price_per_gib: float,
    is_free_tier: bool = False,
    is_bare_metal: bool = False,
    disk_type: Optional[str] = None,
    disk_note: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct a standard v3 CloudInstance record for a flex shape."""
    # OCI: 1 OCPU = 2 vCPU for x86_64; 1 OCPU = 1 vCPU for Ampere (arm64)
    vcpu = ocpu if architecture == "arm64" else ocpu * 2

    hourly = round(ocpu * price_per_ocpu + memory_gib * price_per_gib, 6)
    monthly = round(hourly * 730.44, 4)

    instance_type = f"{shape_name}.{ocpu}OCPU.{memory_gib}GB"

    instance: Dict[str, Any] = {
        "provider": "oci",
        "type": "cloud-server",
        "instanceType": instance_type,
        "vCPU": vcpu,
        "memoryGiB": float(memory_gib),
        "architecture": architecture,
        "family": family.lower(),
        "generation": generation,
        "priceUSD_hourly": hourly,
        "priceUSD_monthly": monthly,
        # OCI on-demand pricing is uniform across regions (no per-region variation
        # for standard shapes in the public pricing API)
        "regions": _REGION_CODES,
        "locationDetails": [
            {
                "code": r["code"],
                "city": r["name"].split("(")[-1].rstrip(")") if "(" in r["name"] else r["name"],
                "country": r["country"],
                "countryCode": r["countryCode"],
                "region": r["code"],
            }
            for r in OCI_REGIONS
        ],
        # On-demand only — see commitment note below
        "commitments": [],
        "source": "oci_cetools_pricing_api",
        "description": (
            f"Oracle Cloud {shape_name} — {ocpu} OCPU, {memory_gib} GiB RAM"
            + (" (Free Tier Eligible)" if is_free_tier else "")
            + (" (Bare Metal)" if is_bare_metal else "")
        ),
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "raw": {
            "shape": shape_name,
            "ocpu": ocpu,
            "memory_gib": memory_gib,
            "price_per_ocpu_hourly": price_per_ocpu,
            "price_per_gib_hourly": price_per_gib,
            "free_tier_eligible": is_free_tier,
            "bare_metal": is_bare_metal,
            "note": _COMMITMENT_NOTE,
        },
    }

    if disk_type:
        instance["diskType"] = disk_type
    if disk_note:
        instance["raw"]["nvme_note"] = disk_note

    return instance


def _build_gpu_instance(
    shape_name: str,
    gpu_type: str,
    gpu_count: int,
    ocpu: int,
    memory_gib: int,
    price_per_gpu: float,
    architecture: str = "x86_64",
) -> Dict[str, Any]:
    """Construct a v3 CloudInstance record for a fixed GPU shape."""
    vcpu = ocpu * 2  # GPU shapes are x86_64

    hourly = round(gpu_count * price_per_gpu, 6)
    monthly = round(hourly * 730.44, 4)

    # Determine family/generation from shape name
    parts = shape_name.split(".")
    family = "gpu"
    generation = parts[2] if len(parts) > 2 else "unknown"

    # Resolve GPU memory from known types
    _gpu_mem: Dict[str, int] = {
        "NVIDIA V100": 16,
        "NVIDIA A10": 24,
        "NVIDIA A10G": 24,
        "NVIDIA A100 40GB": 40,
        "NVIDIA A100 80GB": 80,
        "NVIDIA H100": 80,
        "NVIDIA H200": 141,
        "NVIDIA L40S": 48,
    }
    gpu_mem = _gpu_mem.get(gpu_type, 16)

    instance: Dict[str, Any] = {
        "provider": "oci",
        "type": "cloud-server",
        "instanceType": shape_name,
        "vCPU": vcpu,
        "memoryGiB": float(memory_gib),
        "architecture": architecture,
        "family": family,
        "generation": generation,
        "gpu": {
            "count": gpu_count,
            "type": gpu_type,
            "memoryGiB": gpu_mem,
        },
        "priceUSD_hourly": hourly,
        "priceUSD_monthly": monthly,
        "regions": _REGION_CODES,
        "locationDetails": [
            {
                "code": r["code"],
                "city": r["name"].split("(")[-1].rstrip(")") if "(" in r["name"] else r["name"],
                "country": r["country"],
                "countryCode": r["countryCode"],
                "region": r["code"],
            }
            for r in OCI_REGIONS
        ],
        "commitments": [],
        "source": "oci_cetools_pricing_api",
        "description": f"Oracle Cloud {shape_name} — {gpu_count}x {gpu_type}, {ocpu} OCPU, {memory_gib} GiB RAM",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "raw": {
            "shape": shape_name,
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "ocpu": ocpu,
            "memory_gib": memory_gib,
            "price_per_gpu_hourly": price_per_gpu,
            "note": _COMMITMENT_NOTE,
        },
    }

    return instance


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

class OCIFetcher:
    def __init__(self) -> None:
        self.session = _make_session()

    def fetch_all(self) -> List[Dict[str, Any]]:
        """Fetch all OCI compute instances from the cetools API."""
        logger.info("Fetching OCI pricing from cetools API...")
        raw_data = _get_json(self.session, OCI_PRICING_URL)

        if not isinstance(raw_data, dict) or "items" not in raw_data:
            raise ValueError(f"Unexpected API response structure: {type(raw_data)}")

        items = raw_data["items"]
        logger.info(f"Received {len(items)} total items from cetools API")

        # Filter to VM/BM/GPU compute categories
        compute_items = [
            i for i in items
            if i.get("serviceCategory", "") in VM_CATEGORIES
        ]
        logger.info(f"Found {len(compute_items)} compute (VM/BM/GPU) items")

        # Build lookup by display name (normalized) for OCPU + memory pairing
        ocpu_prices: Dict[str, float] = {}
        gib_prices: Dict[str, float] = {}
        gpu_prices: Dict[str, Tuple[str, float]] = {}  # name -> (metric, price)

        for item in compute_items:
            name = item["displayName"]
            metric = item.get("metricName", "")
            price = _get_usd_price(item, range_min=3000) if "A1" in name.upper() else _get_usd_price(item)

            if price is None:
                logger.debug(f"No USD price for: {name}")
                continue

            metric_lower = metric.lower()
            if "ocpu" in metric_lower:
                ocpu_prices[name] = price
            elif "gigabyte" in metric_lower or "gigabytes" in metric_lower:
                gib_prices[name] = price
            elif "gpu" in metric_lower and "nvidia" not in name.lower() and "ai enterprise" not in name.lower():
                # GPU per-hour items (skip NVIDIA AI Enterprise add-on licenses)
                gpu_prices[name] = (metric, price)

        logger.info(
            f"Parsed prices: {len(ocpu_prices)} OCPU, {len(gib_prices)} GiB, {len(gpu_prices)} GPU items"
        )

        # ---------------------------------------------------------------------------
        # Build flex shape instances
        # ---------------------------------------------------------------------------
        instances: List[Dict[str, Any]] = []

        # Shape definitions: (display_name_fragment_ocpu, display_name_fragment_mem, shape_prefix, is_free, configs)
        flex_shapes = [
            # (ocpu_key_substr, gib_key_substr, shape_name, is_free_tier, configs)
            ("Standard - E3 - OCPU",    "Standard - E3 - Memory",    "VM.Standard.E3.Flex",    False, _FLEX_CONFIGS),
            ("Standard - E4 - OCPU",    "Standard - E4  - Memory",   "VM.Standard.E4.Flex",    False, _FLEX_CONFIGS),
            ("Standard - E5 - OCPU",    "Standard - E5 - Memory",    "VM.Standard.E5.Flex",    False, _FLEX_CONFIGS),
            ("Standard - E6 - OCPU",    "Standard - E6 - Memory",    "VM.Standard.E6.Flex",    False, _FLEX_CONFIGS),
            ("Standard - A1 - OCPU",    "Standard - A1 - Memory",    "VM.Standard.A1.Flex",    True,  _A1_FLEX_CONFIGS),
            ("Standard - A2 OCPU",      "Standard - A2 Memory",      "VM.Standard.A2.Flex",    False, _A1_FLEX_CONFIGS),
            ("Optimized - X9 - OCPU",   "Optimized - X9 - Memory",   "VM.Optimized3.Flex",     False, _FLEX_CONFIGS),
            ("Standard - X9 - OCPU",    "Standard - X9 - Memory",    "VM.Standard3.Flex",      False, _FLEX_CONFIGS),
            # DenseIO flex shapes (OCPU + memory only; NVMe cost not included in instance compute price)
            ("Dense I/O - E4 - OCPU",   "Dense I/O - E4 - Memory",   "VM.DenseIO.E4.Flex",     False, _FLEX_CONFIGS),
            ("Dense I/O - E5 OCPU",     "Dense I/O - E5 Memory",     "VM.DenseIO.E5.Flex",     False, _FLEX_CONFIGS),
        ]

        for ocpu_substr, gib_substr, shape_name, is_free, configs in flex_shapes:
            # Find matching price items
            ocpu_key = next((k for k in ocpu_prices if ocpu_substr in k), None)
            gib_key = next((k for k in gib_prices if gib_substr in k), None)

            if ocpu_key is None:
                logger.warning(f"No OCPU price found for shape {shape_name} (substr: {ocpu_substr})")
                continue

            price_ocpu = ocpu_prices[ocpu_key]
            price_gib = gib_prices.get(gib_key, 0.0) if gib_key else 0.0

            # Determine architecture
            arch = "arm64" if ("A1" in shape_name or "A2" in shape_name) else "x86_64"

            # Determine family and generation from shape name
            parts = shape_name.split(".")
            # VM.Standard.E3.Flex -> family=standard, generation=E3
            if len(parts) >= 3:
                series_raw = parts[1]  # Standard, DenseIO, Optimized
                gen_raw = parts[2]     # E3, E4, A1, X9, etc.
            else:
                series_raw = "Standard"
                gen_raw = "unknown"

            # Map series to family label
            family_map = {
                "Standard": "standard",
                "DenseIO": "denseio",
                "Optimized": "optimized",
                "GPU": "gpu",
                "HPC": "hpc",
            }
            family = family_map.get(series_raw, series_raw.lower())

            # DenseIO note
            disk_type = "NVMe SSD" if "DenseIO" in shape_name or "DenseIO" in series_raw else None
            disk_note = "NVMe storage pricing is separate from compute pricing" if disk_type else None

            for (ocpu, memory_gib) in configs:
                inst = _build_flex_instance(
                    shape_name=shape_name,
                    family=family,
                    generation=gen_raw,
                    architecture=arch,
                    ocpu=ocpu,
                    memory_gib=memory_gib,
                    price_per_ocpu=price_ocpu,
                    price_per_gib=price_gib,
                    is_free_tier=is_free,
                    disk_type=disk_type,
                    disk_note=disk_note,
                )
                instances.append(inst)

        # ---------------------------------------------------------------------------
        # Fixed (non-flex) legacy shapes
        # ---------------------------------------------------------------------------
        fixed_shapes = [
            # (shape_name, ocpu, memory_gib, price_ocpu_key_substr, arch, family, gen, desc_note)
            ("VM.Standard.E2.1.Micro", 1, 1,  "Standard - E2", "x86_64", "standard", "E2", "Free Tier Eligible"),
            ("VM.Standard.E2.1",       1, 8,  "Standard - E2", "x86_64", "standard", "E2", None),
            ("VM.Standard.E2.2",       2, 16, "Standard - E2", "x86_64", "standard", "E2", None),
            ("VM.Standard.E2.4",       4, 32, "Standard - E2", "x86_64", "standard", "E2", None),
            ("VM.Standard.E2.8",       8, 64, "Standard - E2", "x86_64", "standard", "E2", None),
            ("VM.Standard.B1.1",       1, 16, "Virtual Machine Standard - B1", "x86_64", "standard", "B1", None),
            ("VM.Standard.B1.2",       2, 32, "Virtual Machine Standard - B1", "x86_64", "standard", "B1", None),
            ("VM.Standard.B1.4",       4, 64, "Virtual Machine Standard - B1", "x86_64", "standard", "B1", None),
            ("VM.Standard2.1",         1, 15, "Virtual Machine Standard - X7", "x86_64", "standard", "X7", None),
            ("VM.Standard2.2",         2, 30, "Virtual Machine Standard - X7", "x86_64", "standard", "X7", None),
            ("VM.Standard2.4",         4, 60, "Virtual Machine Standard - X7", "x86_64", "standard", "X7", None),
            ("VM.Standard2.8",         8, 120, "Virtual Machine Standard - X7", "x86_64", "standard", "X7", None),
            ("VM.Standard2.16",        16, 240, "Virtual Machine Standard - X7", "x86_64", "standard", "X7", None),
            ("VM.Standard2.24",        24, 320, "Virtual Machine Standard - X7", "x86_64", "standard", "X7", None),
            ("VM.DenseIO2.8",          8, 120, "Virtual Machine Dense I/O - X7", "x86_64", "denseio", "X7", None),
            ("VM.DenseIO2.16",         16, 240, "Virtual Machine Dense I/O - X7", "x86_64", "denseio", "X7", None),
            ("VM.DenseIO2.24",         24, 320, "Virtual Machine Dense I/O - X7", "x86_64", "denseio", "X7", None),
        ]

        for (shape_name, ocpu, memory_gib, ocpu_key_substr, arch, family, gen, note) in fixed_shapes:
            ocpu_key = next((k for k in ocpu_prices if ocpu_key_substr in k), None)
            if ocpu_key is None:
                logger.debug(f"No price found for fixed shape {shape_name} (key: {ocpu_key_substr})")
                continue

            price_ocpu = ocpu_prices[ocpu_key]
            is_free = note is not None and "Free Tier" in note
            disk_type = "NVMe SSD" if "DenseIO" in shape_name else None

            hourly = round(ocpu * price_ocpu, 6)
            monthly = round(hourly * 730.44, 4)
            vcpu = ocpu if arch == "arm64" else ocpu * 2

            inst: Dict[str, Any] = {
                "provider": "oci",
                "type": "cloud-server",
                "instanceType": shape_name,
                "vCPU": vcpu,
                "memoryGiB": float(memory_gib),
                "architecture": arch,
                "family": family,
                "generation": gen,
                "priceUSD_hourly": hourly,
                "priceUSD_monthly": monthly,
                "regions": _REGION_CODES,
                "locationDetails": [
                    {
                        "code": r["code"],
                        "city": r["name"].split("(")[-1].rstrip(")") if "(" in r["name"] else r["name"],
                        "country": r["country"],
                        "countryCode": r["countryCode"],
                        "region": r["code"],
                    }
                    for r in OCI_REGIONS
                ],
                "commitments": [],
                "source": "oci_cetools_pricing_api",
                "description": f"Oracle Cloud {shape_name} — {ocpu} OCPU, {memory_gib} GiB RAM"
                    + (f" ({note})" if note else ""),
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
                "raw": {
                    "shape": shape_name,
                    "ocpu": ocpu,
                    "memory_gib": memory_gib,
                    "price_per_ocpu_hourly": price_ocpu,
                    "free_tier_eligible": is_free,
                    "note": _COMMITMENT_NOTE,
                },
            }

            if disk_type:
                inst["diskType"] = disk_type

            instances.append(inst)

        # ---------------------------------------------------------------------------
        # GPU shapes
        # ---------------------------------------------------------------------------
        for shape_name, cfg in _GPU_SHAPES.items():
            # Look for a matching GPU price in the API data
            gpu_type_short = cfg["gpu_type"].split()[1] if " " in cfg["gpu_type"] else cfg["gpu_type"]

            # Try to find API price; fall back to known static price
            api_price: Optional[float] = None
            for api_name, (metric, p) in gpu_prices.items():
                name_upper = api_name.upper()
                type_upper = gpu_type_short.upper()
                if type_upper in name_upper or (cfg["gpu_type"] and cfg["gpu_type"].upper() in name_upper):
                    api_price = p
                    break

            price_per_gpu = api_price if api_price is not None else cfg["price_per_gpu"]

            inst = _build_gpu_instance(
                shape_name=shape_name,
                gpu_type=cfg["gpu_type"],
                gpu_count=cfg["gpu_count"],
                ocpu=cfg["ocpu"],
                memory_gib=cfg["memory_gib"],
                price_per_gpu=price_per_gpu,
                architecture=cfg["arch"],
            )
            instances.append(inst)

        logger.info(f"Built {len(instances)} OCI compute instances")
        return instances


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_output(instances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate each instance and return only valid ones."""
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
    parser = argparse.ArgumentParser(description="OCI compute pricing fetcher (CloudPriceFinder v3)")
    parser.add_argument(
        "--output",
        default="data/providers/oci.raw.json",
        help="Output JSON file path (default: data/providers/oci.raw.json)",
    )
    args = parser.parse_args(argv)

    logger.info("=== OCI Fetcher (CloudPriceFinder v3) ===")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    fetcher = OCIFetcher()
    instances = fetcher.fetch_all()

    if not instances:
        logger.error("No instances collected — aborting")
        return 1

    valid = _validate_output(instances)
    logger.info(f"Valid instances: {len(valid)}/{len(instances)}")

    if len(valid) < 20:
        logger.error(f"Too few valid instances ({len(valid)}), expected >=20")
        return 1

    # Write output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(valid, f, indent=2, ensure_ascii=False)

    logger.info(f"Wrote {len(valid)} instances to {out_path}")

    # Summary
    by_family: Dict[str, int] = {}
    by_arch: Dict[str, int] = {}
    for inst in valid:
        fam = inst.get("family", "unknown")
        by_family[fam] = by_family.get(fam, 0) + 1
        arch = inst.get("architecture", "unknown")
        by_arch[arch] = by_arch.get(arch, 0) + 1

    logger.info("Summary by family:")
    for k, v in sorted(by_family.items()):
        logger.info(f"  {k}: {v}")
    logger.info("Summary by architecture:")
    for k, v in sorted(by_arch.items()):
        logger.info(f"  {k}: {v}")

    # Commitment info
    with_commitments = sum(1 for i in valid if i.get("commitments"))
    logger.info(
        f"Instances with commitment entries: {with_commitments}/{len(valid)} "
        f"(OCI does not expose per-shape commitment pricing publicly — on-demand only)"
    )

    return 0


# ---------------------------------------------------------------------------
# Orchestrator compatibility
# ---------------------------------------------------------------------------

def fetch_oci_data() -> List[Dict[str, Any]]:
    """Entry point for the orchestrator."""
    fetcher = OCIFetcher()
    instances = fetcher.fetch_all()
    return _validate_output(instances)


if __name__ == "__main__":
    sys.exit(main())
