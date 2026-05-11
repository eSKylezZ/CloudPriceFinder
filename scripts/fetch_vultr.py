#!/usr/bin/env python3
"""
Vultr Cloud Fetcher for CloudPriceFinder v3.

Uses the public Vultr API (no authentication required):
    https://api.vultr.com/v2/plans
    https://api.vultr.com/v2/plans-metal
    https://api.vultr.com/v2/regions

Commitment pricing note:
    Vultr does not publish per-plan commitment or reserved pricing.
    On-demand (monthly billing) is the only publicly available pricing model.
    This fetcher ships on-demand-only pricing with an empty commitments list.

Usage:
    python scripts/fetch_vultr.py [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# Repo root import setup
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.data_validator import validate_instance_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_vultr")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VULTR_PLANS_URL = "https://api.vultr.com/v2/plans"
VULTR_PLANS_METAL_URL = "https://api.vultr.com/v2/plans-metal"
VULTR_REGIONS_URL = "https://api.vultr.com/v2/regions"
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_BACKOFF = 5

HOURS_PER_MONTH = 730.44

FAMILY_MAP: Dict[str, str] = {
    "vc2": "cloud-compute",
    "vhf": "high-frequency",
    "vhp": "high-performance",
    "vbm": "bare-metal",
    "vcg": "cloud-gpu",
    "voc": "optimized-cloud",  # Vultr Optimized Cloud (AMD/Intel compute, memory, storage variants)
    "vdm": "dedicated-gpu",    # Vultr Dedicated GPU Metal (bare metal + discrete GPU)
    "vx1": "extended-cloud",   # Vultr Extended Cloud (AMD, block storage)
}

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
            logger.warning(f"Attempt {attempt} failed for {url}: {exc}. Retrying in {RETRY_BACKOFF}s...")
            time.sleep(RETRY_BACKOFF)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


def _fetch_all_pages(
    session: requests.Session,
    url: str,
    result_key: str,
) -> List[Dict[str, Any]]:
    """Fetch all pages from a cursor-paginated Vultr endpoint."""
    results: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    page = 0
    while True:
        page += 1
        params: Dict[str, str] = {"per_page": "500"}
        if cursor:
            params["cursor"] = cursor
        data = _get_json(session, url, params)
        items: List[Dict[str, Any]] = data.get(result_key, [])
        results.extend(items)
        cursor = (data.get("meta", {}).get("links", {}).get("next") or "").strip() or None
        logger.info(f"  {url} page {page}: {len(items)} items (total so far: {len(results)})")
        if not cursor or not items:
            break
    return results


# ---------------------------------------------------------------------------
# GPU type inference (for plans that lack a gpu_type field)
# ---------------------------------------------------------------------------

_GPU_ID_MAP = {
    "a100": "NVIDIA A100",
    "a40":  "NVIDIA A40",
    "a16":  "NVIDIA A16",
    "l40s": "NVIDIA L40S",
    "l40":  "NVIDIA L40",
    "h100": "NVIDIA H100",
    "v100": "NVIDIA V100",
    "t4":   "NVIDIA T4",
}


def _infer_gpu_type(plan_id: str) -> Optional[str]:
    """Infer GPU model from plan ID substring (e.g. 'vcg-a16-...' → 'NVIDIA A16')."""
    lower = plan_id.lower()
    for key, label in _GPU_ID_MAP.items():
        if f"-{key}-" in lower or lower.endswith(f"-{key}"):
            return label
    return None


# ---------------------------------------------------------------------------
# Data building
# ---------------------------------------------------------------------------

def _build_location_details(
    region_codes: List[str],
    region_lookup: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    details = []
    for code in region_codes:
        r = region_lookup.get(code)
        if r is None:
            details.append({"code": code, "city": code, "country": "Unknown", "region": code})
        else:
            details.append({
                "code": r["id"],
                "city": r["city"],
                "country": r["country"],
                "continent": r.get("continent", ""),
                "region": r["id"],
            })
    return details


def _build_instance(
    plan: Dict[str, Any],
    region_lookup: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    plan_type = plan.get("type", "vc2")
    family = FAMILY_MAP.get(plan_type, plan_type)

    monthly_cost = float(plan.get("monthly_cost", 0))
    hourly = round(monthly_cost / HOURS_PER_MONTH, 6)
    memory_gib = plan["ram"] / 1024
    region_codes: List[str] = plan.get("locations", [])

    desc_parts = [f"Vultr {plan['id']}"]
    if plan.get("vcpu_count"):
        desc_parts.append(f"{plan['vcpu_count']} vCPU")
    desc_parts.append(f"{memory_gib:.0f} GiB RAM")
    if plan.get("disk"):
        desc_parts.append(f"{plan['disk']} GB disk")

    instance: Dict[str, Any] = {
        "provider": "vultr",
        "type": "cloud-server",
        "instanceType": plan["id"],
        "vCPU": plan["vcpu_count"],
        "memoryGiB": memory_gib,
        "diskSizeGB": plan.get("disk", 0),
        "priceUSD_hourly": hourly,
        "priceUSD_monthly": monthly_cost,
        "family": family,
        "regions": region_codes,
        "locationDetails": _build_location_details(region_codes, region_lookup),
        "commitments": [],
        "source": "vultr_public_api",
        "description": " — ".join(desc_parts),
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "raw": dict(plan),
    }

    if plan_type in ("vcg", "vdm"):
        gpu_type = plan.get("gpu_type") or _infer_gpu_type(plan["id"])
        if gpu_type:
            vram_gib = plan.get("gpu_vram_mb", 0) / 1024
            if not vram_gib:
                # vdm plans encode total VRAM in the plan ID as e.g. "256vram"
                import re as _re
                m = _re.search(r"(\d+)vram", plan["id"])
                if m:
                    vram_gib = int(m.group(1))
            instance["gpu"] = {"count": 1, "type": gpu_type, "memoryGiB": vram_gib}

    return instance


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

class VultrFetcher:
    def __init__(self) -> None:
        self.session = _make_session()

    def _fetch_regions(self) -> Dict[str, Dict[str, Any]]:
        """Fetch all regions and return a lookup dict keyed by region code."""
        logger.info("Fetching Vultr regions...")
        regions = _fetch_all_pages(self.session, VULTR_REGIONS_URL, "regions")
        logger.info(f"Fetched {len(regions)} Vultr regions")
        return {r["id"]: r for r in regions}

    def _fetch_plans(self) -> List[Dict[str, Any]]:
        """Fetch all compute plans (cursor-paginated)."""
        logger.info("Fetching Vultr compute plans (all pages)...")
        plans = _fetch_all_pages(self.session, VULTR_PLANS_URL, "plans")
        logger.info(f"Fetched {len(plans)} Vultr compute plans total")
        return plans

    def _fetch_plans_metal(self) -> List[Dict[str, Any]]:
        """Fetch bare-metal plans (cursor-paginated)."""
        logger.info("Fetching Vultr bare-metal plans (all pages)...")
        plans = _fetch_all_pages(self.session, VULTR_PLANS_METAL_URL, "plans_metal")
        logger.info(f"Fetched {len(plans)} Vultr bare-metal plans total")
        return plans

    def fetch_all(self) -> List[Dict[str, Any]]:
        """Fetch all Vultr plans and return CloudInstance records."""
        region_lookup = self._fetch_regions()

        plans = self._fetch_plans()
        metal_plans = self._fetch_plans_metal()

        # De-duplicate by plan id; metal endpoint is authoritative for bare-metal
        plan_by_id: Dict[str, Dict[str, Any]] = {}
        for p in plans:
            plan_by_id[p["id"]] = p
        for p in metal_plans:
            plan_by_id[p["id"]] = p

        all_plans = list(plan_by_id.values())
        logger.info(f"Processing {len(all_plans)} unique Vultr plans")

        instances: List[Dict[str, Any]] = []
        for plan in all_plans:
            monthly_cost = plan.get("monthly_cost", 0)
            vcpu = plan.get("vcpu_count", 0)
            if monthly_cost <= 0 or vcpu <= 0:
                logger.debug(f"Skipping plan {plan.get('id')}: no price or no vCPU")
                continue

            instances.append(_build_instance(plan, region_lookup))

        logger.info(f"Built {len(instances)} Vultr instances")
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
    parser = argparse.ArgumentParser(description="Vultr cloud pricing fetcher (CloudPriceFinder v3)")
    parser.add_argument(
        "--output",
        default="data/providers/vultr.raw.json",
        help="Output JSON file path (default: data/providers/vultr.raw.json)",
    )
    args = parser.parse_args(argv)

    logger.info("=== Vultr Fetcher (CloudPriceFinder v3) ===")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    fetcher = VultrFetcher()
    instances = fetcher.fetch_all()

    if not instances:
        logger.error("No instances collected — aborting")
        return 1

    valid = _validate_output(instances)
    logger.info(f"Valid instances: {len(valid)}/{len(instances)}")

    if len(valid) < 50:
        logger.error(f"Too few valid instances ({len(valid)}), expected >=50")
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(valid, f, indent=2, ensure_ascii=False)

    logger.info(f"Wrote {len(valid)} instances to {out_path}")

    by_family: Dict[str, int] = {}
    for inst in valid:
        fam = inst.get("family", "unknown")
        by_family[fam] = by_family.get(fam, 0) + 1

    logger.info("Summary by family:")
    for k, v in sorted(by_family.items()):
        logger.info(f"  {k}: {v}")

    with_gpu = sum(1 for i in valid if i.get("gpu"))
    logger.info(f"GPU instances: {with_gpu}/{len(valid)}")

    return 0


# ---------------------------------------------------------------------------
# Orchestrator compatibility
# ---------------------------------------------------------------------------

def fetch_vultr_data() -> List[Dict[str, Any]]:
    """Entry point for the orchestrator."""
    fetcher = VultrFetcher()
    instances = fetcher.fetch_all()
    return _validate_output(instances)


if __name__ == "__main__":
    sys.exit(main())
