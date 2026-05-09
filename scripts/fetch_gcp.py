#!/usr/bin/env python3
"""
GCP Compute Engine pricing fetcher for CloudPriceFinder v3.

Uses the Cloud Billing Catalog API (public endpoint, API key required).
API key must be supplied via GCP_API_KEY environment variable.

GCP pricing model:
  - CPU and RAM are priced as separate SKUs for most machine families.
  - We reconstruct per-machine-type prices by summing the per-vCPU + per-GiB
    charges from the applicable SKU family.
  - On-demand SKUs have category.usageType == "OnDemand".
  - Committed-use discount SKUs have usageType == "Commit1Yr" or "Commit3Yr".
  - GPU SKUs are separate and have "GPU" in the description.

Out of scope for v1:
  - Sole-tenant node pricing
  - Spot/preemptible pricing (deferred to v3.1+)
  - GPU-only A3/G2/H3 SKUs that are not yet generally available in all regions

Usage:
    GCP_API_KEY=... python scripts/fetch_gcp.py [--output PATH]

Requirements:
  - GCP Cloud Billing API enabled on the GCP project that owns the key.
  - API key: https://console.cloud.google.com/apis/credentials
"""

from __future__ import annotations

import argparse
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
# Path setup — allow running as `python scripts/fetch_gcp.py` from repo root
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.data_normalizer import normalize_commitments
from scripts.utils.data_validator import validate_commitments, validate_instance_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_gcp")

# ---------------------------------------------------------------------------
# GCP Cloud Billing Catalog API
# ---------------------------------------------------------------------------
BILLING_BASE = "https://cloudbilling.googleapis.com/v1"
COMPUTE_SERVICE_NAME = "Compute Engine"
# Resolved dynamically by display name; hard-coded as fallback.
COMPUTE_SERVICE_ID_FALLBACK = "6F81-5844-456A"

# ---------------------------------------------------------------------------
# GCP standard regions — all standard commercial (non-China, non-GovCloud)
# ---------------------------------------------------------------------------
GCP_REGIONS: List[str] = [
    # Americas
    "us-central1",
    "us-east1",
    "us-east4",
    "us-east5",
    "us-south1",
    "us-west1",
    "us-west2",
    "us-west3",
    "us-west4",
    "northamerica-northeast1",
    "northamerica-northeast2",
    "southamerica-east1",
    "southamerica-west1",
    # Europe
    "europe-central2",
    "europe-north1",
    "europe-southwest1",
    "europe-west1",
    "europe-west2",
    "europe-west3",
    "europe-west4",
    "europe-west6",
    "europe-west8",
    "europe-west9",
    "europe-west10",
    "europe-west12",
    # Asia Pacific
    "asia-east1",
    "asia-east2",
    "asia-northeast1",
    "asia-northeast2",
    "asia-northeast3",
    "asia-south1",
    "asia-south2",
    "asia-southeast1",
    "asia-southeast2",
    # Australia
    "australia-southeast1",
    "australia-southeast2",
    # Middle East & Africa
    "me-central1",
    "me-central2",
    "me-west1",
    "africa-south1",
]

