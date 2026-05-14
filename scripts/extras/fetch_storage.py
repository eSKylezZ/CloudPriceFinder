#!/usr/bin/env python3
"""
Block storage pricing fetcher for CloudPriceFinder.

Fetches Persistent Disk (GCP), Managed Disks (Azure), and Block Volume (OCI)
pricing from the same APIs used by the compute fetchers.

AWS EBS pricing is produced by scripts/fetch_aws.py as a side output
(aws.storage.raw.json) — it is no longer handled here.

Output files written by the extras orchestrator (or the CLI below):
  data/providers/gcp.storage.raw.json
  data/providers/azure.storage.raw.json
  data/providers/oci.storage.raw.json

Schema per record:
  {
    "provider":          "gcp",
    "type":              "cloud-volume",
    "instanceType":      "pd-ssd",      # volume/disk type slug
    "family":            "persistent-disk",
    "priceUSD_monthly":  0.17,          # per-GiB-month (or per-disk-month for fixed Azure tiers)
    "priceUSD_hourly":   0.000233,      # priceUSD_monthly / 730
    "storageGiB":        null,          # null = per-GiB; integer = fixed-size disk tier (Azure)
    "maxIops":           null,          # null if unknown
    "maxThroughputMBps": null,          # null if unknown
    "regions":           ["us-central1"],
    "source":            "gcp_billing_catalog_api",
    "lastUpdated":       "2026-...",
    "pricingModel":      "on-demand"
  }

Orchestrator entry point: fetch_data(provider)

CLI usage:
  python scripts/extras/fetch_storage.py
  python scripts/extras/fetch_storage.py --provider gcp azure
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Repo root import setup (works both as script and module)
# ---------------------------------------------------------------------------
_EXTRAS_DIR = Path(__file__).resolve().parent      # scripts/extras/
_SCRIPTS_DIR = _EXTRAS_DIR.parent                  # scripts/
_REPO_ROOT = _SCRIPTS_DIR.parent                   # repo root

for _p in [str(_REPO_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.utils.http_client import get_json, make_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_storage")

# Per-GiB-month → per-GiB-hour using AWS convention (730 h/month).
# HOURS_PER_MONTH in http_client is 730.44; the task spec requires exactly 730.
_MONTH_HOURS = 730.0


# ===========================================================================
# GCP Persistent Disk
# ===========================================================================

_GCP_BILLING_BASE = "https://cloudbilling.googleapis.com/v1"
_GCP_COMPUTE_SERVICE_NAME = "Compute Engine"
_GCP_COMPUTE_SERVICE_ID_FALLBACK = "6F81-5844-456A"
_GCP_MAX_RETRIES = 4
_GCP_RETRY_SLEEP = 2.0

# Billing API resourceGroup → (instanceType slug, family slug).
# Includes both documented groups and common aliases seen in the live API.
_GCP_PD_GROUPS: Dict[str, Tuple[str, str]] = {
    "PDStandard": ("pd-standard",  "persistent-disk"),
    "PDSD":       ("pd-balanced",  "persistent-disk"),  # "SSD-standard" → balanced
    "PDBalanced": ("pd-balanced",  "persistent-disk"),
    "PDSSD":      ("pd-ssd",       "persistent-disk"),
    "SSD":        ("pd-ssd",       "persistent-disk"),
    "PDExtreme":  ("pd-extreme",   "persistent-disk"),
    "LocalSSD":   ("local-ssd",    "local-ssd"),
}
_GCP_CANONICAL_REGIONS = [
    "us-central1", "us-east1", "us-east4", "europe-west1", "europe-west4", "global",
]


def _gcp_get_adc_credentials():
    try:
        import google.auth
        import google.auth.transport.requests as g_transport
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"]
        )
        creds.refresh(g_transport.Request())
        return creds
    except Exception as exc:
        logger.debug("GCP ADC unavailable: %s", exc)
        return None


def _gcp_make_session(credentials=None) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "CloudPriceFinder/3.0 (cloudpricefinder.com)"})
    if credentials is not None:
        try:
            import google.auth.transport.requests as g_transport
            credentials.refresh(g_transport.Request())
            s.headers["Authorization"] = f"Bearer {credentials.token}"
        except Exception as exc:
            logger.warning("Could not attach GCP bearer token: %s", exc)
    return s


def _gcp_get_json(
    session: requests.Session, url: str, params: Optional[Dict] = None
) -> Dict[str, Any]:
    for attempt in range(_GCP_MAX_RETRIES):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == _GCP_MAX_RETRIES - 1:
                raise
            wait = _GCP_RETRY_SLEEP * (2 ** attempt)
            logger.warning(
                "GCP GET %s failed (attempt %d): %s — retry in %.1fs",
                url, attempt + 1, type(exc).__name__, wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"All retries exhausted for {url}")


def _gcp_paginate_skus(
    session: requests.Session, service_id: str, api_key: Optional[str] = None
) -> Generator[Dict[str, Any], None, None]:
    url = f"{_GCP_BILLING_BASE}/services/{service_id}/skus"
    page_token: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"pageSize": 5000}
        if api_key:
            params["key"] = api_key
        if page_token:
            params["pageToken"] = page_token
        data = _gcp_get_json(session, url, params=params)
        for sku in data.get("skus", []):
            yield sku
        page_token = data.get("nextPageToken")
        if not page_token:
            break


def _gcp_extract_unit_price(pricing_info: List[Dict[str, Any]]) -> Optional[float]:
    if not pricing_info:
        return None
    tiers = pricing_info[0].get("pricingExpression", {}).get("tieredRates", [])
    for tier in tiers:
        if tier.get("startUsageAmount", 0) == 0:
            up = tier.get("unitPrice", {})
            return int(up.get("units", 0)) + int(up.get("nanos", 0)) / 1_000_000_000.0
    if tiers:
        up = tiers[0].get("unitPrice", {})
        return int(up.get("units", 0)) + int(up.get("nanos", 0)) / 1_000_000_000.0
    return None


def fetch_gcp_storage(api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch GCP Persistent Disk pricing from the Cloud Billing Catalog API."""
    credentials = None
    if not api_key:
        logger.info("No GCP_API_KEY — attempting ADC…")
        credentials = _gcp_get_adc_credentials()
        if credentials is None:
            raise RuntimeError(
                "GCP auth failed: no GCP_API_KEY and ADC unavailable. "
                "Set GCP_API_KEY or run: gcloud auth application-default login"
            )
    session = _gcp_make_session(credentials=credentials)
    now = datetime.now(timezone.utc).isoformat()

    # Resolve Compute Engine service ID dynamically.
    logger.info("Resolving GCP Compute Engine service ID…")
    params: Dict[str, Any] = {"pageSize": 500}
    if api_key:
        params["key"] = api_key
    services_data = _gcp_get_json(session, f"{_GCP_BILLING_BASE}/services", params=params)
    service_id = _GCP_COMPUTE_SERVICE_ID_FALLBACK
    for svc in services_data.get("services", []):
        if _GCP_COMPUTE_SERVICE_NAME in svc.get("displayName", ""):
            service_id = svc["name"].split("/")[-1]
            break
    logger.info("Using GCP service ID: %s", service_id)

    # Fetch all Compute Engine SKUs.
    logger.info("Fetching GCP SKUs…")
    all_skus = list(_gcp_paginate_skus(session, service_id, api_key=api_key))
    logger.info("Total GCP SKUs: %d", len(all_skus))

    # Filter to on-demand Storage SKUs with known PD resource groups.
    storage_skus = [
        sku for sku in all_skus
        if sku.get("category", {}).get("resourceFamily") == "Storage"
        and sku.get("category", {}).get("usageType") == "OnDemand"
        and sku.get("category", {}).get("resourceGroup") in _GCP_PD_GROUPS
    ]
    logger.info("GCP on-demand Storage SKUs: %d", len(storage_skus))

    # resource_group → {region: min_price}
    group_region_prices: Dict[str, Dict[str, float]] = {}
    for sku in storage_skus:
        rg = sku.get("category", {}).get("resourceGroup", "")
        price = _gcp_extract_unit_price(sku.get("pricingInfo", []))
        if price is None or price <= 0:
            continue
        for region in sku.get("serviceRegions", []) or ["global"]:
            rp = group_region_prices.setdefault(rg, {})
            if region not in rp or price < rp[region]:
                rp[region] = price

    # Build records, one per resource group.
    # Deduplicate: if two resource groups map to the same instanceType, keep the lower price.
    by_instance_type: Dict[str, Dict[str, Any]] = {}
    for rg, region_prices in sorted(group_region_prices.items()):
        instance_type, family = _GCP_PD_GROUPS.get(rg, (rg.lower(), "persistent-disk"))
        canonical_price = next(
            (region_prices[r] for r in _GCP_CANONICAL_REGIONS if r in region_prices),
            next(iter(region_prices.values())),
        )
        non_global = {r: p for r, p in region_prices.items() if r != "global"}

        existing = by_instance_type.get(instance_type)
        if existing is None or canonical_price < existing["priceUSD_monthly"]:
            by_instance_type[instance_type] = {
                "provider": "gcp",
                "type": "cloud-volume",
                "instanceType": instance_type,
                "family": family,
                "priceUSD_monthly": round(canonical_price, 8),
                "priceUSD_hourly": round(canonical_price / _MONTH_HOURS, 8),
                "storageGiB": None,
                "maxIops": None,
                "maxThroughputMBps": None,
                "regions": sorted(non_global.keys()),
                "regionPricing": non_global,
                "source": "gcp_billing_catalog_api",
                "lastUpdated": now,
                "pricingModel": "on-demand",
            }

    results = sorted(by_instance_type.values(), key=lambda r: r["instanceType"])
    logger.info("GCP Persistent Disk: %d volume types", len(results))
    return results


