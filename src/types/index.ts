export * from './cloud';

export interface APIResponse<T> {
  data: T;
  success: boolean;
  error?: string;
  timestamp: string;
}

export interface ProviderFileInfo {
  file: string;
  count: number;
  lastUpdated: string;
}

export interface DataStructure {
  combined: string;
  providers: Record<string, ProviderFileInfo>;
  description: string;
}

export interface DataSummary {
  totalInstances: number;
  providersCount: number;
  lastUpdated: string;
  priceRange: {
    min: number;
    max: number;
  };
  byProvider?: Record<string, number>;
  byType?: Record<string, number>;
  errors?: Record<string, string>;
  providerFiles?: Record<string, ProviderFileInfo>;
  dataStructure?: DataStructure;
}

// ---------------------------------------------------------------------------
// v3 Three-tier data types (produced by scripts/aggregate.py)
// ---------------------------------------------------------------------------

/** Family-level summary stored inside index.json's provider.families[] */
export interface V3FamilySummary {
  id: string;
  count: number;
  vCPURange: [number, number];
  ramRange: [number, number];
  architectures: string[];
  hasGPU: boolean;
  commitmentTerms: string[];
  medianPricePerVCPU: number;
  medianPricePerGiB: number;
}

/** Provider entry inside index.json */
export interface V3ProviderEntry {
  id: string;
  instanceCount: number;
  familyCount: number;
  regionCount: number;
  regions: string[];
  vcpuRange: [number, number];
  ramRange: [number, number];
  commitmentTerms: string[];
  families: V3FamilySummary[];
}

/** Shape of /data/index.json */
export interface V3Index {
  schemaVersion: string;
  lastUpdated: string;
  providers: V3ProviderEntry[];
  instanceCounts: Record<string, number>;
  primaryRegions?: Record<string, string[]>;
}

/** A single instance record as it appears in a family file or instance file */
export interface V3Instance {
  provider: string;
  type: string;
  instanceType: string;
  family: string;
  architecture: string;
  vCPU: number;
  memoryGiB: number;
  priceUSD_hourly: number;
  priceUSD_monthly: number;
  commitments: Array<{
    term: '1yr' | '3yr';
    payment: string;
    product: string;
    priceUSD_hourly: number;
    effectiveHourlyUSD: number;
    savingsVsOnDemandPct: number;
  }>;
  regions: string[];
  source: string;
  lastUpdated: string;
  generation?: string;
  gpu?: { count: number; type: string; memoryGiB: number };
  diskType?: string;
  diskSizeGB?: number;
  networkPerformance?: string;
}

/** Shape of /data/families/{provider}/{family}.json — an array of instances */
export type V3FamilyFile = V3Instance[];

/** Shape of /data/instances/{provider}/{id}.json — single instance + per-region pricing */
export interface V3InstanceFile extends V3Instance {
  regionPricing?: Record<string, { priceUSD_hourly: number; priceUSD_monthly: number }>;
}
