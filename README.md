# CloudPriceFinder

> Free, open-source cloud instance cost comparison across AWS, Azure, GCP, OCI, OVH, Scaleway, Vast.ai, and Vultr.
> No ads, no upselling, no tracking.

**Live site:** https://cloudpricefinder.com

---

## What is this?

CloudPriceFinder lets you compare compute instance specifications and costs across multiple cloud providers in one view. It shows on-demand pricing and 1-year / 3-year reserved/committed pricing so you can see the full cost picture before committing.

Data is collected from official public provider APIs and served as a fully static site on Cloudflare Pages.

## Providers

| Provider     | On-demand | 1-yr     | 3-yr     | Notes                                                    |
| ------------ | --------- | -------- | -------- | -------------------------------------------------------- |
| **AWS**      | Yes       | Yes (RI) | Yes (RI) | Savings Plans also included                              |
| **Azure**    | Yes       | Yes      | Yes      | Reservation pricing                                      |
| **GCP**      | Yes       | Yes      | Yes      | Committed Use Discounts                                  |
| **OCI**      | Yes       | —        | —        | Commitment pricing via Universal Credits (not per-shape) |
| **OVH**      | Yes       | —        | —        | Includes bare-metal GPU instances                        |
| **Scaleway** | Yes       | —        | —        | Includes ARM (Ampere) instances                          |
| **Vast.ai**  | Yes       | —        | —        | GPU marketplace; consumer and professional cards         |
| **Vultr**    | Yes       | —        | —        | Cloud compute, high-frequency, optimized, and GPU tiers  |

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

| Command              | Description                               |
| -------------------- | ----------------------------------------- |
| `npm run dev`        | Start development server                  |
| `npm run build`      | Build static site (copies data/ to dist/) |
| `npm run fetch-data` | Run data collection pipeline              |
| `npm run preview`    | Preview production build                  |
| `npm run type-check` | TypeScript type checking                  |
| `npm run lint`       | ESLint                                    |
| `npm run format`     | Prettier                                  |
| `npm test`           | Vitest unit tests                         |

## Architecture

```
cloudpricefinder/
├── scripts/                    # Python data collection
│   ├── fetch_aws.py            # AWS Pricing API (streaming JSON)
│   ├── fetch_azure.py          # Azure Retail Prices API
│   ├── fetch_gcp.py            # GCP Cloud Billing Catalog API
│   ├── fetch_oci.py            # Oracle Cloud cetools API
│   ├── fetch_ovh.py            # OVH Public Cloud API
│   ├── fetch_scaleway.py       # Scaleway Instance API
│   ├── fetch_vast.py           # Vast.ai marketplace API (GPU)
│   ├── fetch_vultr.py          # Vultr Plans API
│   ├── aggregate.py            # Three-tier output builder
│   ├── orchestrator.py         # Master coordinator
│   ├── generate-static-pages.js# Post-build SEO pre-render step
│   └── utils/                  # Validator, normalizer, currency converter
├── src/                        # Astro frontend
│   ├── layouts/BaseLayout.astro
│   ├── pages/
│   │   ├── index.astro              # Main comparison table
│   │   ├── compare.astro            # Side-by-side compare view + print/PDF export
│   │   ├── compare/[pair].astro     # Pre-rendered compare pages (e.g. /compare/aws-vs-azure/)
│   │   ├── providers/[provider].astro # Per-provider landing pages
│   │   ├── gpu-instances.astro      # GPU instance listing
│   │   ├── arm-instances.astro      # ARM instance listing
│   │   └── about.astro
│   ├── components/
│   │   ├── ComparisonTable.astro  # Table with multi-select compare bar + CSV export
│   │   ├── FilterPanel.astro      # Filters including searchable region filter
│   │   ├── PresetBar.astro        # Quick-select filter preset pills
│   │   ├── CurrencySelector.astro
│   │   └── DataSummary.astro
│   ├── lib/data-loader.ts      # Lazy-load helpers
│   └── types/                  # TypeScript interfaces
├── data/                       # Generated at runtime (gitignored raw files)
│   ├── index.json              # Provider + family index (< 100 KB)
│   ├── families/{provider}/    # Per-family instance lists
│   ├── instances/{provider}/   # Per-instance detail files
│   └── equivalents.json        # Cross-provider family matches
├── .github/workflows/
│   └── build.yml               # PR lint/type-check/build
└── public/                     # Static assets copied to dist/
    └── robots.txt
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
```

AWS, Azure, OCI, OVH, Scaleway, Vast.ai, and Vultr all use public/unauthenticated endpoints and require no credentials. Only GCP requires an API key.

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