# ===========================================================================
# Azure Managed Disks
# ===========================================================================

_AZURE_RETAIL_URL = "https://prices.azure.com/api/retail/prices"
_AZURE_API_VERSION = "2023-01-01-preview"

# Managed disk product names to query (serviceName is now always 'Storage').
_AZURE_DISK_SERVICES = [
    "Premium SSD Managed Disks",
    "Standard SSD Managed Disks",
    "Ultra Disks",
    "Azure Premium SSD v2",
]

# Fixed-tier disk size mappings (GiB per tier).
_AZURE_P_SIZES: Dict[str, int] = {
    "P1": 4, "P2": 8, "P3": 16, "P4": 32, "P6": 64, "P10": 128,
    "P15": 256, "P20": 512, "P30": 1024, "P40": 2048, "P50": 4096,
    "P60": 8192, "P70": 16384, "P80": 32768,
}
_AZURE_E_SIZES: Dict[str, int] = {
    "E1": 4, "E2": 8, "E3": 16, "E4": 32, "E6": 64, "E10": 128,
    "E15": 256, "E20": 512, "E30": 1024, "E40": 2048, "E50": 4096,
    "E60": 8192, "E70": 16384, "E80": 32768,
}
_AZURE_CANONICAL_REGIONS = ["eastus", "eastus2", "westus2", "westeurope"]

