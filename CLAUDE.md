# CLAUDE.md

Development guidance for Claude Code when working in this repository.

## Project Overview

CloudPriceFinder is a static cloud price comparison site for AWS, Azure, GCP, OCI, OVH, Scaleway, Vast.ai, and Vultr.
Architecture: Python data collection + Astro frontend + Cloudflare Pages hosting.

**Live site:** https://cloudpricefinder.com

## Architecture

### Data Collection (Python)

- `scripts/fetch_{provider}.py` — provider-specific fetchers
  - `fetch_aws.py` — AWS Pricing API (streaming `ijson`); on-demand + RI + Savings Plans; regions auto-discovered from EC2 region index
  - `fetch_azure.py` — Azure Retail Prices API; Consumption + Reservation rows; global API, no region filter needed
  - `fetch_gcp.py` — GCP Cloud Billing Catalog API; on-demand + CUD commitments; regions derived from SKU serviceRegions data
  - `fetch_oci.py` — Oracle cetools API; on-demand only (commitment pricing is account-level); 33 regions
  - `fetch_ovh.py` — OVH Public Cloud API; on-demand; includes bare-metal GPU instances
  - `fetch_scaleway.py` — Scaleway Instance API; on-demand; includes ARM (Ampere) instances
  - `fetch_vast.py` — Vast.ai marketplace API; GPU-only; consumer and professional GPU cards
  - `fetch_vultr.py` — Vultr Plans API; on-demand; cloud compute, high-frequency, optimized, GPU tiers
- `scripts/orchestrator.py` — runs fetchers in parallel; `PROVIDER_CONFIG` controls which are enabled
- `scripts/aggregate.py` — consumes `data/providers/*.raw.json`, produces three-tier output; `build_equivalents` stores a representative `instanceType` per family match
- `scripts/generate-static-pages.js` — post-build SEO step; writes pre-rendered HTML for `/providers/{provider}/`, `/compare/aws-vs-azure/`, `/gpu-instances/`, `/arm-instances/` into `dist/`
- `scripts/utils/` — `data_validator.py`, `data_normalizer.py`, `currency_converter.py`

### Three-tier Data Output

```
data/
├── index.json              # < 100 KB — provider list, family list, instance counts
├── families/{provider}/    # < 250 KB each — all instances in a family
├── instances/{provider}/   # < 20 KB each — single instance detail
└── equivalents.json        # cross-provider family matches; each entry has instanceType (representative slug)
```

### Frontend (Astro 5 + TypeScript + Tailwind)

- `src/pages/index.astro` — main comparison table; bootstraps from index.json client-side
- `src/pages/compare.astro` — side-by-side compare; deep-link via `?items=aws:m7i.xlarge,...`; Print/PDF export via `window.print()`
- `src/pages/compare/[pair].astro` — static pre-rendered compare pages (e.g. `/compare/aws-vs-azure/`)
- `src/pages/providers/[provider].astro` — static per-provider landing pages
- `src/pages/gpu-instances.astro` — filtered view of GPU instances across all providers
- `src/pages/arm-instances.astro` — filtered view of ARM instances across all providers
- `src/pages/about.astro` — about + data-source attribution
- `src/lib/data-loader.ts` — `loadIndex()`, `loadFamily()`, `loadFamilies()`, `loadInstance()` with in-memory cache
- `src/lib/seo-instances.ts` — helpers for building SEO metadata on static landing pages
- `src/components/ComparisonTable.astro` — table with commitment toggle, row-expand, multi-select compare bar (max 4), CSV export
- `src/components/FilterPanel.astro` — driven by index.json; dispatches `filtersChanged` event; includes searchable region filter with All/None shortcuts
- `src/components/PresetBar.astro` — quick-select filter preset pills (arch, term, GPU-only, vCPU/memory ranges)
- `src/components/CurrencySelector.astro` — currency display selector
- `src/components/DataSummary.astro` — summary stats bar above the table
- `src/layouts/BaseLayout.astro` — nav, footer with disclaimer and creator link, OG tags

### CI/CD

- `.github/workflows/build.yml` — PR lint + type-check + build (runs on all non-main branches and PRs targeting main)

## Development Commands

```bash
npm run dev            # dev server at http://localhost:4321
npm run build          # Astro build + copy data/ to dist/
npm run type-check     # astro check (TypeScript)
npm run lint           # ESLint
npm run format         # Prettier
npm test               # Vitest
npm run fetch-data     # python scripts/orchestrator.py
```

## Conventions

- All prices in USD; original currency preserved in `originalPrice` field
- `data/providers/*.raw.json` and `data/providers/*.json` are gitignored (generated at runtime)
- `data/providers/_archive/` contains disabled secondary providers — kept for v3.1
- Never commit secrets; `GCP_API_KEY` goes in `.env` (gitignored) or GitHub Actions secrets
- The `local/` directory at repo root is gitignored — use it for developer scratch files

## Schema

See `scripts/schema/instance.schema.json` for the canonical JSON Schema.
TypeScript interfaces are in `src/types/cloud.ts`.

Key types:
- `CommitmentPrice` — `term`, `payment`, `product`, `priceUSD_hourly`, `effectiveHourlyUSD`, `savingsVsOnDemandPct`
- `CloudInstance` — all instance fields including `commitments[]`, `gpu`, `architecture`, `family`

## Out of scope

Spot pricing, storage/database/networking pricing, Hetzner/DigitalOcean and other secondary providers,
AWS China/GovCloud, historical price tracking.
See `PROJECT_TODO.md` for the full roadmap and backlog.
