export * from './cloud';

export interface APIResponse<T> {
  data: T;
  success: boolean;
  error?: string;
  timestamp: string;
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
}