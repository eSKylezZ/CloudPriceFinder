#!/usr/bin/env python3
"""
CloudPriceFinder Extras Orchestrator
Runs extras fetchers (spot, storage, object storage, databases, AI, OCI bare metal)
independently of the main compute pipeline.

Usage:
  python scripts/extras/orchestrator.py                          # all categories, all providers
  python scripts/extras/orchestrator.py --category spot          # one category
  python scripts/extras/orchestrator.py --category databases --provider aws
"""

import sys
import json
import time
import logging
import importlib
import concurrent.futures
import argparse
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Optional, List, Tuple

# ── Windows UTF-8 stdout ─────────────────────────────────────────────────────
# Prevents UnicodeEncodeError for emoji on cp1252 terminals (Windows default).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Path setup ────────────────────────────────────────────────────────────────
_EXTRAS_DIR = Path(__file__).parent          # scripts/extras/
_SCRIPTS_DIR = _EXTRAS_DIR.parent           # scripts/
_REPO_ROOT = _SCRIPTS_DIR.parent            # repo root

# Allow `import fetch_spot` (extras modules) and `import fetch_aws` (main modules)
for _p in [str(_EXTRAS_DIR), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROVIDERS_DIR = _REPO_ROOT / "data" / "providers"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MAX_WORKERS = 6
TIMEOUT_SECONDS = 300  # 5 minutes per task

# ── Category × Provider configuration ────────────────────────────────────────
# Set enabled: False to skip a category or an individual provider without
# removing the entry.  All default to True — extras are opt-in to run at all,
# so once you're running this script you probably want everything.
CATEGORY_CONFIG: dict = {
    "spot": {
        "enabled": False,
        "description": "Spot / Preemptible pricing",
        "module": "fetch_spot",
        "providers": {
            "aws":   {"enabled": True,  "description": "AWS EC2 Spot"},
            "gcp":   {"enabled": True,  "description": "GCP Preemptible"},
            "azure": {"enabled": True,  "description": "Azure Spot"},
        },
    },
    "storage": {
        "enabled": True,
        "description": "Block storage pricing (GCP PD, Azure Managed Disks, OCI Block Volumes)",
        "module": "fetch_storage",
        "providers": {
            # aws removed — EBS is emitted by scripts/fetch_aws.py as a side output
            "gcp":   {"enabled": True,  "description": "GCP Persistent Disk"},
            "azure": {"enabled": True,  "description": "Azure Managed Disks"},
            "oci":   {"enabled": True,  "description": "OCI Block Volumes"},
        },
    },
    "object_storage": {
        "enabled": True,
        "description": "Object storage pricing (S3, GCS, Azure Blob)",
        "module": "fetch_object_storage",
        "providers": {
            "aws":   {"enabled": True,  "description": "AWS S3"},
            "gcp":   {"enabled": True,  "description": "GCP Cloud Storage"},
            "azure": {"enabled": True,  "description": "Azure Blob Storage"},
        },
    },
    "databases": {
        "enabled": True,
        "description": "Managed database pricing (RDS, Cloud SQL, Azure DB)",
        "module": "fetch_databases",
        "providers": {
            "aws":   {"enabled": True,  "description": "AWS RDS"},
            "gcp":   {"enabled": True,  "description": "GCP Cloud SQL"},
            "azure": {"enabled": True,  "description": "Azure Database"},
        },
    },
    "ai": {
        "enabled": True,
        "description": "LLM / AI inference pricing (Bedrock, Vertex AI, Azure OpenAI)",
        "module": "fetch_ai",
        "providers": {
            "aws":   {"enabled": True,  "description": "AWS Bedrock"},
            "gcp":   {"enabled": True,  "description": "GCP Vertex AI"},
            "azure": {"enabled": True,  "description": "Azure OpenAI"},
        },
    },
    "oci_baremetal": {
        "enabled": True,
        "description": "OCI Bare Metal compute shapes",
        "module": "fetch_oci_baremetal",  # scripts/extras/fetch_oci_baremetal.py
        "providers": {
            "oci": {"enabled": True, "description": "OCI Bare Metal"},
        },
    },
}

# ── Output filename suffix per category ──────────────────────────────────────
_OUTPUT_SUFFIX = {
    "spot":           ".spot.raw.json",
    "storage":        ".storage.raw.json",
    "object_storage": ".object-storage.raw.json",
    "databases":      ".databases.raw.json",
    "ai":             ".ai.raw.json",
    "oci_baremetal":  ".baremetal.raw.json",
}


# ── Task result ───────────────────────────────────────────────────────────────
class TaskResult(NamedTuple):
    category: str
    provider: str
    record_count: int
    output_file: str        # relative to repo root for display
    duration_s: float
    status: str             # ok | failed | skipped
    error: str


# ── Core task runner ──────────────────────────────────────────────────────────
def _output_path(category: str, provider: str) -> Path:
    suffix = _OUTPUT_SUFFIX[category]
    return PROVIDERS_DIR / f"{provider}{suffix}"


def _load_fetcher(module_name: str):
    """Import an extras fetcher module; return None if not yet created."""
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def _run_task(category: str, provider: str) -> TaskResult:
    """Fetch one (category, provider) pair and write its raw JSON output."""
    out_path = _output_path(category, provider)
    t0 = time.monotonic()

    module_name = CATEGORY_CONFIG[category]["module"]
    module = _load_fetcher(module_name)

    if module is None:
        dur = round(time.monotonic() - t0, 2)
        logger.warning("⏭  %s/%s: module %r not found — skipped", category, provider, module_name)
        return TaskResult(category, provider, 0, out_path.name, dur, "skipped", "module not found")

    fetch_fn = getattr(module, "fetch_data", None)
    if fetch_fn is None:
        dur = round(time.monotonic() - t0, 2)
        logger.warning("⏭  %s/%s: fetch_data() not in %r — skipped", category, provider, module_name)
        return TaskResult(category, provider, 0, out_path.name, dur, "skipped", "fetch_data() missing")

    try:
        logger.info("🔄 %s/%s: starting fetch…", category, provider)
        records = fetch_fn(provider)
        dur = round(time.monotonic() - t0, 2)

        PROVIDERS_DIR.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)

        logger.info("✅ %s/%s: %d records in %.1fs → %s", category, provider, len(records), dur, out_path.name)
        return TaskResult(category, provider, len(records), out_path.name, dur, "ok", "")

    except Exception as exc:
        dur = round(time.monotonic() - t0, 2)
        logger.error("❌ %s/%s: %s", category, provider, exc)
        return TaskResult(category, provider, 0, out_path.name, dur, "failed", str(exc))


