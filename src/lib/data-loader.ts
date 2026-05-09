/**
 * Data loading utilities for CloudPriceFinder v3 frontend.
 *
 * v3 lazy-loading strategy:
 *  - loadIndex()              fetch /data/index.json once; cached
 *  - loadFamily(prov, fam)    fetch /data/families/{prov}/{fam}.json; cached per URL
 *  - loadInstance(prov, id)   fetch /data/instances/{prov}/{id}.json; cached per URL
 *
 * All functions use relative paths so Cloudflare Pages serves them as static files.
 * In-memory cache prevents duplicate network requests within the same page session.
 *
 * Build-time usage: loadIndex() is also called from index.astro (server-side Node context)
 * using the same function — Astro's fetch() polyfill makes this work at build time when
 * the data files exist locally.
 */

import type { V3Index, V3FamilyFile, V3InstanceFile } from '../types';

// ---------------------------------------------------------------------------
// In-memory cache keyed by URL string
// ---------------------------------------------------------------------------
const _cache = new Map<string, unknown>();

async function _fetchJSON<T>(url: string): Promise<T> {
  if (_cache.has(url)) {
    return _cache.get(url) as T;
  }
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch ${url}: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as T;
  _cache.set(url, data);
  return data;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Load the top-level index produced by aggregate.py.
 * Contains provider list, family list per provider, region list, vCPU/RAM buckets,
 * commitment terms supported, lastUpdated, instance counts.
 */
export async function loadIndex(): Promise<V3Index> {
  return _fetchJSON<V3Index>('/data/index.json');
}

/**
 * Load all instances in a provider+family combination.
 * Returns an array of instance objects, each with commitments[].
 */
export async function loadFamily(provider: string, family: string): Promise<V3FamilyFile> {
  return _fetchJSON<V3FamilyFile>(`/data/families/${provider}/${family}.json`);
}

/**
 * Load full detail for a single instance (includes per-region pricing breakdown).
 * The instance id is the instanceType string (e.g. "m7i.xlarge").
 */
export async function loadInstance(provider: string, id: string): Promise<V3InstanceFile> {
  // Instance file names use the instanceType as-is; dots are kept in the filename.
  return _fetchJSON<V3InstanceFile>(`/data/instances/${provider}/${id}.json`);
}

/**
 * Load multiple family files in parallel for a given provider.
 * Returns a flat array of all instances across all requested families.
 */
export async function loadFamilies(
  provider: string,
  families: string[]
): Promise<V3FamilyFile> {
  const results = await Promise.all(families.map((f) => loadFamily(provider, f)));
  return results.flat() as V3FamilyFile;
}

/**
 * Clear the in-memory cache. Useful in tests or when data is stale.
 */
export function clearCache(): void {
  _cache.clear();
}
