# Cloud Provider Pricing API Research

Research into public and authenticated pricing APIs for cloud compute providers, with a focus on feasibility for integration into CloudPriceFinder.

Last updated: 2026-05-11

---

## Tier 1 — Currently Supported

These providers are already integrated and serve as the baseline reference.

| Provider | Endpoint | Auth | Notes |
|---|---|---|---|
| AWS | `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/{region}/index.json` | None | Bulk JSON pricing files, streaming via `ijson` |
| Azure | `https://prices.azure.com/api/retail/prices` | None | REST API, filterable by service family/region |
| GCP | `https://cloudbilling.googleapis.com/v1beta/...` | API key | Requires `GCP_API_KEY` env var |
| OCI | `https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/` | None | Public JSON endpoint |

---

## Tier 2 — Feasible to Add (No Auth Required)

### Vultr

- **Public API:** Yes — plans and regions endpoints require no authentication
- **Plans endpoint:** `https://api.vultr.com/v2/plans`
- **Regions endpoint:** `https://api.vultr.com/v2/regions`
- **Bare metal plans:** `https://api.vultr.com/v2/plans-metal`
- **Format:** JSON
- **Coverage:** 151 plans including Cloud Compute (vc2), High Frequency (vhf), High Performance (vhp), Cloud GPU, Bare Metal; 33 global regions
- **Response fields:** `id`, `vcpu_count`, `ram`, `disk`, `bandwidth`, `monthly_cost`, `type`, `locations[]`
- **Filter by type:** `?type=vhf` / `?type=vc2` / `?type=vbm` (bare metal)
- **Rate limits:** Not enforced on public read endpoints
- **Notes:** US company (West Palm Beach, FL); regions in NA, EU, APAC, South America, Middle East; GPU instances (A100, L40S) also in plans list
- **Verdict: Add as `fetch_vultr.py` — confirmed public, good global coverage**

---

### Vast.ai

- **Public API:** Yes — GPU marketplace offers queryable without authentication
- **Bundles endpoint:** `https://cloud.vast.ai/api/v0/bundles/`
- **Format:** JSON with `offers[]` array
- **Coverage:** Decentralised GPU marketplace — RTX 4090, A100, H100 and others from many host providers; real-time availability and hourly pricing
- **Response fields:** `dph_total` ($/hr), `gpu_name`, `gpu_ram`, `cpu_cores`, `cpu_ram`, `disk_space`, `rentable`, `reliability`, `geolocation`
- **Filtering:** Pass URL-encoded JSON query param `q={"gpu_name":{"eq":"RTX 4090"}}` to filter by GPU, RAM, price, etc.
- **Notes:** Marketplace model — prices are set by individual hosts and fluctuate with demand. Not comparable to fixed-price cloud providers; useful specifically for GPU price benchmarking.
- **Verdict: Add as `fetch_vast.py` for GPU pricing context — confirmed public, unique marketplace data**

---

### OVHcloud

- **Public API:** Yes — no authentication required
- **Compute endpoint:** `https://eu.api.ovh.com/v1/order/catalog/public/cloud`
- **Bare metal endpoint:** `https://eu.api.ovh.com/v1/order/catalog/public/baremetalServers`
- **Eco/secondary ranges:** `https://eu.api.ovh.com/v1/order/catalog/public/eco`
- **Format:** JSON
- **Coverage:** Full VM/instance catalog, per-region, per-commitment-term pricing
- **Multi-region subsidiaries:** Pass `?ovhSubsidiary=IE` (or FR, DE, GB, CA, AU, etc.) to get region-specific pricing and currency
- **Rate limits:** Not documented for the public catalog; catalog versioned via `catalogId`
- **ToS:** Using the public catalog API is explicitly supported and documented by OVHcloud
- **Implementation notes:**
  - Response includes `planFamilies`, `plans`, `products`, and `addons` arrays
  - Pricing tiers include on-demand hourly, monthly, and committed options
  - European pricing in EUR; UK subsidiary in GBP; CA in CAD
  - `catalogId` increments when pricing changes — useful for cache invalidation
- **Verdict: Add as `fetch_ovh.py`**

---

### Scaleway

- **Public API:** Yes — public catalog endpoint requires no authentication
- **Endpoint:** `https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products`
- **Format:** JSON
- **Coverage:** All Scaleway products, including Instances (VMs), GPU instances, Bare Metal, object storage
- **Pagination:** `page_size` parameter supported
- **Response fields:** `sku`, `service_category`, `product_name`, `variant`, `locality` (zone), `retail_price`, `currency_code`
- **Rate limits:** Not documented for the public catalog endpoint
- **Notes:**
  - French company (Paris-based); zones: `fr-par-1/2/3`, `nl-ams-1/2/3`, `pl-waw-1/2/3`
  - All prices in EUR
  - Main API (`api.scaleway.com`) still requires `X-Auth-Token` for account operations
  - This specific catalog endpoint is intentionally public
