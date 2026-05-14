#!/usr/bin/env python3
"""
CloudPriceFinder Build-Time Aggregator

Consumes data/providers/*.raw.json and produces three-tier output:
  data/index.json                    < 100 KB  eager load
  data/families/{provider}/{id}.json < 250 KB  lazy load per filter
  data/instances/{provider}/{id}.json < 20 KB  lazy load per row expand
  data/equivalents.json              cross-provider family lookup
"""

import json
import math
import re
import shutil
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PROVIDERS_DIR = DATA_DIR / "providers"
FAMILIES_DIR = DATA_DIR / "families"
INSTANCES_DIR = DATA_DIR / "instances"
PRODUCTS_DIR = DATA_DIR / "products"

PROVIDERS = ["aws", "azure", "gcp", "oci", "ovh", "scaleway", "vultr", "vast"]

# Fields excluded from family-level summary files.
# regionPricing and locationDetails are stripped because they are large and
# only needed in the per-instance detail files.
# New schema fields are included in family files automatically unless
# explicitly added to FAMILY_STRIP.
FAMILY_STRIP = {"raw", "regionPricing", "locationDetails"}

# Fields stripped from instance detail files (raw is debug-only noise)
INSTANCE_STRIP = {"raw"}

# ---------------------------------------------------------------------------
# Product category routing
# ---------------------------------------------------------------------------

# Types that continue through the existing compute pipeline (families/instances)
COMPUTE_TYPES: frozenset[str] = frozenset({
    "cloud-server",
    "dedicated-server",
    "dedicated-auction",
    "dedicated-colocation",
})

# Non-compute types mapped to their output category directory under data/products/
PRODUCT_CATEGORY_MAP: dict[str, str] = {
    "cloud-volume":       "storage",
    "dedicated-storage":  "storage",
    "cloud-snapshot":     "storage",
    "cloud-loadbalancer": "networking",
    "cloud-network":      "networking",
    "cloud-floating-ip":  "networking",
    "cloud-certificate":  "networking",
    "rds-instance":       "database",
    "ai-model":           "ai",
    "object-storage":     "object-storage",
}

# Additional raw-file sources per provider for non-compute product data.
# Each source name maps to data/providers/{source}.raw.json.
PRODUCT_SOURCES: dict[str, list[str]] = {
    "aws":   ["aws.storage", "aws.object-storage", "aws.databases", "aws.ai"],
    "gcp":   ["gcp.storage", "gcp.object-storage", "gcp.databases", "gcp.ai"],
    "azure": ["azure.storage", "azure.object-storage", "azure.databases", "azure.ai"],
    "oci":   ["oci.storage"],
}

# Primary region per provider for normalized $/vCPU and $/GiB metrics
PRIMARY_REGION: dict[str, str] = {
    "aws": "us-east-1",
    "azure": "eastus",
    "gcp": "us-central1",
    "oci": "us-ashburn-1",
    "ovh": "BHS",       # Beauharnois — OVH's default API region
    "scaleway": "fr-par",  # Paris — Scaleway default
    "vultr": "ewr",     # New Jersey — Vultr default
    "vast": "global",   # Vast has no regions; sentinel triggers priceUSD_hourly fallback
}

# Max log2-space Euclidean distance for a family to be considered "equivalent".
# sqrt(log2(2)^2 + log2(2)^2) = 1.41 — a 2x difference in BOTH vCPU and RAM simultaneously.
# 1.5 allows families whose medians differ by up to ~2x in each dimension to match,
# while a genuine mismatch (e.g. 2 GiB vs 16 GiB at same vCPU) scores ~2.5 and is filtered.
MAX_EQUIV_DISTANCE = 1.5

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_raw(provider: str) -> list[dict[str, Any]]:
    """Load raw instances for a provider, trying .raw.json then fallbacks."""
    candidates = [
        PROVIDERS_DIR / f"{provider}.raw.json",
        PROVIDERS_DIR / f"{provider}_test.json",
        PROVIDERS_DIR / f"{provider}.json",
    ]
    found_any = False
    for path in candidates:
        if path.exists():
            found_any = True
            print(f"  Loading {path.name} ...", end=" ", flush=True)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                print(f"{len(data)} instances")
                return data
            if isinstance(data, dict):
                for key in ("instances", "data", "items"):
                    if key in data and isinstance(data[key], list):
                        print(f"{len(data[key])} instances (from '{key}')")
                        return data[key]
            print(f"\n  WARNING: {path.name} has unexpected structure, skipping")
    if not found_any:
        print(f"  WARNING: no raw file for {provider}")
    return []


