# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CloudPriceFinder v2.0 is a completely redesigned multi-cloud instance comparison application that helps users compare costs and specifications across AWS, Azure, Google Cloud, Hetzner, Oracle Cloud, and OVH. The project uses a modern hybrid architecture with Python data collection and an Astro-based frontend.

## Architecture

### Data Collection Layer (Python)
- **Location**: `scripts/` directory
- **Orchestrator**: `scripts/orchestrator.py` - Master controller for all data fetching
- **Individual Fetchers**: `scripts/fetch_{provider}.py` - Provider-specific data collection
- **Shared Utilities**: `scripts/utils/` - Common functionality for validation, normalization, and currency conversion
- **Output**: Standardized JSON files in `data/` directory with both combined and split formats
  - `data/all_instances.json` - Combined data (backward compatibility)
  - `data/providers/{provider}.json` - Individual provider files for selective loading
  - `data/summary.json` - Enhanced summary with provider file metadata

### Frontend Layer (Astro + TypeScript)
- **Framework**: Astro v4 with static site generation
- **Styling**: Tailwind CSS for responsive design
- **Components**: Reusable UI components in `src/components/`
- **Pages**: Route-based pages in `src/pages/`
- **Data Integration**: TypeScript utilities for loading and processing JSON data with multiple loading strategies:
  - Combined loading (legacy compatibility)
  - Split loading (load individual provider files)
  - Selective loading (load only specific providers)
- **Type Safety**: Comprehensive TypeScript definitions in `src/types/`

### Build & Deploy Pipeline
- **Data Collection**: Automated via `npm run fetch-data`
- **Build Process**: `npm run build` (fetches data then builds site)
- **Development**: `npm run dev` for live development server

## Development Commands

