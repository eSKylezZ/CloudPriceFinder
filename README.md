# CloudPriceFinder v3

> Free, open-source cloud instance cost comparison for AWS, Azure, GCP, and OCI.
> No ads, no upselling, no tracking.

**Live site:** https://cloudpricefinder.com

---

## What is this?

CloudPriceFinder lets you compare compute instance specifications and costs across the four major cloud providers in one view. It shows on-demand pricing and 1-year / 3-year reserved/committed pricing so you can see the full cost picture before committing.

Data is fetched weekly from official public provider APIs via GitHub Actions and served as a fully static site on Cloudflare Pages.

## Providers

| Provider | On-demand | 1-yr | 3-yr | Notes |
|----------|-----------|------|------|-------|
| **AWS** | Yes | Yes (RI) | Yes (RI) | Savings Plans also included |
| **Azure** | Yes | Yes | Yes | Reservation pricing |
| **GCP** | Yes | Yes | Yes | Committed Use Discounts |
| **OCI** | Yes | — | — | Commitment pricing via Universal Credits (not per-shape) |

## Quick Start (local development)

### Prerequisites

- Node.js 20+
- Python 3.11+
- Git

### Setup

```bash
git clone https://github.com/eSKylezZ/cloudpricefinder.com.git
cd cloudpricefinder.com

# Install Node.js dependencies
npm install

# Install Python dependencies
pip install -r requirements.txt

# Build the site (uses existing data/index.json if present)
npm run build
npm run preview
```

To regenerate the pricing data locally you need provider API credentials. See [Environment Variables](#environment-variables) below.

### Development server

```bash
npm run dev      # hot-reload at http://localhost:4321
```

## Commands

| Command | Description |
|---------|-------------|
| `npm run dev` | Start development server |
| `npm run build` | Build static site (copies data/ to dist/) |
| `npm run fetch-data` | Run data collection pipeline |
| `npm run preview` | Preview production build |
| `npm run type-check` | TypeScript type checking |
| `npm run lint` | ESLint |
| `npm run format` | Prettier |
| `npm test` | Vitest unit tests |

## Architecture

```
cloudpricefinder/
├── scripts/                 # Python data collection
│   ├── fetch_aws.py         # AWS Pricing API (streaming JSON)
│   ├── fetch_azure.py       # Azure Retail Prices API
│   ├── fetch_gcp.py         # GCP Cloud Billing Catalog API
│   ├── fetch_oci.py         # Oracle Cloud cetools API
│   ├── aggregate.py         # Three-tier output builder
│   ├── orchestrator.py      # Master coordinator
│   └── utils/               # Validator, normalizer, currency converter
├── src/                     # Astro frontend
│   ├── layouts/BaseLayout.astro
│   ├── pages/
│   │   ├── index.astro      # Main comparison table
│   │   ├── compare.astro    # Side-by-side compare view
│   │   └── about.astro
│   ├── components/
│   │   ├── ComparisonTable.astro
│   │   └── FilterPanel.astro
│   ├── lib/data-loader.ts   # Lazy-load helpers
│   └── types/               # TypeScript interfaces
├── data/                    # Generated at runtime (gitignored raw files)
│   ├── index.json           # Provider + family index (< 100 KB)
│   ├── families/{provider}/ # Per-family instance lists
│   ├── instances/{provider}/# Per-instance detail files
│   └── equivalents.json     # Cross-provider family matches
├── .github/workflows/
│   ├── data-collection.yml  # Weekly Sunday cron + workflow_dispatch
│   └── build.yml            # PR lint/type-check/build
└── public/                  # Static assets copied to dist/
```

### Data pipeline

1. **Fetch** — each `scripts/fetch_{provider}.py` hits the provider's public API and writes `data/providers/{provider}.raw.json`.
2. **Aggregate** — `scripts/aggregate.py` reads the raw files and produces the three-tier output:
   - `data/index.json` — lightweight index (provider list, family list, instance counts, lastUpdated)
   - `data/families/{provider}/{family}.json` — all instances in a family
   - `data/instances/{provider}/{id}.json` — single-instance detail with regional pricing
3. **Build** — `npm run build` runs Astro's static build and copies `data/` to `dist/data/`.
4. **Deploy** — Cloudflare Pages serves `dist/` from the edge.

### Frontend lazy-loading

The site only loads `data/index.json` on initial page load (~a few KB). Family files are fetched on demand when you apply filters. Instance detail files are fetched when you expand a row. This keeps the initial payload well under 200 KB.

## Environment Variables

Create a `.env` file (gitignored) for local data collection:

```env
# GCP — obtain from https://console.cloud.google.com/apis/credentials
GCP_API_KEY=your-gcp-api-key

# Hetzner (disabled in v3 — v3.1 only)
# HETZNER_API_TOKEN=...
```

AWS, Azure, and OCI use public/unauthenticated endpoints and require no credentials.

For the GitHub Actions weekly cron, set `GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`, and `GCP_PROJECT_USERNAME` as repository secrets and configure Workload Identity Federation (see `.github/workflows/data-collection.yml` for details).

## Contributing

Pull requests are welcome. The easiest contributions are:

- Fixing data issues (wrong instance specs, missing regions)
- Improving the UI (filtering, sorting, accessibility)
- Adding missing instance families to existing provider fetchers

For large changes, open an issue first to discuss the approach.

## License

MIT — see [LICENSE](LICENSE) for details.

---

Made by [Kyle Blenkinsop](https://kyleblenkinsop.co.uk)