def safe_id(text: str) -> str:
    """Convert instance type / family name to a filesystem-safe slug."""
    return re.sub(r"[^a-zA-Z0-9._-]", "-", text).lower().strip("-")


def azure_family_id(inst: dict[str, Any]) -> str:
    """
    Derive a granular Azure family ID.  The raw `family` field uses single
    letters (d, e, m, ...) which produce files > 250 KB.  Split the two
    largest families (d, e) by version.
    """
    base: str = inst.get("family", "other")
    if base in ("d", "e", "m", "f"):
        m = re.search(r"_v(\d+)", inst.get("instanceType", ""))
        if m:
            return f"{base}-v{m.group(1)}"
    return base


def get_family_id(inst: dict[str, Any]) -> str:
    """Return the family file key for an instance."""
    provider = inst.get("provider", "")
    if provider == "azure":
        return azure_family_id(inst)
    return inst.get("family", "unknown")


def on_demand_price_in_primary(inst: dict) -> float | None:
    """Return the on-demand hourly USD price in the provider's primary region."""
    provider = inst.get("provider", "")
    primary = PRIMARY_REGION.get(provider)
    rp = inst.get("regionPricing", {})
    if primary and primary in rp:
        v = rp[primary]
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
        if isinstance(v, dict) and v.get("onDemand", 0) > 0:
            return float(v["onDemand"])
    price = inst.get("priceUSD_hourly", 0)
    return float(price) if price and price > 0 else None


def median_or_none(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and v > 0]
    return round(statistics.median(clean), 8) if clean else None


def trimmed_median(values: list[float], trim: float = 0.2) -> float:
    """Median of the middle (1-2*trim) fraction of sorted values."""
    if not values:
        return 0.0
    s = sorted(v for v in values if v > 0)
    if len(s) <= 2:
        return statistics.median(s) if s else 0.0
    cut = max(1, int(len(s) * trim))
    trimmed = s[cut:-cut]
    return statistics.median(trimmed) if trimmed else statistics.median(s)


# ---------------------------------------------------------------------------
# Instance deduplication / region merging
# ---------------------------------------------------------------------------

