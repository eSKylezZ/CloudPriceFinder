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
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PROVIDERS_DIR = DATA_DIR / "providers"
FAMILIES_DIR = DATA_DIR / "families"
INSTANCES_DIR = DATA_DIR / "instances"

PROVIDERS = ["aws", "azure", "gcp", "oci"]

# Fields kept in family-level summary entries (strips raw/locationDetails/regionPricing)
FAMILY_FIELDS = {
    "provider", "type", "instanceType", "vCPU", "memoryGiB", "architecture",
    "family", "generation", "gpu", "priceUSD_hourly", "priceUSD_monthly",
    "commitments", "regions", "source", "lastUpdated", "marketSegment",
}

# Fields stripped from instance detail files (raw is debug-only noise)
INSTANCE_STRIP = {"raw"}

# Primary region per provider for normalized $/vCPU and $/GiB metrics
PRIMARY_REGION: dict[str, str] = {
    "aws": "us-east-1",
    "azure": "eastus",
    "gcp": "us-central1",
    "oci": "us-ashburn-1",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_raw(provider: str) -> list[dict]:
    """Load raw instances for a provider, trying .raw.json then fallbacks."""
    candidates = [
        PROVIDERS_DIR / f"{provider}.raw.json",
        PROVIDERS_DIR / f"{provider}_test.json",
        PROVIDERS_DIR / f"{provider}.json",
    ]
    for path in candidates:
        if path.exists():
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
    print(f"  WARNING: no raw data found for {provider}")
    return []


def safe_id(text: str) -> str:
    """Convert instance type / family name to a filesystem-safe slug."""
    return re.sub(r"[^a-zA-Z0-9._-]", "-", text).lower().strip("-")


def azure_family_id(inst: dict) -> str:
    """
    Derive a granular Azure family ID.  The raw `family` field uses single
    letters (d, e, m, ...) which produce files > 250 KB.  Split the two
    largest families (d, e) by version.
    """
    base = inst.get("family", "other")
    if base in ("d", "e"):
        m = re.search(r"_v(\d+)", inst.get("instanceType", ""))
        if m:
            return f"{base}-v{m.group(1)}"
    return base


def get_family_id(inst: dict) -> str:
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


# ---------------------------------------------------------------------------
# Family ID computation (grouped per provider)
# ---------------------------------------------------------------------------

def group_by_family(instances: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for inst in instances:
        groups[get_family_id(inst)].append(inst)
    return dict(groups)


# ---------------------------------------------------------------------------
# Normalized metrics per family
# ---------------------------------------------------------------------------

def compute_family_metrics(instances: list[dict]) -> dict:
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

def build_equivalents(
    all_families: dict[str, dict[str, list[dict]]],
) -> dict:
    """
    For each (provider, family), find the closest equivalent family in every
    other provider.  Similarity is measured by the Euclidean distance in
    log2-normalised (vCPU, RAM) space on the family's median instance.
    """
    import math

    # Build representative (vCPU, RAM) per provider+family
    rep: dict[tuple[str, str], tuple[float, float]] = {}
    for provider, families in all_families.items():
        for fam_id, insts in families.items():
            vcpus = [i.get("vCPU", 0) or 0 for i in insts]
            gibs = [i.get("memoryGiB", 0) or 0 for i in insts]
            vcpu = statistics.median([v for v in vcpus if v > 0] or [0])
            gib = statistics.median([g for g in gibs if g > 0] or [0])
            if vcpu > 0 and gib > 0:
                rep[(provider, fam_id)] = (vcpu, gib)

    def log_dist(a: tuple[float, float], b: tuple[float, float]) -> float:
        import math
        dv = math.log2(a[0] + 1) - math.log2(b[0] + 1)
        dm = math.log2(a[1] + 1) - math.log2(b[1] + 1)
        return math.sqrt(dv * dv + dm * dm)

    equivalents: dict[str, dict] = {}
    for (prov, fam), profile in rep.items():
        matches: dict[str, dict] = {}
        for other_prov, families in all_families.items():
            if other_prov == prov:
                continue
            best_fam = None
            best_dist = float("inf")
            for other_fam in families:
                other_key = (other_prov, other_fam)
                if other_key not in rep:
                    continue
                d = log_dist(profile, rep[other_key])
                if d < best_dist:
                    best_dist = d
                    best_fam = other_fam
            if best_fam is not None:
                matches[other_prov] = {
                    "family": best_fam,
                    "distance": round(best_dist, 4),
                }
        equivalents[f"{prov}/{fam}"] = {
            "provider": prov,
            "family": fam,
            "vCPU": profile[0],
            "memoryGiB": profile[1],
            "equivalents": matches,
        }

    return equivalents


# ---------------------------------------------------------------------------
# Index construction
# ---------------------------------------------------------------------------

def build_index(
    all_families: dict[str, dict[str, list[dict]]],
    family_metrics: dict[str, dict[str, dict]],
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
        regions = sorted({r for i in all_instances for r in (i.get("regions") or [])})

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
    sep = (",", ":") if compact else (", ", ": ")
    content = json.dumps(obj, ensure_ascii=False, separators=sep if compact else None)
    path.write_text(content, encoding="utf-8")
    return len(content)


def slim_for_family(inst: dict) -> dict:
    return {k: v for k, v in inst.items() if k in FAMILY_FIELDS}


def slim_for_instance(inst: dict) -> dict:
    return {k: v for k, v in inst.items() if k not in INSTANCE_STRIP}


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
    import shutil
    for path in (FAMILIES_DIR, INSTANCES_DIR):
        if path.exists():
            shutil.rmtree(path)
    for path in (DATA_DIR / "index.json", DATA_DIR / "equivalents.json"):
        if path.exists():
            path.unlink()


def aggregate() -> bool:
    print(f"\n=== CloudPriceFinder Aggregator ===\n")

    # 0. Clean previous output so stale files never accumulate
    print("Cleaning previous output ...")
    clean_output_dirs()

    # 1. Load raw data
    print("Loading raw provider data ...")
    all_raw: dict[str, list[dict]] = {}
    for provider in PROVIDERS:
        all_raw[provider] = load_raw(provider)

    total_loaded = sum(len(v) for v in all_raw.values())
    if total_loaded == 0:
        print("ERROR: no raw data found for any provider. Run fetchers first.")
        return False

    # Derive stable timestamp from the raw data (idempotency)
    now = latest_timestamp(all_raw)
    print(f"\nData timestamp: {now}")

    # 2. Group by family
    print("\nGrouping by family ...")
    all_families: dict[str, dict[str, list[dict]]] = {}
    for provider, instances in all_raw.items():
        groups = group_by_family(instances)
        all_families[provider] = groups
        print(f"  {provider}: {len(instances)} instances -> {len(groups)} families")

    # 3. Compute family-level metrics
    print("\nComputing normalized metrics ...")
    family_metrics: dict[str, dict[str, dict]] = {}
    for provider, families in all_families.items():
        family_metrics[provider] = {}
        for fam_id, insts in families.items():
            family_metrics[provider][fam_id] = compute_family_metrics(insts)

    # 4. Write family files
    print("\nWriting family files ...")
    oversize_families: list[str] = []
    family_file_count = 0
    for provider, families in all_families.items():
        for fam_id, insts in families.items():
            payload = [slim_for_family(i) for i in insts]
            path = FAMILIES_DIR / provider / f"{safe_id(fam_id)}.json"
            size = write_json(path, payload)
            family_file_count += 1
            kb = size / 1024
            if size > 250 * 1024:
                oversize_families.append(f"{provider}/{fam_id} ({kb:.0f} KB)")
                print(f"  WARNING: {provider}/{fam_id} = {kb:.0f} KB > 250 KB")
    print(f"  Wrote {family_file_count} family files")

    # 5. Write instance files
    print("\nWriting instance files ...")
    oversize_instances: list[str] = []
    instance_file_count = 0
    instance_counts: dict[str, int] = {}
    # Track seen slugs per provider to disambiguate duplicates (e.g. Azure global vs china)
    for provider, instances in all_raw.items():
        instance_counts[provider] = len(instances)
        seen_slugs: dict[str, int] = {}
        for inst in instances:
            it = inst.get("instanceType", "unknown")
            base_slug = safe_id(it)
            # For Azure, append market segment to disambiguate global vs china variants
            market = inst.get("marketSegment", "")
            if market and market != "global":
                slug = f"{base_slug}-{safe_id(market)}"
            else:
                slug = base_slug
            # Final dedup guard: suffix with counter if still colliding
            if slug in seen_slugs:
                seen_slugs[slug] += 1
                slug = f"{slug}-{seen_slugs[slug]}"
            else:
                seen_slugs[slug] = 0
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

    # 6. Build and write equivalents
    print("\nBuilding cross-provider equivalents ...")
    equivalents = build_equivalents(all_families)
    eq_path = DATA_DIR / "equivalents.json"
    write_json(eq_path, equivalents)
    print(f"  {len(equivalents)} family equivalents computed -> {eq_path.name}")

    # 7. Build and write index
    print("\nBuilding index ...")
    index = build_index(all_families, family_metrics, instance_counts, now)
    index_path = DATA_DIR / "index.json"
    index_size = write_json(index_path, index)
    print(f"  index.json: {index_size // 1024} KB")
    if index_size > 100 * 1024:
        print(f"  WARNING: index.json ({index_size // 1024} KB) exceeds 100 KB target")

    # 8. Summary
    print(f"\n{'='*50}")
    print("Aggregation complete")
    print(f"  Providers:   {', '.join(p for p in PROVIDERS if all_raw.get(p))}")
    print(f"  Instances:   {sum(instance_counts.values())}")
    print(f"  Families:    {family_file_count} files")
    print(f"  Instances:   {instance_file_count} files")
    print(f"  index.json:  {index_size // 1024} KB")

    if oversize_families:
        print(f"\nERROR: {len(oversize_families)} family file(s) exceed 250 KB:")
        for s in oversize_families:
            print(f"  {s}")
        return False

    print("\nAll size constraints satisfied. OK")
    return True


if __name__ == "__main__":
    ok = aggregate()
    sys.exit(0 if ok else 1)