- **Verdict: Add as `fetch_scaleway.py`**

---

## Tier 3 — Feasible to Add (Auth Required, Simple Token)

These providers require an API key/token but have well-documented pricing endpoints. Could be supported if credentials are provided via environment variables.

### Hetzner Cloud

- **Public API:** No
- **Authenticated endpoint:** `https://api.hetzner.cloud/v1/server_types`
- **Auth method:** `Authorization: Bearer <token>` header
- **Token generation:** Cloud Console → Security → API Tokens (read-only token sufficient)
- **Format:** JSON
- **Coverage:** All cloud server types with per-location hourly/monthly pricing, traffic pricing
- **Response includes:** `cores`, `memory`, `disk`, `prices[]` (location-specific), `included_traffic`, `price_per_tb_traffic`
- **Env var:** `HETZNER_API_TOKEN`
- **Verdict: Add as `fetch_hetzner.py` (optional, requires token)**

### Hetzner Dedicated / Auction

- **Public API:** No — the old public JSON endpoints (`live_data_sb.json`, `a_hz_serverboerse/live_data.json`) both return 404; Hetzner removed them
- **Authenticated endpoint:** `GET https://robot.hetzner.com/order/server_market/product`
- **Auth method:** Robot API credentials (HTTP Basic Auth with Robot username/password)
- **Format:** JSON
- **Coverage:** Current server auction listings (refurbished/used dedicated servers only) — NOT standard dedicated server pricing
- **Notes:** Auction prices fluctuate constantly and aren't representative of stable dedicated server pricing. The main dedicated server configurator (`https://www.hetzner.com/dedicated-rootserver/`) has no public pricing API either.
- **Verdict: Auction data is low-value for a price comparison tool (volatile). Skip unless specifically building an "auction tracker" feature. If needed, use Robot API with `HETZNER_ROBOT_USER` / `HETZNER_ROBOT_PASSWORD` env vars.**

### Gcore

- **Public API:** No
- **Authenticated endpoint:** `https://api.gcore.com/cloud/v1/pricing/{project_id}/{region_id}/instances/{instance_id}`
- **Auth method:** `Authorization: APIKey <token>`
- **Format:** JSON
- **Coverage:** Per-instance pricing with `price_per_hour`, `price_per_month`, `discount_percent`, `currency_code`
- **Notes:** Luxembourg-based; strong GPU and edge cloud presence; ~25 regions globally
- **Env var:** `GCORE_API_KEY`
- **Verdict: Worth adding for GPU compute coverage. Requires API key.**

### UpCloud

- **Public API:** No
- **Authenticated endpoint:** `https://api.upcloud.com/1.3/price`
- **Auth method:** Bearer token (or Basic Auth, deprecated)
- **Format:** JSON
- **Coverage:** Per-zone pricing for server cores, memory, disk, bandwidth, firewalls
- **Notes:** Finnish provider; zones in Helsinki, Frankfurt, Amsterdam, Chicago, Singapore, Dubai, Warsaw, Sydney, São Paulo
- **Env var:** `UPCLOUD_API_TOKEN`
- **Verdict: Add if European coverage is a priority. Simple API.**

### IONOS Cloud

- **Public API:** No
- **Authenticated endpoint:** `https://api.ionos.com/billing/{contract_id}/products`
- **Auth method:** Bearer token or Basic Auth
- **Format:** JSON
- **Coverage:** Per-product `unitCost` and billing unit for all IONOS cloud products
- **Notes:** German provider (subsidiary of United Internet); strong presence in DE, UK, US, ES, FR, IT, MX
- **Env var:** `IONOS_API_TOKEN`
- **Caveat:** Endpoint requires a `contract_id` which is account-specific — pricing may be account-tier dependent
- **Verdict: Investigate further. Contract-scoped endpoint is a concern for a public price comparison tool.**

---

## Tier 4 — Scraping Required or High Friction

These providers have no structured public pricing API and would require HTML scraping or significant custom work.

### Contabo

- **Provider type:** German budget VPS/cloud provider
- **Public API:** No
- **Authenticated API:** Yes (OAuth2), but no pricing endpoint — pricing is only on the web
- **Pricing page:** `https://contabo.com/en/pricing/`
- **Format:** HTML page (structured but no JSON source)
- **Notes:** Very popular budget provider; pricing changes infrequently. Simple SKU list (VPS S/M/L/XL, storage VPS, dedicated). No commitment tiers — all month-to-month.
- **Verdict: Scrape pricing page if adding. Low maintenance due to infrequent price changes.**