# meter name substrings that indicate operational/IOPS meters (skip these)
_AZURE_SKIP_KEYWORDS = ("operations", "iops", "throughput", "snapshot", "transaction", "reservation")


def _azure_parse_sku(sku_name: str) -> Tuple[str, Optional[int], str]:
    """
    Return (instance_type_slug, storage_gib_or_None, family_slug).

    Per-GiB tiers (Ultra, Premium v2): storageGiB=None.
    Fixed-size tiers (P4 LRS, E10 ZRS): storageGiB = tier size.
    """
    low = sku_name.lower().strip()
    slug = re.sub(r"\s+", "-", low)

    if "ultra" in low:
        return slug, None, "ultra-disk"
    if "premium ssd v2" in low or "premium v2" in low or "azure premium ssd v2" in low:
        return slug, None, "premium-ssd-v2"

    # Premium SSD P-tier: "P4 LRS" / "P4 ZRS"
    m = re.match(r"(p\d+)\s+(lrs|zrs)", low)
    if m:
        tier_key = m.group(1).upper()
        gib = _AZURE_P_SIZES.get(tier_key)
        return f"{tier_key.lower()}-{m.group(2)}", gib, "premium-ssd"

    # Standard SSD E-tier: "E4 LRS" / "E4 ZRS"
    m = re.match(r"(e\d+)\s+(lrs|zrs)", low)
    if m:
        tier_key = m.group(1).upper()
        gib = _AZURE_E_SIZES.get(tier_key)
        return f"{tier_key.lower()}-{m.group(2)}", gib, "standard-ssd"

    return slug, None, "storage"


