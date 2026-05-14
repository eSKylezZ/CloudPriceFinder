#!/usr/bin/env python3
"""
Managed database pricing fetcher for CloudPriceFinder extras.

Captures AWS RDS, GCP Cloud SQL, and Azure Database instance pricing into
sidecar raw files following the same schema as cloud-server records, with
"type": "rds-instance" and extra database-specific fields.

Outputs (all gitignored):
  data/providers/aws.databases.raw.json
  data/providers/gcp.databases.raw.json
  data/providers/azure.databases.raw.json

Usage:
  python scripts/extras/fetch_databases.py
  python scripts/extras/fetch_databases.py --provider aws
  python scripts/extras/fetch_databases.py --provider aws --engine postgres
  python scripts/extras/fetch_databases.py --engine mysql --engine mariadb

GCP auth (in priority order):
  1. GCP_API_KEY environment variable
  2. Application Default Credentials (gcloud auth application-default login)

Skips SQL Server and Oracle by default (--engine sqlserver|oracle to include).
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
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

import ijson
import requests

# ---------------------------------------------------------------------------
# Repo root — one level above scripts/extras/
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.data_normalizer import normalize_commitments
from scripts.utils.http_client import HOURS_PER_MONTH, get_json, make_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_databases")

# ---------------------------------------------------------------------------
# Engine normalization
# ---------------------------------------------------------------------------

DEFAULT_ENGINES: Set[str] = {"mysql", "postgres", "aurora-mysql", "aurora-postgres", "mariadb"}
ALL_ENGINES: Set[str] = DEFAULT_ENGINES | {"sqlserver", "oracle"}

# AWS databaseEngine attribute values (lowercase) → normalized engine name
_AWS_ENGINE_MAP: Dict[str, Optional[str]] = {
    "mysql":              "mysql",
    "mysql community":    "mysql",
    "postgresql":         "postgres",
    "aurora mysql":       "aurora-mysql",
    "aurora postgresql":  "aurora-postgres",
    "aurora":             "aurora-mysql",
    "oracle":             "oracle",
    "sql server":         "sqlserver",
    "mariadb":            "mariadb",
    "docdb":              None,   # DocumentDB — not a standard RDBMS
    "neptune":            None,   # graph DB — skip
}

# GCP Cloud SQL SKU description substrings → normalized engine
_GCP_ENGINE_KEYWORDS: List[Tuple[str, str]] = [
    ("aurora",      "aurora-mysql"),  # shouldn't appear in GCP but guard
    ("postgresql",  "postgres"),
    ("postgres",    "postgres"),
    ("mysql",       "mysql"),
    ("sql server",  "sqlserver"),
    ("sqlserver",   "sqlserver"),
]

# Azure serviceName (lowercase) → normalized engine
_AZURE_SERVICE_ENGINE_MAP: Dict[str, str] = {
    "azure database for postgresql": "postgres",
    "azure database for mysql":      "mysql",
    "azure database for mariadb":    "mariadb",
    "azure sql database":            "sqlserver",
    "sql database":                  "sqlserver",
}

# Azure service names to query from the Retail API
AZURE_DB_SERVICE_NAMES = [
    "Azure Database for PostgreSQL",
    "Azure Database for MySQL",
    "Azure Database for MariaDB",
    "Azure SQL Database",
]


def _normalize_engine(raw: str, provider: str) -> Optional[str]:
    e = raw.lower().strip()
    if provider == "aws":
        return _AWS_ENGINE_MAP.get(e)
    if provider == "gcp":
        for keyword, normalized in _GCP_ENGINE_KEYWORDS:
            if keyword in e:
                return normalized
        return None
    if provider == "azure":
        for svc_key, normalized in _AZURE_SERVICE_ENGINE_MAP.items():
            if svc_key in e:
                return normalized
        return None
    return None


# ---------------------------------------------------------------------------
# AWS RDS constants
# ---------------------------------------------------------------------------
_PRICING_BASE = "https://pricing.us-east-1.amazonaws.com"
_RDS_REGION_INDEX_PATH = "/offers/v1.0/aws/AmazonRDS/current/region_index.json"
_EXCLUDED_PREFIXES = ("cn-", "us-gov-")
_REQUEST_TIMEOUT = 120
_MAX_RETRIES = 3
_RETRY_BACKOFF = 5


def _aws_stream_response(session: requests.Session, url: str) -> requests.Response:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = session.get(url, stream=True, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning(f"Attempt {attempt} failed for {url}: {exc}. Retrying in {_RETRY_BACKOFF}s…")
            time.sleep(_RETRY_BACKOFF)
    raise RuntimeError(f"Failed to stream {url} after {_MAX_RETRIES} attempts")


def _parse_memory_gib(mem_str: str) -> float:
    if not mem_str or mem_str.lower() in ("na", "n/a", ""):
        return 0.0
    cleaned = mem_str.replace(",", "").lower().replace("gib", "").replace("gb", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_vcpu(vcpu_str: str) -> int:
    m = re.search(r"(\d+)", str(vcpu_str or ""))
    return int(m.group(1)) if m else 0


def _normalize_license(s: str) -> str:
    l = s.lower()
    if "bring" in l or "byol" in l:
        return "byol"
    return "included"


def _normalize_deployment(s: str) -> str:
    return "multi-az" if "multi" in s.lower() else "single-az"


def _rds_family(instance_type: str) -> str:
    """'db.m7g.xlarge' → 'db.m7g'"""
    parts = instance_type.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else instance_type


def _stream_rds_skus(
    session: requests.Session, url: str
) -> Generator[Dict[str, Any], None, None]:
    """
    Three-pass ijson streaming of an RDS per-region pricing JSON.
    Same pattern as fetch_aws.py _stream_ec2_skus.
    """
    logger.info(f"  Downloading RDS pricing from {url} …")
    t0 = time.time()
    resp = _aws_stream_response(session, url)
    raw_bytes = resp.content
    logger.info(f"  Downloaded {len(raw_bytes)/1024/1024:.1f} MB in {time.time()-t0:.1f}s")

    # Pass 1: collect products keyed by SKU
    products: Dict[str, Dict[str, Any]] = {}
    for sku, attrs in ijson.kvitems(io.BytesIO(raw_bytes), "products"):
        attributes = attrs.get("attributes", {})
        instance_type = attributes.get("instanceType", "")
        if not instance_type or not instance_type.startswith("db."):
            continue
        products[sku] = attributes
    logger.info(f"  {len(products)} RDS product SKUs")

    # Pass 2: on-demand prices
    on_demand: Dict[str, float] = {}
    for sku, offer_terms in ijson.kvitems(io.BytesIO(raw_bytes), "terms.OnDemand"):
        if sku not in products:
            continue
        for _tc, term in offer_terms.items():
            for _dc, dim in term.get("priceDimensions", {}).items():
                try:
                    price = float(dim.get("pricePerUnit", {}).get("USD", "0"))
                except (ValueError, TypeError):
                    price = 0.0
                if price > 0:
                    on_demand[sku] = price
                    break
    logger.info(f"  {len(on_demand)} on-demand prices")

    # Pass 3: reserved terms
    reserved: Dict[str, List[Dict[str, Any]]] = {}
    for sku, offer_terms in ijson.kvitems(io.BytesIO(raw_bytes), "terms.Reserved"):
        if sku not in products:
            continue
        terms_list = []
        for _tc, term in offer_terms.items():
            ta = term.get("termAttributes", {})
            lease = ta.get("LeaseContractLength", "")
            purchase = ta.get("PurchaseOption", "")

            if "1" in lease:
                schema_term = "1yr"
            elif "3" in lease:
                schema_term = "3yr"
            else:
                continue

            p = purchase.lower()
            if "no" in p:
                schema_payment = "no-upfront"
            elif "partial" in p:
                schema_payment = "partial-upfront"
            elif "all" in p:
                schema_payment = "all-upfront"
            else:
                continue

            hourly_usd = 0.0
            upfront_usd = 0.0
            for _dc, dim in term.get("priceDimensions", {}).items():
                desc = dim.get("description", "").lower()
                try:
                    amount = float(dim.get("pricePerUnit", {}).get("USD", "0"))
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
                "priceUSD_hourly": hourly_usd,
                "upfront_usd": upfront_usd,
            })
        if terms_list:
            reserved[sku] = terms_list
    logger.info(f"  Reserved terms for {len(reserved)} SKUs")

    for sku, attrs in products.items():
        od = on_demand.get(sku)
        if od and od > 0:
            yield {
                "sku": sku,
                "attributes": attrs,
                "on_demand_hourly": od,
                "raw_reservations": reserved.get(sku, []),
            }


def fetch_aws_databases(
    engines: Set[str],
    regions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Fetch AWS RDS per-instance-type pricing for the given engines."""
    session = make_session()

    logger.info("Fetching RDS region index …")
    region_index = get_json(
        session, _PRICING_BASE + _RDS_REGION_INDEX_PATH, timeout=_REQUEST_TIMEOUT
    )

    region_url_map: Dict[str, str] = {}
    for _display, rdata in region_index.get("regions", {}).items():
        code = rdata.get("regionCode", "")
        url_path = rdata.get("currentVersionUrl", "")
        if code and url_path:
            region_url_map[code] = _PRICING_BASE + url_path
    logger.info(f"RDS region index: {len(region_url_map)} regions")

    if regions is None:
        regions = sorted(
            c for c in region_url_map
            if not any(c.startswith(p) for p in _EXCLUDED_PREFIXES)
        )
        logger.info(f"Using all {len(regions)} standard commercial regions")

    now_iso = datetime.now(timezone.utc).isoformat()
    # key: (instanceType, engine, deploymentOption)
    merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for region in regions:
        region_url = region_url_map.get(region)
        if not region_url:
            logger.warning(f"No pricing URL for {region} — skipping")
            continue

        logger.info(f"Processing RDS region {region} …")
        try:
            for raw_sku in _stream_rds_skus(session, region_url):
                attrs = raw_sku["attributes"]
                od = raw_sku["on_demand_hourly"]
                reservations = raw_sku["raw_reservations"]

                instance_type = attrs.get("instanceType", "")
                raw_engine = attrs.get("databaseEngine", "")
                deployment = attrs.get("deploymentOption", "Single-AZ")
                license_model = attrs.get("licenseModel", "license-included")

                engine = _normalize_engine(raw_engine, "aws")
                if engine is None or engine not in engines:
                    continue

                norm_deploy = _normalize_deployment(deployment)
                norm_license = _normalize_license(license_model)
                family = _rds_family(instance_type)
                vcpu = _parse_vcpu(attrs.get("vcpu", "0"))
                mem_gib = _parse_memory_gib(attrs.get("memory", "0 GiB"))

                key = (instance_type, engine, norm_deploy)
                if key in merged:
                    rec = merged[key]
                    if region not in rec["regions"]:
                        rec["regions"].append(region)
                    rec.setdefault("regionPricing", {})[region] = od
                    rec["_raw_reservations"].extend(reservations)
                else:
                    merged[key] = {
                        "provider": "aws",
                        "type": "rds-instance",
                        "instanceType": instance_type,
                        "family": family,
                        "vCPU": vcpu,
                        "memoryGiB": mem_gib,
                        "priceUSD_hourly": round(od, 6),
                        "priceUSD_monthly": round(od * HOURS_PER_MONTH, 4),
                        "engine": engine,
                        "engineVersion": None,
                        "deploymentOption": norm_deploy,
                        "licenseModel": norm_license,
                        "commitments": [],
                        "regions": [region],
                        "regionPricing": {region: od},
                        "source": "aws_pricing_api",
                        "lastUpdated": now_iso,
                        "pricingModel": "on-demand",
                        "_raw_reservations": list(reservations),
                    }
        except Exception as exc:
            logger.error(f"Failed to process RDS region {region}: {exc}")
            continue

    # Normalize price to primary region and build commitments
    primary_regions = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1"]
    results: List[Dict[str, Any]] = []

    for record in merged.values():
        rp = record.get("regionPricing", {})
        for candidate in primary_regions:
            if candidate in rp:
                price = float(rp[candidate])
                if price > 0:
                    record["priceUSD_hourly"] = round(price, 6)
                    record["priceUSD_monthly"] = round(price * HOURS_PER_MONTH, 4)
                    break

        raw_res = record.pop("_raw_reservations", [])
        # Dedup by (term, payment) — keep lower effective cost
        deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in raw_res:
            k = (r["term"], r["payment"])
            if k not in deduped:
                deduped[k] = r
            else:
                existing_cost = deduped[k].get("priceUSD_hourly", 0) + deduped[k].get("upfront_usd", 0) / 8760
                new_cost = r.get("priceUSD_hourly", 0) + r.get("upfront_usd", 0) / 8760
                if new_cost < existing_cost:
                    deduped[k] = r

        record["commitments"] = normalize_commitments(
            list(deduped.values()), record["priceUSD_hourly"]
        )
        results.append(record)

    logger.info(f"AWS RDS: {len(results)} unique (instanceType, engine, deployment) configs")
    return results


