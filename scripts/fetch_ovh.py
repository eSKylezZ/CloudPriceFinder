#!/usr/bin/env python3
"""
OVHcloud Fetcher for CloudPriceFinder v3.

Uses the public OVH catalog API (no authentication required):
    Compute VMs:  https://eu.api.ovh.com/v1/order/catalog/public/cloud?ovhSubsidiary=IE
    Bare Metal:   https://eu.api.ovh.com/v1/order/catalog/public/baremetalServers?ovhSubsidiary=IE

API structure note:
    Despite the task description referencing a "plans[]" array with per-plan pricings
    using ISO 8601 duration strings, the actual OVH catalog API stores compute instances
    in the "addons[]" array. Each addon has:
      - planCode: e.g. "b3-8.consumption" or "b3-8.consumption.3AZ"
      - blobs.technical.name: canonical instance name, e.g. "b3-8"
      - blobs.technical.cpu.cores: vCPU count
      - blobs.technical.memory.size: RAM in GiB
      - pricings[].intervalUnit: "hour" (on-demand) or "month" (monthly)
      - pricings[].price: in 1/100,000,000 EUR units

    OVH does not publish per-instance commitment/reserved pricing via this API.
    On-demand (hourly/monthly) pricing only.

Usage:
    python scripts/fetch_ovh.py [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
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
logger = logging.getLogger("fetch_ovh")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OVH_CLOUD_URL = "https://eu.api.ovh.com/v1/order/catalog/public/cloud?ovhSubsidiary=IE"
OVH_BM_URL = "https://eu.api.ovh.com/v1/order/catalog/public/baremetalServers?ovhSubsidiary=IE"
OVH_ECO_URL = "https://eu.api.ovh.com/v1/order/catalog/public/eco?ovhSubsidiary=IE"
OVH_VPS_URL = "https://eu.api.ovh.com/v1/order/catalog/public/vps?ovhSubsidiary=IE"
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_BACKOFF = 5

# OVH API: prices are in 1/100,000,000 EUR units
PRICE_FACTOR = 100_000_000

# Plan code prefixes to skip entirely (not compute instances)
_SKIP_PREFIXES = (
    "vps-ssd-",      # VPS product line
    "databases.",    # Managed databases
    "ai-",           # AI platform services
    "data-processing",
    "certification.",
    "quota-",
    "bandwidth_",
    "publicip.",
    "volume.",
    "cloud.credit",
    "snapshot.",
)

# Family mapping: checked in insertion order; first prefix match wins
_FAMILY_PREFIXES: List[Tuple[str, str]] = [
    # GPU families
    ("a10-",     "gpu"),
    ("a100-",    "gpu"),
    ("g1-",      "gpu"),
    ("g2-",      "gpu"),
    ("g3-",      "gpu"),
    ("h100-",    "gpu"),
    ("h200-",    "gpu"),
    ("l4-",      "gpu"),
    ("l40s-",    "gpu"),
    ("rtx5000-", "gpu"),
    ("t1-",      "gpu"),   # Tesla V100
    ("t2-",      "gpu"),   # Tesla V100S
    # Compute families (matching the spec + discovered types)
    ("b2-",      "balanced"),
    ("b3-",      "balanced"),
    ("c2-",      "compute-optimized"),
    ("c3-",      "compute-optimized"),
    ("r2-",      "memory-optimized"),
    ("r3-",      "memory-optimized"),
    ("eg-",      "memory-optimized"),   # Extra-generous RAM
    ("i1-",      "storage-optimized"),
    ("sp-",      "storage-optimized"),  # Storage-performance
    ("hg-",      "high-performance"),
    ("d2-",      "sandbox"),
    ("s1-",      "sandbox"),
    ("bm-",      "bare-metal"),
]

# VPS datacenter code → location detail
_VPS_DC_LOCATIONS: Dict[str, Dict[str, str]] = {
    "GRA": {"code": "GRA", "name": "Gravelines (GRA)",  "country": "France",         "countryCode": "FR"},
    "SBG": {"code": "SBG", "name": "Strasbourg (SBG)",  "country": "France",         "countryCode": "FR"},
    "RBX": {"code": "RBX", "name": "Roubaix (RBX)",     "country": "France",         "countryCode": "FR"},
    "UK":  {"code": "UK1", "name": "London (UK1)",       "country": "United Kingdom", "countryCode": "GB"},
    "DE":  {"code": "DE1", "name": "Frankfurt (DE1)",    "country": "Germany",        "countryCode": "DE"},
    "WAW": {"code": "WAW", "name": "Warsaw (WAW)",       "country": "Poland",         "countryCode": "PL"},
    "BHS": {"code": "BHS", "name": "Beauharnois (BHS)", "country": "Canada",         "countryCode": "CA"},
    "SGP": {"code": "SGP", "name": "Singapore (SGP)",   "country": "Singapore",      "countryCode": "SG"},
    "SYD": {"code": "SYD", "name": "Sydney (SYD)",      "country": "Australia",      "countryCode": "AU"},
}

# VPS product name prefix → family (first match wins)
_VPS_FAMILY_MAP: List[Tuple[str, str]] = [
    ("vps-starter-",   "sandbox"),
    ("vps-le-",        "entry-level"),
    ("vps-value-",     "entry-level"),
    ("vps-essential-", "general-purpose"),
    ("vps-comfort-",   "general-purpose"),
    ("vps-elite-",     "compute-optimized"),
    ("vps-2025-",      "general-purpose"),
]

# Eco server location suffix → datacenter metadata
_ECO_SUFFIX_LOCATIONS: List[Tuple[str, Dict[str, str]]] = [
    ("-sgp", {"code": "SGP", "name": "Singapore (SGP)", "country": "Singapore",  "countryCode": "SG"}),
    ("-mum", {"code": "MUM", "name": "Mumbai (MUM)",    "country": "India",       "countryCode": "IN"}),
    ("-syd", {"code": "SYD", "name": "Sydney (SYD)",    "country": "Australia",   "countryCode": "AU"}),
    ("-can", {"code": "BHS", "name": "Beauharnois (BHS)","country": "Canada",     "countryCode": "CA"}),
    ("-us",  {"code": "US-EAST-VA", "name": "Vint Hill (US-EAST-VA)", "country": "United States", "countryCode": "US"}),
]
_ECO_DEFAULT_LOCATION: Dict[str, str] = {
    "code": "RBX", "name": "Roubaix (RBX)", "country": "France", "countryCode": "FR"
}

# Eco commercial-name prefix → family (first match wins)
_ECO_FAMILY_MAP: List[Tuple[str, str]] = [
    ("KS-GAME",     "high-performance"),
    ("KS-STOR",     "storage-optimized"),
    ("KS-",         "entry-level"),
    ("RISE-GAME",   "high-performance"),
    ("RISE-STOR",   "storage-optimized"),
    ("RISE-",       "general-purpose"),
    ("ADVANCE-STOR","storage-optimized"),
    ("ADVANCE-",    "compute-optimized"),
    ("SYS-GAME",    "high-performance"),
    ("SYS-STOR",    "storage-optimized"),
    ("SYS-",        "high-performance"),
]

# OVH Public Cloud regions — pricing is uniform across all regions
OVH_REGIONS: List[Dict[str, str]] = [
    {"code": "GRA", "name": "Gravelines (GRA)",    "country": "France",         "countryCode": "FR"},
    {"code": "SBG", "name": "Strasbourg (SBG)",     "country": "France",         "countryCode": "FR"},
    {"code": "RBX", "name": "Roubaix (RBX)",        "country": "France",         "countryCode": "FR"},
    {"code": "UK1", "name": "London (UK1)",          "country": "United Kingdom", "countryCode": "GB"},
    {"code": "DE1", "name": "Frankfurt (DE1)",       "country": "Germany",        "countryCode": "DE"},
    {"code": "WAW", "name": "Warsaw (WAW)",          "country": "Poland",         "countryCode": "PL"},
    {"code": "BHS", "name": "Beauharnois (BHS)",     "country": "Canada",         "countryCode": "CA"},
    {"code": "YYZ", "name": "Toronto (YYZ)",         "country": "Canada",         "countryCode": "CA"},
    {"code": "SGP", "name": "Singapore (SGP)",       "country": "Singapore",      "countryCode": "SG"},
    {"code": "SYD", "name": "Sydney (SYD)",          "country": "Australia",      "countryCode": "AU"},
    {"code": "US-EAST-VA",       "name": "Vint Hill (US-EAST-VA)",         "country": "United States", "countryCode": "US"},
    {"code": "AP-SOUTHEAST-SIN", "name": "Singapore (AP-SOUTHEAST-SIN)",   "country": "Singapore",     "countryCode": "SG"},
    {"code": "CA-EAST-BHS",      "name": "Beauharnois (CA-EAST-BHS)",      "country": "Canada",        "countryCode": "CA"},
    {"code": "EU-WEST-PAR",      "name": "Paris (EU-WEST-PAR)",            "country": "France",        "countryCode": "FR"},
]

_REGION_CODES = [r["code"] for r in OVH_REGIONS]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_family(instance_name: str) -> str:
    """Map an instance name to a family string via prefix matching."""
    name_lower = instance_name.lower()
    for prefix, family in _FAMILY_PREFIXES:
        if name_lower.startswith(prefix):
            return family
    return "general-purpose"


def _extract_generation(instance_name: str) -> str:
    """Extract generation digit from instance name (e.g. 'b3-8' -> '3')."""
    m = re.match(r'^[a-z]+(\d+)-', instance_name.lower())
    return m.group(1) if m else "unknown"


def _should_skip(plan_code: str) -> bool:
    """Return True if this plan code should be excluded."""
    code_lower = plan_code.lower()
    return any(code_lower.startswith(p) for p in _SKIP_PREFIXES)


def _detect_os(plan_code: str, tech: Dict[str, Any]) -> str:
    """Return 'Windows' or 'Linux' based on plan code and technical metadata."""
    if plan_code.lower().startswith("win-") or "windows" in (tech.get("name") or "").lower():
        return "Windows"
    return "Linux"


def _get_hourly_price_eur(pricings: List[Dict[str, Any]]) -> Optional[float]:
    """Extract hourly on-demand EUR price from pricings list."""
    for p in pricings:
        if p.get("intervalUnit") == "hour" and p.get("price", 0) > 0:
            return p["price"] / PRICE_FACTOR
    return None


def _get_monthly_price_eur(pricings: List[Dict[str, Any]]) -> Optional[float]:
    """Extract monthly EUR price from pricings list."""
    for p in pricings:
        if p.get("intervalUnit") == "month" and p.get("price", 0) > 0:
            return p["price"] / PRICE_FACTOR
    return None


def _build_location_details() -> List[Dict[str, Any]]:
    return [
        {
            "code": r["code"],
            "city": r["name"],
            "country": r["country"],
            "countryCode": r["countryCode"],
            "region": r["code"],
        }
        for r in OVH_REGIONS
    ]


def _eco_family(commercial_name: str) -> str:
    for prefix, family in _ECO_FAMILY_MAP:
        if commercial_name.startswith(prefix):
            return family
    return "general-purpose"


def _eco_location(plan_code: str) -> Dict[str, str]:
    for suffix, loc in _ECO_SUFFIX_LOCATIONS:
        if plan_code.endswith(suffix):
            return loc
    return _ECO_DEFAULT_LOCATION


def _parse_ram_from_addon(addon_name: str) -> Optional[float]:
    """Parse GiB from addon name like 'ram-128g-ecc-2933-24rise07'."""
    m = re.match(r"^ram-(\d+)g-", addon_name)
    return float(m.group(1)) if m else None


def _get_eco_monthly_price_eur(pricings: List[Dict[str, Any]]) -> Optional[float]:
    for p in pricings:
        if (
            p.get("mode") == "default"
            and p.get("intervalUnit") == "month"
            and "renew" in (p.get("capacities") or [])
            and p.get("price", 0) > 0
        ):
            return p["price"] / PRICE_FACTOR
    return None


def _get_eco_commitments(
    pricings: List[Dict[str, Any]], od_hourly_usd: float
) -> List[Dict[str, Any]]:
    result = []
    for p in pricings:
        mode = p.get("mode", "")
        if mode not in ("upfront12", "upfront24"):
            continue
        if "renew" not in (p.get("capacities") or []):
            continue
        total_eur = p.get("price", 0) / PRICE_FACTOR
        interval = p.get("interval", 0)
        if total_eur <= 0 or interval <= 0:
            continue
        effective_hourly_usd = round(
            convert_currency(total_eur / interval / HOURS_PER_MONTH, "EUR", "USD"), 6
        )
        savings = round((1 - effective_hourly_usd / od_hourly_usd) * 100, 1) if od_hourly_usd > 0 else 0
        result.append({
            "term": "1yr" if mode == "upfront12" else "2yr",
            "payment": "all-upfront",
            "product": "reserved",
            "priceUSD_hourly": effective_hourly_usd,
            "effectiveHourlyUSD": effective_hourly_usd,
            "savingsVsOnDemandPct": savings,
        })
    return result


def _parse_eco_plan(
    plan: Dict[str, Any],
    products_map: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    plan_code = (plan.get("planCode") or "").strip()
    if not plan_code:
        return None

    prod = products_map.get(plan.get("product") or "") or {}
    server = ((prod.get("blobs") or {}).get("technical") or {}).get("server") or {}

    cpu = server.get("cpu") or {}
    cores = cpu.get("cores")
    sockets = cpu.get("number", 1)
    if not cores:
        return None
    vcpu = int(cores) * int(sockets)

    # RAM from default memory addon name
    mem_fam = next((f for f in (plan.get("addonFamilies") or []) if f.get("name") == "memory"), None)
    memory_gib: Optional[float] = None
    if mem_fam and mem_fam.get("default"):
        memory_gib = _parse_ram_from_addon(mem_fam["default"])
    if memory_gib is None:
        return None

    monthly_eur = _get_eco_monthly_price_eur(plan.get("pricings") or [])
    if monthly_eur is None or monthly_eur <= 0:
        return None

    monthly_usd = round(convert_currency(monthly_eur, "EUR", "USD"), 4)
    hourly_eur = monthly_eur / HOURS_PER_MONTH
    hourly_usd = round(convert_currency(hourly_eur, "EUR", "USD"), 6)

    commercial = ((plan.get("blobs") or {}).get("commercial") or {})
    commercial_name = (commercial.get("name") or "").strip()
    cpu_model = cpu.get("model") or ""
    invoice_name = (plan.get("invoiceName") or "").strip()
    range_name = (server.get("range") or "").lower()
    loc = _eco_location(plan_code)

    return {
        "provider": "ovh",
        "type": "dedicated-server",
        "instanceType": plan_code,
        "vCPU": vcpu,
        "memoryGiB": memory_gib,
        "architecture": "x86_64",
        "family": _eco_family(commercial_name) if commercial_name else "general-purpose",
        "generation": "unknown",
        "priceUSD_hourly": hourly_usd,
        "priceUSD_monthly": monthly_usd,
        "originalPrice": {
            "hourly": round(hourly_eur, 8),
            "monthly": round(monthly_eur, 6),
            "currency": "EUR",
        },
        "regions": [loc["code"]],
        "locationDetails": [{
            "code": loc["code"],
            "city": loc["name"],
            "country": loc["country"],
            "countryCode": loc["countryCode"],
            "region": loc["code"],
        }],
        "commitments": _get_eco_commitments(plan.get("pricings") or [], hourly_usd),
        "source": "ovh_eco_catalog_api",
        "description": invoice_name or f"OVHcloud {commercial_name or plan_code} ({cpu_model})",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "raw": {
            "planCode": plan_code,
            "invoiceName": invoice_name,
            "cpuModel": cpu_model,
            "commercialName": commercial_name,
            "range": range_name,
        },
    }


def _vps_family(product_name: str) -> str:
    for prefix, family in _VPS_FAMILY_MAP:
        if product_name.startswith(prefix):
            return family
    return "general-purpose"


def _parse_vps_plan(
    plan: Dict[str, Any],
    products_map: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Parse a single VPS plan into a CloudInstance record.

    Specs live in the linked product blob. Multiple plans may share a product
    (promotional/discount variants); callers should pre-select one canonical
    plan per product before calling this function.
    """
    plan_code = (plan.get("planCode") or "").strip()
    if not plan_code:
        return None

    prod = products_map.get(plan.get("product") or "") or {}
    tech = (prod.get("blobs") or {}).get("technical") or {}

    vcpu = (tech.get("cpu") or {}).get("cores")
    if not vcpu:
        return None
    vcpu = int(vcpu)

    memory_gib = (tech.get("memory") or {}).get("size")
    if not memory_gib:
        return None
    memory_gib = float(memory_gib)

    disks = (tech.get("storage") or {}).get("disks") or []
    disk_size_gb = float(disks[0]["capacity"]) if disks else None
    disk_tech = (disks[0].get("technology") or "").lower() if disks else None

    monthly_eur = _get_eco_monthly_price_eur(plan.get("pricings") or [])
    if monthly_eur is None or monthly_eur <= 0:
        return None

    monthly_usd = round(convert_currency(monthly_eur, "EUR", "USD"), 4)
    hourly_eur = monthly_eur / HOURS_PER_MONTH
    hourly_usd = round(convert_currency(hourly_eur, "EUR", "USD"), 6)

    # Regions from vps_datacenter configuration
    dc_cfg = next(
        (c for c in (plan.get("configurations") or []) if c.get("name") == "vps_datacenter"),
        None,
    )
    dc_codes: List[str] = (dc_cfg.get("values") or []) if dc_cfg else []
    region_codes: List[str] = []
    location_details: List[Dict[str, Any]] = []
    for code in dc_codes:
        loc = _VPS_DC_LOCATIONS.get(code)
        if loc:
            region_codes.append(loc["code"])
            location_details.append({
                "code": loc["code"],
                "city": loc["name"],
                "country": loc["country"],
                "countryCode": loc["countryCode"],
                "region": loc["code"],
            })
    if not region_codes:
        region_codes = ["GRA"]
        location_details = [{
            "code": "GRA", "city": "Gravelines (GRA)",
            "country": "France", "countryCode": "FR", "region": "GRA",
        }]

    prod_name = plan.get("product") or plan_code
    invoice_name = (plan.get("invoiceName") or "").strip()

    record: Dict[str, Any] = {
        "provider": "ovh",
        "type": "cloud-server",
        "instanceType": plan_code,
        "vCPU": vcpu,
        "memoryGiB": memory_gib,
        "architecture": "x86_64",
        "family": _vps_family(prod_name),
        "generation": "unknown",
        "priceUSD_hourly": hourly_usd,
        "priceUSD_monthly": monthly_usd,
        "originalPrice": {
            "hourly": round(hourly_eur, 8),
            "monthly": round(monthly_eur, 6),
            "currency": "EUR",
        },
        "regions": region_codes,
        "locationDetails": location_details,
        "commitments": _get_eco_commitments(plan.get("pricings") or [], hourly_usd),
        "source": "ovh_vps_catalog_api",
        "description": invoice_name or f"OVHcloud VPS {plan_code}",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "raw": {
            "planCode": plan_code,
            "invoiceName": invoice_name,
            "product": prod_name,
        },
    }
    if disk_size_gb is not None:
        record["diskSizeGB"] = disk_size_gb
    if disk_tech:
        record["diskType"] = disk_tech
    return record


