#!/usr/bin/env python3
"""
Vast.ai GPU Marketplace Fetcher for CloudPriceFinder v3.

Captures a real-time snapshot of available GPU offers from the Vast.ai
marketplace for benchmarking context. Prices reflect spot-market conditions
and fluctuate with supply and demand.

API endpoint (public, no auth required):
    https://cloud.vast.ai/api/v0/bundles/

Filtering:
    Only full-GPU offers are included (gpu_frac >= 1.0, rentable == true).
    De-duplicated by (gpu_name, num_gpus, geolocation) keeping the cheapest.

Commitment-pricing note:
    Vast.ai is a peer-to-peer GPU marketplace — there are no commitment tiers.
    dph_total is an on-demand, per-hour USD rate set by individual hosts.

Out of scope for v1 (deferred to v3.1+):
    - Fractional GPU offers (gpu_frac < 1.0)
    - Bid/interruptible pricing
    - Historical price tracking

Usage:
    python scripts/fetch_vast.py [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.parse
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

from scripts.utils.data_validator import validate_instance_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_vast")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAST_API_URL = "https://cloud.vast.ai/api/v0/bundles/"
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_BACKOFF = 5

_MARKETPLACE_NOTE = (
    "Vast.ai is a marketplace; prices fluctuate with supply/demand "
    "and reflect a point-in-time snapshot."
)

# ---------------------------------------------------------------------------
# GPU family classification
# ---------------------------------------------------------------------------

def _derive_family(gpu_name: str) -> str:
    """
    Map a gpu_name string to a CloudPriceFinder family bucket.

    Tier mapping (case-insensitive substring match, first wins):
      H100 / H200 / B100 / B200      -> gpu-h100
      A100                           -> gpu-a100
      L40S / L40 / A6000 / 6000      -> gpu-professional
        (catches RTX A6000, RTX 6000 Ada, RTX PRO 6000 WS/S, Quadro RTX 6000)
      RTX 4090 / RTX 3090            -> gpu-consumer
      everything else                -> gpu-other
    """
    upper = gpu_name.upper()
    if "H100" in upper or "H200" in upper or "B100" in upper or "B200" in upper:
        return "gpu-h100"
    if "A100" in upper:
        return "gpu-a100"
    if "L40" in upper or "A6000" in upper or "6000" in upper:
        return "gpu-professional"
    if "4090" in upper or "3090" in upper:
        return "gpu-consumer"
    return "gpu-other"


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


def _get_json(session: requests.Session, url: str, params: Dict[str, str]) -> Any:
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
# Offer parsing and normalisation
# ---------------------------------------------------------------------------

def _build_instance(offer: Dict[str, Any], timestamp: str) -> Dict[str, Any]:
    """Convert a single Vast.ai offer dict into a v3 CloudInstance record."""
    gpu_name: str = offer["gpu_name"]
    num_gpus: int = int(offer["num_gpus"])
    geolocation: str = offer.get("geolocation") or "unknown"
    dph: float = float(offer["dph_total"])
    cpu_cores: int = int(offer.get("cpu_cores") or 0)
    # Vast.ai API returns cpu_ram and gpu_ram in MiB; convert to GiB
    cpu_ram: float = round(float(offer.get("cpu_ram") or 0) / 1024, 2)
    disk_space: float = float(offer.get("disk_space") or 0)
    gpu_ram: float = round(float(offer.get("gpu_ram") or 0) / 1024, 2)

    instance_type = f"{gpu_name.replace(' ', '_')}x{num_gpus}"
    family = _derive_family(gpu_name)
    monthly = round(dph * 730.44, 4)

    return {
        "provider": "vast",
        "type": "cloud-server",
        "instanceType": instance_type,
        "vCPU": cpu_cores,
        "memoryGiB": cpu_ram,
        "diskSizeGB": disk_space,
        "priceUSD_hourly": dph,
        "priceUSD_monthly": monthly,
        "family": family,
        "architecture": "x86_64",
        "gpu": {
            "count": num_gpus,
            "type": gpu_name,
            "memoryGiB": gpu_ram,
        },
        "regions": [geolocation],
        "locationDetails": [
            {
                "code": geolocation,
                "city": "",
                "country": geolocation,
                "countryCode": geolocation,
                "region": geolocation,
            }
        ],
        "commitments": [],
        "source": "vast_ai_marketplace_api",
        "description": (
            f"Vast.ai marketplace - {num_gpus}x {gpu_name}, "
            f"{cpu_cores} CPU, {cpu_ram} GiB RAM"
        ),
        "lastUpdated": timestamp,
        "raw": {
            "offer_id": offer["id"],
            "reliability": offer.get("reliability"),
            "gpu_frac": float(offer.get("gpu_frac", 1.0)),
            "inet_down": offer.get("inet_down"),
            "inet_up": offer.get("inet_up"),
            "note": _MARKETPLACE_NOTE,
        },
    }


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

class VastFetcher:
    def __init__(self) -> None:
        self.session = _make_session()

    def fetch_all(self) -> List[Dict[str, Any]]:
        """Fetch and de-duplicate Vast.ai GPU marketplace offers."""
        logger.info("Fetching Vast.ai marketplace offers...")

        query = json.dumps(
            {
                "rentable": {"eq": True},
                "order": [["dph_total", "asc"]],
                "limit": 5000,
            },
            separators=(",", ":"),
        )
        params = {"q": query}

        raw = _get_json(self.session, VAST_API_URL, params)
        if not isinstance(raw, dict) or "offers" not in raw:
            raise ValueError(f"Unexpected API response shape: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}")

        offers: List[Dict[str, Any]] = raw["offers"]
        logger.info(f"Received {len(offers)} raw offers from Vast.ai API")

        # Filter: rentable AND full GPU only
        filtered = [
            o for o in offers
            if o.get("rentable") is True and float(o.get("gpu_frac", 0)) >= 1.0
        ]
        logger.info(f"After filtering (rentable + full GPU): {len(filtered)} offers")

        # De-duplicate: keep cheapest offer per (gpu_name, num_gpus, geolocation)
        # Offers are already sorted ascending by dph_total, so first-seen = cheapest.
        seen: Dict[Tuple[str, int, str], bool] = {}
        deduped: List[Dict[str, Any]] = []
        for offer in filtered:
            key: Tuple[str, int, str] = (
                offer.get("gpu_name", ""),
                int(offer.get("num_gpus", 0)),
                offer.get("geolocation") or "unknown",
            )
            if key not in seen:
                seen[key] = True
                deduped.append(offer)

        logger.info(f"After de-duplication: {len(deduped)} unique (gpu, count, geo) combinations")

        timestamp = datetime.now(timezone.utc).isoformat()
        instances = [_build_instance(o, timestamp) for o in deduped]

        logger.info(f"Built {len(instances)} Vast.ai instance records")
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
        description="Vast.ai GPU marketplace fetcher (CloudPriceFinder v3)"
    )
    parser.add_argument(
        "--output",
        default="data/providers/vast.raw.json",
        help="Output JSON file path (default: data/providers/vast.raw.json)",
    )
    args = parser.parse_args(argv)

    logger.info("=== Vast.ai Fetcher (CloudPriceFinder v3) ===")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    fetcher = VastFetcher()
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

    # Summary by family and geolocation
    by_family: Dict[str, int] = {}
    by_geo: Dict[str, int] = {}
    for inst in valid:
        fam = inst.get("family", "unknown")
        by_family[fam] = by_family.get(fam, 0) + 1
        for geo in inst.get("regions", []):
            by_geo[geo] = by_geo.get(geo, 0) + 1

    logger.info("Summary by GPU family:")
    for k, v in sorted(by_family.items()):
        logger.info(f"  {k}: {v}")
    logger.info("Summary by geolocation (top 10):")
    for k, v in sorted(by_geo.items(), key=lambda x: -x[1])[:10]:
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

def fetch_vast_data() -> List[Dict[str, Any]]:
    """Entry point for the orchestrator."""
    fetcher = VastFetcher()
    instances = fetcher.fetch_all()
    return _validate_output(instances)


if __name__ == "__main__":
    sys.exit(main())