# ---------------------------------------------------------------------------
# Machine type specification table.
#
# GCP prices CPU and RAM as separate resource SKUs (e.g. "N2 Instance Core"
# and "N2 Instance Ram"). We need the specs for each concrete machine type
# so we can compute total hourly price = vCPUs * cpu_price + memGiB * ram_price.
#
# Source: https://cloud.google.com/compute/docs/machine-resource
# Format: family_prefix -> list of (machine_type, vcpu, mem_gib, architecture, gpu_count, gpu_type, gpu_mem_gib)
# ---------------------------------------------------------------------------
# architecture: 'x86_64' or 'arm64'
_MACHINE_SPECS: List[Tuple[str, int, float, str, int, str, float]] = [
    # (machine_type, vcpu, mem_gib, arch, gpu_count, gpu_type, gpu_mem_gib)

    # --- E2 general-purpose (x86) ---
    ("e2-micro",       2,   1.0,  "x86_64", 0, "", 0),
    ("e2-small",       2,   2.0,  "x86_64", 0, "", 0),
    ("e2-medium",      2,   4.0,  "x86_64", 0, "", 0),
    ("e2-standard-2",  2,   8.0,  "x86_64", 0, "", 0),
    ("e2-standard-4",  4,  16.0,  "x86_64", 0, "", 0),
    ("e2-standard-8",  8,  32.0,  "x86_64", 0, "", 0),
    ("e2-standard-16", 16, 64.0,  "x86_64", 0, "", 0),
    ("e2-standard-32", 32, 128.0, "x86_64", 0, "", 0),
    ("e2-highmem-2",   2,  16.0,  "x86_64", 0, "", 0),
    ("e2-highmem-4",   4,  32.0,  "x86_64", 0, "", 0),
    ("e2-highmem-8",   8,  64.0,  "x86_64", 0, "", 0),
    ("e2-highmem-16",  16, 128.0, "x86_64", 0, "", 0),
    ("e2-highcpu-2",   2,  2.0,   "x86_64", 0, "", 0),
    ("e2-highcpu-4",   4,  4.0,   "x86_64", 0, "", 0),
    ("e2-highcpu-8",   8,  8.0,   "x86_64", 0, "", 0),
    ("e2-highcpu-16",  16, 16.0,  "x86_64", 0, "", 0),
    ("e2-highcpu-32",  32, 32.0,  "x86_64", 0, "", 0),

    # --- N1 general-purpose (x86) ---
    ("n1-standard-1",  1,   3.75,  "x86_64", 0, "", 0),
    ("n1-standard-2",  2,   7.5,   "x86_64", 0, "", 0),
    ("n1-standard-4",  4,  15.0,   "x86_64", 0, "", 0),
    ("n1-standard-8",  8,  30.0,   "x86_64", 0, "", 0),
    ("n1-standard-16", 16,  60.0,  "x86_64", 0, "", 0),
    ("n1-standard-32", 32, 120.0,  "x86_64", 0, "", 0),
    ("n1-standard-64", 64, 240.0,  "x86_64", 0, "", 0),
    ("n1-standard-96", 96, 360.0,  "x86_64", 0, "", 0),
    ("n1-highmem-2",   2,  13.0,   "x86_64", 0, "", 0),
    ("n1-highmem-4",   4,  26.0,   "x86_64", 0, "", 0),
    ("n1-highmem-8",   8,  52.0,   "x86_64", 0, "", 0),
    ("n1-highmem-16",  16, 104.0,  "x86_64", 0, "", 0),
    ("n1-highmem-32",  32, 208.0,  "x86_64", 0, "", 0),
    ("n1-highmem-64",  64, 416.0,  "x86_64", 0, "", 0),
    ("n1-highmem-96",  96, 624.0,  "x86_64", 0, "", 0),
    ("n1-highcpu-2",   2,   1.8,   "x86_64", 0, "", 0),
    ("n1-highcpu-4",   4,   3.6,   "x86_64", 0, "", 0),
    ("n1-highcpu-8",   8,   7.2,   "x86_64", 0, "", 0),
    ("n1-highcpu-16",  16,  14.4,  "x86_64", 0, "", 0),
    ("n1-highcpu-32",  32,  28.8,  "x86_64", 0, "", 0),
    ("n1-highcpu-64",  64,  57.6,  "x86_64", 0, "", 0),
    ("n1-highcpu-96",  96,  86.4,  "x86_64", 0, "", 0),

    # --- N2 general-purpose (x86, Intel Cascade/Ice Lake) ---
    ("n2-standard-2",   2,   8.0,  "x86_64", 0, "", 0),
    ("n2-standard-4",   4,  16.0,  "x86_64", 0, "", 0),
    ("n2-standard-8",   8,  32.0,  "x86_64", 0, "", 0),
    ("n2-standard-16",  16,  64.0, "x86_64", 0, "", 0),
    ("n2-standard-32",  32, 128.0, "x86_64", 0, "", 0),
    ("n2-standard-48",  48, 192.0, "x86_64", 0, "", 0),
    ("n2-standard-64",  64, 256.0, "x86_64", 0, "", 0),
    ("n2-standard-80",  80, 320.0, "x86_64", 0, "", 0),
    ("n2-standard-96",  96, 384.0, "x86_64", 0, "", 0),
    ("n2-standard-128", 128, 512.0,"x86_64", 0, "", 0),
    ("n2-highmem-2",    2,  16.0,  "x86_64", 0, "", 0),
    ("n2-highmem-4",    4,  32.0,  "x86_64", 0, "", 0),
    ("n2-highmem-8",    8,  64.0,  "x86_64", 0, "", 0),
    ("n2-highmem-16",   16, 128.0, "x86_64", 0, "", 0),
    ("n2-highmem-32",   32, 256.0, "x86_64", 0, "", 0),
    ("n2-highmem-48",   48, 384.0, "x86_64", 0, "", 0),
    ("n2-highmem-64",   64, 512.0, "x86_64", 0, "", 0),
    ("n2-highmem-80",   80, 640.0, "x86_64", 0, "", 0),
    ("n2-highmem-96",   96, 768.0, "x86_64", 0, "", 0),
    ("n2-highcpu-2",    2,   2.0,  "x86_64", 0, "", 0),
    ("n2-highcpu-4",    4,   4.0,  "x86_64", 0, "", 0),
    ("n2-highcpu-8",    8,   8.0,  "x86_64", 0, "", 0),
    ("n2-highcpu-16",   16,  16.0, "x86_64", 0, "", 0),
    ("n2-highcpu-32",   32,  32.0, "x86_64", 0, "", 0),
    ("n2-highcpu-48",   48,  48.0, "x86_64", 0, "", 0),
    ("n2-highcpu-64",   64,  64.0, "x86_64", 0, "", 0),
    ("n2-highcpu-80",   80,  80.0, "x86_64", 0, "", 0),
    ("n2-highcpu-96",   96,  96.0, "x86_64", 0, "", 0),

    # --- N2D general-purpose (x86, AMD EPYC Rome/Milan) ---
    ("n2d-standard-2",   2,   8.0,  "x86_64", 0, "", 0),
    ("n2d-standard-4",   4,  16.0,  "x86_64", 0, "", 0),
    ("n2d-standard-8",   8,  32.0,  "x86_64", 0, "", 0),
    ("n2d-standard-16",  16,  64.0, "x86_64", 0, "", 0),
    ("n2d-standard-32",  32, 128.0, "x86_64", 0, "", 0),
    ("n2d-standard-48",  48, 192.0, "x86_64", 0, "", 0),
    ("n2d-standard-64",  64, 256.0, "x86_64", 0, "", 0),
    ("n2d-standard-80",  80, 320.0, "x86_64", 0, "", 0),
    ("n2d-standard-96",  96, 384.0, "x86_64", 0, "", 0),
    ("n2d-standard-128", 128, 512.0,"x86_64", 0, "", 0),
    ("n2d-standard-224", 224, 896.0,"x86_64", 0, "", 0),
    ("n2d-highmem-2",    2,  16.0,  "x86_64", 0, "", 0),
    ("n2d-highmem-4",    4,  32.0,  "x86_64", 0, "", 0),
    ("n2d-highmem-8",    8,  64.0,  "x86_64", 0, "", 0),
    ("n2d-highmem-16",   16, 128.0, "x86_64", 0, "", 0),
    ("n2d-highmem-32",   32, 256.0, "x86_64", 0, "", 0),
    ("n2d-highmem-48",   48, 384.0, "x86_64", 0, "", 0),
    ("n2d-highmem-64",   64, 512.0, "x86_64", 0, "", 0),
    ("n2d-highmem-96",   96, 768.0, "x86_64", 0, "", 0),
    ("n2d-highcpu-2",    2,   2.0,  "x86_64", 0, "", 0),
    ("n2d-highcpu-4",    4,   4.0,  "x86_64", 0, "", 0),
    ("n2d-highcpu-8",    8,   8.0,  "x86_64", 0, "", 0),
    ("n2d-highcpu-16",   16,  16.0, "x86_64", 0, "", 0),
    ("n2d-highcpu-32",   32,  32.0, "x86_64", 0, "", 0),
    ("n2d-highcpu-48",   48,  48.0, "x86_64", 0, "", 0),
    ("n2d-highcpu-64",   64,  64.0, "x86_64", 0, "", 0),
    ("n2d-highcpu-80",   80,  80.0, "x86_64", 0, "", 0),
    ("n2d-highcpu-96",   96,  96.0, "x86_64", 0, "", 0),
    ("n2d-highcpu-128",  128, 128.0,"x86_64", 0, "", 0),
    ("n2d-highcpu-224",  224, 224.0,"x86_64", 0, "", 0),

    # --- C2 compute-optimized (x86, Intel Cascade Lake) ---
    ("c2-standard-4",  4,  16.0,  "x86_64", 0, "", 0),
    ("c2-standard-8",  8,  32.0,  "x86_64", 0, "", 0),
    ("c2-standard-16", 16, 64.0,  "x86_64", 0, "", 0),
    ("c2-standard-30", 30, 120.0, "x86_64", 0, "", 0),
    ("c2-standard-60", 60, 240.0, "x86_64", 0, "", 0),

    # --- C2D compute-optimized (x86, AMD EPYC Milan) ---
    ("c2d-standard-2",  2,   8.0,  "x86_64", 0, "", 0),
    ("c2d-standard-4",  4,  16.0,  "x86_64", 0, "", 0),
    ("c2d-standard-8",  8,  32.0,  "x86_64", 0, "", 0),
    ("c2d-standard-16", 16,  64.0, "x86_64", 0, "", 0),
    ("c2d-standard-32", 32, 128.0, "x86_64", 0, "", 0),
    ("c2d-standard-56", 56, 224.0, "x86_64", 0, "", 0),
    ("c2d-standard-112",112, 448.0,"x86_64", 0, "", 0),
    ("c2d-highmem-4",   4,  32.0,  "x86_64", 0, "", 0),
    ("c2d-highmem-8",   8,  64.0,  "x86_64", 0, "", 0),
    ("c2d-highmem-16",  16, 128.0, "x86_64", 0, "", 0),
    ("c2d-highmem-32",  32, 256.0, "x86_64", 0, "", 0),
    ("c2d-highmem-56",  56, 448.0, "x86_64", 0, "", 0),
    ("c2d-highmem-112", 112, 896.0,"x86_64", 0, "", 0),
    ("c2d-highcpu-2",   2,   2.0,  "x86_64", 0, "", 0),
    ("c2d-highcpu-4",   4,   4.0,  "x86_64", 0, "", 0),
    ("c2d-highcpu-8",   8,   8.0,  "x86_64", 0, "", 0),
    ("c2d-highcpu-16",  16,  16.0, "x86_64", 0, "", 0),
    ("c2d-highcpu-32",  32,  32.0, "x86_64", 0, "", 0),
    ("c2d-highcpu-56",  56,  56.0, "x86_64", 0, "", 0),
    ("c2d-highcpu-112", 112, 112.0,"x86_64", 0, "", 0),

    # --- C3 compute-optimized (x86, Intel Sapphire Rapids) ---
    ("c3-standard-4",   4,  16.0,  "x86_64", 0, "", 0),
    ("c3-standard-8",   8,  32.0,  "x86_64", 0, "", 0),
    ("c3-standard-22",  22, 88.0,  "x86_64", 0, "", 0),
    ("c3-standard-44",  44, 176.0, "x86_64", 0, "", 0),
    ("c3-standard-88",  88, 352.0, "x86_64", 0, "", 0),
    ("c3-standard-176", 176,704.0, "x86_64", 0, "", 0),
    ("c3-highmem-4",    4,  32.0,  "x86_64", 0, "", 0),
    ("c3-highmem-8",    8,  64.0,  "x86_64", 0, "", 0),
    ("c3-highmem-22",   22, 176.0, "x86_64", 0, "", 0),
    ("c3-highmem-44",   44, 352.0, "x86_64", 0, "", 0),
    ("c3-highmem-88",   88, 704.0, "x86_64", 0, "", 0),
    ("c3-highmem-176",  176,1408.0,"x86_64", 0, "", 0),
    ("c3-highcpu-4",    4,   4.0,  "x86_64", 0, "", 0),
    ("c3-highcpu-8",    8,   8.0,  "x86_64", 0, "", 0),
    ("c3-highcpu-22",   22,  22.0, "x86_64", 0, "", 0),
    ("c3-highcpu-44",   44,  44.0, "x86_64", 0, "", 0),
    ("c3-highcpu-88",   88,  88.0, "x86_64", 0, "", 0),
    ("c3-highcpu-176",  176, 176.0,"x86_64", 0, "", 0),

    # --- C3D compute-optimized (x86, AMD EPYC Genoa) ---
    ("c3d-standard-4",   4,  16.0,  "x86_64", 0, "", 0),
    ("c3d-standard-8",   8,  32.0,  "x86_64", 0, "", 0),
    ("c3d-standard-16",  16,  64.0, "x86_64", 0, "", 0),
    ("c3d-standard-30",  30, 120.0, "x86_64", 0, "", 0),
    ("c3d-standard-60",  60, 240.0, "x86_64", 0, "", 0),
    ("c3d-standard-90",  90, 360.0, "x86_64", 0, "", 0),
    ("c3d-standard-180", 180,720.0, "x86_64", 0, "", 0),
    ("c3d-standard-360", 360,1440.0,"x86_64", 0, "", 0),
    ("c3d-highmem-4",    4,  32.0,  "x86_64", 0, "", 0),
    ("c3d-highmem-8",    8,  64.0,  "x86_64", 0, "", 0),
    ("c3d-highmem-16",   16, 128.0, "x86_64", 0, "", 0),
    ("c3d-highmem-30",   30, 240.0, "x86_64", 0, "", 0),
    ("c3d-highmem-60",   60, 480.0, "x86_64", 0, "", 0),
    ("c3d-highmem-90",   90, 720.0, "x86_64", 0, "", 0),
    ("c3d-highmem-180",  180,1440.0,"x86_64", 0, "", 0),
    ("c3d-highmem-360",  360,2880.0,"x86_64", 0, "", 0),
    ("c3d-highcpu-4",    4,   4.0,  "x86_64", 0, "", 0),
    ("c3d-highcpu-8",    8,   8.0,  "x86_64", 0, "", 0),
    ("c3d-highcpu-16",   16,  16.0, "x86_64", 0, "", 0),
    ("c3d-highcpu-30",   30,  30.0, "x86_64", 0, "", 0),
    ("c3d-highcpu-60",   60,  60.0, "x86_64", 0, "", 0),
    ("c3d-highcpu-90",   90,  90.0, "x86_64", 0, "", 0),
    ("c3d-highcpu-180",  180, 180.0,"x86_64", 0, "", 0),
    ("c3d-highcpu-360",  360, 360.0,"x86_64", 0, "", 0),

    # --- M1 memory-optimized (x86, Intel Skylake) ---
    ("m1-ultramem-40",  40,  961.0, "x86_64", 0, "", 0),
    ("m1-ultramem-80",  80, 1922.0, "x86_64", 0, "", 0),
    ("m1-ultramem-160", 160,3844.0, "x86_64", 0, "", 0),
    ("m1-megamem-96",   96, 1433.6, "x86_64", 0, "", 0),

    # --- M2 memory-optimized (x86, Intel Cascade Lake) ---
    ("m2-ultramem-208",  208, 5888.0, "x86_64", 0, "", 0),
    ("m2-ultramem-416",  416,11776.0, "x86_64", 0, "", 0),
    ("m2-megamem-416",   416, 5888.0, "x86_64", 0, "", 0),
    ("m2-hypermem-416",  416, 8832.0, "x86_64", 0, "", 0),

    # --- M3 memory-optimized (x86, Intel Sapphire Rapids) ---
    ("m3-ultramem-32",  32, 976.0,  "x86_64", 0, "", 0),
    ("m3-ultramem-64",  64, 1952.0, "x86_64", 0, "", 0),
    ("m3-ultramem-128", 128,3904.0, "x86_64", 0, "", 0),
    ("m3-megamem-64",   64,  976.0, "x86_64", 0, "", 0),
    ("m3-megamem-128",  128,1952.0, "x86_64", 0, "", 0),

    # --- T2D scale-out (AMD EPYC Milan, x86) ---
    ("t2d-standard-1",  1,   4.0,  "x86_64", 0, "", 0),
    ("t2d-standard-2",  2,   8.0,  "x86_64", 0, "", 0),
    ("t2d-standard-4",  4,  16.0,  "x86_64", 0, "", 0),
    ("t2d-standard-8",  8,  32.0,  "x86_64", 0, "", 0),
    ("t2d-standard-16", 16,  64.0, "x86_64", 0, "", 0),
    ("t2d-standard-32", 32, 128.0, "x86_64", 0, "", 0),
    ("t2d-standard-48", 48, 192.0, "x86_64", 0, "", 0),
    ("t2d-standard-60", 60, 240.0, "x86_64", 0, "", 0),

    # --- T2A scale-out (Ampere Altra, ARM64) ---
    ("t2a-standard-1",  1,   4.0,  "arm64", 0, "", 0),
    ("t2a-standard-2",  2,   8.0,  "arm64", 0, "", 0),
    ("t2a-standard-4",  4,  16.0,  "arm64", 0, "", 0),
    ("t2a-standard-8",  8,  32.0,  "arm64", 0, "", 0),
    ("t2a-standard-16", 16,  64.0, "arm64", 0, "", 0),
    ("t2a-standard-32", 32, 128.0, "arm64", 0, "", 0),
    ("t2a-standard-48", 48, 192.0, "arm64", 0, "", 0),

    # --- H3 high-performance (x86, Intel Sapphire Rapids) ---
    ("h3-standard-88",  88, 352.0, "x86_64", 0, "", 0),

    # --- Z3 storage-optimized (x86, Intel Sapphire Rapids) ---
    ("z3-standard-88",  88, 704.0, "x86_64", 0, "", 0),
    ("z3-highmem-88",   88,1408.0, "x86_64", 0, "", 0),

    # --- A2 GPU (NVIDIA A100) ---
    ("a2-highgpu-1g",   12, 85.0,  "x86_64", 1,  "A100",  40.0),
    ("a2-highgpu-2g",   24, 170.0, "x86_64", 2,  "A100",  40.0),
    ("a2-highgpu-4g",   48, 340.0, "x86_64", 4,  "A100",  40.0),
    ("a2-highgpu-8g",   96, 680.0, "x86_64", 8,  "A100",  40.0),
    ("a2-megagpu-16g",  96, 1360.0,"x86_64", 16, "A100",  40.0),
    ("a2-ultragpu-1g",  12, 170.0, "x86_64", 1,  "A100",  80.0),
    ("a2-ultragpu-2g",  24, 340.0, "x86_64", 2,  "A100",  80.0),
    ("a2-ultragpu-4g",  48, 680.0, "x86_64", 4,  "A100",  80.0),
    ("a2-ultragpu-8g",  96,1360.0, "x86_64", 8,  "A100",  80.0),

    # --- A3 GPU (NVIDIA H100) ---
    ("a3-highgpu-8g",   208,1872.0,"x86_64", 8,  "H100",  80.0),
    ("a3-megagpu-8g",   208,1872.0,"x86_64", 8,  "H100",  80.0),
    ("a3-edgegpu-8g",   104, 936.0,"x86_64", 8,  "H100",  80.0),
    ("a3-ultragpu-8g",  208,2952.0,"x86_64", 8,  "H100", 141.0),

    # --- G2 GPU (NVIDIA L4) ---
    ("g2-standard-4",   4,  16.0,  "x86_64", 1,  "L4",    24.0),
    ("g2-standard-8",   8,  32.0,  "x86_64", 1,  "L4",    24.0),
    ("g2-standard-12",  12,  48.0, "x86_64", 1,  "L4",    24.0),
    ("g2-standard-16",  16,  64.0, "x86_64", 1,  "L4",    24.0),
    ("g2-standard-24",  24,  96.0, "x86_64", 2,  "L4",    24.0),
    ("g2-standard-32",  32, 128.0, "x86_64", 1,  "L4",    24.0),
    ("g2-standard-48",  48, 192.0, "x86_64", 4,  "L4",    24.0),
    ("g2-standard-96",  96, 384.0, "x86_64", 8,  "L4",    24.0),
]

