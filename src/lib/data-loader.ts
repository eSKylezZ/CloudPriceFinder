/**
 * Data loading utilities for CloudPriceFinder frontend.
 * Handles loading and processing of cloud instance data with support for both
 * combined and split provider data files.
 */

import type { CloudInstance, DataSummary } from '../types';
import fs from 'fs/promises';
import path from 'path';

const DATA_DIR = path.join(process.cwd(), 'data');
// const PROVIDERS_DIR = path.join(DATA_DIR, 'providers');
const INSTANCES_FILE = path.join(DATA_DIR, 'all_instances.json');
const SUMMARY_FILE = path.join(DATA_DIR, 'summary.json');

export interface LoadingStrategy {
  mode: 'combined' | 'split' | 'selective';
  providers?: string[]; // For selective loading
}

/**
 * Load cloud instance data using specified strategy.
 */
export async function loadInstanceData(strategy: LoadingStrategy = { mode: 'combined' }): Promise<CloudInstance[]> {
  try {
    let instances: CloudInstance[] = [];
    
    switch (strategy.mode) {
      case 'combined':
        instances = await loadCombinedData();
        break;
      
      case 'split':
        instances = await loadSplitData();
        break;
      
      case 'selective':
        instances = await loadSelectiveData(strategy.providers || []);
        break;
      
      default:
        console.warn(`Unknown loading strategy: ${strategy.mode}, falling back to combined`);
        instances = await loadCombinedData();
    }
    
    // Sort by price by default - handle both USD and EUR pricing
    return instances.sort((a, b) => {
      const aPriceHourly = a.priceUSD_hourly || (a as any).priceEUR_hourly_net || 0;
      const bPriceHourly = b.priceUSD_hourly || (b as any).priceEUR_hourly_net || 0;
      return aPriceHourly - bPriceHourly;
    });
  } catch (error) {
    console.error('Failed to load instance data:', error);
    return [];
  }
}

/**
 * Load data from the combined file (backward compatibility).
 */
async function loadCombinedData(): Promise<CloudInstance[]> {
  const data = await fs.readFile(INSTANCES_FILE, 'utf-8');
  return JSON.parse(data);
}

/**
 * Load data from split provider files.
 */
async function loadSplitData(): Promise<CloudInstance[]> {
  const summary = await loadSummaryData();
  if (!summary?.providerFiles) {
    console.warn('No provider file info in summary, falling back to combined data');
    return loadCombinedData();
  }
  
  const allInstances: CloudInstance[] = [];
  
  // Load each provider file
  for (const [provider, info] of Object.entries(summary.providerFiles)) {
    try {
      const providerFile = path.join(DATA_DIR, info.file);
      const data = await fs.readFile(providerFile, 'utf-8');
      const instances: CloudInstance[] = JSON.parse(data);
      allInstances.push(...instances);
      console.log(`✅ Loaded ${instances.length} instances from ${provider}`);
    } catch (error) {
      console.warn(`Failed to load ${provider} data:`, error);
    }
  }
  
  return allInstances;
}

/**
 * Load data only from specified providers.
 */
async function loadSelectiveData(providers: string[]): Promise<CloudInstance[]> {
  if (providers.length === 0) {
    console.warn('No providers specified for selective loading');
    return [];
  }
  
  const summary = await loadSummaryData();
  if (!summary?.providerFiles) {
    console.warn('No provider file info in summary, cannot load selectively');
    return [];
  }
  
  const allInstances: CloudInstance[] = [];
  
  // Load only specified provider files
  for (const provider of providers) {
    const info = summary.providerFiles[provider];
    if (!info) {
      console.warn(`Provider ${provider} not found in available data`);
      continue;
    }
    
    try {
      const providerFile = path.join(DATA_DIR, info.file);
      const data = await fs.readFile(providerFile, 'utf-8');
      const instances: CloudInstance[] = JSON.parse(data);
      allInstances.push(...instances);
      console.log(`✅ Loaded ${instances.length} instances from ${provider}`);
    } catch (error) {
      console.warn(`Failed to load ${provider} data:`, error);
    }
  }
  
  return allInstances;
}