def _parse_cloud_addon(
    addon: Dict[str, Any],
    monthly_addon: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Parse a single OVH cloud addon into a v3 CloudInstance record.
    Returns None if required fields are missing or price is invalid.
    """
    plan_code = (addon.get("planCode") or "").strip()
    if not plan_code or _should_skip(plan_code):
        return None

    blobs = addon.get("blobs") or {}
    tech = blobs.get("technical") or {}
    if not tech:
        return None

    # Canonical instance name (e.g. "b3-8", "a100-180")
    instance_name = (tech.get("name") or "").strip()
    if not instance_name:
        # Fall back: strip suffix from planCode
        instance_name = plan_code.split(".")[0]

    cpu = tech.get("cpu") or {}
    vcpu = cpu.get("cores")
    if not vcpu:
        return None
    vcpu = int(vcpu)

    memory = tech.get("memory") or {}
    memory_gib = memory.get("size")
    if not memory_gib:
        return None
    memory_gib = float(memory_gib)

    # Storage (optional)
    disk_type: Optional[str] = None
    disk_size_gb: Optional[float] = None
    storage = tech.get("storage") or {}
    disks = storage.get("disks") or []
    if disks:
        d0 = disks[0]
        raw_tech = (d0.get("technology") or "").strip().lower()
        if raw_tech:
            disk_type = raw_tech
        cap = d0.get("capacity")
        if cap is not None:
            disk_size_gb = float(cap)

    # GPU (optional)
    gpu_info: Optional[Dict[str, Any]] = None
    gpu_raw = tech.get("gpu") or {}
    if gpu_raw:
        gpu_count = gpu_raw.get("number")
        gpu_model = gpu_raw.get("model") or ""
        gpu_mem_gib = (gpu_raw.get("memory") or {}).get("size")
        if gpu_count and gpu_model:
            entry: Dict[str, Any] = {"count": int(gpu_count), "type": str(gpu_model)}
            if gpu_mem_gib is not None:
                entry["memoryGiB"] = int(gpu_mem_gib)
            gpu_info = entry

    # Pricing
    od_eur = _get_hourly_price_eur(addon.get("pricings") or [])
    if od_eur is None or od_eur <= 0:
        return None

    od_usd = round(convert_currency(od_eur, "EUR", "USD"), 6)

    # Monthly price: from dedicated monthly addon if available, else derive
    monthly_eur: Optional[float] = None
    if monthly_addon:
        monthly_eur = _get_monthly_price_eur(monthly_addon.get("pricings") or [])
    if monthly_eur is None:
        monthly_eur = od_eur * HOURS_PER_MONTH
    monthly_usd = round(convert_currency(monthly_eur, "EUR", "USD"), 4)

    os_type = _detect_os(plan_code, tech)
    family = _map_family(instance_name)
    generation = _extract_generation(instance_name)
    invoice_name = (addon.get("invoiceName") or "").strip()

    record: Dict[str, Any] = {
        "provider": "ovh",
        "type": "cloud-server",
        "instanceType": instance_name,
        "operatingSystem": os_type,
        "vCPU": vcpu,
        "memoryGiB": memory_gib,
        "architecture": "x86_64",
        "family": family,
        "generation": generation,
        "priceUSD_hourly": od_usd,
        "priceUSD_monthly": monthly_usd,
        "originalPrice": {
            "hourly": round(od_eur, 8),
            "monthly": round(monthly_eur, 6),
            "currency": "EUR",
        },
        "regions": _REGION_CODES,
        "locationDetails": _build_location_details(),
        "commitments": [],
        "source": "ovh_cloud_catalog_api",
        "description": invoice_name or f"OVHcloud {instance_name}",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "raw": {
            "planCode": plan_code,
            "invoiceName": invoice_name,
        },
    }

    if disk_type:
        record["diskType"] = disk_type
    if disk_size_gb is not None:
        record["diskSizeGB"] = disk_size_gb
    if gpu_info:
        record["gpu"] = gpu_info

    return record


def _parse_bm_plan(
    plan: Dict[str, Any],
    products_map: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Parse a bare-metal server plan. Returns None if required specs are missing.

    OVH migrated bare-metal server specs: CPU is still in the linked product,
    but RAM is now a configurable addon — parsed from the default memory addon name.
    """
    plan_code = (plan.get("planCode") or "").strip()
    if not plan_code:
        return None

    prod = products_map.get(plan.get("product") or "") or {}
    server = ((prod.get("blobs") or {}).get("technical") or {}).get("server") or {}

    cpu = server.get("cpu") or {}
    cores_per_socket = cpu.get("cores")
    num_sockets = cpu.get("number", 1)
    if not cores_per_socket:
        return None
    vcpu = int(cores_per_socket) * int(num_sockets)

    # RAM from default memory addon name (API no longer embeds memory in product)
    mem_fam = next((f for f in (plan.get("addonFamilies") or []) if f.get("name") == "memory"), None)
    memory_gib: Optional[float] = None
    if mem_fam and mem_fam.get("default"):
        memory_gib = _parse_ram_from_addon(mem_fam["default"])
    if memory_gib is None:
        return None

    monthly_eur = _get_eco_monthly_price_eur(plan.get("pricings") or [])
    if monthly_eur is None or monthly_eur <= 0:
        return None

    monthly_usd = round(convert_currency(monthly_eur, "EUR", "USD"), 4)
    hourly_eur = monthly_eur / HOURS_PER_MONTH
    hourly_usd = round(convert_currency(hourly_eur, "EUR", "USD"), 6)

    family = _map_family(plan_code)
    generation = _extract_generation(plan_code)
    cpu_model = cpu.get("model") or ""
    invoice_name = (plan.get("invoiceName") or "").strip()

    return {
        "provider": "ovh",
        "type": "dedicated-server",
        "instanceType": plan_code,
        "vCPU": vcpu,
        "memoryGiB": memory_gib,
        "architecture": "x86_64",
        "family": family,
        "generation": generation,
        "priceUSD_hourly": hourly_usd,
        "priceUSD_monthly": monthly_usd,
        "originalPrice": {
            "hourly": round(hourly_eur, 8),
            "monthly": round(monthly_eur, 6),
            "currency": "EUR",
        },
        "regions": _REGION_CODES,
        "locationDetails": _build_location_details(),
        "commitments": _get_eco_commitments(plan.get("pricings") or [], hourly_usd),
        "source": "ovh_baremetal_catalog_api",
        "description": invoice_name or f"OVHcloud {plan_code} ({cpu_model})",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "raw": {
            "planCode": plan_code,
            "invoiceName": invoice_name,
            "cpuModel": cpu_model,
            "numSockets": int(num_sockets),
        },
    }


# ---------------------------------------------------------------------------
# Main fetcher class
# ---------------------------------------------------------------------------

class OVHFetcher:
    def __init__(self) -> None:
        self.session = make_session()

    def _fetch_cloud_instances(self) -> List[Dict[str, Any]]:
        """
        Fetch compute instances from the OVH public cloud catalog.

        Instances live in addons[], not plans[]. We deduplicate by tech.name,
        preferring the canonical '{name}.consumption' planCode for each type.
        """
        logger.info(f"Fetching OVH cloud catalog from {OVH_CLOUD_URL}...")
        data = get_json(self.session, OVH_CLOUD_URL)
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected response type: {type(data)}")

        addons = data.get("addons") or []
        logger.info(f"  Cloud catalog: {len(addons)} total addons")

        # Build lookup: (instance_name, os) -> {planCode -> addon}
        # Only include addons with tech.cpu + tech.memory + hourly pricing.
        # Windows variants share hardware with Linux; they are keyed separately.
        by_name: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
        monthly_by_name: Dict[Tuple[str, str], Dict[str, Any]] = {}

        for addon in addons:
            plan_code = (addon.get("planCode") or "").strip()
            if not plan_code or _should_skip(plan_code):
                continue

            blobs = addon.get("blobs") or {}
            tech = blobs.get("technical") or {}
            if not tech.get("cpu") or not tech.get("memory"):
                continue

            instance_name = (tech.get("name") or "").strip()
            if not instance_name:
                continue
            if _should_skip(instance_name):
                continue

            os_type = _detect_os(plan_code, tech)
            key: Tuple[str, str] = (instance_name, os_type)
            pricings = addon.get("pricings") or []

            # Collect hourly-priced addons
            if any(p.get("intervalUnit") == "hour" and p.get("price", 0) > 0 for p in pricings):
                by_name.setdefault(key, {})[plan_code] = addon

            # Collect monthly-priced addons (for better monthly price accuracy)
            if any(p.get("intervalUnit") == "month" and p.get("price", 0) > 0 for p in pricings):
                # Prefer the .monthly.postpaid canonical form
                existing = monthly_by_name.get(key)
                if existing is None or ".monthly.postpaid" in plan_code:
                    monthly_by_name[key] = addon

        logger.info(f"  Cloud catalog: {len(by_name)} unique instance types found")

        # For each unique (instance_name, os), pick the most canonical hourly addon.
        # Priority: exact '{name}.consumption' > any '.consumption' > shortest planCode
        instances: List[Dict[str, Any]] = []
        skipped = 0
        for (instance_name, _os_type), code_map in by_name.items():
            canonical = f"{instance_name}.consumption"
            if canonical in code_map:
                chosen_addon = code_map[canonical]
            else:
                # Pick the shortest planCode that contains '.consumption'
                consumption_codes = [c for c in code_map if ".consumption" in c]
                if consumption_codes:
                    chosen_code = min(consumption_codes, key=len)
                else:
                    chosen_code = min(code_map, key=len)
                chosen_addon = code_map[chosen_code]

            monthly_addon = monthly_by_name.get((instance_name, _os_type))
            record = _parse_cloud_addon(chosen_addon, monthly_addon)
            if record is None:
                skipped += 1
            else:
                instances.append(record)

        logger.info(f"  Cloud catalog: built {len(instances)} instances, skipped {skipped}")
        return instances

    def _fetch_bm_instances(self) -> List[Dict[str, Any]]:
        """
        Fetch bare-metal dedicated server plans.

        Technical specs are stored in products[] linked via plan.product.
        Most plans lack memory data so yield is low; missing-memory plans
        are silently skipped.
        """
        logger.info(f"Fetching OVH bare-metal catalog from {OVH_BM_URL}...")
        data = get_json(self.session, OVH_BM_URL)
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected response type: {type(data)}")

        plans = data.get("plans") or []
        products = data.get("products") or []
        products_map = {p.get("name"): p for p in products}
        logger.info(f"  Bare-metal catalog: {len(plans)} plans, {len(products)} products")

        instances: List[Dict[str, Any]] = []
        skipped = 0
        for plan in plans:
            record = _parse_bm_plan(plan, products_map)
            if record is None:
                skipped += 1
            else:
                instances.append(record)

        logger.info(f"  Bare-metal catalog: built {len(instances)} instances, skipped {skipped}")
        return instances

    def _fetch_eco_instances(self) -> List[Dict[str, Any]]:
        """
        Fetch eco-server plans (Kimsufi / RISE / ADVANCE / SYS lines).

        Technical specs are split: CPU lives in the linked product, RAM in the
        plan's default memory addon name (parsed via regex). Monthly + 1yr/2yr
        upfront commitment pricing are both captured.
        """
        logger.info(f"Fetching OVH eco catalog from {OVH_ECO_URL}...")
        data = get_json(self.session, OVH_ECO_URL)
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected response type: {type(data)}")

        plans = data.get("plans") or []
        products = data.get("products") or []
        products_map = {p.get("name"): p for p in products}
        logger.info(f"  Eco catalog: {len(plans)} plans, {len(products)} products")

        instances: List[Dict[str, Any]] = []
        skipped = 0
        for plan in plans:
            record = _parse_eco_plan(plan, products_map)
            if record is None:
                skipped += 1
            else:
                instances.append(record)

        logger.info(f"  Eco catalog: built {len(instances)} instances, skipped {skipped}")
        return instances

    def _fetch_vps_instances(self) -> List[Dict[str, Any]]:
        """
        Fetch VPS plans from the OVH VPS catalog.

        The catalog has 190+ plans, many of which are promotional variants of
        the same product. We pick one canonical plan per product (preferring
        planCode == productName, then shortest non-promotional code).
        Products prefixed with 'vps-2020v2-' are promotional bundles of legacy
        plan codes with newer hardware and are skipped to avoid duplication.
        """
        logger.info(f"Fetching OVH VPS catalog from {OVH_VPS_URL}...")
        data = get_json(self.session, OVH_VPS_URL)
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected response type: {type(data)}")

        plans = data.get("plans") or []
        products = data.get("products") or []
        products_map = {p.get("name"): p for p in products}
        logger.info(f"  VPS catalog: {len(plans)} plans, {len(products)} products")

        # One canonical plan per product — skip promotional variants
        _PROMO_MARKERS = ("-10percent", "degressivity", "vps-2020v2")
        canonical: Dict[str, Dict[str, Any]] = {}
        for plan in plans:
            prod_name = plan.get("product") or ""
            plan_code = (plan.get("planCode") or "").strip()
            if not plan_code or not prod_name:
                continue
            if any(m in plan_code for m in _PROMO_MARKERS) or any(m in prod_name for m in _PROMO_MARKERS):
                continue
            existing = canonical.get(prod_name)
            if existing is None:
                canonical[prod_name] = plan
            elif plan_code == prod_name:
                # Exact match wins immediately
                canonical[prod_name] = plan
            elif plan_code != prod_name and len(plan_code) < len(existing.get("planCode", "")):
                canonical[prod_name] = plan

        instances: List[Dict[str, Any]] = []
        skipped = 0
        for plan in canonical.values():
            record = _parse_vps_plan(plan, products_map)
            if record is None:
                skipped += 1
            else:
                instances.append(record)

        logger.info(f"  VPS catalog: built {len(instances)} instances, skipped {skipped}")
        return instances

    def fetch_all(self) -> List[Dict[str, Any]]:
        """Fetch cloud, bare-metal, eco, and VPS catalogs and combine."""
        instances: List[Dict[str, Any]] = []

        try:
            instances.extend(self._fetch_cloud_instances())
        except Exception as exc:
            logger.error(f"Failed to fetch OVH cloud catalog: {exc}")

        try:
            instances.extend(self._fetch_bm_instances())
        except Exception as exc:
            logger.error(f"Failed to fetch OVH bare-metal catalog: {exc}")

        try:
            instances.extend(self._fetch_eco_instances())
        except Exception as exc:
            logger.error(f"Failed to fetch OVH eco catalog: {exc}")

        try:
            instances.extend(self._fetch_vps_instances())
        except Exception as exc:
            logger.error(f"Failed to fetch OVH VPS catalog: {exc}")

        logger.info(f"Total OVH instances fetched: {len(instances)}")
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
    parser = argparse.ArgumentParser(description="OVHcloud pricing fetcher (CloudPriceFinder v3)")
    parser.add_argument(
        "--output",
        default="data/providers/ovh.raw.json",
        help="Output JSON file path (default: data/providers/ovh.raw.json)",
    )
    args = parser.parse_args(argv)

    logger.info("=== OVH Fetcher (CloudPriceFinder v3) ===")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    fetcher = OVHFetcher()
    instances = fetcher.fetch_all()

    if not instances:
        logger.error("No instances collected — aborting")
        return 1

    valid = _validate_output(instances)
    logger.info(f"Valid instances: {len(valid)}/{len(instances)}")

    if len(valid) < 30:
        logger.error(f"Too few valid instances ({len(valid)}), expected >=30")
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(valid, f, indent=2, ensure_ascii=False)
    logger.info(f"Wrote {len(valid)} instances to {out_path}")

    # Summary
    by_family: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    for inst in valid:
        fam = inst.get("family", "unknown")
        by_family[fam] = by_family.get(fam, 0) + 1
        t = inst.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    logger.info("Summary by family:")
    for k, v in sorted(by_family.items()):
        logger.info(f"  {k}: {v}")
    logger.info("Summary by type:")
    for k, v in sorted(by_type.items()):
        logger.info(f"  {k}: {v}")

    with_commitments = sum(1 for i in valid if i.get("commitments"))
    logger.info(
        f"Instances with commitments: {with_commitments}/{len(valid)} "
        f"(OVH does not publish per-instance commitment pricing)"
    )

    return 0


# ---------------------------------------------------------------------------
# Orchestrator compatibility
# ---------------------------------------------------------------------------

def fetch_ovh_data() -> List[Dict[str, Any]]:
    """Entry point for the orchestrator."""
    fetcher = OVHFetcher()
    instances = fetcher.fetch_all()
    return _validate_output(instances)


if __name__ == "__main__":
    sys.exit(main())