# Build a fast lookup dict: machine_type -> spec tuple
_SPEC_LOOKUP: Dict[str, Tuple[int, float, str, int, str, float]] = {
    m: (vcpu, mem, arch, gpu_cnt, gpu_t, gpu_mem)
    for m, vcpu, mem, arch, gpu_cnt, gpu_t, gpu_mem in _MACHINE_SPECS
}

# ---------------------------------------------------------------------------
# GPU model info (for the 'gpu' field on the CloudInstance schema)
# ---------------------------------------------------------------------------
_GPU_MODELS: Dict[str, Dict[str, Any]] = {
    "A100": {"type": "NVIDIA A100", "memoryGiB": 40},
    "A100-80": {"type": "NVIDIA A100 80GB", "memoryGiB": 80},
    "H100": {"type": "NVIDIA H100", "memoryGiB": 80},
    "L4":   {"type": "NVIDIA L4",   "memoryGiB": 24},
    "T4":   {"type": "NVIDIA T4",   "memoryGiB": 16},
    "V100": {"type": "NVIDIA V100", "memoryGiB": 16},
    "P100": {"type": "NVIDIA P100", "memoryGiB": 16},
    "P4":   {"type": "NVIDIA P4",   "memoryGiB": 8},
    "K80":  {"type": "NVIDIA K80",  "memoryGiB": 12},
}

