/**
 * generate-static-pages.js
 * Post-build step that writes pre-rendered HTML pages for SEO.
 *
 * Runs after `astro build && node scripts/copy-data.mjs`.
 * Reads data/families/{provider}/*.json, then writes one index.html per
 * route into dist/ so Cloudflare Pages serves them as static files.
 *
 * Pages generated:
 *   /providers/aws/            cheapest AWS instances
 *   /providers/azure/          cheapest Azure instances
 *   /providers/gcp/            cheapest GCP instances
 *   /providers/oracle-cloud/   cheapest OCI instances
 *   /compare/aws-vs-azure/     side-by-side cheapest per vCPU tier
 *   /compare/aws-vs-gcp/
 *   /gpu-instances/            GPU instances across providers
 *   /arm-instances/            ARM64 instances sorted by $/vCPU/hr
 */

import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from 'fs';
import { join, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const repoRoot = resolve(__dirname, '..');
const familiesDir = join(repoRoot, 'data', 'families');
const distDir = join(repoRoot, 'dist');
const BASE_URL = 'https://cloudpricefinder.com';

// ── Data loading ─────────────────────────────────────────────────────────────

function loadAllInstances() {
  const instances = [];
  const providers = ['aws', 'azure', 'gcp', 'oci'];

  for (const provider of providers) {
    const dir = join(familiesDir, provider);
    if (!existsSync(dir)) {
      console.warn(`[generate-pages] No family data for ${provider}, skipping`);
      continue;
    }
    for (const file of readdirSync(dir)) {
      if (!file.endsWith('.json')) continue;
      const arr = JSON.parse(readFileSync(join(dir, file), 'utf-8'));
      for (const inst of arr) {
        if (inst.type === 'cloud-server' && inst.priceUSD_hourly > 0) {
          instances.push(inst);
        }
      }
    }
  }

  return instances;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const PROVIDER_LABEL = { aws: 'AWS', azure: 'Azure', gcp: 'Google Cloud', oci: 'Oracle Cloud' };
const PROVIDER_SHORT = { aws: 'AWS', azure: 'Azure', gcp: 'GCP', oci: 'OCI' };

function fmt(price) {
  return `$${price.toFixed(4)}/hr`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── HTML shell ────────────────────────────────────────────────────────────────

function renderPage({ title, description, canonicalPath, h1, intro, tableHtml, spaLink, jsonLd }) {
  const canonical = `${BASE_URL}${canonicalPath}`;
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escHtml(title)}</title>
<meta name="description" content="${escHtml(description)}">
<link rel="canonical" href="${canonical}">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827;background:#fff;max-width:1100px;margin:0 auto;padding:16px 20px}
header{display:flex;align-items:center;gap:12px;padding:12px 0 16px;border-bottom:1px solid #e5e7eb;margin-bottom:28px}
header a{font-weight:700;font-size:1.1rem;color:#2563eb;text-decoration:none}
header span{font-size:.8rem;color:#6b7280}
h1{font-size:1.6rem;font-weight:700;margin-bottom:10px;line-height:1.3}
.intro{color:#374151;line-height:1.6;margin-bottom:20px;max-width:680px}
.updated{font-size:.75rem;color:#9ca3af;margin-bottom:20px}
.table-wrap{overflow-x:auto;margin-bottom:28px}
table{width:100%;border-collapse:collapse;font-size:.85rem;white-space:nowrap}
th{background:#f9fafb;text-align:left;padding:9px 14px;border-bottom:2px solid #e5e7eb;font-weight:600;color:#374151;font-size:.8rem;text-transform:uppercase;letter-spacing:.03em}
td{padding:8px 14px;border-bottom:1px solid #f3f4f6;color:#1f2937}
tr:hover td{background:#f9fafb}
.num{color:#6b7280;font-size:.8rem}
.price{font-weight:600;color:#059669}
.badge{display:inline-block;padding:2px 8px;border-radius:9999px;font-size:.72rem;font-weight:600}
.aws{background:#fff3e0;color:#b45309}
.azure{background:#eff6ff;color:#1d4ed8}
.gcp{background:#f0fdf4;color:#15803d}
.oci{background:#fff1f2;color:#be123c}
.cta-wrap{margin-top:4px}
.cta{display:inline-block;padding:10px 22px;background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:.95rem}
.cta:hover{background:#1d4ed8}
footer{margin-top:48px;padding-top:16px;border-top:1px solid #e5e7eb;font-size:.78rem;color:#9ca3af}
footer a{color:#6b7280}
</style>
<script type="application/ld+json">${JSON.stringify(jsonLd)}</script>
</head>
<body>
<header>
  <a href="/">CloudPriceFinder</a>
  <span>Free cloud pricing comparison</span>
</header>
<main>
  <h1>${escHtml(h1)}</h1>
  <p class="intro">${escHtml(intro)}</p>
  <div class="table-wrap">${tableHtml}</div>
  <div class="cta-wrap"><a class="cta" href="${escHtml(spaLink)}">View full comparison →</a></div>
</main>
<footer>
  Data sourced from official provider pricing APIs. Prices shown are on-demand USD/hr in the default region.
  <a href="/about">About CloudPriceFinder</a> · <a href="/">View all instances</a>
</footer>
</body>
</html>`;
}

// ── Provider pages ────────────────────────────────────────────────────────────

function generateProviderPage(instances, providerId, { slug, displayName }) {
  const top20 = instances
    .filter(i => i.provider === providerId)
    .sort((a, b) => a.priceUSD_hourly - b.priceUSD_hourly)
    .slice(0, 20);

  const rows = top20.map((inst, i) => `
    <tr>
      <td class="num">${i + 1}</td>
      <td><strong>${escHtml(inst.instanceType)}</strong></td>
      <td>${inst.vCPU ?? '—'}</td>
      <td>${inst.memoryGiB != null ? inst.memoryGiB : '—'}</td>
      <td>${escHtml(inst.architecture ?? '—')}</td>
      <td class="price">${fmt(inst.priceUSD_hourly)}</td>
      <td>$${inst.priceUSD_monthly.toFixed(2)}/mo</td>
    </tr>`).join('');

  const tableHtml = `<table>
  <thead><tr>
    <th>#</th><th>Instance</th><th>vCPU</th><th>RAM&nbsp;(GiB)</th><th>Arch</th>
    <th>On-Demand/hr</th><th>Est.&nbsp;Monthly</th>
  </tr></thead>
  <tbody>${rows}
  </tbody>
</table>`;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: `${displayName} Cloud Instance Pricing`,
    description: `Top ${displayName} compute instances sorted by on-demand hourly price.`,
    url: `${BASE_URL}/providers/${slug}/`,
    numberOfItems: top20.length,
    itemListElement: top20.map((inst, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: `${inst.instanceType} — ${fmt(inst.priceUSD_hourly)}`,
      description: `${inst.vCPU} vCPU, ${inst.memoryGiB} GiB RAM, ${inst.architecture ?? ''}`,
    })),
  };

  return renderPage({
    title: `${displayName} Instance Pricing — CloudPriceFinder`,
    description: `Compare ${displayName} compute instance prices. Top 20 cheapest ${displayName} instances by on-demand hourly rate. Updated weekly from the official API.`,
    canonicalPath: `/providers/${slug}/`,
    h1: `${displayName} Cloud Instance Pricing`,
    intro: `Top 20 cheapest ${displayName} compute instances sorted by on-demand hourly price. Data refreshed weekly from the ${displayName} pricing API.`,
    tableHtml,
    spaLink: `/?providers=${providerId}`,
    jsonLd,
  });
}

// ── GPU instances page ────────────────────────────────────────────────────────

function generateGPUPage(instances) {
  const top20 = instances
    .filter(i => i.gpu != null)
    .sort((a, b) => a.priceUSD_hourly - b.priceUSD_hourly)
    .slice(0, 20);

  const rows = top20.map((inst, i) => {
    const gpuStr = inst.gpu
      ? escHtml(`${inst.gpu.count}× ${inst.gpu.type}${inst.gpu.memoryGiB ? ` (${inst.gpu.memoryGiB} GiB)` : ''}`)
      : '—';
    const badge = PROVIDER_SHORT[inst.provider] ?? inst.provider;
    return `
    <tr>
      <td class="num">${i + 1}</td>
      <td><span class="badge ${inst.provider}">${escHtml(badge)}</span></td>
      <td><strong>${escHtml(inst.instanceType)}</strong></td>
      <td>${inst.vCPU ?? '—'}</td>
      <td>${inst.memoryGiB != null ? inst.memoryGiB : '—'}</td>
      <td>${gpuStr}</td>
      <td class="price">${fmt(inst.priceUSD_hourly)}</td>
    </tr>`;
  }).join('');

  const tableHtml = `<table>
  <thead><tr>
    <th>#</th><th>Provider</th><th>Instance</th><th>vCPU</th><th>RAM&nbsp;(GiB)</th>
    <th>GPU</th><th>On-Demand/hr</th>
  </tr></thead>
  <tbody>${rows}
  </tbody>
</table>`;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: 'GPU Cloud Instances — Price Comparison',
    description: 'Cheapest GPU compute instances across AWS, Azure, GCP, and Oracle Cloud, sorted by hourly on-demand price.',
    url: `${BASE_URL}/gpu-instances/`,
    numberOfItems: top20.length,
    itemListElement: top20.map((inst, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: `${PROVIDER_SHORT[inst.provider] ?? inst.provider} ${inst.instanceType} — ${fmt(inst.priceUSD_hourly)}`,
      description: inst.gpu
        ? `${inst.gpu.count}× ${inst.gpu.type}, ${inst.vCPU} vCPU, ${inst.memoryGiB} GiB RAM`
        : '',
    })),
  };

  return renderPage({
    title: 'GPU Cloud Instances Price Comparison — CloudPriceFinder',
    description:
      'Compare GPU cloud instance prices across AWS, Azure, GCP, and Oracle Cloud. Includes NVIDIA A100, V100, H100, and T4. Sorted by on-demand hourly rate. Updated weekly.',
    canonicalPath: '/gpu-instances/',
    h1: 'GPU Cloud Instances — Price Comparison',
    intro:
      'Top 20 cheapest GPU compute instances across AWS, Azure, Google Cloud, and Oracle Cloud, sorted by on-demand hourly price.',
    tableHtml,
    spaLink: '/?gpu=1',
    jsonLd,
  });
}

// ── ARM64 instances page ──────────────────────────────────────────────────────

function generateARMPage(instances) {
  const top20 = instances
    .filter(i => i.architecture === 'arm64' && i.vCPU > 0)
    .sort((a, b) => a.priceUSD_hourly / a.vCPU - b.priceUSD_hourly / b.vCPU)
    .slice(0, 20);

  const rows = top20.map((inst, i) => {
    const perVCPU = (inst.priceUSD_hourly / inst.vCPU).toFixed(5);
    const badge = PROVIDER_SHORT[inst.provider] ?? inst.provider;
    return `
    <tr>
      <td class="num">${i + 1}</td>
      <td><span class="badge ${inst.provider}">${escHtml(badge)}</span></td>
      <td><strong>${escHtml(inst.instanceType)}</strong></td>
      <td>${inst.vCPU}</td>
      <td>${inst.memoryGiB != null ? inst.memoryGiB : '—'}</td>
      <td class="price">${fmt(inst.priceUSD_hourly)}</td>
      <td>$${perVCPU}/vCPU/hr</td>
    </tr>`;
  }).join('');

  const tableHtml = `<table>
  <thead><tr>
    <th>#</th><th>Provider</th><th>Instance</th><th>vCPU</th><th>RAM&nbsp;(GiB)</th>
    <th>On-Demand/hr</th><th>$/vCPU/hr</th>
  </tr></thead>
  <tbody>${rows}
  </tbody>
</table>`;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: 'ARM64 Cloud Instances — Price Comparison',
    description:
      'Most cost-effective ARM64 instances (AWS Graviton, Google Tau T2A, Azure Ampere Altra, OCI Ampere A1) sorted by price per vCPU per hour.',
    url: `${BASE_URL}/arm-instances/`,
    numberOfItems: top20.length,
    itemListElement: top20.map((inst, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: `${PROVIDER_SHORT[inst.provider] ?? inst.provider} ${inst.instanceType} — $${(inst.priceUSD_hourly / inst.vCPU).toFixed(5)}/vCPU/hr`,
      description: `${inst.vCPU} vCPU, ${inst.memoryGiB} GiB RAM, ${fmt(inst.priceUSD_hourly)}`,
    })),
  };

  return renderPage({
    title: 'ARM64 Cloud Instances Price Comparison — CloudPriceFinder',
    description:
      'Compare ARM64 cloud instances across AWS (Graviton), Azure (Ampere Altra), Google Cloud (Tau T2A), and Oracle Cloud (Ampere A1). Sorted by price per vCPU per hour.',
    canonicalPath: '/arm-instances/',
    h1: 'ARM64 Cloud Instances — Price Comparison',
    intro:
      'Top 20 most cost-effective ARM64 compute instances across AWS (Graviton), Azure (Ampere Altra), Google Cloud (Tau T2A), and Oracle Cloud (Ampere A1), sorted by price per vCPU/hr.',
    tableHtml,
    spaLink: '/?arch=arm64',
    jsonLd,
  });
}

// ── Comparison pages ──────────────────────────────────────────────────────────

function generateComparisonPage(instances, providerA, providerB, slug) {
  const labelA = PROVIDER_SHORT[providerA];
  const labelB = PROVIDER_SHORT[providerB];
  const fullA = PROVIDER_LABEL[providerA];
  const fullB = PROVIDER_LABEL[providerB];

  // Cheapest instance per vCPU count for each provider
  const cheapestA = new Map();
  const cheapestB = new Map();

  for (const inst of instances) {
    if (!inst.vCPU || inst.vCPU <= 0) continue;
    if (inst.provider === providerA) {
      const cur = cheapestA.get(inst.vCPU);
      if (!cur || inst.priceUSD_hourly < cur.priceUSD_hourly) cheapestA.set(inst.vCPU, inst);
    }
    if (inst.provider === providerB) {
      const cur = cheapestB.get(inst.vCPU);
      if (!cur || inst.priceUSD_hourly < cur.priceUSD_hourly) cheapestB.set(inst.vCPU, inst);
    }
  }

  // vCPU tiers present in both, sorted ascending, capped at 20
  const tiers = [...cheapestA.keys()]
    .filter(v => cheapestB.has(v))
    .sort((a, b) => a - b)
    .slice(0, 20);

  const rows = tiers.map((vcpu, i) => {
    const a = cheapestA.get(vcpu);
    const b = cheapestB.get(vcpu);
    const cheaperLabel = a.priceUSD_hourly <= b.priceUSD_hourly ? labelA : labelB;
    const savingsPct = Math.round(
      (Math.abs(a.priceUSD_hourly - b.priceUSD_hourly) /
        Math.max(a.priceUSD_hourly, b.priceUSD_hourly)) *
        100,
    );
    return `
    <tr>
      <td class="num">${i + 1}</td>
      <td><strong>${vcpu}</strong></td>
      <td>${escHtml(a.instanceType)}</td>
      <td class="price">${fmt(a.priceUSD_hourly)}</td>
      <td>${escHtml(b.instanceType)}</td>
      <td class="price">${fmt(b.priceUSD_hourly)}</td>
      <td><span class="badge ${cheaperLabel === labelA ? providerA : providerB}">${escHtml(cheaperLabel)}</span> <small style="color:#6b7280">${savingsPct}% cheaper</small></td>
    </tr>`;
  }).join('');

  const tableHtml = `<table>
  <thead><tr>
    <th>#</th><th>vCPU</th>
    <th>${escHtml(labelA)} Instance</th><th>${escHtml(labelA)} $/hr</th>
    <th>${escHtml(labelB)} Instance</th><th>${escHtml(labelB)} $/hr</th>
    <th>Cheaper</th>
  </tr></thead>
  <tbody>${rows}
  </tbody>
</table>`;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: `${fullA} vs ${fullB} Cloud Instance Price Comparison`,
    description: `Side-by-side comparison of cheapest ${fullA} vs ${fullB} compute instances for each vCPU tier.`,
    url: `${BASE_URL}/compare/${slug}/`,
    numberOfItems: tiers.length,
    itemListElement: tiers.map((vcpu, i) => {
      const a = cheapestA.get(vcpu);
      const b = cheapestB.get(vcpu);
      return {
        '@type': 'ListItem',
        position: i + 1,
        name: `${vcpu} vCPU: ${labelA} ${a.instanceType} ${fmt(a.priceUSD_hourly)} vs ${labelB} ${b.instanceType} ${fmt(b.priceUSD_hourly)}`,
      };
    }),
  };

  return renderPage({
    title: `${fullA} vs ${fullB} Instance Pricing — CloudPriceFinder`,
    description: `Compare ${fullA} vs ${fullB} compute instance prices side by side. Cheapest option per vCPU tier. Updated weekly.`,
    canonicalPath: `/compare/${slug}/`,
    h1: `${fullA} vs ${fullB} — Cloud Instance Price Comparison`,
    intro: `Side-by-side comparison of the cheapest ${fullA} and ${fullB} compute instances for each vCPU tier. Find which provider offers better value for your workload size.`,
    tableHtml,
    spaLink: `/?providers=${providerA},${providerB}`,
    jsonLd,
  });
}

// ── Write helper ──────────────────────────────────────────────────────────────

function writePage(routePath, html) {
  const dir = join(distDir, routePath);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'index.html'), html, 'utf-8');
  console.log(`[generate-pages] ✓ ${routePath}/index.html`);
}

// ── Main ──────────────────────────────────────────────────────────────────────

if (!existsSync(distDir)) {
  console.error('[generate-pages] dist/ not found — run astro build first.');
  process.exit(1);
}

const instances = loadAllInstances();
console.log(`[generate-pages] Loaded ${instances.length} cloud-server instances`);

const PROVIDERS = [
  { id: 'aws', slug: 'aws', displayName: 'AWS' },
  { id: 'azure', slug: 'azure', displayName: 'Azure' },
  { id: 'gcp', slug: 'gcp', displayName: 'Google Cloud' },
  { id: 'oci', slug: 'oracle-cloud', displayName: 'Oracle Cloud' },
];

for (const p of PROVIDERS) {
  writePage(`providers/${p.slug}`, generateProviderPage(instances, p.id, p));
}

writePage('gpu-instances', generateGPUPage(instances));
writePage('arm-instances', generateARMPage(instances));

writePage('compare/aws-vs-azure', generateComparisonPage(instances, 'aws', 'azure', 'aws-vs-azure'));
writePage('compare/aws-vs-gcp', generateComparisonPage(instances, 'aws', 'gcp', 'aws-vs-gcp'));

console.log('[generate-pages] Done. 8 pages written.');
