#!/usr/bin/env python3
"""AI/LLM model pricing fetcher for CloudPriceFinder.

Fetches token-based pricing for:
  - AWS Bedrock  (HTML scrape of aws.amazon.com/bedrock/pricing/)
  - GCP Vertex AI (Cloud Billing Catalog API)
  - Azure OpenAI  (Azure Retail Prices API)

Output:
  data/providers/aws.ai.raw.json
  data/providers/gcp.ai.raw.json
  data/providers/azure.ai.raw.json

Usage:
  python scripts/extras/fetch_ai.py [--provider aws|gcp|azure|all]
  python scripts/extras/fetch_ai.py --provider aws
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
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Path setup — scripts/extras/ is two levels below the repo root
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent   # scripts/extras/
_REPO_ROOT = _SCRIPT_DIR.parent.parent          # repo root
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.http_client import USER_AGENT, make_session, get_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_ai")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NOW = datetime.now(timezone.utc).isoformat()
OUTPUT_DIR = _REPO_ROOT / "data" / "providers"

AWS_BEDROCK_PRICING_URL = "https://aws.amazon.com/bedrock/pricing/"
GCP_BILLING_BASE = "https://cloudbilling.googleapis.com/v1"
AZURE_RETAIL_URL = "https://prices.azure.com/api/retail/prices"
AZURE_API_VERSION = "2023-01-01-preview"

# ---------------------------------------------------------------------------
# Models to exclude (image generation, audio synthesis, embeddings, etc.)
# ---------------------------------------------------------------------------
_SKIP_KEYWORDS = frozenset([
    "dall-e", "imagen", "stable diffusion", "midjourney",
    "image generation", "imagegeneration",
    "embedding", "embeddings",
    "whisper", "text-to-speech", "tts",
    "vision ai", "natural language api",
    "speech", "translation api",
])


def _is_excluded_model(name: str) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in _SKIP_KEYWORDS)


# ---------------------------------------------------------------------------
# Known model metadata  (context_window_K, modality)
# Keyed by lowercase fragment matched against the model slug
# ---------------------------------------------------------------------------
_MODEL_META: Dict[str, Tuple[Optional[int], str]] = {
    "claude-3-7-sonnet":  (200,  "multimodal"),
    "claude-3-5-sonnet":  (200,  "multimodal"),
    "claude-3-5-haiku":   (200,  "multimodal"),
    "claude-3-opus":      (200,  "multimodal"),
    "claude-3-sonnet":    (200,  "multimodal"),
    "claude-3-haiku":     (200,  "multimodal"),
    "llama-3-3":          (128,  "text"),
    "llama-3-2":          (128,  "multimodal"),
    "llama-3-1":          (128,  "text"),
    "llama-3":            (8,    "text"),
    "mistral-large":      (128,  "text"),
    "mistral-small":      (32,   "text"),
    "mistral-7b":         (32,   "text"),
    "mixtral":            (32,   "text"),
    "command-r-plus":     (128,  "multimodal"),
    "command-r":          (128,  "text"),
    "titan-text-premier": (32,   "text"),
    "titan-text-express": (8,    "text"),
    "titan-text-lite":    (4,    "text"),
    "nova-pro":           (300,  "multimodal"),
    "nova-lite":          (300,  "multimodal"),
    "nova-micro":         (128,  "text"),
    "jamba":              (256,  "text"),
    "gemini-2-0-flash":   (1000, "multimodal"),
    "gemini-2-0":         (1000, "multimodal"),
    "gemini-1-5-pro":     (2000, "multimodal"),
    "gemini-1-5-flash":   (1000, "multimodal"),
    "gemini-1-0-pro":     (32,   "text"),
    "gemini-1-pro":       (32,   "text"),
    "gpt-4o-mini":        (128,  "multimodal"),
    "gpt-4o":             (128,  "multimodal"),
    "gpt-4-turbo":        (128,  "multimodal"),
    "gpt-4-32k":          (32,   "text"),
    "gpt-4":              (8,    "text"),
    "gpt-35-turbo-16k":   (16,   "text"),
    "gpt-35-turbo":       (16,   "text"),
    "o3-mini":            (200,  "text"),
    "o3":                 (200,  "text"),
    "o1-mini":            (128,  "text"),
    "o1":                 (200,  "text"),
}


def _get_model_meta(slug: str) -> Tuple[Optional[int], str]:
    slug_lower = slug.lower()
    for key, meta in sorted(_MODEL_META.items(), key=lambda x: -len(x[0])):
        if key in slug_lower:
            return meta
    return (None, "text")


# ---------------------------------------------------------------------------
# Slug / family helpers
# ---------------------------------------------------------------------------
def _slugify(name: str) -> str:
    s = name.lower()
    # Preserve dots between digits as hyphens (e.g. "3.5" -> "3-5")
    s = re.sub(r"(\d)\.(\d)", r"\1-\2", s)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


_VARIANT_SUFFIXES = frozenset([
    "sonnet", "haiku", "opus", "flash", "pro", "turbo", "mini", "lite",
    "micro", "nano", "large", "small", "medium", "instruct", "vision",
    "chat", "it", "preview", "express", "premier", "plus", "inference",
])

_SIZE_RE = re.compile(r"^\d+[bBkKmMgGtT]+$")
_DATE_RE = re.compile(r"-\d{8,}$")


def _extract_family(slug: str) -> str:
    s = _DATE_RE.sub("", slug)
    parts = s.split("-")
    while parts and (parts[-1].lower() in _VARIANT_SUFFIXES or _SIZE_RE.match(parts[-1])):
        parts.pop()
    return "-".join(parts) if parts else s


# ---------------------------------------------------------------------------
# Deduplication: collapse records with identical (model, input_price, output_price)
# into a single record with a merged regions list
# ---------------------------------------------------------------------------
def _dedup_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[Tuple, Dict[str, Any]] = {}
    for rec in records:
        key = (
            rec["instanceType"],
            rec.get("pricePerMInputTokens"),
            rec.get("pricePerMOutputTokens"),
        )
        if key in seen:
            merged = set(seen[key]["regions"]) | set(rec.get("regions", []))
            seen[key]["regions"] = sorted(merged)
        else:
            seen[key] = {**rec, "regions": sorted(rec.get("regions", []))}
    return list(seen.values())


# ===========================================================================
# AWS Bedrock — HTML scrape
# ===========================================================================

AWS_BEDROCK_MODEL_SLUGS: Dict[str, str] = {
    # Exact lower-case match → canonical instanceType slug
    "claude 3.7 sonnet":          "claude-3-7-sonnet-20250219",
    "claude 3.5 sonnet v2":       "claude-3-5-sonnet-20241022",
    "claude 3.5 sonnet":          "claude-3-5-sonnet-20240620",
    "claude 3.5 haiku":           "claude-3-5-haiku-20241022",
    "claude 3 opus":              "claude-3-opus-20240229",
    "claude 3 sonnet":            "claude-3-sonnet-20240229",
    "claude 3 haiku":             "claude-3-haiku-20240307",
    "amazon nova pro":            "nova-pro",
    "amazon nova lite":           "nova-lite",
    "amazon nova micro":          "nova-micro",
    "llama 3.3 70b instruct":     "llama-3-3-70b-instruct",
    "llama 3.2 90b vision":       "llama-3-2-90b-vision-instruct",
    "llama 3.2 11b vision":       "llama-3-2-11b-vision-instruct",
    "llama 3.2 3b instruct":      "llama-3-2-3b-instruct",
    "llama 3.2 1b instruct":      "llama-3-2-1b-instruct",
    "llama 3.1 405b instruct":    "llama-3-1-405b-instruct",
    "llama 3.1 70b instruct":     "llama-3-1-70b-instruct",
    "llama 3.1 8b instruct":      "llama-3-1-8b-instruct",
    "llama 3 70b instruct":       "llama-3-70b-instruct",
    "llama 3 8b instruct":        "llama-3-8b-instruct",
    "mistral large":              "mistral-large-2402",
    "mistral small":              "mistral-small-2402",
    "mistral 7b instruct":        "mistral-7b-instruct-v0-2",
    "mixtral 8x7b instruct":      "mixtral-8x7b-instruct-v0-1",
    "command r+":                 "command-r-plus",
    "command r":                  "command-r",
    "titan text premier":         "titan-text-premier-v1",
    "titan text express":         "titan-text-express-v1",
    "titan text lite":            "titan-text-lite-v1",
    "jamba 1.5 large":            "jamba-1-5-large",
    "jamba 1.5 mini":             "jamba-1-5-mini",
}


def _aws_model_to_slug(display_name: str) -> str:
    key = display_name.lower().strip()
    # Try exact and substring matches
    for known, slug in AWS_BEDROCK_MODEL_SLUGS.items():
        if known == key or known in key:
            return slug
    return _slugify(display_name)


def _parse_price_value(text: str, headers: List[str], col_idx: int) -> Optional[float]:
    """
    Return USD per 1M tokens from a price cell.

    Detects whether the table header indicates per-1K or per-1M tokens and
    scales accordingly.  Falls back to a magnitude heuristic when headers
    are ambiguous.
    """
    text = text.strip().replace(",", "")
    m = re.search(r"\$?([\d.]+)", text)
    if not m:
        return None
    price = float(m.group(1))
    if price == 0:
        return None

    # Try to determine the unit from the column header
    header = headers[col_idx].lower() if col_idx < len(headers) else ""
    if "1k" in header or "1,000" in header or "per 1k" in header:
        return round(price * 1_000.0, 6)   # per-1K -> per-1M
    if "1m" in header or "million" in header or "per 1m" in header:
        return round(price, 6)              # already per-1M

    # Heuristic: prices below $0.05 are almost certainly per-1K tokens
    # (the cheapest models cost ~$0.035/1M; that would be $0.000035/1K,
    # well below the threshold, so per-1K prices are identifiable).
    if price < 0.05:
        return round(price * 1_000.0, 6)
    return round(price, 6)


def _parse_bedrock_html(html: str) -> List[Dict[str, Any]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    records: List[Dict[str, Any]] = []
    tables = soup.find_all("table")
    logger.info(f"AWS Bedrock: found {len(tables)} tables in HTML")

    if not tables:
        raise ValueError("No <table> elements found — page structure may have changed")

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue

        # Parse header row
        header_cells = rows[0].find_all(["th", "td"])
        headers = [c.get_text(separator=" ", strip=True).lower() for c in header_cells]

        # Must have an input-token price column and an output-token price column
        input_idx = next(
            (i for i, h in enumerate(headers) if "input" in h and "output" not in h),
            None,
        )
        output_idx = next(
            (i for i, h in enumerate(headers) if "output" in h and "input" not in h),
            None,
        )
        if input_idx is None or output_idx is None:
            continue

        # Find model name column (first column that mentions "model" or is column 0)
        model_idx = next(
            (i for i, h in enumerate(headers) if "model" in h),
            0,
        )

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= max(input_idx, output_idx):
                continue

            model_text = cells[model_idx].get_text(separator=" ", strip=True) if model_idx < len(cells) else ""
            input_text = cells[input_idx].get_text(separator=" ", strip=True)
            output_text = cells[output_idx].get_text(separator=" ", strip=True) if output_idx < len(cells) else ""

            if not model_text or model_text.lower() in ("model", "model id", "models"):
                continue
            if _is_excluded_model(model_text):
                continue

            input_price = _parse_price_value(input_text, headers, input_idx)
            if input_price is None:
                continue
            # Sanity gate: realistic LLM per-1M token prices are < $500;
            # larger values are likely scraping artifacts (throughput, context sizes, etc.)
            if input_price > 500:
                continue
            output_price = _parse_price_value(output_text, headers, output_idx)

            slug = _aws_model_to_slug(model_text)
            family = _extract_family(slug)
            ctx_k, modality = _get_model_meta(slug)

            records.append({
                "provider": "aws",
                "type": "ai-model",
                "instanceType": slug,
                "family": family,
                "modelName": model_text,
                "priceUSD_hourly": None,
                "pricePerMInputTokens": input_price,
                "pricePerMOutputTokens": output_price,
                "contextWindowK": ctx_k,
                "modality": modality,
                "regions": ["us-east-1"],
                "source": "aws_bedrock_pricing_page",
                "lastUpdated": NOW,
                "pricingModel": "on-demand",
            })

    if not records:
        raise ValueError(
            "Parsed 0 model records from pricing tables — page structure may have changed"
        )
    return records


def fetch_aws_bedrock() -> List[Dict[str, Any]]:
    url = AWS_BEDROCK_PRICING_URL
    print(f"AWS Bedrock: fetching {url}", flush=True)
    logger.info(f"Fetching AWS Bedrock pricing from: {url}")

    try:
        session = make_session()
        session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        print(
            f"WARNING: failed to fetch AWS Bedrock pricing page ({url}): {exc}",
            file=sys.stderr,
        )
        logger.warning("AWS fetch failed: %s", exc)
        return []

    try:
        records = _parse_bedrock_html(html)
    except Exception as exc:
        print(
            f"WARNING: failed to parse AWS Bedrock pricing page ({url}): {exc}",
            file=sys.stderr,
        )
        logger.warning("AWS parse failed: %s", exc)
        return []

    logger.info("AWS Bedrock: parsed %d model records", len(records))
    return records


# ===========================================================================
# GCP Vertex AI — Cloud Billing Catalog API
# ===========================================================================

VERTEX_AI_SERVICE_NAME = "Vertex AI"

# GCP pricingExpression.usageUnit -> multiplier to reach price-per-1M-tokens.
# Gemini models are billed per character (~4 chars ≈ 1 token).
_GCP_UNIT_MULTIPLIERS: Dict[str, float] = {
    "character":       4_000_000.0,
    "char":            4_000_000.0,
    "token":           1_000_000.0,
    "kilo_token":      1_000.0,
    "kilotoken":       1_000.0,
    "1k_token":        1_000.0,
    "kilo_character":  4_000.0,
    "kilocharacter":   4_000.0,
    "1k_character":    4_000.0,
    "million_token":   1.0,
    "megacharacter":   4.0,
}


def _gcp_price_to_per_m_tokens(unit_price: float, usage_unit: str) -> float:
    unit_key = usage_unit.lower().replace(" ", "_")
    multiplier = _GCP_UNIT_MULTIPLIERS.get(unit_key, 1_000_000.0)
    return round(unit_price * multiplier, 6)


def _gcp_extract_model_name(description: str) -> Optional[str]:
    """Strip token-type suffixes from a Vertex AI SKU description."""
    desc = description.strip()
    suffixes = [
        " (within context window)", " (over 128k context window)",
        " (over 200k context window)", " per 1k characters",
        " text input", " text output", " image input", " audio input",
        " video input", " document input", " input tokens", " output tokens",
        " characters", " cached input",
    ]
    # Strip iteratively — a description may need multiple passes
    # e.g. "Gemini 1.5 Pro Text Input (within context window)"
    changed = True
    while changed:
        changed = False
        for sfx in suffixes:
            if desc.lower().endswith(sfx.lower()):
                desc = desc[: -len(sfx)].strip()
                changed = True
                break
    return desc or None


def _gcp_is_input(description: str) -> bool:
    low = description.lower()
    return ("input" in low or "prompt" in low) and "output" not in low


def _gcp_is_output(description: str) -> bool:
    low = description.lower()
    return ("output" in low or "completion" in low) and "input" not in low


def _gcp_extract_unit_price(pricing_info: List[Dict]) -> Tuple[Optional[float], str]:
    if not pricing_info:
        return None, ""
    expr = pricing_info[0].get("pricingExpression", {})
    usage_unit = expr.get("usageUnit", "")
    tiers = expr.get("tieredRates", [])
    if not tiers:
        return None, usage_unit
    for tier in tiers:
        if tier.get("startUsageAmount", 0) == 0:
            up = tier.get("unitPrice", {})
            price = int(up.get("units", 0)) + int(up.get("nanos", 0)) / 1_000_000_000.0
            return price, usage_unit
    up = tiers[0].get("unitPrice", {})
    price = int(up.get("units", 0)) + int(up.get("nanos", 0)) / 1_000_000_000.0
    return price, usage_unit


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
        logger.debug("GCP ADC not available: %s", exc)
        return None


def _gcp_make_session(api_key: Optional[str]) -> requests.Session:
    session = make_session()
    if not api_key:
        creds = _gcp_get_adc_credentials()
        if creds:
            try:
                import google.auth.transport.requests as g_transport
                creds.refresh(g_transport.Request())
                session.headers["Authorization"] = f"Bearer {creds.token}"
            except Exception:
                pass
    return session


def _gcp_paginate_skus(session: requests.Session, service_id: str, api_key: Optional[str]):
    url = f"{GCP_BILLING_BASE}/services/{service_id}/skus"
    page_token = None
    while True:
        params: Dict[str, Any] = {"pageSize": 5000}
        if api_key:
            params["key"] = api_key
        if page_token:
            params["pageToken"] = page_token
        data = get_json(session, url, params=params)
        yield from data.get("skus", [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break


def _gcp_load_api_key() -> Optional[str]:
    api_key = os.environ.get("GCP_API_KEY", "").strip() or None
    if not api_key:
        env_path = _REPO_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.strip().startswith("GCP_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'") or None
                    break
    return api_key


def fetch_gcp_vertex() -> List[Dict[str, Any]]:
    api_key = _gcp_load_api_key()
    session = _gcp_make_session(api_key)

    # Resolve Vertex AI service ID from the billing catalog
    logger.info("GCP Vertex AI: resolving service ID from Billing Catalog API...")
    try:
        params: Dict[str, Any] = {"pageSize": 500}
        if api_key:
            params["key"] = api_key
        services_data = get_json(session, f"{GCP_BILLING_BASE}/services", params=params)
    except Exception as exc:
        print(
            f"WARNING: GCP Billing API unreachable (auth or network): {exc}",
            file=sys.stderr,
        )
        logger.warning("GCP services fetch failed: %s", exc)
        return []

    vertex_service_id: Optional[str] = None
    for svc in services_data.get("services", []):
        if VERTEX_AI_SERVICE_NAME in svc.get("displayName", ""):
            vertex_service_id = svc["name"].split("/")[-1]
            logger.info("Found Vertex AI service: %s", svc["name"])
            break

    if vertex_service_id is None:
        print(
            "WARNING: Vertex AI service not found in GCP Billing Catalog. "
            "Check that the GCP_API_KEY has Cloud Billing API access.",
            file=sys.stderr,
        )
        logger.warning("Vertex AI service not found in GCP Billing Catalog")
        return []

    logger.info("GCP Vertex AI: fetching SKUs for service %s...", vertex_service_id)
    try:
        all_skus = list(_gcp_paginate_skus(session, vertex_service_id, api_key))
    except Exception as exc:
        print(f"WARNING: GCP Vertex AI SKU fetch failed: {exc}", file=sys.stderr)
        logger.warning("GCP SKU fetch failed: %s", exc)
        return []

    logger.info("GCP Vertex AI: fetched %d SKUs", len(all_skus))

    # model_name -> {"input": (price_per_m, regions), "output": (price_per_m, regions)}
    model_pricing: Dict[str, Dict[str, Any]] = {}

    for sku in all_skus:
        desc = sku.get("description", "")
        desc_lower = desc.lower()

        if _is_excluded_model(desc):
            continue

        # Limit to text/multimodal LLM token SKUs
        is_token_sku = any(kw in desc_lower for kw in (
            "text input", "text output", "input token", "output token",
            "prompt token", "completion token", "input characters", "output characters",
        ))
        if not is_token_sku:
            continue

        model_name = _gcp_extract_model_name(desc)
        if not model_name:
            continue

        price, usage_unit = _gcp_extract_unit_price(sku.get("pricingInfo", []))
        if price is None:
            continue

        price_per_m = _gcp_price_to_per_m_tokens(price, usage_unit)
        regions = sku.get("serviceRegions", [])

        if model_name not in model_pricing:
            model_pricing[model_name] = {}

        if _gcp_is_input(desc):
            existing = model_pricing[model_name].get("input")
            if existing is None or price_per_m < existing[0]:
                model_pricing[model_name]["input"] = (price_per_m, regions)
        elif _gcp_is_output(desc):
            existing = model_pricing[model_name].get("output")
            if existing is None or price_per_m < existing[0]:
                model_pricing[model_name]["output"] = (price_per_m, regions)

    records: List[Dict[str, Any]] = []
    for model_name, pricing_data in model_pricing.items():
        if "input" not in pricing_data:
            continue

        input_price, input_regions = pricing_data["input"]
        output_entry = pricing_data.get("output")
        output_price = output_entry[0] if output_entry else None
        output_regions = output_entry[1] if output_entry else []

        all_regions = sorted(set(input_regions) | set(output_regions))
        slug = _slugify(model_name)
        ctx_k, modality = _get_model_meta(slug)

        records.append({
            "provider": "gcp",
            "type": "ai-model",
            "instanceType": slug,
            "family": _extract_family(slug),
            "modelName": model_name,
            "priceUSD_hourly": None,
            "pricePerMInputTokens": input_price,
            "pricePerMOutputTokens": output_price,
            "contextWindowK": ctx_k,
            "modality": modality,
            "regions": all_regions,
            "source": "gcp_billing_catalog_api",
            "lastUpdated": NOW,
            "pricingModel": "on-demand",
        })

    logger.info("GCP Vertex AI: built %d model records", len(records))
    return records


# ===========================================================================
# Azure AI — Azure Retail Prices API ("Foundry Models" service)
#
# Azure renamed "Azure OpenAI" to the "Foundry Models" service in 2025.
# The meterName format is: "{model} {Inp|Outp} {region} [unit] Tokens"
# unitOfMeasure is "1K" or "1M" (tokens), which drives price conversion.
# ===========================================================================

AZURE_FOUNDRY_SERVICE = "Foundry Models"

# Products that are NOT LLM inference (image gen, embeddings, tools)
_AZURE_SKIP_PRODUCTS = frozenset([
    "Azure OpenAI Embedding",
    "Azure OpenAI Media",       # DALL-E / image generation
    "Azure BFL Flux Models",    # Flux image generation
    "Azure OpenAI PP GPT4s",    # provisioned GPT-4s
    "Azure Fireworks Models",   # fine-tuning tools
    "Foundry Tools",
])

# Meter-name word tokens (after split on whitespace+hyphens) that flag non-standard items.
# Also checked as substrings of the full lowercase meter name for compound words like "fine-tuned".
_AZURE_SKIP_WORDS = frozenset([
    "batch", "ft", "training", "trng", "cached", "grader",
    "provisioned", "hosting", "hstng", "deploy", "embed",
    "session", "code-interpreter", "tuned",
])

_AZURE_REGION_TAGS = frozenset([
    "glbl", "gl", "regnl", "dzone", "dz", "global", "regional", "local",
])
_AZURE_REGION_2WORD = frozenset(["data zone"])
_AZURE_UNIT_TAGS = frozenset(["1m", "1k"])
_AZURE_INPUT_TAGS = frozenset(["inp", "input", "prompt"])
_AZURE_OUTPUT_TAGS = frozenset(["outp", "output", "opt", "out"])


def _azure_should_skip(item: Dict) -> bool:
    if item.get("productName", "") in _AZURE_SKIP_PRODUCTS:
        return True
    meter = item.get("meterName", "").lower()
    if not meter.endswith("tokens"):
        return True
    meter_words = set(re.split(r"[\s\-]+", meter))
    return bool(meter_words & _AZURE_SKIP_WORDS)


def _azure_parse_meter(meter_name: str) -> Optional[Tuple[str, str]]:
    """
    Extract (raw_model_name, token_type) from a Foundry Models meterName.
    Handles:
      - Space-separated:  "Llama 3.3 70B Inp glbl Tokens"
      - With 2-word region: "gpt 4.1 Inp Data Zone Tokens"
      - With unit tag: "5.4 opt Dz 1M Tokens"
      - Hyphen-separated: "gpt-4o-0806-Inp-glbl Tokens"
    """
    if not meter_name.lower().endswith(" tokens"):
        return None

    name = meter_name[:-7].strip()  # strip " Tokens"
    parts = name.split()

    # Strip trailing 2-word region tag ("Data Zone")
    if len(parts) >= 2 and " ".join(parts[-2:]).lower() in _AZURE_REGION_2WORD:
        parts = parts[:-2]

    # Strip trailing unit tag ("1M", "1K") and 1-word region tag
    for _ in range(2):
        if parts and parts[-1].lower() in _AZURE_UNIT_TAGS:
            parts.pop()
        if parts and parts[-1].lower() in _AZURE_REGION_TAGS:
            parts.pop()

    if not parts:
        return None

    # Token type from last space-separated word
    if parts[-1].lower() in _AZURE_INPUT_TAGS:
        raw = " ".join(parts[:-1]).strip()
        return (raw, "input") if raw else None
    if parts[-1].lower() in _AZURE_OUTPUT_TAGS:
        raw = " ".join(parts[:-1]).strip()
        return (raw, "output") if raw else None

    # Fallback: hyphen-separated (e.g. "gpt-4o-0806-Inp-glbl Tokens" → name="gpt-4o-0806-Inp-glbl")
    hyph = name.split("-")
    if len(hyph) >= 3:
        if hyph[-1].lower() in _AZURE_REGION_TAGS:
            hyph.pop()
        if hyph and hyph[-1].lower() in _AZURE_INPUT_TAGS:
            raw = "-".join(hyph[:-1]).strip()
            return (raw, "input") if raw else None
        if hyph and hyph[-1].lower() in _AZURE_OUTPUT_TAGS:
            raw = "-".join(hyph[:-1]).strip()
            return (raw, "output") if raw else None

    return None


# For model names that are just version numbers (e.g. "5.4" from GPT5 product),
# prepend a provider-specific prefix derived from productName.
_AZURE_PRODUCT_PREFIX: Dict[str, str] = {
    "Azure OpenAI GPT5":       "GPT-5",
    "Azure Mistral Models":    "Mistral",
    "Azure Deepseek Models":   "DeepSeek",
    "Cohere Models":           "Command",
    "Azure Phi Models":        "Phi",
    "Azure Grok Models":       "Grok",
    "Azure Kimi":              "Kimi",
    "MAI Models":              "MAI",
    "Qwen models":             "Qwen",
    "Azure OpenAI OSS Models": "OSS",
}

_AZURE_KNOWN_PREFIXES = (
    "gpt", "o1", "o2", "o3", "o4", "llama", "mistral", "deepseek",
    "command", "phi", "grok", "kimi", "qwen", "codestral", "cohere",
    "azure", "oss", "jamba", "mai",
)


def _azure_resolve_model(raw: str, product_name: str) -> str:
    """Prepend product-specific prefix to ambiguous raw model names."""
    raw = raw.strip()
    # Strip "Az " or "Az-" provider prefix that some Azure meter names include
    if re.match(r"^az[\s\-]", raw, re.IGNORECASE):
        raw = raw[3:].strip()
    if any(raw.lower().startswith(p) for p in _AZURE_KNOWN_PREFIXES):
        return raw
    prefix = _AZURE_PRODUCT_PREFIX.get(product_name, "")
    if prefix and not raw.lower().startswith(prefix.lower()):
        return f"{prefix} {raw}"
    return raw


def _azure_price_to_per_m(retail_price: float, unit_of_measure: str) -> float:
    uom = unit_of_measure.strip().upper()
    if uom == "1K":
        return round(retail_price * 1_000.0, 6)
    if uom == "1M":
        return round(retail_price, 6)
    # Heuristic fallback
    if retail_price < 0.05:
        return round(retail_price * 1_000.0, 6)
    return round(retail_price, 6)


def _azure_paginate(session: requests.Session):
    """Yield all Foundry Models Consumption items from the Azure Retail Prices API."""
    url: Optional[str] = AZURE_RETAIL_URL
    params: Optional[Dict] = {
        "api-version": AZURE_API_VERSION,
        "$filter": f"serviceName eq '{AZURE_FOUNDRY_SERVICE}' and priceType eq 'Consumption'",
    }
    while url:
        data = get_json(session, url, params=params)
        params = None   # NextPageLink already embeds all params
        yield from data.get("Items", [])
        url = data.get("NextPageLink")


def fetch_azure_openai() -> List[Dict[str, Any]]:
    session = make_session()
    logger.info("Azure AI (Foundry Models): fetching from Retail Prices API...")

    # (slug, region) -> {model_name, input, output}
    per_region: Dict[Tuple[str, str], Dict[str, Any]] = {}

    try:
        for item in _azure_paginate(session):
            if _azure_should_skip(item):
                continue

            meter_name = item.get("meterName", "")
            product_name = item.get("productName", "")
            region = item.get("armRegionName", "")
            retail_price = float(item.get("retailPrice", 0))
            uom = item.get("unitOfMeasure", "")

            parsed = _azure_parse_meter(meter_name)
            if parsed is None:
                continue
            raw_model, token_type = parsed

            model_name = _azure_resolve_model(raw_model, product_name)
            if not model_name or _is_excluded_model(model_name):
                continue

            slug = _slugify(model_name)
            price_per_m = _azure_price_to_per_m(retail_price, uom)

            key = (slug, region)
            if key not in per_region:
                per_region[key] = {
                    "model_name": model_name,
                    "slug": slug,
                    "region": region,
                }
            entry = per_region[key]
            if token_type == "input":
                if "input" not in entry or price_per_m < entry["input"]:
                    entry["input"] = price_per_m
            else:
                if "output" not in entry or price_per_m < entry["output"]:
                    entry["output"] = price_per_m

    except Exception as exc:
        print(f"WARNING: Azure AI fetch failed: {exc}", file=sys.stderr)
        logger.warning("Azure fetch failed: %s", exc)
        return []

    raw: List[Dict[str, Any]] = []
    for (slug, region), data in per_region.items():
        if "input" not in data:
            continue
        ctx_k, modality = _get_model_meta(slug)
        raw.append({
            "provider": "azure",
            "type": "ai-model",
            "instanceType": slug,
            "family": _extract_family(slug),
            "modelName": data["model_name"],
            "priceUSD_hourly": None,
            "pricePerMInputTokens": data["input"],
            "pricePerMOutputTokens": data.get("output"),
            "contextWindowK": ctx_k,
            "modality": modality,
            "regions": [region] if region else [],
            "source": "azure_retail_prices_api",
            "lastUpdated": NOW,
            "pricingModel": "on-demand",
        })

    records = _dedup_records(raw)
    logger.info("Azure AI: built %d model records (after dedup)", len(records))
    return records


# ===========================================================================
# Output + CLI
# ===========================================================================

def fetch_data(provider: str) -> List[Dict[str, Any]]:
    """Dispatcher called by the extras orchestrator."""
    if provider == "aws":
        return _dedup_records(fetch_aws_bedrock())
    if provider == "gcp":
        return _dedup_records(fetch_gcp_vertex())
    if provider == "azure":
        return fetch_azure_openai()
    raise ValueError(f"Unsupported provider: {provider!r}")


def _write(records: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    print(f"  Wrote {len(records)} records -> {path}", flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch AI/LLM token pricing for AWS Bedrock, GCP Vertex AI, Azure OpenAI"
    )
    parser.add_argument(
        "--provider",
        choices=["aws", "gcp", "azure", "all"],
        default="all",
        help="Provider to fetch (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.output_dir)
    providers = ["aws", "gcp", "azure"] if args.provider == "all" else [args.provider]

    for provider in providers:
        print(f"\n=== {provider.upper()} AI model pricing ===", flush=True)
        if provider == "aws":
            records = _dedup_records(fetch_aws_bedrock())
            _write(records, out_dir / "aws.ai.raw.json")
        elif provider == "gcp":
            records = _dedup_records(fetch_gcp_vertex())
            _write(records, out_dir / "gcp.ai.raw.json")
        elif provider == "azure":
            records = fetch_azure_openai()  # already deduped internally
            _write(records, out_dir / "azure.ai.raw.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