/**
 * Get list of available providers from summary data.
 */
export async function getAvailableProviders(): Promise<string[]> {
  try {
    const summary = await loadSummaryData();
    if (summary?.providerFiles) {
      return Object.keys(summary.providerFiles);
    }
    
    // Fallback: get providers from combined data
    const instances = await loadCombinedData();
    const providers = new Set(instances.map(i => i.provider));
    return Array.from(providers);
  } catch (error) {
    console.error('Failed to get available providers:', error);
    return [];
  }
}

/**
 * Load only instances from a specific provider (optimized for single provider).
 */
export async function loadProviderData(provider: string): Promise<CloudInstance[]> {
  try {
    const summary = await loadSummaryData();
    if (summary?.providerFiles?.[provider]) {
      const info = summary.providerFiles[provider];
      const providerFile = path.join(DATA_DIR, info.file);
      const data = await fs.readFile(providerFile, 'utf-8');
      return JSON.parse(data);
    }
    
    // Fallback: filter from combined data
    console.warn(`Provider file for ${provider} not found, filtering from combined data`);
    const allInstances = await loadCombinedData();
    return allInstances.filter(instance => instance.provider === provider);
  } catch (error) {
    console.error(`Failed to load ${provider} data:`, error);
    return [];
  }
}

/**
 * Load data summary statistics with provider file information.
 */
export async function loadSummaryData(): Promise<DataSummary | null> {
  try {
    const data = await fs.readFile(SUMMARY_FILE, 'utf-8');
    return JSON.parse(data);
  } catch (error) {
    console.error('Failed to load summary data:', error);
    return null;
  }
}

/**
 * Get unique values for a specific field across all instances.
 */
export function getUniqueValues<T extends keyof CloudInstance>(
  instances: CloudInstance[],
  field: T
): CloudInstance[T][] {
  const values = instances.map(instance => instance[field]);
  return [...new Set(values)].filter(Boolean);
}

/**
 * Filter instances based on criteria.
 */
export function filterInstances(
  instances: CloudInstance[],
  filters: {
    providers?: string[];
    minVCPU?: number;
    maxVCPU?: number;
    minMemory?: number;
    maxMemory?: number;
    maxPrice?: number;
    instanceTypes?: string[];
  }
): CloudInstance[] {
  return instances.filter(instance => {
    // Provider filter
    if (filters.providers?.length && !filters.providers.includes(instance.provider)) {
      return false;
    }

    // vCPU filters
    if (filters.minVCPU && instance.vCPU && instance.vCPU < filters.minVCPU) {
      return false;
    }
    if (filters.maxVCPU && instance.vCPU && instance.vCPU > filters.maxVCPU) {
      return false;
    }

    // Memory filters
    if (filters.minMemory && instance.memoryGiB && instance.memoryGiB < filters.minMemory) {
      return false;
    }
    if (filters.maxMemory && instance.memoryGiB && instance.memoryGiB > filters.maxMemory) {
      return false;
    }

    // Price filter
    if (filters.maxPrice && instance.priceUSD_hourly > filters.maxPrice) {
      return false;
    }

    // Instance type filter
    if (filters.instanceTypes?.length && !filters.instanceTypes.includes(instance.type)) {
      return false;
    }

    return true;
  });
}

/**
 * Sort instances by specified field and direction.
 */
export function sortInstances(
  instances: CloudInstance[],
  field: keyof CloudInstance,
  direction: 'asc' | 'desc' = 'asc'
): CloudInstance[] {
  return [...instances].sort((a, b) => {
    const aVal = a[field];
    const bVal = b[field];

    if (aVal === bVal) return 0;
    if (aVal == null && bVal == null) return 0;
    if (aVal == null) return 1;
    if (bVal == null) return -1;

    const comparison = aVal < bVal ? -1 : 1;
    return direction === 'asc' ? comparison : -comparison;
  });
}