# ---------------------------------------------------------------------------
# Mapping from machine-type prefix to GCP SKU "resource group" / description
# keywords used in the Billing API.
#
# The Billing API describes SKUs with descriptions like:
#   "N2 Instance Core running in Americas"
#   "N2 Instance Ram running in Americas"
#   "Spot Preemptible N2 Instance Core running in Americas"
#   "N2 Committed Use Discount CUDs"  (per CPU)
#   "N2 Committed Use Discount CUDs"  (per GiB)
# We match by the prefix token that appears at the start of the description.
#
# Format: machine_family_prefix -> sku_family_label
# The sku_family_label is the prefix used in GCP SKU descriptions.
# ---------------------------------------------------------------------------
_FAMILY_SKU_MAP: Dict[str, str] = {
    "e2":   "E2",
    "n1":   "N1",
    "n2":   "N2",
    "n2d":  "N2D",
    "c2":   "C2",
    "c2d":  "C2D",
    "c3":   "C3",
    "c3d":  "C3D",
    "m1":   "Memory Optimized",
    "m2":   "Memory Optimized Upgrade",
    "m3":   "M3",
    "t2d":  "T2D",
    "t2a":  "T2A",
    "h3":   "H3",
    "z3":   "Z3",
    "a2":   "A2",
    "a3":   "A3",
    "g2":   "G2",
}

# ---------------------------------------------------------------------------
# GCP region -> GCP pricing "location" label used in SKU descriptions.
# The Billing API uses plain English location names in SKU descriptions.
# We need to map from region code to the location label suffix.
# ---------------------------------------------------------------------------
# For many families the SKU description uses broad geography labels.
# We will handle per-region pricing by filtering serviceRegions in pricingInfo.
# So we do NOT need to parse the description location — we use pricingInfo.
# (GCP SKU pricingInfo may contain tieredRates with optional serviceRegions.)
# This dict is used as a fallback label only.
_REGION_LABELS: Dict[str, str] = {
    "us-central1": "Iowa",
    "us-east1": "South Carolina",
    "us-east4": "Northern Virginia",
    "us-east5": "Columbus",
    "us-south1": "Dallas",
    "us-west1": "Oregon",
    "us-west2": "Los Angeles",
    "us-west3": "Salt Lake City",
    "us-west4": "Las Vegas",
    "northamerica-northeast1": "Montreal",
    "northamerica-northeast2": "Toronto",
    "southamerica-east1": "São Paulo",
    "southamerica-west1": "Santiago",
    "europe-central2": "Warsaw",
    "europe-north1": "Finland",
    "europe-southwest1": "Madrid",
    "europe-west1": "Belgium",
    "europe-west2": "London",
    "europe-west3": "Frankfurt",
    "europe-west4": "Netherlands",
    "europe-west6": "Zurich",
    "europe-west8": "Milan",
    "europe-west9": "Paris",
    "europe-west10": "Berlin",
    "europe-west12": "Turin",
    "asia-east1": "Taiwan",
    "asia-east2": "Hong Kong",
    "asia-northeast1": "Tokyo",
    "asia-northeast2": "Osaka",
    "asia-northeast3": "Seoul",
    "asia-south1": "Mumbai",
    "asia-south2": "Delhi",
    "asia-southeast1": "Singapore",
    "asia-southeast2": "Jakarta",
    "australia-southeast1": "Sydney",
    "australia-southeast2": "Melbourne",
    "me-central1": "Doha",
    "me-central2": "Dammam",
    "me-west1": "Tel Aviv",
    "africa-south1": "Johannesburg",
}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
_DEFAULT_HEADERS = {"User-Agent": "CloudPriceFinder/3.0 (github.com/eSKylezZ/cloudpricefinder.com)"}
_MAX_RETRIES = 4
_RETRY_SLEEP = 2.0


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_DEFAULT_HEADERS)
    return s