# ---------------------------------------------------------------------------
# GCP Cloud SQL constants and helpers
# ---------------------------------------------------------------------------
_GCP_BILLING_BASE = "https://cloudbilling.googleapis.com/v1"
_CLOUD_SQL_DISPLAY_PREFIXES = ("cloud sql", "cloudsql")
_CLOUD_SQL_SERVICE_ID_FALLBACK = "9662-B51E-5089"  # Cloud SQL billing service ID

# Known Cloud SQL instance specs: instanceType -> (vCPU, memGiB)
# Source: https://cloud.google.com/sql/docs/mysql/instance-settings
_GCP_SQL_SPECS: Dict[str, Tuple[int, float]] = {
    "db-f1-micro":         (1,  0.6),
    "db-g1-small":         (1,  1.7),
    # N1 Standard (3.75 GiB/vCPU)
    "db-n1-standard-1":    (1,   3.75),
    "db-n1-standard-2":    (2,   7.5),
    "db-n1-standard-4":    (4,  15.0),
    "db-n1-standard-8":    (8,  30.0),
    "db-n1-standard-16":   (16,  60.0),
    "db-n1-standard-32":   (32, 120.0),
    "db-n1-standard-64":   (64, 240.0),
    "db-n1-standard-96":   (96, 360.0),
    # N1 High-memory (6.5 GiB/vCPU)
    "db-n1-highmem-2":     (2,  13.0),
    "db-n1-highmem-4":     (4,  26.0),
    "db-n1-highmem-8":     (8,  52.0),
    "db-n1-highmem-16":    (16, 104.0),
    "db-n1-highmem-32":    (32, 208.0),
    "db-n1-highmem-64":    (64, 416.0),
    "db-n1-highmem-96":    (96, 624.0),
    # N2 Standard (4 GiB/vCPU)
    "db-n2-standard-2":    (2,   8.0),
    "db-n2-standard-4":    (4,  16.0),
    "db-n2-standard-8":    (8,  32.0),
    "db-n2-standard-16":   (16,  64.0),
    "db-n2-standard-32":   (32, 128.0),
    "db-n2-standard-64":   (64, 256.0),
    "db-n2-standard-96":   (96, 384.0),
    # N2 High-memory (8 GiB/vCPU)
    "db-n2-highmem-2":     (2,  16.0),
    "db-n2-highmem-4":     (4,  32.0),
    "db-n2-highmem-8":     (8,  64.0),
    "db-n2-highmem-16":    (16, 128.0),
    "db-n2-highmem-32":    (32, 256.0),
    "db-n2-highmem-64":    (64, 512.0),
    "db-n2-highmem-96":    (96, 768.0),
    # N2D AMD Standard (4 GiB/vCPU)
    "db-n2d-standard-2":   (2,   8.0),
    "db-n2d-standard-4":   (4,  16.0),
    "db-n2d-standard-8":   (8,  32.0),
    "db-n2d-standard-16":  (16,  64.0),
    "db-n2d-standard-32":  (32, 128.0),
    "db-n2d-standard-64":  (64, 256.0),
    "db-n2d-standard-96":  (96, 384.0),
    # N2D AMD High-memory (8 GiB/vCPU)
    "db-n2d-highmem-2":    (2,  16.0),
    "db-n2d-highmem-4":    (4,  32.0),
    "db-n2d-highmem-8":    (8,  64.0),
    "db-n2d-highmem-16":   (16, 128.0),
    "db-n2d-highmem-32":   (32, 256.0),
    "db-n2d-highmem-64":   (64, 512.0),
    "db-n2d-highmem-96":   (96, 768.0),
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
        logger.debug(f"ADC not available: {exc}")
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
            logger.warning(f"Could not attach ADC bearer token: {exc}")
    return s


def _gcp_get_json(session: requests.Session, url: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    for attempt in range(4):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == 3:
                raise
            wait = 2.0 * (2 ** attempt)
            logger.warning(f"GET failed (attempt {attempt+1}/4): {type(exc).__name__} — retrying in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"All retries exhausted for {url}")


def _gcp_paginate_skus(
    session: requests.Session,
    service_id: str,
    api_key: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    url = f"{_GCP_BILLING_BASE}/services/{service_id}/skus"
    page_token = None
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
    if not tiers:
        return None
    for tier in tiers:
        if tier.get("startUsageAmount", 0) == 0:
            up = tier.get("unitPrice", {})
            return int(up.get("units", 0)) + int(up.get("nanos", 0)) / 1_000_000_000.0
    up = tiers[0].get("unitPrice", {})
    return int(up.get("units", 0)) + int(up.get("nanos", 0)) / 1_000_000_000.0


# New-format Cloud SQL descriptions: "Cloud SQL for X: Zonal - N vCPU + M GB RAM in Location"
# The specs are embedded in the description; we synthesize a db-custom-N-M instance type.
_GCP_CLOUD_SQL_VCPU_RAM_RE = re.compile(
    r"(?:Zonal|Regional) - (\d+) vCPU \+ ([\d.]+)\s*(?:GB|GiB) RAM",
    re.IGNORECASE,
)


def _gcp_parse_sql_instance_type(description: str) -> Optional[str]:
    """
    Extract Cloud SQL instance type from a SKU description.

    New format (current):
      "Cloud SQL for MySQL: Zonal - 8 vCPU + 52GB RAM in Bangkok"
    Old format (legacy, may still appear):
      "Cloud SQL for MySQL: Zonal - n2-standard-4 in Iowa"
      "DB-N1 Standard 4 - Cloud SQL for MySQL"
      "Cloud SQL for PostgreSQL: db-custom-2-13312 in Americas"

    Skips extended-support surcharge SKUs and per-resource (vCPU-only, RAM-only) SKUs.
    """
    # Skip extended-support surcharge SKUs (they add cost on top of base pricing)
    if re.search(r"extended support", description, re.IGNORECASE):
        return None

    desc_lower = description.lower()

    # New format: "Zonal - N vCPU + M GB RAM in ..."
    m = _GCP_CLOUD_SQL_VCPU_RAM_RE.search(description)
    if m:
        vcpu = int(m.group(1))
        ram_gb = float(m.group(2))
        return f"db-custom-{vcpu}-{int(ram_gb * 1024)}"

    # Old format: look for explicit db-* prefix patterns
    for prefix in ("db-n2d-", "db-n2-", "db-n1-", "db-f1-", "db-g1-", "db-custom-"):
        idx = desc_lower.find(prefix)
        if idx >= 0:
            m2 = re.search(
                r"(db-(?:n2d|n2|n1|f1|g1)-(?:standard|highmem|micro|small)-?\d*"
                r"|db-custom-\d+-\d+)",
                desc_lower[idx:],
            )
            if m2:
                return m2.group(0)

    # Shorthand like "n1 standard 4" without the db- prefix
    m3 = re.search(r"\b(n1|n2|n2d)\s+(standard|highmem)\s+(\d+)", desc_lower)
    if m3:
        family, tier, n = m3.group(1), m3.group(2), m3.group(3)
        return f"db-{family}-{tier}-{n}"

    return None


def _gcp_parse_sql_specs_fallback(instance_type: str) -> Tuple[int, float]:
    """Derive vCPU/memGiB from instance type name when not in the spec table."""
    # db-custom-N-M  (M is memory in MB)
    m = re.match(r"db-custom-(\d+)-(\d+)", instance_type)
    if m:
        return int(m.group(1)), round(int(m.group(2)) / 1024, 2)
    # db-n1-standard-N
    m = re.match(r"db-n1-standard-(\d+)", instance_type)
    if m:
        n = int(m.group(1))
        return n, n * 3.75
    # db-n1-highmem-N
    m = re.match(r"db-n1-highmem-(\d+)", instance_type)
    if m:
        n = int(m.group(1))
        return n, n * 6.5
    # db-n2-standard-N or db-n2d-standard-N
    m = re.match(r"db-n2d?-standard-(\d+)", instance_type)
    if m:
        n = int(m.group(1))
        return n, n * 4.0
    # db-n2-highmem-N or db-n2d-highmem-N
    m = re.match(r"db-n2d?-highmem-(\d+)", instance_type)
    if m:
        n = int(m.group(1))
        return n, n * 8.0
    return 0, 0.0


def fetch_gcp_databases(
    engines: Set[str],
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch GCP Cloud SQL pricing for the given engines."""
    credentials = None
    if not api_key:
        logger.info("No GCP_API_KEY — attempting ADC …")
        credentials = _gcp_get_adc_credentials()
        if credentials is None:
            raise RuntimeError(
                "GCP authentication failed: set GCP_API_KEY or configure ADC "
                "(gcloud auth application-default login)."
            )

    session = _gcp_make_session(credentials=credentials)

    # Resolve Cloud SQL service ID — paginate since the API caps pageSize at ~100.
    logger.info("Resolving Cloud SQL service ID …")
    sql_service_id: Optional[str] = None
    svc_params: Dict[str, Any] = {"pageSize": 100}
    if api_key:
        svc_params["key"] = api_key
    while sql_service_id is None:
        services_data = _gcp_get_json(session, f"{_GCP_BILLING_BASE}/services", params=svc_params)
        for svc in services_data.get("services", []):
            display = svc.get("displayName", "").lower()
            if any(display.startswith(prefix) for prefix in _CLOUD_SQL_DISPLAY_PREFIXES):
                sql_service_id = svc["name"].split("/")[-1]
                logger.info(f"Found Cloud SQL service: {svc['displayName']} ({sql_service_id})")
                break
        next_token = services_data.get("nextPageToken")
        if sql_service_id or not next_token:
            break
        svc_params["pageToken"] = next_token

    if sql_service_id is None:
        logger.warning(
            "Cloud SQL service not found in GCP Billing API — using fallback ID %s",
            _CLOUD_SQL_SERVICE_ID_FALLBACK,
        )
        sql_service_id = _CLOUD_SQL_SERVICE_ID_FALLBACK

    logger.info("Fetching Cloud SQL SKUs …")
    all_skus: List[Dict[str, Any]] = list(
        _gcp_paginate_skus(session, sql_service_id, api_key=api_key)
    )
    logger.info(f"Total Cloud SQL SKUs: {len(all_skus)}")

    now_iso = datetime.now(timezone.utc).isoformat()

    # (instance_type, engine) -> { "on_demand", "1yr", "3yr", "regions" }
    pricing_data: Dict[Tuple[str, str], Dict[str, Any]] = {}
    skipped: Dict[str, int] = {"no_instance": 0, "no_engine": 0, "engine_filter": 0, "no_price": 0}

    for sku in all_skus:
        desc = sku.get("description", "")
        usage_type_raw = sku.get("category", {}).get("usageType", "")

        if usage_type_raw == "Commit1Yr":
            usage_type = "1yr"
        elif usage_type_raw == "Commit3Yr":
            usage_type = "3yr"
        elif usage_type_raw == "OnDemand":
            usage_type = "on-demand"
        else:
            continue

        instance_type = _gcp_parse_sql_instance_type(desc)
        if instance_type is None:
            skipped["no_instance"] += 1
            continue

        engine = _normalize_engine(desc, "gcp")
        if engine is None:
            skipped["no_engine"] += 1
            continue
        if engine not in engines:
            skipped["engine_filter"] += 1
            continue

        price = _gcp_extract_unit_price(sku.get("pricingInfo", []))
        if price is None or price <= 0:
            skipped["no_price"] += 1
            continue

        sku_regions = sku.get("serviceRegions", [])
        key = (instance_type, engine)
        if key not in pricing_data:
            pricing_data[key] = {"on_demand": None, "1yr": None, "3yr": None, "regions": []}

        entry = pricing_data[key]
        if usage_type == "on-demand":
            if entry["on_demand"] is None or price < entry["on_demand"]:
                entry["on_demand"] = price
        else:
            if entry[usage_type] is None or price < entry[usage_type]:
                entry[usage_type] = price

        for region in sku_regions:
            if region not in entry["regions"]:
                entry["regions"].append(region)

    logger.info(
        f"Cloud SQL: {len(pricing_data)} (instance, engine) pairs. "
        f"Skipped: {skipped}"
    )

    results: List[Dict[str, Any]] = []
    for (instance_type, engine), entry in pricing_data.items():
        od = entry["on_demand"]
        if od is None or od <= 0:
            continue

        specs = _GCP_SQL_SPECS.get(instance_type)
        if specs:
            vcpu, mem_gib = specs
        else:
            vcpu, mem_gib = _gcp_parse_sql_specs_fallback(instance_type)

        # Family: drop the trailing size segment
        parts = instance_type.rsplit("-", 1)
        family = parts[0] if len(parts) > 1 and parts[1].isdigit() else instance_type

        raw_commitments = []
        for term_key in ("1yr", "3yr"):
            if entry.get(term_key) is not None:
                raw_commitments.append({
                    "term": term_key,
                    "payment": "flexible",
                    "product": "cud",
                    "priceUSD_hourly": entry[term_key],
                    "upfront_usd": 0,
                })

        results.append({
            "provider": "gcp",
            "type": "rds-instance",
            "instanceType": instance_type,
            "family": family,
            "vCPU": vcpu,
            "memoryGiB": mem_gib,
            "priceUSD_hourly": round(od, 6),
            "priceUSD_monthly": round(od * HOURS_PER_MONTH, 4),
            "engine": engine,
            "engineVersion": None,
            "deploymentOption": "single-az",
            "licenseModel": "included",
            "commitments": normalize_commitments(raw_commitments, od),
            "regions": sorted(entry["regions"]),
            "source": "gcp_billing_catalog_api",
            "lastUpdated": now_iso,
            "pricingModel": "on-demand",
        })

    logger.info(f"GCP Cloud SQL: {len(results)} instances with on-demand pricing")
    return results


# ---------------------------------------------------------------------------
# Azure Database helpers
# ---------------------------------------------------------------------------
_AZURE_RETAIL_URL = "https://prices.azure.com/api/retail/prices"
_AZURE_API_VERSION = "2023-01-01-preview"
_AZURE_TERM_MAP = {"1 Year": "1yr", "3 Years": "3yr"}


def _azure_page_db_items(service_name: str, price_type: str) -> List[Dict[str, Any]]:
    """Page through the Azure Retail API for a specific DB service and priceType."""
    params = {
        "$filter": f"serviceName eq '{service_name}' and priceType eq '{price_type}'",
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
            logger.warning(f"  [{service_name} page {page}] Request error: {exc}")
            break

        body = resp.json()
        items.extend(body.get("Items", []) if isinstance(body, dict) else body)
        url = body.get("NextPageLink") if isinstance(body, dict) else None
        if page % 5 == 0:
            logger.info(f"  {service_name} {price_type}: {len(items)} records (page {page})")

    logger.info(f"  {service_name} {price_type}: {len(items)} total records")
    return items


def _azure_parse_db_sku_specs(sku_name: str) -> Tuple[Optional[int], Optional[float]]:
    """
    Parse (vCPU, memGiB) from Azure Database armSkuName values.

    Handled formats:
      GP_Gen5_4                — General Purpose, Gen5, 4 vCPUs
      MO_Gen5_8                — Memory Optimized, Gen5, 8 vCPUs
      GP_Standard_D4ds_v4      — Flexible Server with embedded VM SKU
      Standard_D4s_v3          — Some Azure SQL SKUs
    """
    if not sku_name:
        return None, None

    # Tier_Gen5_N pattern
    m = re.match(r"^(GP|MO|BC|B)_Gen5_(\d+)$", sku_name, re.I)
    if m:
        tier = m.group(1).upper()
        vcpu = int(m.group(2))
        mem_ratio = {"GP": 5.1, "MO": 10.2, "BC": 5.1, "B": 2.0}.get(tier, 5.1)
        return vcpu, round(vcpu * mem_ratio, 1)

    # Flexible Server: Tier_Standard_DXds_vN
    m = re.match(r"^(GP|MO|BC|B|Burstable)_Standard_([A-Z]+)(\d+)[a-z_]*(?:_v\d+)?$", sku_name, re.I)
    if m:
        tier = m.group(1).upper()
        n = int(m.group(3))
        mem_ratio = {"GP": 4.0, "MO": 8.0, "BC": 4.0, "B": 2.0, "BURSTABLE": 2.0}.get(tier, 4.0)
        return n, round(n * mem_ratio, 1)

    # Plain Standard_DXs_vY (used by some Azure SQL SKUs)
    m = re.match(r"^Standard_([A-Z]+)(\d+)(?:s?_v\d+)?$", sku_name, re.I)
    if m:
        letter = m.group(1).upper()
        n = int(m.group(2))
        mem = n * 8.0 if "E" in letter else n * 4.0
        return n, mem

    return None, None


def _azure_parse_db_item(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], Optional[float]]:
    """
    Return (instance_type, vcpu, mem_gib) for an Azure DB pricing item.

    Tries armSkuName with the old regex first. Falls back to parsing vCPU
    from skuName ("N vCore") and memory ratio from productName tier keyword,
    which covers the Flexible Server format introduced in 2024-2025.
    """
    arm_sku = (item.get("armSkuName") or "").strip()
    sku_display = (item.get("skuName") or "").strip()
    product_name = (item.get("productName") or "").strip()

    # Try old-format armSkuName (GP_Gen5_4, GP_Standard_D4ds_v4)
    vcpu, mem_gib = _azure_parse_db_sku_specs(arm_sku)
    if vcpu is not None:
        return arm_sku, vcpu, mem_gib

    # New format: vCPU comes from skuName "N vCore" / "N vCores"
    m = re.match(r"^(\d+)\s+vcores?$", sku_display.lower())
    if not m:
        return None, None, None

    vcpu = int(m.group(1))
    product_lower = product_name.lower()

    if "memory" in product_lower:
        mem_ratio = 8.0
    elif "burstable" in product_lower:
        mem_ratio = 2.0
    else:
        mem_ratio = 4.0  # General Purpose default

    mem_gib = round(vcpu * mem_ratio, 1)

    # Build a stable instance_type slug from armSkuName or productName + vcpu
    if arm_sku:
        instance_type = re.sub(r"[^a-z0-9]+", "-", arm_sku.lower()).strip("-")
    else:
        slug = re.sub(r"azure\s+database\s+for\s+\w+\s*", "", product_lower)
        slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
        instance_type = f"{slug}-{vcpu}vcpu"

    return instance_type, vcpu, mem_gib


def _azure_db_family(sku_name: str) -> str:
    """'GP_Gen5_4' → 'gp-gen5', 'MO_Standard_D8ds_v4' → 'mo-standard'"""
    if not sku_name:
        return "unknown"
    parts = sku_name.split("_")
    if len(parts) >= 2:
        return f"{parts[0].lower()}-{parts[1].lower()}"
    return sku_name.lower()


def fetch_azure_databases(engines: Set[str]) -> List[Dict[str, Any]]:
    """Fetch Azure managed database pricing for the given engines."""
    now_iso = datetime.now(timezone.utc).isoformat()

    # Only query services whose engine falls within the requested filter
    services_to_fetch = [
        svc for svc in AZURE_DB_SERVICE_NAMES
        if _normalize_engine(svc, "azure") in engines
    ]
    if not services_to_fetch:
        logger.info("No Azure DB services match the engine filter")
        return []
    logger.info(f"Azure services: {services_to_fetch}")

    # (sku_name, region, engine) → record
    consumption_groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for svc_name in services_to_fetch:
        engine = _normalize_engine(svc_name, "azure")
        assert engine is not None
        logger.info(f"Fetching {svc_name} Consumption rows …")
        for item in _azure_page_db_items(svc_name, "Consumption"):
            region = item.get("armRegionName", "") or ""
            price_type = item.get("priceType", "")
            sku_display = item.get("skuName", "") or ""

            if price_type == "DevTestConsumption":
                continue
            if "Spot" in sku_display or "Low Priority" in sku_display:
                continue

            sku_name, vcpu, mem_gib = _azure_parse_db_item(item)
            if vcpu is None:
                continue

            hourly = float(item.get("unitPrice", 0) or 0)
            key = (sku_name, region, engine)

            if key not in consumption_groups:
                consumption_groups[key] = {
                    "provider": "azure",
                    "type": "rds-instance",
                    "instanceType": sku_name,
                    "family": _azure_db_family(sku_name),
                    "vCPU": vcpu,
                    "memoryGiB": float(mem_gib),
                    "priceUSD_hourly": hourly,
                    "priceUSD_monthly": round(hourly * HOURS_PER_MONTH, 4),
                    "engine": engine,
                    "engineVersion": None,
                    "deploymentOption": "single-az",
                    "licenseModel": "included",
                    "commitments": [],
                    "regions": [region] if region else [],
                    "regionPricing": {region: hourly} if region else {},
                    "source": "Azure Retail Prices API",
                    "lastUpdated": now_iso,
                    "pricingModel": "on-demand",
                }
            else:
                rec = consumption_groups[key]
                if hourly > 0 and (rec["priceUSD_hourly"] == 0 or hourly < rec["priceUSD_hourly"]):
                    rec["priceUSD_hourly"] = hourly
                    rec["priceUSD_monthly"] = round(hourly * HOURS_PER_MONTH, 4)

    logger.info(f"Azure consumption groups: {len(consumption_groups)}")

    # Merge Reservation rows as commitments
    matched = 0
    unmatched = 0
    for svc_name in services_to_fetch:
        engine = _normalize_engine(svc_name, "azure")
        assert engine is not None
        logger.info(f"Fetching {svc_name} Reservation rows …")
        for item in _azure_page_db_items(svc_name, "Reservation"):
            region = item.get("armRegionName", "") or ""
            reservation_term = item.get("reservationTerm", "") or ""

            sku_name, _, _ = _azure_parse_db_item(item)
            if not sku_name or not region:
                unmatched += 1
                continue

            term_key = _AZURE_TERM_MAP.get(reservation_term)
            if term_key is None:
                unmatched += 1
                continue

            base = consumption_groups.get((sku_name, region, engine))
            if base is None:
                unmatched += 1
                continue

            term_hours = 8760 if term_key == "1yr" else 26280
            total_cost = float(item.get("unitPrice", 0) or 0)
            amortised = round(total_cost / term_hours, 8) if term_hours > 0 else 0.0

            normalised = normalize_commitments(
                [{"term": term_key, "payment": "all-upfront", "product": "reserved",
                  "priceUSD_hourly": amortised}],
                base["priceUSD_hourly"],
            )
            existing_keys = {(c["term"], c["payment"]) for c in base["commitments"]}
            for nc in normalised:
                if (nc["term"], nc["payment"]) not in existing_keys:
                    base["commitments"].append(nc)
                    existing_keys.add((nc["term"], nc["payment"]))
            matched += 1

    logger.info(f"Azure reservations: {matched} matched, {unmatched} unmatched/skipped")

    # Collapse per-region into per-(SKU, engine) records
    CANONICAL_REGION = "eastus"
    sku_engine_map: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
    for (sku_name, region, engine), inst in consumption_groups.items():
        sku_engine_map.setdefault((sku_name, engine), {})[region] = inst

    results: List[Dict[str, Any]] = []
    for (sku_name, engine), region_data in sku_engine_map.items():
        canonical = region_data.get(CANONICAL_REGION) or next(iter(region_data.values()))
        agg = {
            **canonical,
            "regions": [],
            "commitments": list(canonical.get("commitments", [])),
            "regionPricing": {},
        }
        seen: set = set()
        for region, inst in region_data.items():
            od = inst["priceUSD_hourly"]
            if od > 0:
                agg["regionPricing"][region] = od
            if region not in seen:
                agg["regions"].append(region)
                seen.add(region)

        if agg["vCPU"] and agg["memoryGiB"] and (agg["priceUSD_hourly"] > 0 or agg["commitments"]):
            results.append(agg)

    logger.info(f"Azure DB: {len(results)} unique SKU configs")
    return results


# ---------------------------------------------------------------------------
# Orchestrator entry point
# ---------------------------------------------------------------------------

def fetch_data(provider: str) -> List[Dict[str, Any]]:
    """Dispatcher called by the extras orchestrator."""
    if provider == "aws":
        return fetch_aws_databases(engines=DEFAULT_ENGINES)
    if provider == "gcp":
        api_key = os.environ.get("GCP_API_KEY", "").strip() or None
        if not api_key:
            env_path = _REPO_ROOT / ".env"
            if env_path.exists():
                with open(env_path) as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith("GCP_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip('"').strip("'") or None
                            break
        return fetch_gcp_databases(engines=DEFAULT_ENGINES, api_key=api_key)
    if provider == "azure":
        return fetch_azure_databases(engines=DEFAULT_ENGINES)
    raise ValueError(f"Unsupported provider: {provider!r}")


# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch managed database pricing for AWS RDS, GCP Cloud SQL, and Azure "
            "Database services. Outputs sidecar *.databases.raw.json files."
        )
    )
    parser.add_argument(
        "--provider",
        choices=["aws", "gcp", "azure"],
        help="Fetch only this provider (default: all three).",
    )
    parser.add_argument(
        "--engine",
        action="append",
        dest="engines",
        choices=sorted(ALL_ENGINES),
        metavar="ENGINE",
        help=(
            "Engine to include: mysql, postgres, aurora-mysql, aurora-postgres, "
            "mariadb, sqlserver, oracle. Repeat for multiple. "
            "Default: all except sqlserver and oracle."
        ),
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        metavar="REGION",
        help="AWS region codes to fetch (default: all standard commercial regions).",
    )
    parser.add_argument(
        "--output-dir",
        default="data/providers",
        metavar="DIR",
        help="Directory for output files (default: data/providers).",
    )
    args = parser.parse_args(argv)

    engines: Set[str] = set(args.engines) if args.engines else DEFAULT_ENGINES
    providers = [args.provider] if args.provider else ["aws", "gcp", "azure"]
    output_dir = _REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Engines: {sorted(engines)}")
    logger.info(f"Providers: {providers}")

    summary: Dict[str, int] = {}

    if "aws" in providers:
        logger.info("=== AWS RDS ===")
        try:
            records = fetch_aws_databases(
                engines=engines,
                regions=args.regions or None,
            )
            out = output_dir / "aws.databases.raw.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
            logger.info(f"Wrote {len(records)} records to {out}")
            summary["aws"] = len(records)
        except Exception as exc:
            logger.error(f"AWS RDS fetch failed: {exc}")
            summary["aws"] = 0

    if "gcp" in providers:
        logger.info("=== GCP Cloud SQL ===")
        api_key = os.environ.get("GCP_API_KEY", "").strip() or None
        if not api_key:
            env_path = _REPO_ROOT / ".env"
            if env_path.exists():
                with open(env_path) as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith("GCP_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip('"').strip("'") or None
                            break
        try:
            records = fetch_gcp_databases(engines=engines, api_key=api_key)
            out = output_dir / "gcp.databases.raw.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
            logger.info(f"Wrote {len(records)} records to {out}")
            summary["gcp"] = len(records)
        except Exception as exc:
            logger.error(f"GCP Cloud SQL fetch failed: {exc}")
            summary["gcp"] = 0

    if "azure" in providers:
        logger.info("=== Azure Database ===")
        try:
            records = fetch_azure_databases(engines=engines)
            out = output_dir / "azure.databases.raw.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
            logger.info(f"Wrote {len(records)} records to {out}")
            summary["azure"] = len(records)
        except Exception as exc:
            logger.error(f"Azure DB fetch failed: {exc}")
            summary["azure"] = 0

    print("\n=== Summary ===")
    for provider, count in summary.items():
        print(f"  {provider}: {count} records")

    return 0


if __name__ == "__main__":
    sys.exit(main())