### Contabo Regions

EU (Germany), US-Central (St. Louis), US-East (New York), US-West (Seattle), Singapore, Tokyo, Sydney

---

### Cherry Servers

- **Provider type:** Lithuanian bare metal and virtual server provider
- **Public API:** No
- **Authenticated API:** Yes (token), but pricing requires scraping
- **Pricing page:** `https://www.cherryservers.com/pricing`
- **Rate limits:** 4 req/sec on authenticated API
- **Notes:** Strong bare metal presence in EU (Vilnius, Amsterdam) and US (New York, San Jose)
- **Verdict: Scraping only; niche provider.**

### Infomaniak

- **Provider type:** Swiss provider (Geneva), focus on privacy/sustainability
- **Public API:** No
- **Authenticated API:** OAuth2, 60 req/min limit; no pricing endpoint
- **Pricing page:** `https://www.infomaniak.com/en/hosting/public-cloud/prices`
- **Notes:** OpenStack-based; GDPR/Swiss DPA compliant; niche European market
- **Verdict: Scraping only; niche provider.**

### Exoscale

- **Provider type:** Swiss provider (Lausanne), enterprise cloud
- **Public API:** No
- **Authenticated API:** HMAC-SHA256 signed requests; pricing portal endpoint at `https://portal.exoscale.com/api/pricing/{service}`
- **Notes:** Small footprint (CH-GVA, CH-DK2, AT-VIE, DE-FRA, DE-MUC, BG-SOF); OpenStack-based; primarily serves Swiss/Austrian enterprise market
- **Verdict: Low priority; complex auth, small market.**

### Leaseweb

- **Provider type:** Dutch hosting and cloud provider
- **Public API:** No
- **Authenticated API:** `X-Lsw-Auth` header; dedicated pricing endpoint exists
- **Notes:** Primarily bare metal / dedicated servers; cloud VMs are secondary offering
- **Verdict: Worth revisiting for bare metal coverage; auth is simple (single header).**

### Netcup

- **Provider type:** German budget hosting/VPS provider
- **Public API:** No
- **Authenticated API:** REST API in beta (stable since Nov 2025)
- **Notes:** Very budget-focused; not a major cloud player; ARM-based VPS servers notable
- **Verdict: Low priority.**

---

## Tier 5 — Major Regional Providers (Auth + Regional Complexity)

These are significant global providers but require credentials and have complex regional structures.

### Alibaba Cloud

- **Public API:** No
- **API type:** Authenticated (AK/SK); `DescribeInstanceTypes` endpoint
- **Notes:** Dominant in China; significant in SEA. ~100+ regions/zones. Pricing varies dramatically by region and account tier. China pricing often CNY and not directly comparable.
- **Verdict: High value for SEA/APAC coverage but high integration complexity.**

### Tencent Cloud

- **Public API:** No
- **API type:** Authenticated (SecretId/SecretKey)
- **Notes:** China-dominant; international presence in Singapore, Frankfurt, Silicon Valley. Similar complexity to Alibaba.
- **Verdict: Same situation as Alibaba. Defer until there's clear demand.**

### Huawei Cloud

- **Public API:** No
- **API type:** Authenticated (AK/SK or IAM token); `POST https://bss-intl.myhuaweicloud.com/v2/bills/ratings/on-demand-resources`
- **Notes:** International cloud available in DE, FR, SG, ZA, LA, MX, TR, TH, and others. Growing presence outside China.
- **Verdict: Interesting for international markets; complex auth.**

### IBM Cloud

- **Public API:** No
- **API type:** Authenticated (IAM API key); no dedicated public pricing endpoint found
- **Notes:** Infracost does not support IBM Cloud. IBM Cloud pricing is complex (virtual servers, bare metal, Power VS). IBM's cloud market share has declined significantly.
- **Verdict: Low priority; complex integration; declining market share.**

---

## Tier 5b — GPU & AI Cloud Providers (Global)

Most GPU cloud providers have no public pricing API. Prices change frequently and are often negotiated.