def merge_instances(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Collapse duplicate raw entries (same instanceType + marketSegment) into one
    by unioning their regions, regionPricing, locationDetails, and commitments.
    All scalar fields (vCPU, memoryGiB, architecture, etc.) are taken from the
    first entry encountered; later entries only contribute regional data.
    """
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for inst in instances:
        key = f"{inst.get('instanceType', '')}|{inst.get('marketSegment', 'global')}|{inst.get('operatingSystem', '')}|{inst.get('tenancy', 'shared')}"
        if key not in groups:
            groups[key] = dict(inst)
            order.append(key)
        else:
            base = groups[key]

            # Union regions list
            merged_regions: set[str] = set(cast(list[str], base.get("regions") or []))
            merged_regions.update(cast(list[str], inst.get("regions") or []))
            base["regions"] = sorted(merged_regions)

            # Merge regionPricing dict (first-writer wins per region key)
            base_rp = cast(dict[str, Any], base.setdefault("regionPricing", {}))
            for region, price in cast(dict[str, Any], inst.get("regionPricing") or {}).items():
                if region not in base_rp:
                    base_rp[region] = price

            # Merge locationDetails list (keyed by 'region' to avoid dupes)
            base_ld = cast(list[dict[str, Any]], base.setdefault("locationDetails", []))
            ld_seen: set[str] = {str(d.get("region", "")) for d in base_ld}
            for detail in cast(list[dict[str, Any]], inst.get("locationDetails") or []):
                region_key = str(detail.get("region", ""))
                if region_key not in ld_seen:
                    base_ld.append(detail)
                    ld_seen.add(region_key)

            # Merge commitments (keyed by term+payment+product)
            base_commits = cast(list[dict[str, Any]], base.setdefault("commitments", []))
            commit_seen: set[str] = {
                f"{c.get('term')}|{c.get('payment')}|{c.get('product')}"
                for c in base_commits
            }
            for c in cast(list[dict[str, Any]], inst.get("commitments") or []):
                ck = f"{c.get('term')}|{c.get('payment')}|{c.get('product')}"
                if ck not in commit_seen:
                    base_commits.append(c)
                    commit_seen.add(ck)

    return [groups[k] for k in order]


# ---------------------------------------------------------------------------
# Family ID computation (grouped per provider)
# ---------------------------------------------------------------------------

def group_by_family(instances: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for inst in instances:
        groups[get_family_id(inst)].append(inst)
    return dict(groups)


# ---------------------------------------------------------------------------
# Normalized metrics per family
# ---------------------------------------------------------------------------

def compute_family_metrics(instances: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute $/vCPU/hr and $/GiB/hr using primary-region on-demand prices."""
    per_vcpu: list[float] = []
    per_gib: list[float] = []
    for inst in instances:
        price = on_demand_price_in_primary(inst)
        vcpu = inst.get("vCPU", 0) or 0
        gib = inst.get("memoryGiB", 0) or 0
        if price and vcpu > 0:
            per_vcpu.append(price / vcpu)
        if price and gib > 0:
            per_gib.append(price / gib)
    return {
        "medianPricePerVCPU": median_or_none(per_vcpu),
        "medianPricePerGiB": median_or_none(per_gib),
    }


# ---------------------------------------------------------------------------
# Equivalents: naive closest-match by vCPU + RAM profile
# ---------------------------------------------------------------------------

def _log_dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    dv = math.log2(a[0] + 1) - math.log2(b[0] + 1)
    dm = math.log2(a[1] + 1) - math.log2(b[1] + 1)
    return math.sqrt(dv * dv + dm * dm)


def _build_rep_points(
    all_families: dict[str, dict[str, list[dict[str, Any]]]],
) -> tuple[
    dict[tuple[str, str], tuple[float, float]],
    dict[tuple[str, str], str],
    dict[tuple[str, str], float],
]:
    """Returns (rep, rep_instance, rep_ratio) dicts keyed by (provider, fam_id)."""
    # Build representative (vCPU, RAM) per provider+family using a trimmed
    # median (drop top/bottom 20%) to avoid outlier sizes skewing wide families.
    # Also compute GB-per-vCPU ratio to guard against cross-workload-class matches.
    rep: dict[tuple[str, str], tuple[float, float]] = {}
    rep_instance: dict[tuple[str, str], str] = {}
    rep_ratio: dict[tuple[str, str], float] = {}  # GB per vCPU

    for provider, families in all_families.items():
        for fam_id, insts in families.items():
            # Exclude dedicated/host instances so Mac and dedicated variants don't
            # skew the family median used for cross-provider matching.
            shared_insts = [i for i in insts if (i.get("tenancy") or "shared") == "shared"]
            if not shared_insts:
                continue
            vcpus = [i.get("vCPU", 0) or 0 for i in shared_insts]
            gibs = [i.get("memoryGiB", 0) or 0 for i in shared_insts]
            vcpu = trimmed_median([v for v in vcpus if v > 0])
            gib = trimmed_median([g for g in gibs if g > 0])
            if vcpu > 0 and gib > 0:
                rep[(provider, fam_id)] = (vcpu, gib)
                ratio = gib / vcpu
                if ratio > 0:
                    rep_ratio[(provider, fam_id)] = ratio
                candidates = [
                    i for i in shared_insts
                    if (i.get("vCPU") or 0) > 0 and (i.get("memoryGiB") or 0) > 0
                ]
                if candidates:
                    best = min(
                        candidates,
                        key=lambda i: (
                            (math.log2((i.get("vCPU") or 1) + 1) - math.log2(vcpu + 1)) ** 2
                            + (math.log2((i.get("memoryGiB") or 1) + 1) - math.log2(gib + 1)) ** 2
                        ),
                    )
                    rep_instance[(provider, fam_id)] = best.get("instanceType", "")

    return rep, rep_instance, rep_ratio


def build_equivalents(
    all_families: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    """
    For each (provider, family), find the closest equivalent family in every
    other provider.  Similarity is measured by the Euclidean distance in
    log2-normalised (vCPU, RAM) space on the family's median instance.

    Each equivalent entry now includes `instanceType` — the instance within
    that family whose (vCPU, RAM) is closest to the family's median — so the
    compare page can load a specific instance file rather than a family stub.
    """
    rep, rep_instance, rep_ratio = _build_rep_points(all_families)

    # Pre-flatten each provider's instances so the outer equivalents loop never
    # re-traverses the nested families dict — O(providers × instances) once here
    # instead of O(source_families × providers × instances) across all iterations.
    # Exclude non-shared-tenancy instances (dedicated/host) so Mac dedicated hosts
    # and dedicated instances don't produce spurious cross-provider equivalents.
    provider_instances: dict[str, list[dict[str, Any]]] = {
        prov: [
            i for fam_insts in fams.values() for i in fam_insts
            if (i.get("tenancy") or "shared") == "shared"
        ]
        for prov, fams in all_families.items()
    }
    provider_fam_of: dict[str, dict[str, str]] = {
        prov: {
            i.get("instanceType", ""): fam_id
            for fam_id, fam_insts in fams.items()
            for i in fam_insts
        }
        for prov, fams in all_families.items()
    }

    # Compare the source family's rep point against every individual instance
    # in each target provider, not against family medians. This lets large
    # specialised families (e.g. u7in-24tb) find their true counterpart even
    # when the target family's median is far from the extreme end of its range.
    equivalents: dict[str, dict] = {}
    for (prov, fam), profile in rep.items():
        src_ratio = rep_ratio.get((prov, fam), 0)
        matches: dict[str, dict] = {}
        for other_prov in all_families:
            if other_prov == prov:
                continue
            best_fam: str | None = None
            best_dist = float("inf")
            best_inst_type = ""
            best_inst_ratio = 0.0
            for inst in provider_instances[other_prov]:
                v: float = inst.get("vCPU") or 0
                g: float = inst.get("memoryGiB") or 0
                if v <= 0 or g <= 0:
                    continue
                d = _log_dist(profile, (v, g))
                if d < best_dist:
                    best_dist = d
                    best_fam = provider_fam_of[other_prov].get(inst.get("instanceType", ""))
                    best_inst_type = inst.get("instanceType", "")
                    best_inst_ratio = g / v
            if best_fam is not None and best_dist <= MAX_EQUIV_DISTANCE:
                ratio_ok = (
                    src_ratio > 0 and best_inst_ratio > 0
                    and max(src_ratio, best_inst_ratio) / min(src_ratio, best_inst_ratio) <= 3.0
                )
                if ratio_ok:
                    matches[other_prov] = {
                        "family": best_fam,
                        "instanceType": best_inst_type,
                        "distance": round(best_dist, 4),
                        "ratioMatch": True,
                    }
        equivalents[f"{prov}/{fam}"] = {
            "provider": prov,
            "family": fam,
            "instanceType": rep_instance.get((prov, fam), ""),
            "vCPU": profile[0],
            "memoryGiB": profile[1],
            "equivalents": matches,
        }

    return equivalents


# ---------------------------------------------------------------------------
# Index construction
# ---------------------------------------------------------------------------

def build_index(
    all_families: dict[str, dict[str, list[dict[str, Any]]]],
    family_metrics: dict[str, dict[str, dict[str, Any]]],
    instance_counts: dict[str, int],
    now: str,
) -> dict:
    providers_meta: list[dict] = []

    for provider in PROVIDERS:
        families = all_families.get(provider, {})
        if not families:
            continue

        all_instances = [i for insts in families.values() for i in insts]
        vcpus = sorted({i.get("vCPU", 0) for i in all_instances if i.get("vCPU")})
        rams = sorted({i.get("memoryGiB", 0) for i in all_instances if i.get("memoryGiB")})
        regions: list[str] = sorted({r for i in all_instances for r in cast(list[str], i.get("regions") or [])})

        # Commitment terms present
        terms: set[str] = set()
        for i in all_instances:
            for c in i.get("commitments", []):
                terms.add(c.get("term", ""))
        terms.discard("")

        family_summaries: list[dict] = []
        for fam_id, insts in sorted(families.items()):
            metrics = family_metrics.get(provider, {}).get(fam_id, {})
            fam_vcpus = sorted({i.get("vCPU", 0) for i in insts if i.get("vCPU")})
            fam_rams = sorted({i.get("memoryGiB", 0) for i in insts if i.get("memoryGiB")})
            has_gpu = any(i.get("gpu") for i in insts)
            archs = sorted({i.get("architecture", "") for i in insts} - {""})
            fam_terms: set[str] = set()
            for i in insts:
                for c in i.get("commitments", []):
                    fam_terms.add(c.get("term", ""))
            fam_terms.discard("")

            family_summaries.append({
                "id": fam_id,
                "count": len(insts),
                "vCPURange": [min(fam_vcpus), max(fam_vcpus)] if fam_vcpus else [0, 0],
                "ramRange": [min(fam_rams), max(fam_rams)] if fam_rams else [0, 0],
                "architectures": archs,
                "hasGPU": has_gpu,
                "commitmentTerms": sorted(fam_terms),
                **metrics,
            })

        providers_meta.append({
            "id": provider,
            "instanceCount": instance_counts.get(provider, 0),
            "familyCount": len(families),
            "regionCount": len(regions),
            "regions": regions,
            "vcpuRange": [min(vcpus), max(vcpus)] if vcpus else [0, 0],
            "ramRange": [min(rams), max(rams)] if rams else [0, 0],
            "commitmentTerms": sorted(terms),
            "families": family_summaries,
        })

    return {
        "schemaVersion": "3.0",
        "lastUpdated": now,
        "providers": providers_meta,
        "instanceCounts": instance_counts,
        "primaryRegions": PRIMARY_REGION,
    }


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def write_json(path: Path, obj: Any, compact: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    separators = (",", ":") if compact else None
    content = json.dumps(obj, ensure_ascii=False, separators=separators)
    path.write_text(content, encoding="utf-8")
    return len(content)


def slim_for_family(inst: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in inst.items() if k not in FAMILY_STRIP}


def _normalize_region_pricing(raw_rp: Any) -> dict[str, dict[str, float]] | None:
    """
    Normalize regionPricing to {region: {priceUSD_hourly, priceUSD_monthly}}.
    Raw fetchers may store plain floats (hourly) or dicts with an 'onDemand' key.
    """
    if not raw_rp or not isinstance(raw_rp, dict):
        return None
    typed_rp = cast(dict[str, Any], raw_rp)
    result: dict[str, dict[str, float]] = {}
    for region, val in typed_rp.items():
        if isinstance(val, (int, float)) and val > 0:
            hourly = float(val)
        elif isinstance(val, dict):
            val_dict = cast(dict[str, Any], val)
            raw_h = val_dict.get("priceUSD_hourly") or val_dict.get("onDemand")
            if raw_h is None or not isinstance(raw_h, (int, float)) or raw_h <= 0:
                continue
            hourly = float(raw_h)
        else:
            continue
        result[region] = {
            "priceUSD_hourly": round(hourly, 8),
            "priceUSD_monthly": round(hourly * 730.44, 4),
        }
    return result or None


def slim_for_instance(inst: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in inst.items() if k not in INSTANCE_STRIP}
    normalized = _normalize_region_pricing(out.get("regionPricing"))
    if normalized is not None:
        out["regionPricing"] = normalized
    elif "regionPricing" in out:
        del out["regionPricing"]
    return out


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------

def latest_timestamp(all_raw: dict[str, list[dict]]) -> str:
    """
    Derive lastUpdated from the raw data rather than wall-clock time so
    that re-running aggregate.py on unchanged input produces byte-identical
    output (idempotency requirement).
    """
    stamps: list[str] = []
    for instances in all_raw.values():
        for inst in instances:
            ts = inst.get("lastUpdated")
            if ts and isinstance(ts, str):
                stamps.append(ts)
    return max(stamps) if stamps else datetime.now(timezone.utc).isoformat()


def clean_output_dirs() -> None:
    """Delete all previously generated output so stale files never accumulate."""
    for path in (FAMILIES_DIR, INSTANCES_DIR, PRODUCTS_DIR):
        if path.exists():
            shutil.rmtree(path)
    for path in (DATA_DIR / "index.json", DATA_DIR / "equivalents.json"):
        if path.exists():
            path.unlink()


# ---------------------------------------------------------------------------
# Product aggregation (non-compute types)
# ---------------------------------------------------------------------------

def merge_object_storage_records(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Collapse per-region object-storage records into one entry per instanceType.
    Raw fetchers emit one record per (instanceType, region); this produces a
    single record with unioned regions and min/max pricePerGiBMonth.
    """
    by_type: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for rec in recs:
        key = rec.get("instanceType", "")
        price = rec.get("pricePerGiBMonth")
        if key not in by_type:
            entry = {k: v for k, v in rec.items() if k != "raw"}
            entry["minPricePerGiBMonth"] = price
            entry["maxPricePerGiBMonth"] = price
            by_type[key] = entry
            order.append(key)
        else:
            entry = by_type[key]
            if price is not None:
                if entry["minPricePerGiBMonth"] is None or price < entry["minPricePerGiBMonth"]:
                    entry["minPricePerGiBMonth"] = price
                if entry["maxPricePerGiBMonth"] is None or price > entry["maxPricePerGiBMonth"]:
                    entry["maxPricePerGiBMonth"] = price
            existing_regions: set[str] = set(entry.get("regions") or [])
            existing_regions.update(rec.get("regions") or [])
            entry["regions"] = sorted(existing_regions)
    result = [by_type[k] for k in order]
    result.sort(key=lambda r: r.get("minPricePerGiBMonth") or 0)
    return result


def aggregate_products(
    all_product_records: dict[str, list[dict[str, Any]]],
    now: str,
) -> dict[str, Any]:
    """
    Write data/products/{provider}/{category}.json for each non-compute product
    type and return the content for data/products/index.json.

    Output files are JSON arrays — one per (provider, category) — analogous to
    the per-family compute files.  Each record is slimmed (raw field stripped).
    """
    provider_summaries: dict[str, dict[str, Any]] = {}

    for provider, records in all_product_records.items():
        if not records:
            continue

        by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rec in records:
            cat = PRODUCT_CATEGORY_MAP.get(rec.get("type", ""), "other")
            by_category[cat].append(rec)

        categories: dict[str, Any] = {}
        for category, recs in sorted(by_category.items()):
            if category == "object-storage":
                slim_recs = merge_object_storage_records(recs)
            else:
                slim_recs = [{k: v for k, v in r.items() if k != "raw"} for r in recs]
            path = PRODUCTS_DIR / provider / f"{category}.json"
            size = write_json(path, slim_recs)
            categories[category] = {
                "count": len(slim_recs),
                "file": f"products/{provider}/{category}.json",
                "types": sorted({r.get("type", "?") for r in recs}),
            }
            print(f"  {provider}/{category}: {len(slim_recs)} records ({size // 1024} KB)")

        provider_summaries[provider] = categories

    return {
        "schemaVersion": "3.0",
        "lastUpdated": now,
        "providers": provider_summaries,
    }


def aggregate() -> bool:
    print("\n=== CloudPriceFinder Aggregator ===\n")

    # 0. Clean previous output so stale files never accumulate
    print("Cleaning previous output ...")
    clean_output_dirs()

    # Warn if raw files exist for providers not in PROVIDERS
    discovered = {p.stem.replace(".raw", "") for p in PROVIDERS_DIR.glob("*.raw.json")}
    unknown = discovered - set(PROVIDERS)
    if unknown:
        print(f"  WARNING: raw files found for unlisted providers: {', '.join(sorted(unknown))}")

    # 1. Load raw data
    print("Loading raw provider data ...")
    t0 = time.perf_counter()
    all_raw: dict[str, list[dict[str, Any]]] = {}
    for provider in PROVIDERS:
        all_raw[provider] = load_raw(provider)
    print(f"  ({time.perf_counter() - t0:.1f}s)")

    total_loaded = sum(len(v) for v in all_raw.values())
    if total_loaded == 0:
        print("ERROR: no raw data found for any provider. Run fetchers first.")
        return False

    # 1b. Merge duplicate instance types (same instanceType + marketSegment)
    print("Merging duplicate instance types ...")
    t0 = time.perf_counter()
    for provider in PROVIDERS:
        before = len(all_raw[provider])
        all_raw[provider] = merge_instances(all_raw[provider])
        merged = before - len(all_raw[provider])
        if merged:
            print(f"  {provider}: merged {merged} duplicate entr{'y' if merged == 1 else 'ies'}")
    print(f"  ({time.perf_counter() - t0:.1f}s)")

    # 1c. Route non-compute product types out of the compute pipeline.
    # Records with types not in COMPUTE_TYPES are separated here so that
    # steps 2–7 (family/instance/equivalents/index) only see compute records.
    print("Routing product types ...")
    all_product_records: dict[str, list[dict[str, Any]]] = {}
    for provider in PROVIDERS:
        compute_recs: list[dict[str, Any]] = []
        product_recs: list[dict[str, Any]] = []
        for rec in all_raw[provider]:
            t = rec.get("type", "cloud-server")
            if t in COMPUTE_TYPES:
                compute_recs.append(rec)
            elif t in PRODUCT_CATEGORY_MAP:
                product_recs.append(rec)
            # unknown types silently dropped
        all_raw[provider] = compute_recs
        if product_recs:
            all_product_records[provider] = product_recs
            print(f"  {provider}: {len(product_recs)} non-compute records routed to products")

    # 1d. Load additional product-specific raw files (aws_storage, aws_databases, aws_ai …)
    for provider, sources in PRODUCT_SOURCES.items():
        for source in sources:
            recs = load_raw(source)
            if not recs:
                continue
            routed = [r for r in recs if r.get("type") in PRODUCT_CATEGORY_MAP]
            if routed:
                all_product_records.setdefault(provider, []).extend(routed)
                print(f"  {source}: {len(routed)} product records loaded")

    # Derive stable timestamp from the raw data (idempotency)
    now = latest_timestamp(all_raw)
    print(f"\nData timestamp: {now}")

    # 2. Group by family
    print("\nGrouping by family ...")
    t0 = time.perf_counter()
    all_families: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for provider, instances in all_raw.items():
        groups = group_by_family(instances)
        all_families[provider] = groups
        print(f"  {provider}: {len(instances)} instances -> {len(groups)} families")
    print(f"  ({time.perf_counter() - t0:.1f}s)")

    # 3. Compute family-level metrics
    print("\nComputing normalized metrics ...")
    t0 = time.perf_counter()
    family_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for provider, families in all_families.items():
        family_metrics[provider] = {}
        for fam_id, insts in families.items():
            family_metrics[provider][fam_id] = compute_family_metrics(insts)
    print(f"  ({time.perf_counter() - t0:.1f}s)")

    # 4. Write family files
    print("\nWriting family files ...")
    t0 = time.perf_counter()
    oversize_families: list[str] = []
    family_file_count = 0
    for provider, families in all_families.items():
        for fam_id, insts in families.items():
            # Build per-instanceType OS price map and embed in each record so the
            # UI can show a primary-region OS comparison without extra fetches.
            # Only include shared-tenancy records so dedicated/host variants don't
            # overwrite or mix with standard prices.
            os_price_map: dict[str, dict[str, float]] = defaultdict(dict)
            for inst in insts:
                if (inst.get("tenancy") or "shared") != "shared":
                    continue
                it = inst.get("instanceType", "")
                os = inst.get("operatingSystem") or "Linux"
                price = on_demand_price_in_primary(inst)
                if price and price > 0:
                    os_price_map[it][os] = price
            for inst in insts:
                it = inst.get("instanceType", "")
                if len(os_price_map[it]) > 1:
                    inst["osPricing"] = dict(os_price_map[it])

            payload = [slim_for_family(i) for i in insts]
            path = FAMILIES_DIR / provider / f"{safe_id(fam_id)}.json"
            size = write_json(path, payload)
            family_file_count += 1
            kb = size / 1024
            if size > 250 * 1024:
                oversize_families.append(f"{provider}/{fam_id} ({kb:.0f} KB)")
                print(f"  WARNING: {provider}/{fam_id} = {kb:.0f} KB > 250 KB")
    print(f"  Wrote {family_file_count} family files ({time.perf_counter() - t0:.1f}s)")

    # 5. Write instance files
    print("\nWriting instance files ...")
    t0 = time.perf_counter()
    oversize_instances: list[str] = []
    instance_file_count = 0
    instance_counts: dict[str, int] = {}
    # Track seen slugs per provider to disambiguate duplicates (e.g. Azure global vs china)
    for provider, instances in all_raw.items():
        instance_counts[provider] = len(instances)
        seen_slugs: set[str] = set()
        for inst in instances:
            it = inst.get("instanceType", "unknown")
            base_slug = safe_id(it)
            os_name = inst.get("operatingSystem") or "Linux"
            market = inst.get("marketSegment", "") or ""
            tenancy = inst.get("tenancy") or "shared"
            has_os = os_name != "Linux"
            has_market = bool(market) and market != "global"
            has_tenancy = tenancy != "shared"
            parts = [base_slug]
            if has_os:
                parts.append(safe_id(os_name))
            if has_market:
                parts.append(safe_id(market))
            if has_tenancy:
                parts.append(safe_id(tenancy))
            slug = "-".join(parts)
            if slug in seen_slugs:
                print(f"  WARNING: duplicate slug '{provider}/{slug}' — check OS/market data")
            seen_slugs.add(slug)
            payload = slim_for_instance(inst)
            path = INSTANCES_DIR / provider / f"{slug}.json"
            size = write_json(path, payload)
            instance_file_count += 1
            if size > 20 * 1024:
                oversize_instances.append(f"{provider}/{it} ({size//1024} KB)")
    print(f"  Wrote {instance_file_count} instance files")
    if oversize_instances:
        print(f"  WARNING: {len(oversize_instances)} instance files > 20 KB:")
        for s in oversize_instances[:10]:
            print(f"    {s}")
    print(f"  ({time.perf_counter() - t0:.1f}s)")

    # 6. Build and write equivalents
    print("\nBuilding cross-provider equivalents ...")
    t0 = time.perf_counter()
    equivalents = build_equivalents(all_families)
    eq_path = DATA_DIR / "equivalents.json"
    write_json(eq_path, equivalents)
    print(f"  {len(equivalents)} family equivalents computed -> {eq_path.name} ({time.perf_counter() - t0:.1f}s)")

    # 7. Build and write index
    print("\nBuilding index ...")
    t0 = time.perf_counter()
    index = build_index(all_families, family_metrics, instance_counts, now)
    index_path = DATA_DIR / "index.json"
    index_size = write_json(index_path, index)
    print(f"  index.json: {index_size // 1024} KB")
    if index_size > 100 * 1024:
        print(f"  WARNING: index.json ({index_size // 1024} KB) exceeds 100 KB target")
    print(f"  ({time.perf_counter() - t0:.1f}s)")

    # 7b. Write product files (storage, database, ai, networking …)
    if any(v for v in all_product_records.values()):
        print("\nWriting product files ...")
        products_summary = aggregate_products(all_product_records, now)
        write_json(PRODUCTS_DIR / "index.json", products_summary)
        total_products = sum(
            cat["count"]
            for cats in products_summary.get("providers", {}).values()
            for cat in cats.values()
        )
        print(f"  products/index.json: {total_products} records across "
              f"{len(products_summary.get('providers', {}))} provider(s)")

    # 8. Summary
    print(f"\n{'='*50}")
    print("Aggregation complete")
    print(f"  Providers:   {', '.join(p for p in PROVIDERS if all_raw.get(p))}")
    print(f"  Instances:   {sum(instance_counts.values())}")
    print(f"  Families:    {family_file_count} files")
    print(f"  Instance files: {instance_file_count} files")
    print(f"  index.json:  {index_size // 1024} KB")

    if oversize_families:
        print(f"\nERROR: {len(oversize_families)} family file(s) exceed 250 KB:")
        for s in oversize_families[:10]:
            print(f"  {s}")
        if len(oversize_families) > 10:
            print(f"  ...and {len(oversize_families) - 10} more")
        return False

    print("\nAll size constraints satisfied. OK")
    return True


if __name__ == "__main__":
    ok = aggregate()
    sys.exit(0 if ok else 1)