def _azure_page_items(service_name: str) -> List[Dict[str, Any]]:
    params = {
        "$filter": (
            f"serviceName eq 'Storage' "
            f"and productName eq '{service_name}' "
            f"and priceType eq 'Consumption'"
        ),
        "api-version": _AZURE_API_VERSION,
    }
    items: List[Dict[str, Any]] = []
    url: Optional[str] = _AZURE_RETAIL_URL
    page = 0

    while url:
        page += 1
        try:
            resp = requests.get(url, params=params if page == 1 else None, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Azure request error (page %d, %s): %s", page, service_name, exc)
            break
        body = resp.json()
        items.extend(body.get("Items", []))
        url = body.get("NextPageLink")
        if page % 5 == 0:
            logger.info("  Azure %s: %d records so far (page %d)…", service_name, len(items), page)

    logger.info("  Azure %s: %d total records", service_name, len(items))
    return items


def fetch_azure_storage() -> List[Dict[str, Any]]:
    """Fetch Azure Managed Disk pricing from the Retail Prices API."""
    now = datetime.now(timezone.utc).isoformat()

    # (sku_name, region) → lowest price seen
    sku_region_price: Dict[Tuple[str, str], float] = {}
    sku_meta: Dict[str, Tuple[str, Optional[int], str]] = {}  # sku_name → parsed

    for service_name in _AZURE_DISK_SERVICES:
        logger.info("Fetching Azure %s…", service_name)
        for item in _azure_page_items(service_name):
            sku_name = (item.get("skuName") or "").strip()
            region = (item.get("armRegionName") or "").strip()
            unit_measure = (item.get("unitOfMeasure") or "").strip()
            meter_name = (item.get("meterName") or "").lower()
            price = float(item.get("unitPrice") or 0)

            if not sku_name or price <= 0 or not region:
                continue

            # Skip non-capacity meters (IOPS, throughput, snapshots, etc.)
            if any(kw in meter_name for kw in _AZURE_SKIP_KEYWORDS):
                continue

            # Only accept capacity-based units (not IOPS/throughput/reservation).
            if unit_measure not in ("1/Month", "1 GiB/Month", "1 TiB/Month", "1 GiB/Hour"):
                continue

            # Normalise TiB → GiB (price per TiB → price per GiB)
            if unit_measure == "1 TiB/Month":
                price = price / 1024.0
            # Normalise GiB/Hour → GiB/Month
            elif unit_measure == "1 GiB/Hour":
                price = price * _MONTH_HOURS

            key = (sku_name, region)
            existing = sku_region_price.get(key)
            if existing is None or price < existing:
                sku_region_price[key] = price

            if sku_name not in sku_meta:
                sku_meta[sku_name] = _azure_parse_sku(sku_name)

    # Collapse per-(sku, region) into per-sku with region map.
    sku_regions: Dict[str, Dict[str, float]] = {}
    for (sku_name, region), price in sku_region_price.items():
        sku_regions.setdefault(sku_name, {})[region] = price

    results: List[Dict[str, Any]] = []
    for sku_name, region_prices in sorted(sku_regions.items()):
        instance_type, storage_gib, family = sku_meta.get(
            sku_name, (re.sub(r"\s+", "-", sku_name.lower()), None, "storage")
        )
        canonical_price = next(
            (region_prices[r] for r in _AZURE_CANONICAL_REGIONS if r in region_prices),
            next(iter(region_prices.values())),
        )
        results.append({
            "provider": "azure",
            "type": "cloud-volume",
            "instanceType": instance_type,
            "family": family,
            "priceUSD_monthly": round(canonical_price, 8),
            "priceUSD_hourly": round(canonical_price / _MONTH_HOURS, 8),
            # storageGiB: None for per-GiB tiers; disk size for fixed P/E tiers.
            "storageGiB": storage_gib,
            "maxIops": None,
            "maxThroughputMBps": None,
            "regions": sorted(region_prices.keys()),
            "regionPricing": region_prices,
            "source": "azure_retail_prices_api",
            "lastUpdated": now,
            "pricingModel": "on-demand",
        })

    results.sort(key=lambda r: (r["family"], r["instanceType"]))
    logger.info("Azure Managed Disks: %d disk SKUs", len(results))
    return results


# ===========================================================================
# OCI Block Volumes
# ===========================================================================

_OCI_PRICING_URL = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/"

# All standard commercial OCI region codes (same list as fetch_oci.py).
_OCI_REGION_CODES = [
    "us-ashburn-1", "us-phoenix-1", "us-sanjose-1", "us-chicago-1",
    "ca-toronto-1", "ca-montreal-1", "sa-saopaulo-1", "sa-vinhedo-1",
    "mx-queretaro-1", "mx-monterrey-1", "eu-frankfurt-1", "eu-zurich-1",
    "eu-amsterdam-1", "eu-london-1", "eu-stockholm-1", "eu-milan-1",
    "eu-paris-1", "eu-madrid-1", "ap-mumbai-1", "ap-hyderabad-1",
    "ap-seoul-1", "ap-chuncheon-1", "ap-tokyo-1", "ap-osaka-1",
    "ap-sydney-1", "ap-melbourne-1", "ap-singapore-1", "ap-singapore-2",
    "me-jeddah-1", "me-dubai-1", "me-abudhabi-1", "af-johannesburg-1",
    "il-jerusalem-1",
]


def _oci_usd_price(item: Dict[str, Any]) -> Optional[float]:
    for loc in item.get("currencyCodeLocalizations", []):
        if loc.get("currencyCode") == "USD":
            for p in loc.get("prices", []):
                if p.get("model") == "PAY_AS_YOU_GO":
                    return float(p["value"])
    return None


def _oci_volume_type(display_name: str) -> Tuple[str, str]:
    """Return (instanceType slug, family) from the OCI displayName."""
    name_lower = display_name.lower()
    if "ultra high performance" in name_lower or "uhp" in name_lower:
        return "block-volume-ultra-hp", "block-volume"
    if "high performance" in name_lower:
        return "block-volume-high-perf", "block-volume"
    if "balanced" in name_lower:
        return "block-volume-balanced", "block-volume"
    return "block-volume-standard", "block-volume"


def fetch_oci_storage() -> List[Dict[str, Any]]:
    """Fetch OCI Block Volume pricing from the cetools API."""
    session = make_session()
    now = datetime.now(timezone.utc).isoformat()

    logger.info("Fetching OCI cetools data…")
    raw = get_json(session, _OCI_PRICING_URL)
    items = raw.get("items", [])
    logger.info("OCI cetools: %d total items", len(items))

    # Filter to Block Volume storage-capacity items.
    # The cetools API uses serviceCategory; the exact value varies by API version,
    # so we match on both the category and the display name.
    storage_items = [
        i for i in items
        if (
            "block volume" in i.get("serviceCategory", "").lower()
            or "block volume" in i.get("displayName", "").lower()
        )
    ]
    logger.info("OCI Block Volume items: %d", len(storage_items))

    # Accumulate per volume type; keep lowest price if same type appears twice.
    seen: Dict[str, Dict[str, Any]] = {}

    for item in storage_items:
        display_name = item.get("displayName", "")
        metric = (item.get("metricName") or "").lower()

        # Only storage capacity metrics, not IOPS / performance unit pricing.
        if not ("gigabyte" in metric or "storage" in metric):
            continue
        if any(kw in metric for kw in ("iops", "performance unit", "vpu")):
            continue

        price = _oci_usd_price(item)
        if price is None or price <= 0:
            continue

        instance_type, family = _oci_volume_type(display_name)

        existing = seen.get(instance_type)
        if existing is None or price < existing["priceUSD_monthly"]:
            seen[instance_type] = {
                "provider": "oci",
                "type": "cloud-volume",
                "instanceType": instance_type,
                "family": family,
                "priceUSD_monthly": round(price, 8),
                "priceUSD_hourly": round(price / _MONTH_HOURS, 8),
                "storageGiB": None,
                "maxIops": None,
                "maxThroughputMBps": None,
                "regions": list(_OCI_REGION_CODES),
                "source": "oci_cetools_pricing_api",
                "lastUpdated": now,
                "pricingModel": "on-demand",
            }

    results = sorted(seen.values(), key=lambda r: r["instanceType"])
    logger.info("OCI Block Volumes: %d volume types", len(results))
    return results


# ===========================================================================
# Orchestrator entry point + CLI
# ===========================================================================

def fetch_data(provider: str) -> List[Dict[str, Any]]:
    """
    Called by scripts/extras/orchestrator.py as fetch_data(provider).
    GCP auth: reads GCP_API_KEY from environment (or .env at repo root).
    """
    if provider == "gcp":
        api_key = _load_gcp_api_key()
        return fetch_gcp_storage(api_key=api_key)
    if provider == "azure":
        return fetch_azure_storage()
    if provider == "oci":
        return fetch_oci_storage()
    raise ValueError(f"Unknown provider: {provider!r}")


def _load_gcp_api_key() -> Optional[str]:
    key = os.environ.get("GCP_API_KEY", "").strip() or None
    if key:
        return key
    env_path = _REPO_ROOT / ".env"
    if env_path.exists():
        with open(env_path) as fh:
            for line in fh:
                if line.strip().startswith("GCP_API_KEY="):
                    return line.strip().split("=", 1)[1].strip('"').strip("'") or None
    return None


_PROVIDERS = ("gcp", "azure", "oci")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch block storage pricing (GCP Persistent Disk, Azure Managed Disks, OCI Block Volumes)."
    )
    parser.add_argument(
        "--provider",
        nargs="+",
        choices=list(_PROVIDERS),
        default=list(_PROVIDERS),
        metavar="PROVIDER",
        help=f"Provider(s) to fetch. Default: all ({', '.join(_PROVIDERS)}).",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default=None,
        help="Output directory (default: data/providers/ relative to repo root).",
    )
    args = parser.parse_args(argv)

    output_dir = (
        Path(args.output_dir) if args.output_dir
        else _REPO_ROOT / "data" / "providers"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    gcp_api_key = _load_gcp_api_key()

    total = 0
    for provider in args.provider:
        logger.info("=== Fetching %s storage pricing ===", provider.upper())
        try:
            if provider == "gcp":
                records = fetch_gcp_storage(api_key=gcp_api_key)
            elif provider == "azure":
                records = fetch_azure_storage()
            elif provider == "oci":
                records = fetch_oci_storage()
            else:
                continue
        except Exception as exc:
            logger.error("Failed to fetch %s storage: %s", provider, exc)
            continue

        out_path = output_dir / f"{provider}.storage.raw.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)

        size_kb = out_path.stat().st_size / 1024
        logger.info("Wrote %d records to %s (%.1f KB)", len(records), out_path, size_kb)
        total += len(records)

    logger.info("Done. Total records: %d", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