| Provider | Public API | Notes |
|---|---|---|
| **Vast.ai** | ✅ Yes | Marketplace — see Tier 2 above |
| **Lambda Labs** | No | Pricing page only; popular for ML; static SKUs (H100, A100) — scrapeable |
| **CoreWeave** | No | Enterprise GPU cloud; no public pricing; sales-led |
| **RunPod** | GraphQL + key | `api.runpod.io/graphql` with API key; can query `costPerHr` per pod type |
| **Genesis Cloud** | No | EU GPU cloud (Frankfurt, Iceland); pricing page only; `GDPR`-compliant |
| **Nebius** | No | Yandex spinoff, EU-based AI cloud (Amsterdam); pricing docs only |
| **Paperspace** | No | Now part of DigitalOcean; GPU droplets via DO API (auth required) |

---

## Tier 5c — Asia-Pacific Providers

All require authentication. Listed for future reference if regional APAC coverage is added.

| Provider | Country | Auth Type | Notes |
|---|---|---|---|
| **Naver Cloud (NCP)** | Korea | API Key + HMAC signature | `getPriceList` endpoint exists; complex auth |
| **KT Cloud** | Korea | API Key | Limited English docs |
| **NHN Cloud** | Korea | API Key | OpenStack-based |
| **Kakao Cloud** | Korea | API Key | No compute pricing API found |
| **NIFCLOUD (Fujitsu)** | Japan | API Key | Usage pricing API with `IsCharge=true`; XML response |
| **IDC Frontier** | Japan | API Key | Japanese-only docs |
| **JD Cloud** | China | API Key | Some pricing docs but no clear endpoint |
| **Alibaba Cloud** | China/Global | AK/SK | High value for SEA; high complexity |
| **Tencent Cloud** | China/Global | SecretId/Key | Similar to Alibaba |
| **Huawei Cloud** | China/Global | AK/SK or IAM | Growing international footprint |

---

## Tier 5d — Eastern Europe / Russia

| Provider | Country | Auth Type | Notes |
|---|---|---|---|
| **Yandex Cloud** | Russia | API Key | Good API docs; VPN may be needed; sanctions risk |
| **VK Cloud** | Russia | API Key | OpenStack-based; Mail.ru group |
| **Selectel** | Russia | API Key | Cloud Management API exists |

---

## Implementation Priority

```
Phase 1 (no credentials needed):
  ✅ Vultr       — confirmed public API, 151 plans, 33 regions
  ✅ Vast.ai     — confirmed public GPU marketplace API
  ✅ OVHcloud    — public API, good EU/global coverage
  ✅ Scaleway    — public catalog API, French provider, EUR pricing

Phase 2 (simple token, high value):
  🔑 Hetzner Cloud   — widely used, simple Bearer token
  🔑 Gcore           — useful for GPU compute coverage
  🔑 UpCloud         — good for Finnish/European coverage

Phase 3 (scraping, lower friction providers):
  🌐 Contabo         — budget market, static pricing
  🌐 Lambda Labs     — popular ML cloud, static GPU SKUs, scrapeable
  🌐 Leaseweb        — bare metal coverage

Phase 4 (deferred — complex auth or niche):
  ⏳ IONOS           — contract-scoped pricing concern
  ⏳ RunPod          — GraphQL with API key; GPU serverless
  ⏳ Alibaba Cloud   — high complexity, large SEA/APAC market
  ⏳ Huawei Cloud    — international growth markets
  ⏳ Naver Cloud     — Korean market, complex HMAC auth
  ⏳ Cherry Servers  — niche
  ⏳ Exoscale        — niche Swiss market
  ⏳ Tencent Cloud   — high complexity
  ⏳ IBM Cloud       — declining share

Phase 5 (not recommended):
  ❌ Equinix Metal   — service sunsets June 30, 2026
  ❌ Yandex/VK/Selectel — sanctions/legal risk for a public comparison site
```

---

## Notes on Web Scraping

Where scraping is the only option, the general approach for this project would be:

1. Fetch the pricing HTML page
2. Parse with BeautifulSoup / lxml
3. Normalize to the existing `CloudInstance` schema
4. Bake output into `data/providers/{provider}.raw.json` at collection time

Scraping is fragile — a redesign of the pricing page breaks the fetcher. Prefer authenticated APIs even if they require an env var.

---

## Schema Compatibility Notes

The existing `CloudInstance` TypeScript type (`src/types/cloud.ts`) and JSON Schema (`scripts/schema/instance.schema.json`) cover:
- `provider`, `name`, `family`, `region`
- `vCPU`, `memoryGB`, `storageGB`, `networkGbps`
- `priceUSD_hourly` (on-demand)
- `commitments[]` — commitment pricing tiers
- `gpu`, `architecture`, `os`

New providers should normalize to this schema. OVHcloud monthly-only pricing should be converted to hourly (`/730`). Scaleway EUR prices need `CurrencyConverter` (already in `scripts/utils/currency_converter.py`).
