# Contributing to CloudPriceFinder

Thank you for your interest in contributing to CloudPriceFinder! This guide will help you get started with contributing to our multi-cloud cost comparison platform.

## 🎯 Ways to Contribute

### High Priority Contributions
1. **Add Cloud Providers** - Implement data fetchers for AWS, Azure, GCP, OCI, or OVH
2. **Enhance Frontend Features** - Improve filtering, sorting, and comparison capabilities
3. **Data Export Features** - Add CSV/JSON export functionality
4. **Bug Fixes** - Fix reported issues and improve stability

### Medium Priority Contributions
1. **Price Alerts** - Notification system for price changes
2. **Historical Data** - Price trend tracking and charts
3. **Advanced Search** - Semantic search capabilities
4. **Performance Optimization** - Improve loading times and responsiveness

## 🏗️ Development Setup

### Prerequisites
- Node.js 18+
- Python 3.8+
- Git

### Local Development Setup
```bash
# Fork and clone the repository
git clone https://github.com/eSKylezZ/CloudPriceFinder.git
cd CloudPriceFinder

# Install dependencies
npm install
pip install -r requirements.txt

# Set up environment variables (optional - for Hetzner data)
export HETZNER_API_TOKEN="your-hetzner-api-token"

# Start development
npm run fetch-data
npm run dev
```

## 🌩️ Adding a New Cloud Provider

### Step 1: Create Provider Script
1. Copy `scripts/fetch_hetzner_v2.py` as a template
2. Rename it to `scripts/fetch_[provider].py`
3. Update the class name and provider-specific logic

### Step 2: Implement Data Fetching
1. Research the provider's pricing API or documentation
2. Implement authentication (prefer read-only API keys)
3. Fetch instance types, specifications, and pricing
4. Convert data to the standardized CloudInstance format

### Step 3: Data Normalization
Ensure your data matches the CloudInstance interface:
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

### Step 4: Integration
1. Add your provider to `scripts/orchestrator.py`
2. Update the provider enum in `src/types/cloud.ts`
3. Add provider badge colors in `src/components/ComparisonTable.astro`

### Step 5: Testing
1. Test data collection: `python scripts/fetch_[provider].py`
2. Validate data structure: Check generated JSON files
3. Test frontend integration: `npm run dev`
4. Verify filtering and sorting work correctly

## 🎨 Frontend Development

### Component Guidelines
1. **Use Astro Components** - Prefer `.astro` files for UI components
2. **TypeScript First** - Use strict typing with defined interfaces
3. **Tailwind CSS** - Use utility classes for consistent styling
4. **Accessibility** - Include ARIA labels and keyboard navigation
5. **Responsive Design** - Mobile-first approach

### State Management
- Use Astro's built-in state management
- Minimize client-side JavaScript
- Prefer server-side rendering when possible

### Performance
- Optimize for static site generation
- Use efficient filtering and sorting algorithms
- Minimize bundle size

## 📊 Data Standards

### Pricing Normalization
- Convert all prices to USD (preserve original in `originalPrice`)
- Use consistent units (hourly/monthly pricing)
- Handle regional pricing variations

### Data Validation
- All data must pass validation in `scripts/utils/data_validator.py`
- Required fields must be populated
- Numeric values must be within reasonable ranges

### Error Handling
- Log errors but don't crash the pipeline
- Provide meaningful error messages
- Gracefully handle API failures

## 🧪 Testing

### Running Tests
```bash
# TypeScript type checking
npm run type-check

# Linting
npm run lint

# Format code
npm run format

# Data collection testing
python scripts/fetch_[provider].py --test
```

### Test Requirements
- All new features should include tests
- Test both success and failure scenarios
- Validate data structure and content

## 📝 Documentation

### Required Documentation
1. **Update README.md** - Add your provider to the status table
2. **API Setup Guide** - Document how to get API credentials
3. **Code Comments** - Document complex logic and data transformations
4. **Type Definitions** - Update TypeScript interfaces if needed

## 🔒 Security Guidelines

### API Credentials
- Use read-only API access whenever possible
- Document required permissions clearly
- Never commit API keys or secrets
- Use environment variables for credentials

### Data Privacy
- Don't collect or store sensitive information
- Respect rate limits and provider terms of service
- Implement proper error handling for API failures

## 📋 Pull Request Process

### Before Submitting
1. **Test Thoroughly** - Ensure your changes work as expected
2. **Update Documentation** - Include relevant documentation updates
3. **Follow Code Style** - Run linting and formatting tools
4. **Write Clear Commit Messages** - Describe what and why, not just how

### PR Description Template
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Tested locally
- [ ] Added/updated tests
- [ ] Updated documentation

## Provider (if applicable)
- Provider: [AWS/Azure/GCP/etc.]
- Data source: [API/scraping/etc.]
- Coverage: [instance types covered]
```

### Review Process
1. Automated checks must pass
2. Code review by maintainers
3. Testing of new provider integrations
4. Documentation review

## 🐛 Bug Reports

### Before Reporting
1. Check existing issues
2. Test with latest version
3. Gather relevant information

### Bug Report Template
```markdown
**Describe the bug**
Clear description of the issue

**To Reproduce**
Steps to reproduce the behavior

**Expected behavior**
What you expected to happen

**Screenshots/Logs**
Relevant error messages or screenshots

**Environment**
- OS: [e.g., Windows/macOS/Linux]
- Browser: [e.g., Chrome/Firefox/Safari]
- Version: [e.g., v2.0.1]
```

## 💡 Feature Requests

### Feature Request Template
```markdown
**Feature Description**
Clear description of the proposed feature

**Use Case**
Why would this feature be useful?

**Proposed Implementation**
How do you think this should work?

**Additional Context**
Any other context or screenshots
```
---

Thank you for contributing to CloudPriceFinder! Your efforts help make cloud cost comparison accessible to everyone.