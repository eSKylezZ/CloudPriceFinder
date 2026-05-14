#!/usr/bin/env python3
"""
Object storage pricing fetcher for CloudPriceFinder extras.

Fetches AWS S3, GCP Cloud Storage, and Azure Blob Storage pricing and
normalizes each to a consistent per-GiB-month + per-request schema.

Outputs (all gitignored):
  data/providers/aws.object-storage.raw.json
  data/providers/gcp.object-storage.raw.json
  data/providers/azure.object-storage.raw.json

Usage:
  python scripts/extras/fetch_object_storage.py
  python scripts/extras/fetch_object_storage.py --provider aws
  python scripts/extras/fetch_object_storage.py --provider gcp
  python scripts/extras/fetch_object_storage.py --provider azure

GCP auth (in priority order):
  1. GCP_API_KEY environment variable
  2. Application Default Credentials (gcloud auth application-default login)
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import re

import ijson
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.http_client import HOURS_PER_MONTH, make_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_object_storage")

_NOW = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_BACKOFF = 5.0  # seconds


def _get_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict] = None,
    timeout: int = 60,
) -> Any:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == _MAX_RETRIES:
                raise
            wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                attempt, _MAX_RETRIES, url, type(exc).__name__, wait,
            )
            time.sleep(wait)
    raise RuntimeError("Unreachable")


def _get_bytes(
    session: requests.Session,
    url: str,
    timeout: int = 120,
) -> bytes:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            if attempt == _MAX_RETRIES:
                raise
            wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                attempt, _MAX_RETRIES, url, type(exc).__name__, wait,
            )
            time.sleep(wait)
    raise RuntimeError("Unreachable")


# ===========================================================================
# AWS S3
# ===========================================================================

_AWS_PRICING_BASE = "https://pricing.us-east-1.amazonaws.com"
_AWS_EC2_REGION_INDEX = (
    f"{_AWS_PRICING_BASE}/offers/v1.0/aws/AmazonEC2/current/region_index.json"
)
_AWS_S3_PRICING_URL = (
    f"{_AWS_PRICING_BASE}/offers/v1.0/aws/AmazonS3/current/{{region}}/index.json"
)
_AWS_EXCLUDED_PREFIXES = ("cn-", "us-gov-")
# Local Zones (ap-northeast-1-tpe-1) and Wavelength Zones (us-east-1-wl1-…)
# have more than 3 dash-separated parts — S3 has no pricing pages for them.
_AWS_STANDARD_REGION_RE = re.compile(r"^[a-z]+-[a-z]+-\d+$")

# AWS Pricing API storageClass attribute value → (slug, label)
# Note: AWS renamed several storageClass values in the pricing API ~2024.
# Both old and new names are kept so regional endpoints that lag behind still work.
_AWS_STORAGE_CLASS_MAP: Dict[str, Tuple[str, str]] = {
    # Current API names
    "General Purpose":              ("standard",              "Standard"),
    "Infrequent Access":            ("standard-ia",           "Standard-IA"),
    "Non-Critical Data":            ("one-zone-ia",           "One Zone-IA"),
    "Archive":                      ("glacier",               "Glacier Flexible Retrieval"),
    "Archive Instant Retrieval":    ("glacier-ir",            "Glacier Instant Retrieval"),
    "Intelligent-Tiering":          ("intelligent-tiering",   "Intelligent-Tiering"),
    # Legacy names (some regional endpoints still use these)
    "One Zone - Infrequent Access": ("one-zone-ia",           "One Zone-IA"),
    "Amazon Glacier":               ("glacier",               "Glacier Flexible Retrieval"),
    "Amazon Glacier Deep Archive":  ("glacier-deep-archive",  "Glacier Deep Archive"),
    "Amazon Intelligent Tiering":   ("intelligent-tiering",   "Intelligent-Tiering"),
    "Reduced Redundancy":           ("rrs",                   "Reduced Redundancy"),
}


def _aws_discover_regions(session: requests.Session) -> List[str]:
    data = _get_json(session, _AWS_EC2_REGION_INDEX)
    return sorted(
        code for code in data.get("regions", {})
        if _AWS_STANDARD_REGION_RE.match(code)
        and not any(code.startswith(p) for p in _AWS_EXCLUDED_PREFIXES)
    )


def _aws_extract_first_tier_price(price_dims: Dict[str, Any]) -> Optional[float]:
    """Return the USD price from the first pricing tier (beginRange == '0')."""
    if not price_dims:
        return None
    # Single-dimension products (flat rate, no tiers) — use that dimension.
    if len(price_dims) == 1:
        dim = next(iter(price_dims.values()))
        try:
            p = float(dim.get("pricePerUnit", {}).get("USD", "0") or "0")
            return p if p > 0 else None
        except (ValueError, TypeError):
            return None
    # Multi-tier: find the dimension where beginRange == "0".
    for dim in price_dims.values():
        if str(dim.get("beginRange", "")) == "0":
            try:
                p = float(dim.get("pricePerUnit", {}).get("USD", "0") or "0")
                return p if p > 0 else None
            except (ValueError, TypeError):
                pass
    return None


def _aws_fetch_region(
    session: requests.Session, region: str
) -> List[Dict[str, Any]]:
    """
    Fetch and parse S3 pricing for one AWS region.

    Two ijson passes over the buffered response:
      Pass 1 — products section: collect SKU attributes.
      Pass 2 — terms.OnDemand section: collect prices.
    """
    url = _AWS_S3_PRICING_URL.format(region=region)
    try:
        content = _get_bytes(session, url)
    except requests.RequestException as exc:
        logger.warning("Skipping %s (S3 pricing fetch failed): %s", region, exc)
        return []

    # Pass 1 — products
    # storage_skus: sku -> {storageClass_raw, volumeType}
    # request_skus: sku -> {storageClass_raw, requestType}
    storage_skus: Dict[str, Dict] = {}
    request_skus: Dict[str, Dict] = {}

    try:
        for sku, product in ijson.kvitems(io.BytesIO(content), "products"):
            family = product.get("productFamily", "")
            attrs = product.get("attributes", {})
            sc_raw = attrs.get("storageClass", "")

            if family in ("Storage", "Archive") and sc_raw in _AWS_STORAGE_CLASS_MAP:
                storage_skus[sku] = {"storageClass": sc_raw}

            elif family == "API Request" and sc_raw in _AWS_STORAGE_CLASS_MAP:
                req_type = attrs.get("requestType", "")
                if req_type:
                    request_skus[sku] = {
                        "storageClass": sc_raw,
                        "requestType": req_type,
                    }
    except Exception as exc:
        logger.warning("Error parsing products for %s: %s", region, exc)
        return []

    # Pass 2 — OnDemand terms
    prices: Dict[str, float] = {}  # sku -> price

    try:
        for sku, term_group in ijson.kvitems(io.BytesIO(content), "terms.OnDemand"):
            if sku not in storage_skus and sku not in request_skus:
                continue
            for _term_code, term in term_group.items():
                p = _aws_extract_first_tier_price(term.get("priceDimensions", {}))
                if p is not None:
                    prices[sku] = p
                    break
    except Exception as exc:
        logger.warning("Error parsing terms for %s: %s", region, exc)
        return []

    # Aggregate storage + request prices by storage class
    storage_price: Dict[str, float] = {}  # sc_raw -> price/GiB-month
    get_price: Dict[str, float] = {}       # sc_raw -> price/request
    put_price: Dict[str, float] = {}       # sc_raw -> price/request

    for sku, meta in storage_skus.items():
        p = prices.get(sku)
        if p is None:
            continue
        sc = meta["storageClass"]
        if sc not in storage_price or p < storage_price[sc]:
            storage_price[sc] = p

    for sku, meta in request_skus.items():
        p = prices.get(sku)
        if p is None:
            continue
        sc = meta["storageClass"]
        rt = meta["requestType"]
        rt_lower = rt.lower()
        # GET: "get", "select", "all other" appear in GET request type strings
        if "get" in rt_lower or "select" in rt_lower:
            if sc not in get_price or p < get_price[sc]:
                get_price[sc] = p
        # PUT: "put", "copy", "post", "list" appear in PUT-class request type strings
        elif "put" in rt_lower or "copy" in rt_lower or "post" in rt_lower or "list" in rt_lower:
            if sc not in put_price or p < put_price[sc]:
                put_price[sc] = p

    # Build output records
    records: List[Dict[str, Any]] = []
    for sc_raw, pgm in storage_price.items():
        slug, label = _AWS_STORAGE_CLASS_MAP[sc_raw]
        records.append({
            "provider": "aws",
            "type": "object-storage",
            "instanceType": f"s3-{slug}",
            "family": "s3",
            "storageClass": label,
            "pricePerGiBMonth": pgm,
            "pricePerGiBHour": pgm / HOURS_PER_MONTH,
            "pricePerGetRequest": get_price.get(sc_raw),
            "pricePerPutRequest": put_price.get(sc_raw),
            "regions": [region],
            "redundancy": None,
            "source": "aws_pricing_api",
            "lastUpdated": _NOW,
            "pricingModel": "on-demand",
        })

    return records


def fetch_aws_object_storage(
    regions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    session = make_session()

    if regions is None:
        logger.info("Discovering AWS regions from EC2 region index…")
        regions = _aws_discover_regions(session)
        logger.info("Found %d regions", len(regions))

    all_records: List[Dict[str, Any]] = []
    for region in regions:
        logger.info("Fetching S3 pricing for %s…", region)
        all_records.extend(_aws_fetch_region(session, region))

    return all_records


# ===========================================================================
# GCP Cloud Storage
# ===========================================================================

_GCP_BILLING_BASE = "https://cloudbilling.googleapis.com/v1"
_GCS_SERVICE_NAME = "Cloud Storage"
_GCS_SERVICE_ID_FALLBACK = "95FF-2EF5-5EA1"  # Cloud Storage billing service ID

_GCS_TIER_MAP: Dict[str, str] = {
    "standard": "Standard",
    "nearline": "Nearline",
    "coldline": "Coldline",
    "archive":  "Archive",
}


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
        logger.debug("ADC not available: %s", exc)
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
            logger.warning("Could not attach ADC bearer token: %s", exc)
    return s


def _gcp_get_json(
    session: requests.Session, url: str, params: Optional[Dict] = None
) -> Dict[str, Any]:
    for attempt in range(4):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == 3:
                raise
            wait = 2.0 * (2 ** attempt)
            logger.warning(
                "GET %s failed (attempt %d/4): %s — retrying in %.1fs",
                url, attempt + 1, type(exc).__name__, wait,
            )
            time.sleep(wait)
    raise RuntimeError("Unreachable")


def _gcp_paginate_skus(
    session: requests.Session, service_id: str, api_key: Optional[str]
) -> List[Dict[str, Any]]:
    url = f"{_GCP_BILLING_BASE}/services/{service_id}/skus"
    skus: List[Dict] = []
    page_token: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"pageSize": 5000}
        if api_key:
            params["key"] = api_key
        if page_token:
            params["pageToken"] = page_token
        data = _gcp_get_json(session, url, params=params)
        skus.extend(data.get("skus", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return skus


def _gcp_tier0_price(pricing_info: List[Dict]) -> Optional[float]:
    """Return the startUsageAmount==0 tier price in USD per usage unit."""
    if not pricing_info:
        return None
    pi = pricing_info[0]
    expr = pi.get("pricingExpression", {})
    for tier in expr.get("tieredRates", []):
        if tier.get("startUsageAmount", -1) == 0:
            up = tier.get("unitPrice", {})
            price = int(up.get("units", 0)) + int(up.get("nanos", 0)) / 1_000_000_000.0
            if price > 0:
                return price
    return None


def _gcp_usage_unit_desc(pricing_info: List[Dict]) -> str:
    if not pricing_info:
        return ""
    return (
        pricing_info[0]
        .get("pricingExpression", {})
        .get("usageUnitDescription", "")
        .lower()
    )


def _gcp_normalize_op_price(price: float, unit_desc: str) -> float:
    """Convert GCS operation price to per-individual-request."""
    if "10k" in unit_desc or "10,000" in unit_desc or "10 k" in unit_desc:
        return price / 10_000
    if "1k" in unit_desc or "1,000" in unit_desc:
        return price / 1_000
    if "million" in unit_desc:
        return price / 1_000_000
    # Assume price is already per individual request
    return price


def _gcp_classify_sku(
    sku: Dict,
) -> Optional[Tuple[str, str]]:
    """
    Classify a GCS SKU.

    Returns (tier, op_type) where:
      tier:    standard | nearline | coldline | archive
      op_type: storage | class_a | class_b

    Returns None if the SKU is not relevant to at-rest storage pricing.
    """
    cat = sku.get("category", {})
    if cat.get("resourceFamily") != "Storage":
        return None

    desc = sku.get("description", "").lower()

    # Determine storage tier from description
    tier: Optional[str] = None
    for key in _GCS_TIER_MAP:
        if key in desc:
            tier = key
            break
    if tier is None:
        return None

    # Skip data transfer, retrieval, and early-delete SKUs
    skip_keywords = ("retrieval", "transfer", "egress", "early delete", "restore")
    if any(kw in desc for kw in skip_keywords):
        return None

    # Classify operation type
    if "class a" in desc:
        return (tier, "class_a")
    if "class b" in desc:
        return (tier, "class_b")
    # Storage-at-rest: description contains "storage" but not "operations" or "class"
    if "storage" in desc and "operations" not in desc and "class" not in desc:
        return (tier, "storage")

    return None


def fetch_gcp_object_storage(
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    credentials = None
    if not api_key:
        logger.info("No GCP_API_KEY — attempting ADC…")
        credentials = _gcp_get_adc_credentials()
        if credentials is None:
            raise RuntimeError(
                "GCP auth failed: neither GCP_API_KEY nor working ADC found.\n"
                "Set GCP_API_KEY or run: gcloud auth application-default login"
            )

    session = _gcp_make_session(credentials=credentials)

    # Resolve Cloud Storage service ID
    logger.info("Resolving Cloud Storage service ID…")
    svc_params: Dict[str, Any] = {"pageSize": 500}
    if api_key:
        svc_params["key"] = api_key
    services = _gcp_get_json(session, f"{_GCP_BILLING_BASE}/services", params=svc_params)
    service_id: Optional[str] = None
    for svc in services.get("services", []):
        if svc.get("displayName", "") == _GCS_SERVICE_NAME:
            service_id = svc["name"].split("/")[-1]
            break
    if service_id is None:
        logger.warning(
            "Could not resolve Cloud Storage service ID; using fallback %s",
            _GCS_SERVICE_ID_FALLBACK,
        )
        service_id = _GCS_SERVICE_ID_FALLBACK

    logger.info("Cloud Storage service ID: %s", service_id)
    logger.info("Fetching Cloud Storage SKUs…")
    all_skus = _gcp_paginate_skus(session, service_id, api_key=api_key)
    logger.info("Total Cloud Storage SKUs: %d", len(all_skus))

    # Collect prices indexed by (tier, region)
    storage_prices: Dict[Tuple[str, str], float] = {}  # price per GiB/month
    class_a_prices: Dict[Tuple[str, str], float] = {}  # price per individual op
    class_b_prices: Dict[Tuple[str, str], float] = {}

    for sku in all_skus:
        classified = _gcp_classify_sku(sku)
        if classified is None:
            continue
        tier, op_type = classified

        pricing_info = sku.get("pricingInfo", [])
        price = _gcp_tier0_price(pricing_info)
        if price is None or price == 0:
            continue

        unit_desc = _gcp_usage_unit_desc(pricing_info)

        for region in sku.get("serviceRegions", []):
            key = (tier, region)
            if op_type == "storage":
                if key not in storage_prices or price < storage_prices[key]:
                    storage_prices[key] = price
            elif op_type == "class_a":
                norm = _gcp_normalize_op_price(price, unit_desc)
                if key not in class_a_prices or norm < class_a_prices[key]:
                    class_a_prices[key] = norm
            elif op_type == "class_b":
                norm = _gcp_normalize_op_price(price, unit_desc)
                if key not in class_b_prices or norm < class_b_prices[key]:
                    class_b_prices[key] = norm

    records: List[Dict[str, Any]] = []
    for (tier, region), pgm in storage_prices.items():
        label = _GCS_TIER_MAP[tier]
        key = (tier, region)
        # GCS: Class B = reads (GET), Class A = writes (PUT)
        records.append({
            "provider": "gcp",
            "type": "object-storage",
            "instanceType": f"gcs-{tier}",
            "family": "gcs",
            "storageClass": label,
            "pricePerGiBMonth": pgm,
            "pricePerGiBHour": pgm / HOURS_PER_MONTH,
            "pricePerGetRequest": class_b_prices.get(key),
            "pricePerPutRequest": class_a_prices.get(key),
            "regions": [region],
            "redundancy": None,
            "source": "gcp_billing_catalog_api",
            "lastUpdated": _NOW,
            "pricingModel": "on-demand",
        })

    return records


# ===========================================================================
# Azure Blob Storage
# ===========================================================================

_AZURE_RETAIL_URL = "https://prices.azure.com/api/retail/prices"
_AZURE_API_VERSION = "2023-01-01-preview"

_AZURE_TIER_KEYWORDS = ("archive", "cold", "cool", "hot")  # order: longest match first
_AZURE_REDUNDANCY_KEYWORDS = (
    "ra-gzrs", "ra-grs", "gzrs", "grs", "zrs", "lrs",
)


def _azure_paginate(
    session: requests.Session, filter_str: str
) -> List[Dict[str, Any]]:
    url = _AZURE_RETAIL_URL
    params: Optional[Dict] = {"api-version": _AZURE_API_VERSION, "$filter": filter_str}
    items: List[Dict] = []
    while True:
        data = _get_json(session, url, params=params)
        items.extend(data.get("Items", []))
        next_link = data.get("NextPageLink")
        if not next_link:
            break
        url = next_link
        params = None  # NextPageLink already encodes all params
    return items


def _azure_parse_item(
    item: Dict,
) -> Optional[Dict[str, Any]]:
    """
    Parse one Azure Blob Storage pricing item.

    Returns a dict with keys: tier, redundancy, op_type, price, unit, region.
    Returns None if the item should be skipped.
    """
    sku_name = (item.get("skuName") or "").lower()
    meter_name = (item.get("meterName") or "").lower()
    product_name = (item.get("productName") or "").lower()
    region = item.get("armRegionName") or ""
    price = float(item.get("retailPrice") or 0)
    unit = (item.get("unitOfMeasure") or "").lower()

    if not region or price <= 0:
        return None

    # Skip global / cross-region meters (data transfer, etc.)
    skip_meter = (
        "bandwidth", "data transfer", "geo", "replication", "lrs to",
        "delete operations", "lifecycle", "inventory", "batch",
    )
    if any(kw in meter_name for kw in skip_meter):
        return None

    # Determine storage tier
    tier: Optional[str] = None
    combined = f"{product_name} {sku_name}"
    for t in _AZURE_TIER_KEYWORDS:
        if t in combined:
            tier = t
            break
    if tier is None:
        return None

    # Determine redundancy from skuName
    redundancy = "LRS"
    for r_lower in _AZURE_REDUNDANCY_KEYWORDS:
        if r_lower in sku_name:
            redundancy = r_lower.upper().replace("RA-GRS", "RA-GRS").replace("RA-GZRS", "RA-GZRS")
            break

    # Classify operation type
    if "data stored" in meter_name:
        op_type = "storage"
        if "gb" not in unit:
            return None
    elif "write" in meter_name or "put" in meter_name:
        op_type = "write"
    elif "read" in meter_name or "get" in meter_name or ("other operations" in meter_name):
        op_type = "read"
    else:
        return None

    return {
        "tier": tier,
        "redundancy": redundancy,
        "op_type": op_type,
        "price": price,
        "unit": unit,
        "region": region,
    }


def _azure_ops_to_per_request(price: float, unit: str) -> float:
    """Normalize Azure operation price to per-individual-request."""
    # Azure typically reports "10K" = 10,000 operations
    if "10k" in unit or "10,000" in unit:
        return price / 10_000
    if "1k" in unit or "1,000" in unit:
        return price / 1_000
    return price  # assume already per-request


def fetch_azure_object_storage() -> List[Dict[str, Any]]:
    session = make_session()

    logger.info("Fetching Azure Blob Storage pricing…")
    items = _azure_paginate(
        session,
        "serviceName eq 'Storage' and productName eq 'Blob Storage' and priceType eq 'Consumption'",
    )
    logger.info("Fetched %d Azure Blob Storage items", len(items))

    # Collect by (tier, redundancy, region)
    storage_prices: Dict[Tuple[str, str, str], float] = {}
    write_prices: Dict[Tuple[str, str, str], Tuple[float, str]] = {}
    read_prices: Dict[Tuple[str, str, str], Tuple[float, str]] = {}

    for item in items:
        parsed = _azure_parse_item(item)
        if parsed is None:
            continue
        key: Tuple[str, str, str] = (parsed["tier"], parsed["redundancy"], parsed["region"])
        op = parsed["op_type"]
        p, u = parsed["price"], parsed["unit"]

        if op == "storage":
            if key not in storage_prices or p < storage_prices[key]:
                storage_prices[key] = p
        elif op == "write":
            if key not in write_prices or p < write_prices[key][0]:
                write_prices[key] = (p, u)
        elif op == "read":
            if key not in read_prices or p < read_prices[key][0]:
                read_prices[key] = (p, u)

    records: List[Dict[str, Any]] = []
    for (tier, redundancy, region), pgm in storage_prices.items():
        label = tier.capitalize()
        slug = f"{tier}-{redundancy.lower()}"
        key = (tier, redundancy, region)

        get_price: Optional[float] = None
        put_price: Optional[float] = None
        if key in read_prices:
            p, u = read_prices[key]
            get_price = _azure_ops_to_per_request(p, u)
        if key in write_prices:
            p, u = write_prices[key]
            put_price = _azure_ops_to_per_request(p, u)

        records.append({
            "provider": "azure",
            "type": "object-storage",
            "instanceType": f"azure-blob-{slug}",
            "family": "azure-blob",
            "storageClass": label,
            "pricePerGiBMonth": pgm,
            "pricePerGiBHour": pgm / HOURS_PER_MONTH,
            "pricePerGetRequest": get_price,
            "pricePerPutRequest": put_price,
            "regions": [region],
            "redundancy": redundancy,
            "source": "azure_retail_api",
            "lastUpdated": _NOW,
            "pricingModel": "on-demand",
        })

    return records


# ===========================================================================
# Orchestrator entry point
# ===========================================================================

def fetch_data(provider: str) -> List[Dict[str, Any]]:
    """Dispatcher called by the extras orchestrator."""
    if provider == "aws":
        return fetch_aws_object_storage()
    if provider == "gcp":
        api_key = os.environ.get("GCP_API_KEY", "").strip() or None
        if not api_key:
            env_path = _REPO_ROOT / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.strip().startswith("GCP_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'") or None
                        break
        return fetch_gcp_object_storage(api_key=api_key)
    if provider == "azure":
        return fetch_azure_object_storage()
    raise ValueError(f"Unsupported provider: {provider!r}")


# CLI entry point
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch object storage pricing (S3, GCP Cloud Storage, Azure Blob)"
    )
    parser.add_argument(
        "--provider",
        choices=["aws", "gcp", "azure", "all"],
        default="all",
        help="Provider to fetch (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: data/providers/ relative to repo root)",
    )
    args = parser.parse_args()

    output_dir = (
        Path(args.output_dir) if args.output_dir
        else _REPO_ROOT / "data" / "providers"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    providers = ["aws", "gcp", "azure"] if args.provider == "all" else [args.provider]

    for provider in providers:
        try:
            if provider == "aws":
                logger.info("=== Fetching AWS S3 pricing ===")
                records = fetch_aws_object_storage()

            elif provider == "gcp":
                logger.info("=== Fetching GCP Cloud Storage pricing ===")
                api_key = os.environ.get("GCP_API_KEY", "").strip() or None
                if not api_key:
                    env_path = _REPO_ROOT / ".env"
                    if env_path.exists():
                        for line in env_path.read_text().splitlines():
                            if line.strip().startswith("GCP_API_KEY="):
                                api_key = line.split("=", 1)[1].strip().strip('"').strip("'") or None
                                break
                records = fetch_gcp_object_storage(api_key=api_key)

            elif provider == "azure":
                logger.info("=== Fetching Azure Blob Storage pricing ===")
                records = fetch_azure_object_storage()

            else:
                continue

            out_path = output_dir / f"{provider}.object-storage.raw.json"
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(records, fh, indent=2, ensure_ascii=False)

            logger.info("Wrote %d records to %s", len(records), out_path)
            print(f"[{provider}] {len(records):,} records -> {out_path}")

        except Exception as exc:
            logger.error("Failed to fetch %s: %s", provider, exc, exc_info=True)


if __name__ == "__main__":
    main()