# ── Task selection ────────────────────────────────────────────────────────────
def _build_tasks(
    category_filter: Optional[str],
    provider_filter: Optional[str],
) -> List[Tuple[str, str]]:
    tasks: List[Tuple[str, str]] = []
    for cat, cat_cfg in CATEGORY_CONFIG.items():
        if not cat_cfg["enabled"]:
            continue
        if category_filter and cat != category_filter:
            continue
        for prov, prov_cfg in cat_cfg["providers"].items():
            if not prov_cfg["enabled"]:
                continue
            if provider_filter and prov != provider_filter:
                continue
            tasks.append((cat, prov))
    return tasks


# ── Summary table ─────────────────────────────────────────────────────────────
def _print_summary(results: List[TaskResult]) -> None:
    if not results:
        return

    STATUS_ICON = {"ok": "✅", "failed": "❌", "skipped": "⏭ "}

    # Column widths
    w_cat  = max(len("category"),    *(len(r.category)       for r in results))
    w_prov = max(len("provider"),    *(len(r.provider)       for r in results))
    w_rec  = max(len("records"),     *(len(str(r.record_count)) for r in results))
    w_file = max(len("output_file"), *(len(r.output_file)    for r in results))
    w_dur  = max(len("dur(s)"),      *(len(f"{r.duration_s:.1f}") for r in results))
    w_stat = max(len("status"),      *(len(r.status)         for r in results))

    def _row(cat, prov, rec, fname, dur, stat) -> str:
        return (
            f"  {cat:<{w_cat}}  {prov:<{w_prov}}  "
            f"{rec:>{w_rec}}  {fname:<{w_file}}  "
            f"{dur:>{w_dur}}  {stat}"
        )

    header = _row("category", "provider", "records", "output_file", "dur(s)", "status")
    rule = "─" * len(header)
    sep  = "  " + "-" * (len(header) - 2)

    print(f"\n{rule}")
    print(header)
    print(sep)
    for r in sorted(results, key=lambda x: (x.category, x.provider)):
        icon = STATUS_ICON.get(r.status, "  ")
        print(_row(
            r.category, r.provider, str(r.record_count),
            r.output_file, f"{r.duration_s:.1f}", f"{icon} {r.status}",
        ))
    print(rule)

    ok      = sum(1 for r in results if r.status == "ok")
    failed  = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")
    total   = sum(r.record_count for r in results)
    print(f"  {ok} ok · {failed} failed · {skipped} skipped · {total:,} total records\n")

    if failed:
        print("Errors:")
        for r in results:
            if r.status == "failed":
                print(f"  {r.category}/{r.provider}: {r.error}")
        print()


