/**
 * Data loading utilities for CloudPriceFinder frontend.
 * Handles loading and processing of cloud instance data.
 */

import type { CloudInstance, DataSummary } from '../types';
import fs from 'fs/promises';
import path from 'path';

const DATA_DIR = path.join(process.cwd(), 'data');
const INSTANCES_FILE = path.join(DATA_DIR, 'all_instances.json');
const SUMMARY_FILE = path.join(DATA_DIR, 'summary.json');

/**
 * Load all cloud instance data.
 */
export async function loadInstanceData(): Promise<CloudInstance[]> {
  try {
    const data = await fs.readFile(INSTANCES_FILE, 'utf-8');
    const instances: CloudInstance[] = JSON.parse(data);
    
    // Sort by price by default - handle both USD and EUR pricing
    return instances.sort((a, b) => {
      const aPriceHourly = a.priceUSD_hourly || a.priceEUR_hourly_net || 0;
      const bPriceHourly = b.priceUSD_hourly || b.priceEUR_hourly_net || 0;
      return aPriceHourly - bPriceHourly;
    });
  } catch (error) {
    console.error('Failed to load instance data:', error);
    return [];
  }
}

/**
 * Load data summary statistics.
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