### Primary Workflow
- `npm run dev` - Start development server (http://localhost:4321)
- `npm run build` - Build static site only
- `npm run build-with-data` - Full production build (includes data fetching)
- `npm run preview` - Preview production build
- `npm run fetch-data` - Run data collection only

### Data Collection
- `npm run fetch-hetzner` - Fetch Hetzner data only (fully implemented)
- `python scripts/orchestrator.py` - Run complete data collection pipeline
- Individual scripts: `python scripts/fetch_{provider}.py`

#### Provider Configuration (NEW FEATURE)
The orchestrator now includes a **central configuration system** to enable/disable providers:

**Location**: `scripts/orchestrator.py` - `PROVIDER_CONFIG` section

**Usage**:
```python
PROVIDER_CONFIG = {
    'hetzner': {
        'enabled': True,   # Set to False to skip fetching and use existing data
        'description': 'Hetzner Cloud and Dedicated Servers'
    },
    'oci': {
        'enabled': True,   # Set to False to skip fetching and use existing data  
        'description': 'Oracle Cloud Infrastructure'
    },
    # ... other providers
}
```

**Benefits**:
- **Selective Updates**: Only fetch data from providers you want to update
- **Save API Calls**: Skip providers to avoid hitting rate limits or timeouts
- **Use Existing Data**: Disabled providers will load data from saved JSON files
- **Faster Development**: Test with subset of providers during development

### Code Quality
- `npm run type-check` - TypeScript type checking
- `npm run lint` - ESLint code linting
- `npm run format` - Prettier code formatting
- `npm run test` - Run test suite

### Utilities
- `npm run clean` - Clean build artifacts and data files

## Environment Setup

### Node.js Dependencies
Install with: `npm install`

Key dependencies:
- `astro` - Static site generator
- `@astrojs/tailwind` - Tailwind CSS integration
- `@astrojs/sitemap` - SEO sitemap generation
- `lucide-astro` - Icon library
- TypeScript toolchain and linting tools

### Python Dependencies
Install with: `pip install -r requirements.txt`

Required packages:
- `requests` - HTTP client for API calls
- `beautifulsoup4` - HTML parsing (for web scraping)
- `python-dotenv` - Environment variable management

### Environment Variables

#### Required for Hetzner (Fully Implemented)
- `HETZNER_API_TOKEN` - Hetzner Cloud API token
  - Get from: https://console.hetzner.cloud → Security → API Tokens
  - Permissions: Read access is sufficient
  - Used by: `scripts/fetch_hetzner.py`

#### Oracle Cloud Infrastructure (OCI) - Implemented
**No Authentication Required** - Uses public APIs and documentation
- `scripts/fetch_oci.py` - Complete OCI compute instance fetcher
- Fetches pricing for 26+ instance types including:
  - Standard E-series (AMD EPYC and Intel)
  - Ampere A1 (ARM-based instances)
  - DenseIO shapes with NVMe storage
  - GPU instances with NVIDIA V100
  - Free tier eligible instances
- Supports all OCI regions globally
- Uses Oracle's public pricing data and compute shape specifications

#### Placeholders for Other Providers
When implementing other cloud providers, you may need:
- `AWS_ACCESS_KEY_ID` & `AWS_SECRET_ACCESS_KEY`
- `AZURE_SUBSCRIPTION_ID` & `AZURE_CLIENT_ID` etc.
- `GOOGLE_CLOUD_PROJECT` & service account credentials
- `OVH_ENDPOINT` & `OVH_APPLICATION_KEY` etc.

## Data Standards

### Standardized Instance Format
All cloud provider data is normalized to this TypeScript interface:

```typescript
interface CloudInstance {
  provider: 'aws' | 'azure' | 'gcp' | 'hetzner' | 'oci' | 'ovh';
  type: 'cloud-server' | 'cloud-loadbalancer' | 'cloud-volume' | 'cloud-network' | 'cloud-floating-ip' | 'dedicated-server';
  instanceType: string;
  vCPU: number;
  memoryGiB: number;
  diskType?: string;
  diskSizeGB?: number;
  priceUSD_hourly: number;
  priceUSD_monthly: number;
  originalPrice?: {
    hourly: number;
    monthly: number;
    currency: string;
  };
  regions: string[];
  deprecated?: boolean;
  source: string;
  description?: string;
  lastUpdated: string;
  raw?: Record<string, any>;
}
```

### Currency Conversion
- All prices standardized to USD
- Original pricing preserved in `originalPrice` field
- Exchange rates fetched from free API with fallback rates
- Conversion utilities in `scripts/utils/currency_converter.py`

### Data Validation
- Schema validation in `scripts/utils/data_validator.py`
- Required fields enforced
- Numeric ranges validated
- Invalid data filtered out with logging

## File Structure

```
├── src/                          # Astro frontend source
│   ├── components/               # Reusable UI components
│   │   ├── ComparisonTable.astro # Main comparison table
│   │   ├── FilterPanel.astro     # Filtering interface
│   │   └── DataSummary.astro     # Data statistics
│   ├── layouts/                  # Page layouts
│   │   └── BaseLayout.astro      # Main layout template
│   ├── pages/                    # Route pages
│   │   └── index.astro           # Homepage
│   ├── lib/                      # TypeScript utilities
│   │   └── data-loader.ts        # Data loading functions
│   ├── types/                    # TypeScript definitions
│   │   ├── cloud.ts              # Cloud instance types
│   │   └── index.ts              # Exported types
│   └── styles/                   # Global styles
├── scripts/                      # Python data collection
│   ├── utils/                    # Shared utilities
│   │   ├── data_validator.py     # Data validation
│   │   ├── data_normalizer.py    # Data normalization
│   │   └── currency_converter.py # Currency conversion
│   ├── orchestrator.py           # Master data coordinator
│   ├── fetch_hetzner.py          # Hetzner implementation (complete)
│   └── fetch_{provider}.py       # Other providers (placeholders)
├── data/                         # Generated JSON data
│   ├── all_instances.json        # Combined instance data
│   └── summary.json              # Data statistics
├── public/                       # Static assets
├── tests/                        # Test suites
└── Configuration files...
```

## Implementation Status

### ✅ Completed
- **Frontend**: Complete Astro application with filtering, sorting, and responsive design
- **Hetzner Provider**: Full API integration with cloud and dedicated server support
- **Oracle Cloud Infrastructure (OCI)**: Complete compute instance fetcher with 26+ instance types
- **Data Pipeline**: Orchestrator, validation, normalization, currency conversion, and split data files
- **Type Safety**: Complete TypeScript definitions and interfaces
- **Development Tooling**: ESLint, Prettier, build scripts, and development server
- **GitHub Actions**: Complete automation for data collection and deployment
- **Filtering System**: Advanced filtering with region-specific pricing and network options support
- **Regional Pricing**: Support for location-specific pricing display and filtering
- **Currency Persistence**: Fixed currency selection during filtering operations

### 🔄 In Progress / Placeholders
- **AWS Provider**: Placeholder script awaiting implementation
- **Azure Provider**: Placeholder script awaiting implementation
- **Google Cloud Provider**: Placeholder script awaiting implementation
- **OVH Provider**: Placeholder script awaiting implementation

### 🚀 Ready for Enhancement
- **Testing**: Test framework configured but tests need to be written
- **CI/CD**: Ready for GitHub Actions or similar pipeline
- **Monitoring**: Data freshness and error monitoring
- **Caching**: CDN and browser caching optimization

## Development Patterns

### Adding New Cloud Providers
1. **Create Provider Script**: Copy `scripts/fetch_hetzner.py` as template
2. **Implement Data Fetching**: Use provider's API or web scraping
3. **Add to Orchestrator**: Register in `scripts/orchestrator.py`
4. **Test Integration**: Ensure data validates and displays correctly
5. **Update Documentation**: Add environment variables and setup instructions

### Frontend Component Development
1. **Follow Astro Patterns**: Use `.astro` files for components
2. **TypeScript First**: Use strict typing with defined interfaces
3. **Tailwind Styling**: Use utility classes for consistent design
4. **Accessibility**: Include ARIA labels and keyboard navigation
5. **Responsive Design**: Mobile-first approach with breakpoint considerations

### Data Processing Guidelines
1. **Validation First**: All data must pass validation before storage
2. **Normalize Consistently**: Use standard format across all providers
3. **Error Handling**: Log errors but don't crash the entire pipeline
4. **Currency Conversion**: Always convert to USD with original preserved
5. **Source Attribution**: Track data source and last update time

## Performance Considerations

### Data Collection
- **Concurrent Fetching**: Orchestrator runs providers in parallel
- **Rate Limiting**: Respect provider API limits
- **Caching**: API responses cached for development
- **Error Recovery**: Retry logic and graceful degradation

### Frontend Performance
- **Static Generation**: Pre-built pages for fast loading
- **Client-Side Filtering**: JavaScript filtering for responsive UX
- **Lazy Loading**: Large datasets handled efficiently
- **Image Optimization**: Astro's built-in image optimization

## Troubleshooting

### Common Issues
1. **No Data Displayed**: Run `npm run fetch-data` to collect pricing data
2. **Hetzner API Errors**: Check `HETZNER_API_TOKEN` environment variable
3. **Build Failures**: Ensure Node.js 18+ and Python 3.8+ are installed
4. **Type Errors**: Run `npm run type-check` for detailed TypeScript errors
5. **Filtering Issues**: Cloud instances not appearing may indicate network options filter problems - check data structure compatibility

### Data Collection Problems
- Check internet connectivity for API calls
- Verify environment variables are set correctly
- Check logs in console for specific error messages
- Ensure `data/` directory exists and is writable

## Recent Fixes and Improvements

### Filtering System Enhancements (2025-06-18)
- **Fixed Network Options Filter**: Updated to handle both object and string formats for networkOptions data
- **Improved Region Filtering**: Enhanced to work with regionalPricing array structure and locationDetails
- **Instance Type Grouping**: Organized types into "Cloud Server" and "Dedicated Server" categories
- **Regional Pricing Display**: Fixed price display to show region-specific pricing when available
- **Popup Interaction**: Added hover delay for region information popups to improve usability
- **Data Accuracy Disclaimer**: Added notice about pricing data being approximate and requiring verification

### Technical Details
- **Network Options Compatibility**: Filter now checks for both `{ipv4_ipv6: {...}, ipv6_only: {...}}` object format and legacy string format
- **Regional Pricing Logic**: Uses `regionalPricing` array to determine availability and pricing for specific regions
- **Debug Cleanup**: Removed extensive debug logging while maintaining core functionality
- **User Experience**: Added space in "Last Updated: " display and pricing accuracy notice

This architecture provides a solid foundation for a scalable, maintainable cloud cost comparison platform with room for growth and additional providers.