# ── Configuration display ─────────────────────────────────────────────────────
def _print_config(category_filter: Optional[str], provider_filter: Optional[str]) -> None:
    print("\n🔧 Extras Configuration:")
    print("-" * 56)
    for cat, cat_cfg in CATEGORY_CONFIG.items():
        if category_filter and cat != category_filter:
            continue
        cat_status = "🔄 ENABLED " if cat_cfg["enabled"] else "⏭  DISABLED"
        print(f"  [{cat_status}] {cat.upper():<16} — {cat_cfg['description']}")
        for prov, prov_cfg in cat_cfg["providers"].items():
            if provider_filter and prov != provider_filter:
                continue
            prov_status = "enabled " if prov_cfg["enabled"] else "disabled"
            print(f"           {prov:<10} {prov_status} — {prov_cfg['description']}")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────
def run(
    category_filter: Optional[str] = None,
    provider_filter: Optional[str] = None,
) -> bool:
    print("=== CloudPriceFinder Extras Orchestrator ===")
    print(f"Timestamp: {datetime.now().isoformat()}")
    if category_filter:
        print(f"Category filter : {category_filter}")
    if provider_filter:
        print(f"Provider filter : {provider_filter}")

    _print_config(category_filter, provider_filter)

    tasks = _build_tasks(category_filter, provider_filter)
    if not tasks:
        print("No tasks match the given filters — nothing to do.")
        return True

    print(f"📡 Running {len(tasks)} task(s) with up to {MAX_WORKERS} workers…\n")

    results: List[TaskResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(_run_task, cat, prov): (cat, prov)
            for cat, prov in tasks
        }
        for future in concurrent.futures.as_completed(future_map, timeout=TIMEOUT_SECONDS):
            results.append(future.result())

    _print_summary(results)
    return not any(r.status == "failed" for r in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="CloudPriceFinder Extras Orchestrator — runs extras data fetchers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available categories: {', '.join(CATEGORY_CONFIG)}",
    )
    parser.add_argument(
        "--category",
        choices=list(CATEGORY_CONFIG.keys()),
        metavar="CATEGORY",
        help=f"Run only this category. Choices: {', '.join(CATEGORY_CONFIG)}",
    )
    parser.add_argument(
        "--provider",
        metavar="PROVIDER",
        help="Filter to a single provider (e.g. aws, gcp, azure, oci)",
    )
    args = parser.parse_args()

    success = run(category_filter=args.category, provider_filter=args.provider)
    sys.exit(0 if success else 1)
