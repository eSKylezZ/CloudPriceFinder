# CLAUDE.md

Development guidance for Claude Code when working in this repository.

## Project Overview

CloudPriceFinder v3 is a static cloud price comparison site for AWS, Azure, GCP, and OCI.
Architecture: Python data collection + Astro frontend + Cloudflare Pages hosting.

**Live site:** https://cloudpricefinder.com

## Architecture

### Data Collection (Python)

- `scripts/fetch_{provider}.py` — provider-specific fetchers
  - `fetch_aws.py` — AWS Pricing API (streaming `ijson`); on-demand + RI + Savings Plans
  - `fetch_azure.py` — Azure Retail Prices API; Consumption + Reservation rows
  - `fetch_gcp.py` — GCP Cloud Billing Catalog API; on-demand + CUD commitments
  - `fetch_oci.py` — Oracle cetools API; on-demand only (commitment pricing is account-level)
- `scripts/orchestrator.py` — runs fetchers in parallel; `PROVIDER_CONFIG` controls which are enabled
- `scripts/aggregate.py` — consumes `data/providers/*.raw.json`, produces three-tier output
- `scripts/utils/` — `data_validator.py`, `data_normalizer.py`, `currency_converter.py`

### Three-tier Data Output

```
data/
├── index.json              # < 100 KB — provider list, family list, instance counts
├── families/{provider}/    # < 250 KB each — all instances in a family
├── instances/{provider}/   # < 20 KB each — single instance detail
└── equivalents.json        # cross-provider family matches
```

### Frontend (Astro 5 + TypeScript + Tailwind)

- `src/pages/index.astro` — main comparison table; bootstraps from index.json client-side
- `src/pages/compare.astro` — side-by-side compare; deep-link via `?items=aws:m7i.xlarge,...`
- `src/pages/about.astro` — about + data-source attribution
- `src/lib/data-loader.ts` — `loadIndex()`, `loadFamily()`, `loadFamilies()`, `loadInstance()` with in-memory cache
- `src/components/ComparisonTable.astro` — table with commitment toggle, row-expand
- `src/components/FilterPanel.astro` — driven by index.json; dispatches `filtersChanged` event
- `src/layouts/BaseLayout.astro` — nav, footer with disclaimer and creator link, OG tags

### CI/CD

- `.github/workflows/data-collection.yml` — weekly Sunday 04:00 UTC + `workflow_dispatch`
- `.github/workflows/build.yml` — PR lint + type-check + build

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

## Out of scope for v1

Spot pricing, storage/database/networking pricing, secondary providers (Hetzner, OVH, DO etc.),
AWS China/GovCloud, historical price tracking, CSV/PDF export.
These are tracked in `PROJECT_TODO.md` under "Out of scope for v1".
