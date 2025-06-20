export interface LocationDetail {
  code: string;
  city: string;
  country: string;
  countryCode: string;
  region: string;
}

export interface PriceRange {
  min: number | null;
  max: number | null;
  hasVariation?: boolean;
}

export interface RegionalPricing {
  location: string;
  hourly_net: number;
  monthly_net: number;
  included_traffic: number;
  traffic_price_per_tb: number;
}

export interface NetworkPricingOption {
  available: boolean;
  hourly: number | null;
  monthly: number | null;
  savings?: number | null;
  description: string;
  priceRange?: {
    hourly: PriceRange;
    monthly: PriceRange;
  };
}

export interface CloudInstance {
  // Core identification
  provider: CloudProvider;
  type: InstanceType;
  instanceType: string;
  
  // Platform information (for multi-platform providers like Hetzner)
  platform?: 'cloud' | 'dedicated';
  
  // Compute specs
  vCPU?: number;
  memoryGiB?: number;
  
  // Storage
  diskType?: string;
  diskSizeGB?: number;
  
  // Pricing (standardized to USD/hour)
  priceUSD_hourly: number;
  priceUSD_monthly: number;
  
  // Original pricing (provider currency)
  originalPrice?: {
    hourly: number;
    monthly: number;
    currency: string;
  };
  
  // Network & Performance
  networkPerformance?: string;
  bandwidth?: number;
  trafficIncludedTB?: number;
  
  // Network Options & IP Configuration (new structure)
  networkOptions?: {
    ipv4_ipv6?: NetworkPricingOption;
    ipv6_only?: NetworkPricingOption;
  };
  defaultNetworkType?: 'ipv4_ipv6' | 'ipv6_only';
  supportsIPv6Only?: boolean;
  
  // Legacy network options (keep for compatibility)
  networkType?: 'ipv4_ipv6' | 'ipv6_only';
  ipConfiguration?: string;
  includesPublicIPv4?: boolean;
  includesPublicIPv6?: boolean;
  ipv4_savings?: number;
  ipType?: 'ipv4' | 'ipv6' | 'ipv4_ipv6';
  ipv6OnlyAvailable?: boolean;
  ipv6OnlyDiscount?: number;
  priceEUR_monthly_ipv6only?: number;
  priceEUR_hourly_ipv6only?: number;
  
  // Location & Availability
  regions: string[];
  locationDetails?: LocationDetail[];
  locations?: string[];
  
  // Regional Pricing Information
  regionalPricing?: RegionalPricing[];
  priceRange?: {
    hourly: PriceRange;
    monthly: PriceRange;
  };
  
  // Service-specific properties
  max_connections?: number;        // Load balancers
  max_services?: number;           // Load balancers
  max_targets?: number;            // Load balancers
  unit?: string;                   // Storage pricing units
  location?: string;               // Single location services
  
  // Metadata
  deprecated?: boolean;
  source: string;
  description?: string;
  lastUpdated: string;
  
  // Provider-specific metadata
  hetzner_metadata?: {
    platform: 'cloud' | 'dedicated';
    apiSource: 'cloud_api' | 'robot_api' | 'web_scraping';
    serviceCategory: string;
    auctionServer?: boolean;
    robotServerId?: string;
  };
  
  // Provider-specific data
  raw?: Record<string, any>;
}

export type CloudProvider = 
  | 'aws' 
  | 'azure' 
  | 'gcp' 
  | 'hetzner' 
  | 'oci' 
  | 'ovh';

export type InstanceType = 
  | 'cloud-server' 
  | 'cloud-loadbalancer' 
  | 'cloud-volume' 
  | 'cloud-network' 
  | 'cloud-floating-ip'
  | 'cloud-snapshot'
  | 'cloud-certificate'
  | 'dedicated-server'
  | 'dedicated-auction'
  | 'dedicated-storage'
  | 'dedicated-colocation';

export interface FilterOptions {
  providers: CloudProvider[];
  minVCPU: number;
  maxVCPU: number;
  minMemory: number;
  maxMemory: number;
  maxPrice: number;
  regions: string[];
  instanceTypes: InstanceType[];
  ipTypes?: string[];
  networkOptions?: string[];
  ipv6OnlyMode?: boolean;
  pricingMode?: 'ipv4_ipv6' | 'ipv6_only' | 'all';
}

export interface SortOption {
  field: keyof CloudInstance;
  direction: 'asc' | 'desc';
}

export interface ComparisonView {
  instances: CloudInstance[];
  filters: FilterOptions;
  sort: SortOption;
  groupBy?: keyof CloudInstance;
}