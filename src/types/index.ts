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