def _get_json(session: requests.Session, url: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """GET JSON with simple exponential-backoff retry."""
    for attempt in range(_MAX_RETRIES):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = _RETRY_SLEEP * (2 ** attempt)
            logger.warning(f"GET {url} failed (attempt {attempt + 1}/{_MAX_RETRIES}): {exc} — retrying in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"Unreachable: all retries exhausted for {url}")


# ---------------------------------------------------------------------------
# Billing API pagination helper
# ---------------------------------------------------------------------------
def _paginate_skus(
    session: requests.Session,
    service_id: str,
    api_key: str,
) -> Generator[Dict[str, Any], None, None]:
    """Yield every SKU for the given service, handling pagination."""
    url = f"{BILLING_BASE}/services/{service_id}/skus"
    page_token: Optional[str] = None
    page_num = 0

    while True:
        params: Dict[str, Any] = {"key": api_key, "pageSize": 5000}
        if page_token:
            params["pageToken"] = page_token

        data = _get_json(session, url, params=params)
        skus = data.get("skus", [])
        page_num += 1
        logger.debug(f"SKU page {page_num}: {len(skus)} SKUs")

        for sku in skus:
            yield sku

        page_token = data.get("nextPageToken")
        if not page_token:
            break


# ---------------------------------------------------------------------------
# Price extraction helpers
# ---------------------------------------------------------------------------
def _extract_unit_price(pricing_info: List[Dict[str, Any]]) -> Optional[float]:
    """
    Return the base (tier-0) unit price in USD per usage unit from pricingInfo.
    Returns None if no pricing info is available.
    """
    if not pricing_info:
        return None
    # Use the most recent pricing info (first item has the current rates)
    pi = pricing_info[0]
    expr = pi.get("pricingExpression", {})
    tiers = expr.get("tieredRates", [])
    if not tiers:
        return None

    # GCP tiered rates: tier with startUsageAmount == 0 is the base rate.
    for tier in tiers:
        if tier.get("startUsageAmount", 0) == 0:
            unit_price = tier.get("unitPrice", {})
            nanos = int(unit_price.get("nanos", 0))
            units = int(unit_price.get("units", 0))
            return units + nanos / 1_000_000_000.0

    # Fallback: first tier
    unit_price = tiers[0].get("unitPrice", {})
    nanos = int(unit_price.get("nanos", 0))
    units = int(unit_price.get("units", 0))
    return units + nanos / 1_000_000_000.0


def _extract_service_regions(pricing_info: List[Dict[str, Any]]) -> List[str]:
    """
    Return list of GCP regions this SKU applies to, extracted from
    pricingInfo[0].pricingExpression or serviceRegions.

    The Billing API v1 does NOT expose serviceRegions per SKU in the
    standard list endpoint — it uses geographic description suffixes.
    We therefore return an empty list here (indicating "all GCP regions"),
    and apply broad region groupings based on SKU description geography labels.
    """
    return []


# ---------------------------------------------------------------------------
# SKU classification helpers
# ---------------------------------------------------------------------------

# All standard commercial GCP regions in a flat list (for fallback)
_ALL_REGIONS = GCP_REGIONS[:]

# ---------------------------------------------------------------------------
# CUD description family token → machine family prefix mapping.
#
# Real API CUD descriptions follow "Commitment v1: <TOKEN> Cpu/Ram in <loc>".
# Tokens observed from the live API (May 2026).
# ---------------------------------------------------------------------------
_CUD_TOKEN_TO_FAMILY: Dict[str, str] = {
    # token (lowercase) -> family prefix
    "e2":                     "e2",
    "n1":                     "n1",
    "n2":                     "n2",
    "n2d amd":                "n2d",
    "n2d":                    "n2d",
    "c2":                     "c2",
    "compute optimized":      "c2",
    "c2d amd":                "c2d",
    "c2d":                    "c2d",
    "c3":                     "c3",
    "c3d":                    "c3d",
    "m3 memory-optimized":    "m3",
    "m3":                     "m3",
    "memory-optimized upgrade": "m2",
    "memory-optimized":       "m1",
    "t2d amd":                "t2d",
    "t2d":                    "t2d",
    "t2a arm":                "t2a",
    "t2a":                    "t2a",
    "h3":                     "h3",
    "z3":                     "z3",
    "a2":                     "a2",
    "a3":                     "a3",
    "g2":                     "g2",
}

# On-demand SKU description prefixes → family prefix.
# Format: "N2 Instance Core running in ..." or "N2D AMD Instance Ram ..."
# We match the start of the description (case-insensitive).
_OD_DESC_TO_FAMILY: Dict[str, str] = {
    "e2 ":                            "e2",
    "n1 predefined instance":         "n1",
    "n1 custom instance":             "n1",
    "n1 ":                            "n1",
    "n2d amd custom instance":        "n2d",
    "n2d amd instance":               "n2d",
    "n2d amd ":                       "n2d",
    "n2 custom instance":             "n2",
    "n2 instance":                    "n2",
    "n2 ":                            "n2",
    "c2d amd instance":               "c2d",
    "c2d amd ":                       "c2d",
    "c2 instance":                    "c2",
    "c2 ":                            "c2",
    "c3d instance":                   "c3d",
    "c3d ":                           "c3d",
    "c3 instance":                    "c3",
    "c3 ":                            "c3",
    "memory optimized upgrade instance": "m2",
    "memory optimized upgrade ":      "m2",
    "memory optimized instance":      "m1",
    "memory optimized ":              "m1",
    "m3 memory-optimized instance":   "m3",
    "m3 ":                            "m3",
    "t2d amd instance":               "t2d",
    "t2d amd ":                       "t2d",
    "t2a arm instance":               "t2a",
    "t2a arm ":                       "t2a",
    "t2a ":                           "t2a",
    "h3 instance":                    "h3",
    "h3 ":                            "h3",
    "z3 instance":                    "z3",
    "z3 ":                            "z3",
    "a2 instance":                    "a2",
    "a2 ":                            "a2",
    "a3 instance":                    "a3",
    "a3 ":                            "a3",
    "g2 instance":                    "g2",
    "g2 ":                            "g2",
}


def _extract_family_from_sku(description: str) -> Optional[str]:
    """
    Extract the machine family prefix from a GCP SKU description.

    Handles two description formats:
    1. On-demand:  "N2 Instance Core running in <location>"
    2. CUD:        "Commitment v1: N2 Cpu in <location> for 1 Year"

    Returns the lowercase family prefix (e.g. 'n2', 'c3d') or None.
    """
    desc_lower = description.lower().strip()

    # CUD format
    if desc_lower.startswith("commitment v1:"):
        # Extract the token after "Commitment v1: " and before " Cpu" or " Ram"
        rest = desc_lower[len("commitment v1:"):].strip()
        # rest is like "n2 cpu in virginia for 1 year" or "n2d amd ram in ..."
        for token, family in sorted(_CUD_TOKEN_TO_FAMILY.items(), key=lambda x: -len(x[0])):
            if rest.startswith(token + " cpu") or rest.startswith(token + " ram"):
                return family
        return None

    # On-demand format — match by prefix
    for prefix_str, family in sorted(_OD_DESC_TO_FAMILY.items(), key=lambda x: -len(x[0])):
        if desc_lower.startswith(prefix_str):
            return family

    return None


def _is_cpu_sku(description: str) -> bool:
    """Return True if this SKU prices per vCPU.

    Uses resourceGroup='CPU' as the canonical check; this function is used
    for backward-compatibility with tests that pass description strings.
    For live API data, prefer using the resourceGroup field directly.
    """
    desc_lower = description.lower()
    # On-demand per-core SKUs
    if ("instance core" in desc_lower or
            " cpu " in desc_lower or
            "vcpu" in desc_lower or
            "core running" in desc_lower):
        return True
    # CUD per-CPU SKUs: 'Commitment v1: N2 Cpu in ...'
    if "commitment v1:" in desc_lower:
        return " cpu " in desc_lower or desc_lower.split("commitment v1:")[1].strip().split()[1] == "cpu"
    # CUD per-CPU SKUs: description does NOT contain "ram" or "memory"
    if "committed use discount" in desc_lower or "cud" in desc_lower:
        if "ram" in desc_lower or "memory" in desc_lower:
            return False
        return True
    return False


def _is_ram_sku(description: str) -> bool:
    """Return True if this SKU prices per GiB of RAM.

    Uses resourceGroup='RAM' as the canonical check; this function is used
    for backward-compatibility with tests that pass description strings.
    """
    desc_lower = description.lower()
    # On-demand per-RAM SKUs
    if ("instance ram" in desc_lower or
            " ram " in desc_lower or
            "memory running" in desc_lower or
            "memory in " in desc_lower):
        return True
    # CUD per-RAM SKUs: 'Commitment v1: N2 Ram in ...'
    if "commitment v1:" in desc_lower:
        return " ram " in desc_lower or desc_lower.split("commitment v1:")[1].strip().split()[1] == "ram"
    # CUD per-RAM SKUs (legacy format)
    if (("committed use discount" in desc_lower or "cud" in desc_lower) and
            ("ram" in desc_lower or "memory" in desc_lower)):
        return True
    return False


def _is_gpu_sku(description: str) -> bool:
    """Return True if this is a GPU accelerator SKU."""
    desc_lower = description.lower()
    return ("gpu" in desc_lower or
            "accelerator" in desc_lower or
            "a100" in desc_lower or
            "h100" in desc_lower or
            "t4" in desc_lower or
            "l4" in desc_lower or
            "v100" in desc_lower or
            "p100" in desc_lower or
            "p4" in desc_lower)


def _usage_type_from_sku(sku: Dict[str, Any]) -> str:
    """Return 'on-demand', '1yr', or '3yr' from SKU category.usageType."""
    usage_type = sku.get("category", {}).get("usageType", "")
    if usage_type == "Commit1Yr":
        return "1yr"
    elif usage_type == "Commit3Yr":
        return "3yr"
    return "on-demand"


def _is_preemptible(sku: Dict[str, Any]) -> bool:
    """Return True if this is a preemptible/spot SKU."""
    desc = sku.get("description", "").lower()
    usage_type = sku.get("category", {}).get("usageType", "").lower()
    return "preemptible" in desc or "spot" in desc or "preemptible" in usage_type


# ---------------------------------------------------------------------------
# Core SKU processing
#
# Strategy:
# 1. Use resourceGroup ('CPU' or 'RAM') for reliable on-demand/CUD discriminant.
# 2. Use serviceRegions[] (top-level SKU field) for exact region association.
# 3. For each (family, region) pair, store the cheapest on-demand and CUD rates.
# 4. Aggregate to a per-region FamilyPricing structure.
#
# The per-region structure means we can later produce region-specific pricing.
# For v1 simplicity we use "Americas" canonical pricing for priceUSD_hourly,
# but keep all regions in the 'regions' field.
# ---------------------------------------------------------------------------

class _FamilyPricing:
    """Aggregates per-unit (CPU and RAM) prices for a machine family."""

    __slots__ = ("cpu_on_demand", "ram_on_demand", "cpu_1yr", "ram_1yr",
                 "cpu_3yr", "ram_3yr", "regions")

    def __init__(self) -> None:
        self.cpu_on_demand: Optional[float] = None
        self.ram_on_demand: Optional[float] = None
        self.cpu_1yr: Optional[float] = None
        self.ram_1yr: Optional[float] = None
        self.cpu_3yr: Optional[float] = None
        self.ram_3yr: Optional[float] = None
        self.regions: List[str] = []


def _classify_skus(
    skus: List[Dict[str, Any]],
) -> Dict[str, Dict[str, _FamilyPricing]]:
    """
    Classify all Compute Engine SKUs into a nested structure:
      { region_key -> { family_prefix -> _FamilyPricing } }

    We use serviceRegions[] (actual GCP region codes) from each SKU for
    per-region pricing association. The special key "global" collects SKUs
    with no serviceRegions entry (e.g. very broad CUD pricing).

    For on-demand SKUs, each SKU has exactly one serviceRegion.
    For CUD SKUs, each SKU has one or a few serviceRegions.

    Returns: region_key -> family -> FamilyPricing
    """
    # region_key -> family_prefix -> _FamilyPricing
    pricing: Dict[str, Dict[str, _FamilyPricing]] = {}

    skipped_preemptible = 0
    skipped_unknown = 0
    skipped_sole_tenancy = 0
    skipped_custom = 0
    total_processed = 0

    for sku in skus:
        # Skip preemptible/spot
        if _is_preemptible(sku):
            skipped_preemptible += 1
            continue

        cat = sku.get("category", {})
        resource_family = cat.get("resourceFamily", "")
        resource_group = cat.get("resourceGroup", "")
        usage_type_raw = cat.get("usageType", "")

        # Only process Compute family SKUs
        if resource_family != "Compute":
            skipped_unknown += 1
            continue

        # Only CPU and RAM resource groups
        if resource_group not in ("CPU", "RAM"):
            skipped_unknown += 1
            continue

        # Skip CmtCudPremium (custom instance CUD premium) and sole tenancy
        if usage_type_raw in ("CmtCudPremium",):
            skipped_custom += 1
            continue

        desc = sku.get("description", "")

        # Skip sole tenancy SKUs
        if "sole tenancy" in desc.lower() or "sole-tenancy" in desc.lower():
            skipped_sole_tenancy += 1
            continue

        # Skip custom instance on-demand (we use predefined pricing for standard types)
        # Actually, keep custom instance CUD prices as they apply to predefined too.
        # Skip only if it's a custom instance on-demand premium surcharge.
        if "custom instance" in desc.lower() and usage_type_raw == "OnDemand" and "premium" in desc.lower():
            skipped_custom += 1
            continue

        # Determine usage type
        usage_type = _usage_type_from_sku(sku)

        # Extract machine family from description
        family = _extract_family_from_sku(desc)
        if family is None:
            skipped_unknown += 1
            continue

        # Get regions for this SKU
        sku_regions = sku.get("serviceRegions", [])
        if not sku_regions:
            sku_regions = ["global"]

        # Extract unit price
        unit_price = _extract_unit_price(sku.get("pricingInfo", []))
        if unit_price is None:
            skipped_unknown += 1
            continue

        is_cpu = resource_group == "CPU"
        is_ram = resource_group == "RAM"

        # Store pricing for each region this SKU covers
        for region in sku_regions:
            if region not in pricing:
                pricing[region] = {}
            if family not in pricing[region]:
                pricing[region][family] = _FamilyPricing()

            fp = pricing[region][family]

            if is_cpu:
                if usage_type == "on-demand":
                    if fp.cpu_on_demand is None or unit_price < fp.cpu_on_demand:
                        fp.cpu_on_demand = unit_price
                elif usage_type == "1yr":
                    if fp.cpu_1yr is None or unit_price < fp.cpu_1yr:
                        fp.cpu_1yr = unit_price
                elif usage_type == "3yr":
                    if fp.cpu_3yr is None or unit_price < fp.cpu_3yr:
                        fp.cpu_3yr = unit_price
            elif is_ram:
                if usage_type == "on-demand":
                    if fp.ram_on_demand is None or unit_price < fp.ram_on_demand:
                        fp.ram_on_demand = unit_price
                elif usage_type == "1yr":
                    if fp.ram_1yr is None or unit_price < fp.ram_1yr:
                        fp.ram_1yr = unit_price
                elif usage_type == "3yr":
                    if fp.ram_3yr is None or unit_price < fp.ram_3yr:
                        fp.ram_3yr = unit_price

            total_processed += 1

    logger.info(
        f"SKU classification: {total_processed} region-SKU pairs processed, "
        f"{skipped_preemptible} preemptible, {skipped_sole_tenancy} sole-tenancy, "
        f"{skipped_custom} custom-premium, {skipped_unknown} unknown skipped"
    )
    return pricing


# ---------------------------------------------------------------------------
# GPU SKU processing — separate pass
# ---------------------------------------------------------------------------

def _extract_gpu_skus(skus: List[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Build a mapping: { gpu_model -> { region_code -> on_demand_price_per_gpu } }
    for on-demand GPU accelerator SKUs only.
    """
    gpu_prices: Dict[str, Dict[str, Optional[float]]] = {}
    gpu_model_keywords = {
        "A100": ["a100"],
        "H100": ["h100"],
        "L4":   ["l4 gpu", " l4 "],
        "T4":   ["t4 gpu", " t4 "],
        "V100": ["v100"],
        "P100": ["p100"],
        "P4":   ["p4 gpu", " p4 "],
    }

    for sku in skus:
        if _is_preemptible(sku):
            continue

        usage_type = _usage_type_from_sku(sku)
        if usage_type != "on-demand":
            continue

        cat = sku.get("category", {})
        resource_family = cat.get("resourceFamily", "")
        if resource_family != "Compute":
            continue

        desc = sku.get("description", "").lower()

        # Check if it's a GPU SKU
        matched_model: Optional[str] = None
        for model, keywords in gpu_model_keywords.items():
            for kw in keywords:
                if kw in desc:
                    matched_model = model
                    break
            if matched_model:
                break

        if not matched_model:
            continue

        unit_price = _extract_unit_price(sku.get("pricingInfo", []))
        if unit_price is None:
            continue

        # Associate with all serviceRegions this SKU covers
        sku_regions = sku.get("serviceRegions", ["global"])
        for region in sku_regions:
            if matched_model not in gpu_prices:
                gpu_prices[matched_model] = {}
            if region not in gpu_prices[matched_model]:
                gpu_prices[matched_model][region] = unit_price
            else:
                if unit_price < gpu_prices[matched_model][region]:
                    gpu_prices[matched_model][region] = unit_price

    return gpu_prices


# ---------------------------------------------------------------------------
# Build CloudInstance records
# ---------------------------------------------------------------------------

def _extract_family(machine_type: str) -> str:
    """
    Return the machine family prefix (e.g. 'n2', 'c3d', 'a2') from a machine
    type string like 'n2-standard-4' or 'a2-highgpu-1g'.

    Strategy: match the longest known family prefix from _FAMILY_SKU_MAP.
    The map keys are all the valid prefixes (e.g. 'n2', 'n2d', 'c3d', 'a2').
    """
    mt_lower = machine_type.lower()
    # Try longest match first (e.g. 'n2d' before 'n2')
    for candidate in sorted(_FAMILY_SKU_MAP.keys(), key=len, reverse=True):
        if mt_lower.startswith(candidate + "-") or mt_lower == candidate:
            return candidate
    # Fallback: first hyphen-delimited segment
    return machine_type.split("-")[0]


def _extract_generation(machine_type: str) -> Optional[str]:
    """Extract generation digit(s) from the family part (e.g. 'n2' -> '2')."""
    family = _extract_family(machine_type)
    m = re.search(r"(\d+)", family)
    return m.group(1) if m else None


def build_instances(
    pricing: Dict[str, Dict[str, _FamilyPricing]],
    gpu_prices: Dict[str, Dict[str, Optional[float]]],
    target_regions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Build the final list of CloudInstance dicts by cross-referencing the
    machine spec table with the per-family pricing from the billing API.

    Args:
        pricing: region_key -> family_prefix -> FamilyPricing
                 (output of _classify_skus with the new region-keyed structure)
        gpu_prices: gpu_model -> region_key -> price_per_gpu
        target_regions: restrict to these GCP region codes (None = all)

    Returns:
        List of CloudInstance dicts, one per machine type.
    """
    regions_to_process = target_regions if target_regions else GCP_REGIONS
    instances: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    # Build a canonical merged FamilyPricing per family, using us-central1 as
    # the canonical region for priceUSD_hourly (matches how AWS uses us-east-1).
    # Fall back through a priority list of well-known regions.
    canonical_region_priority = [
        "us-central1", "us-east1", "us-east4",
        "europe-west1", "europe-west4",
        "asia-east1", "asia-southeast1",
        "global",
    ]

    # merged_pricing: family -> FamilyPricing (canonical pricing)
    merged_pricing: Dict[str, _FamilyPricing] = {}

    for region in canonical_region_priority:
        for family, fp in pricing.get(region, {}).items():
            if family not in merged_pricing:
                # Start with a copy so we don't modify original
                new_fp = _FamilyPricing()
                new_fp.cpu_on_demand = fp.cpu_on_demand
                new_fp.ram_on_demand = fp.ram_on_demand
                new_fp.cpu_1yr = fp.cpu_1yr
                new_fp.ram_1yr = fp.ram_1yr
                new_fp.cpu_3yr = fp.cpu_3yr
                new_fp.ram_3yr = fp.ram_3yr
                merged_pricing[family] = new_fp
            else:
                # Fill in missing CUD values from other canonical regions
                existing = merged_pricing[family]
                if existing.cpu_on_demand is None:
                    existing.cpu_on_demand = fp.cpu_on_demand
                if existing.ram_on_demand is None:
                    existing.ram_on_demand = fp.ram_on_demand
                if existing.cpu_1yr is None:
                    existing.cpu_1yr = fp.cpu_1yr
                if existing.ram_1yr is None:
                    existing.ram_1yr = fp.ram_1yr
                if existing.cpu_3yr is None:
                    existing.cpu_3yr = fp.cpu_3yr
                if existing.ram_3yr is None:
                    existing.ram_3yr = fp.ram_3yr

    # Determine which regions have pricing for each family
    # (collect all regions where we have on-demand CPU pricing for each family)
    family_regions: Dict[str, List[str]] = {}
    for region, fam_map in pricing.items():
        if region == "global":
            continue
        if region not in regions_to_process:
            continue
        for family, fp in fam_map.items():
            if fp.cpu_on_demand is not None and fp.ram_on_demand is not None:
                if family not in family_regions:
                    family_regions[family] = []
                if region not in family_regions[family]:
                    family_regions[family].append(region)

    built = 0
    skipped_no_price = 0

    for machine_type, spec in _SPEC_LOOKUP.items():
        vcpu, mem_gib, arch, gpu_cnt, gpu_type, gpu_mem_gib = spec

        family_key = _extract_family(machine_type)

        fp = merged_pricing.get(family_key)
        if fp is None:
            skipped_no_price += 1
            continue

        cpu_od = fp.cpu_on_demand
        ram_od = fp.ram_on_demand

        if cpu_od is None or ram_od is None:
            skipped_no_price += 1
            continue

        # Compute canonical on-demand hourly price (in us-central1 or best available)
        od_hourly = round(vcpu * cpu_od + mem_gib * ram_od, 6)
        if od_hourly <= 0:
            skipped_no_price += 1
            continue

        # Add GPU cost if applicable
        if gpu_cnt > 0 and gpu_type:
            gpu_price_per_unit: Optional[float] = None
            # Look for GPU price in canonical regions first
            for region in canonical_region_priority:
                gp = gpu_prices.get(gpu_type, {}).get(region)
                if gp is not None:
                    gpu_price_per_unit = gp
                    break
            # Try partial model name match
            if gpu_price_per_unit is None:
                for model_key, region_dict in gpu_prices.items():
                    if gpu_type.lower() in model_key.lower() or model_key.lower() in gpu_type.lower():
                        for region in canonical_region_priority:
                            gp = region_dict.get(region)
                            if gp is not None:
                                gpu_price_per_unit = gp
                                break
                        if gpu_price_per_unit is not None:
                            break
            if gpu_price_per_unit is not None:
                od_hourly = round(od_hourly + gpu_cnt * gpu_price_per_unit, 6)

        # Build commitments
        raw_commitments = []
        if fp.cpu_1yr is not None and fp.ram_1yr is not None:
            c1yr_hourly = round(vcpu * fp.cpu_1yr + mem_gib * fp.ram_1yr, 6)
            raw_commitments.append({
                "term": "1yr",
                "payment": "flexible",
                "product": "cud",
                "priceUSD_hourly": c1yr_hourly,
                "upfront_usd": 0,
            })
        if fp.cpu_3yr is not None and fp.ram_3yr is not None:
            c3yr_hourly = round(vcpu * fp.cpu_3yr + mem_gib * fp.ram_3yr, 6)
            raw_commitments.append({
                "term": "3yr",
                "payment": "flexible",
                "product": "cud",
                "priceUSD_hourly": c3yr_hourly,
                "upfront_usd": 0,
            })

        commitments = normalize_commitments(raw_commitments, od_hourly)

        # Determine applicable regions
        applicable_regions = family_regions.get(family_key, [])
        if not applicable_regions:
            # Fallback: all target regions
            applicable_regions = list(regions_to_process)

        # Build GPU info field
        gpu_info: Optional[Dict[str, Any]] = None
        if gpu_cnt > 0 and gpu_type:
            model_info = _GPU_MODELS.get(gpu_type, {"type": gpu_type, "memoryGiB": gpu_mem_gib})
            actual_mem = gpu_mem_gib if gpu_mem_gib > 0 else model_info.get("memoryGiB", 0)
            gpu_info = {
                "count": gpu_cnt,
                "type": model_info.get("type", gpu_type),
                "memoryGiB": actual_mem,
            }

        family = _extract_family(machine_type)
        generation = _extract_generation(machine_type)

        instance: Dict[str, Any] = {
            "provider": "gcp",
            "type": "cloud-server",
            "instanceType": machine_type,
            "vCPU": vcpu,
            "memoryGiB": mem_gib,
            "architecture": arch,
            "family": family,
            "generation": generation,
            "priceUSD_hourly": od_hourly,
            "priceUSD_monthly": round(od_hourly * 24 * 30, 4),
            "commitments": commitments,
            "regions": applicable_regions,
            "source": "gcp_billing_catalog_api",
            "lastUpdated": now,
        }

        if gpu_info is not None:
            instance["gpu"] = gpu_info

        instances.append(instance)
        built += 1

    logger.info(
        f"Built {built} instances; skipped {skipped_no_price} (no price)"
    )
    return instances


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

def fetch_gcp_data(
    api_key: str,
    target_regions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch GCP Compute Engine pricing and return a list of CloudInstance dicts.

    Args:
        api_key: GCP API key with Cloud Billing API enabled.
        target_regions: Optional list of GCP region codes to include.
                        Defaults to all standard commercial regions.

    Returns:
        List of CloudInstance dicts.
    """
    session = _make_session()

    # --- Step 1: Resolve the Compute Engine service ID ---
    logger.info("Resolving Compute Engine service ID from Billing API...")
    services_data = _get_json(
        session,
        f"{BILLING_BASE}/services",
        params={"key": api_key, "pageSize": 500},
    )
    compute_service_id: Optional[str] = None
    for svc in services_data.get("services", []):
        if COMPUTE_SERVICE_NAME in svc.get("displayName", ""):
            compute_service_id = svc["name"].split("/")[-1]
            logger.info(f"Found Compute Engine service: {svc['name']}")
            break

    if compute_service_id is None:
        logger.warning(
            f"Could not resolve Compute Engine service dynamically; "
            f"using fallback ID: {COMPUTE_SERVICE_ID_FALLBACK}"
        )
        compute_service_id = COMPUTE_SERVICE_ID_FALLBACK

    # --- Step 2: Fetch all SKUs ---
    logger.info(f"Fetching all SKUs for service {compute_service_id}...")
    all_skus: List[Dict[str, Any]] = list(_paginate_skus(session, compute_service_id, api_key))
    logger.info(f"Total SKUs fetched: {len(all_skus)}")

    # Filter to only VM instance resource SKUs in the Compute family with
    # CPU or RAM resource groups. This covers both on-demand instance SKUs
    # ("N2 Instance Core running in ...") and CUD SKUs ("Commitment v1: N2 Cpu
    # in ... for 1 Year"). GPU accelerator SKUs (resourceGroup=GPU) are also
    # kept for the _extract_gpu_skus pass.
    instance_skus = [
        sku for sku in all_skus
        if sku.get("category", {}).get("resourceFamily") == "Compute"
        and sku.get("category", {}).get("resourceGroup") in (
            "CPU", "RAM", "GPU", "Accelerator",
            "N1Standard",   # E2 micro/small/medium special SKUs
            "F1Micro", "G1Small",  # Legacy shared-core types
        )
    ]
    logger.info(f"Compute instance SKUs after filtering: {len(instance_skus)}")

    # --- Step 3: Classify SKUs into family pricing ---
    pricing = _classify_skus(instance_skus)
    logger.info(f"Pricing families found: {sum(len(v) for v in pricing.values())}")

    # --- Step 4: Extract GPU SKU prices ---
    gpu_prices = _extract_gpu_skus(instance_skus)
    logger.info(f"GPU models with pricing: {list(gpu_prices.keys())}")

    # --- Step 5: Build instances ---
    instances = build_instances(pricing, gpu_prices, target_regions)
    logger.info(f"Total instances built: {len(instances)}")

    return instances


# ---------------------------------------------------------------------------
# CLI validation helper (mirrors what data_validator CLI does)
# ---------------------------------------------------------------------------

def _validate_output(instances: List[Dict[str, Any]]) -> bool:
    """Validate all instances and commitments; return True if all pass."""
    from scripts.utils.data_validator import validate_dataset, validate_commitments

    valid, errors = validate_dataset(instances)
    c_errors: List[str] = []
    for i, inst in enumerate(instances):
        comms = inst.get("commitments", [])
        if comms:
            ok, errs = validate_commitments(comms, inst.get("priceUSD_hourly", 0.0))
            if not ok:
                c_errors.extend(f"Instance {i} ({inst.get('instanceType')}): {e}" for e in errs)

    all_errors = errors + c_errors
    if all_errors:
        for e in all_errors[:20]:
            logger.error(e)
        logger.error(f"Validation: {len(all_errors)} error(s)")
        return False

    logger.info(f"Validation OK: {len(valid)}/{len(instances)} instances passed")
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch GCP Compute Engine pricing via Cloud Billing Catalog API"
    )
    parser.add_argument(
        "--output",
        default="data/providers/gcp.raw.json",
        help="Output JSON path (default: data/providers/gcp.raw.json)",
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        metavar="REGION",
        help="Restrict to specific GCP regions (e.g. --regions us-central1 europe-west1)",
    )
    args = parser.parse_args(argv)

    # Load API key from environment (never from source code)
    api_key = os.environ.get("GCP_API_KEY", "").strip()
    if not api_key:
        # Try loading from .env file
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GCP_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break

    if not api_key:
        print(
            "ERROR: GCP_API_KEY environment variable is not set.\n"
            "Set it with: export GCP_API_KEY=<your-key>\n"
            "Or add GCP_API_KEY=<key> to your .env file.\n"
            "Get a key at: https://console.cloud.google.com/apis/credentials\n"
            "Enable: Cloud Billing API",
            file=sys.stderr,
        )
        return 1

    target_regions = args.regions if args.regions else None
    if target_regions:
        logger.info(f"Restricting to regions: {target_regions}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("=== GCP Compute Engine Fetcher (Stage 5) ===", flush=True)

    instances = fetch_gcp_data(api_key=api_key, target_regions=target_regions)

    # Validate
    _validate_output(instances)

    # Write output
    import json as _json
    with open(output_path, "w", encoding="utf-8") as f:
        _json.dump(instances, f, indent=2)

    machine_families = sorted({inst["family"] for inst in instances})
    with_commitments = sum(1 for i in instances if i.get("commitments"))

    print(f"\nSaved {len(instances)} instances to {output_path}")
    print(f"  - Machine families: {len(machine_families)} ({', '.join(machine_families)})")
    print(f"  - Instances with CUD commitments: {with_commitments}")
    print(f"  - Regions covered: {len(GCP_REGIONS)}")
    print(
        "\nNote: GCP pricing sourced from Cloud Billing Catalog API. "
        "Prices are per-instance computed from per-vCPU and per-GiB-RAM resource rates."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
