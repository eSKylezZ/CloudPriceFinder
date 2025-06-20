# CloudPriceFinder v2.0

> Multi-cloud instance cost comparison tool with automated data collection

CloudPriceFinder is an open-source application that helps you compare cloud instance costs and specifications across multiple providers. Built with Astro + TypeScript frontend and Python data collection, featuring automated GitHub Actions workflows for zero-maintenance operation.

## 🌟 Features

- 🌩️ **Multi-Cloud Support** - Compare instances across major cloud providers
- 💰 **Real-Time Pricing** - Automated data collection from provider APIs
- 🔍 **Advanced Filtering** - Filter by CPU, memory, price, provider, regions, and instance types with regional pricing support
- 📊 **Interactive Comparison** - Sort and compare instances side-by-side with region-specific pricing
- 📱 **Responsive Design** - Works perfectly on desktop and mobile
- 🔒 **Privacy First** - No tracking, no analytics, no data collection
- ⚡ **Fast Performance** - Static site generation for optimal loading speed
- 🤖 **Fully Automated** - GitHub Actions handle data collection and deployment

## 🚀 Quick Start (10 minutes)

### Automated Setup with GitHub Actions + Cloudflare Pages

1. **Fork this repository**
2. **Connect to Cloudflare Pages:**
   - Go to [Cloudflare Pages](https://pages.cloudflare.com/)
   - Connect your GitHub account and select the forked repository
   - Set build command: `npm run build`
   - Set output directory: `dist`
3. **Set up API credentials** following our [API Setup Guide](./docs/API_SETUP.md)
4. **Configure GitHub Secrets:**
   - `HETZNER_API_TOKEN` - Your Hetzner Cloud API token (required)
   - `HETZNER_ROBOT_USER` - Robot API username (optional, for dedicated servers)
   - `HETZNER_ROBOT_PASSWORD` - Robot API password (optional, for dedicated servers)
5. **Enable GitHub Actions** in your repository settings
6. **Enjoy automated data collection** every week!

See our [GitHub Actions Setup Guide](./docs/GITHUB_ACTIONS_SETUP.md) for detailed instructions.

### Local Development

#### Prerequisites
- Node.js 18+ 
- Python 3.8+
- Git

#### Installation
```bash
# Clone the repository
git clone https://github.com/eSKylezZ/CloudPriceFinder.git
cd CloudPriceFinder

# Install Node.js dependencies
npm install

# Install Python dependencies
pip install -r requirements.txt

# Set up environment variables (optional - for Hetzner data)
export HETZNER_API_TOKEN="your-hetzner-api-token"

# Fetch data and start development server
npm run fetch-data
npm run dev
```

Visit `http://localhost:4321` to see the application.

> **Note**: For local development, you may want to ignore `data/` and `dist/` directories locally while keeping them in the repository for Cloudflare Pages. Copy `.gitignore.local` to `.git/info/exclude` to do this.

## 📋 Development Commands

| Command | Description |
|---------|-------------|
| `npm run dev` | Start development server |
| `npm run build` | Build for production (includes data fetching) |
| `npm run fetch-data` | Fetch cloud pricing data |
| `npm run preview` | Preview production build |
| `npm run type-check` | Run TypeScript checks |
| `npm run lint` | Lint code with ESLint |
| `npm run format` | Format code with Prettier |

## 🤖 Automated Data Collection

CloudPriceFinder includes GitHub Actions workflows for fully automated data collection and deployment:

### 🔄 Automated Workflows

- **Data Collection & Deployment**: Runs weekly on Sunday, collects fresh pricing data and deploys to Cloudflare Pages
- **Data Collection Only**: Runs weekly on Wednesday, lightweight data updates without full deployment  
- **Manual Triggers**: Run workflows on-demand with custom options

### 📊 What Gets Automated

- ✅ **Data Collection**: Fetch latest pricing from cloud provider APIs
- ✅ **Data Validation**: Ensure data quality and consistency  
- ✅ **Site Building**: Generate static site with fresh data
- ✅ **Deployment**: Auto-deploy to Cloudflare Pages
- ✅ **Monitoring**: Detailed reports and error notifications

### 🚀 Zero-Maintenance Operation

Once configured, CloudPriceFinder runs completely automatically:
- No manual data updates needed
- Always shows current pricing
- Handles API errors gracefully
- Provides detailed status reports
- Weekly schedule to minimize API usage costs

## 🏢 Provider Status

| Provider | Status | Data Source | Implementation |
|----------|--------|-------------|----------------|
| **Hetzner** | ✅ **Complete** | Cloud API + Robot API | Full integration with cloud services and dedicated servers |
| **AWS** | 📋 **Ready** | Placeholder | Script structure ready for implementation |
| **Azure** | 📋 **Ready** | Placeholder | Script structure ready for implementation |
| **GCP** | 📋 **Ready** | Placeholder | Script structure ready for implementation |
| **OCI** | 📋 **Ready** | Placeholder | Script structure ready for implementation |
| **OVH** | 📋 **Ready** | Placeholder | Script structure ready for implementation |

### 🌩️ Hetzner Integration (Complete)

CloudPriceFinder includes comprehensive Hetzner integration supporting:

- **Cloud Services**: Servers, load balancers, volumes, floating IPs, networks
- **Dedicated Servers**: Full Robot API integration with server types and pricing
- **Regional Pricing**: Location-specific pricing with filtering and comparison
- **Platform Indicators**: Clear distinction between cloud and dedicated offerings
- **Enhanced Metadata**: Service categories, platform types, and detailed specifications
- **Currency Conversion**: EUR to USD with original prices preserved
- **Advanced Filtering**: Network options, instance type grouping, and region-specific availability

## 🏗️ Architecture

CloudPriceFinder uses a modern hybrid architecture:

- **Frontend**: Astro v4 static site generator with TypeScript and Tailwind CSS
- **Data Collection**: Python scripts with orchestrator pattern for provider APIs
- **Data Processing**: Normalization, validation, and currency conversion
- **Automation**: GitHub Actions for scheduled data collection and deployment
- **Deployment**: Static files deployed to Cloudflare Pages for global edge delivery

## 🤝 Contributing

We welcome contributions! Here's how you can help:

### High Priority
1. **Add Cloud Providers**: Implement data fetchers for AWS, Azure, GCP, OCI, or OVH
2. **Enhance Frontend**: Improve filtering, sorting, and comparison features
3. **Data Export**: Add CSV/JSON export capabilities

### Medium Priority
1. **Price Alerts**: Notification system for price changes
2. **Historical Data**: Price trend tracking and charts
3. **Advanced Search**: Semantic search capabilities

### Adding a New Provider

1. Copy `scripts/fetch_hetzner_v2.py` as a template
2. Implement the provider's API integration following the same pattern
3. Add the provider to `scripts/orchestrator.py`
4. Test the integration and data output
5. Submit a pull request with documentation

See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed guidelines.

## 🔧 Environment Variables

### Required for Hetzner (Currently Supported)

- `HETZNER_API_TOKEN` - Hetzner Cloud API token (get from console.hetzner.cloud)
- `HETZNER_ROBOT_USER` - Robot API username (optional, for dedicated servers)
- `HETZNER_ROBOT_PASSWORD` - Robot API password (optional, for dedicated servers)


### Future Provider Variables

- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` - AWS credentials
- `AZURE_SUBSCRIPTION_ID` + `AZURE_CLIENT_ID` - Azure credentials  
- `GOOGLE_CLOUD_PROJECT` + service account - GCP credentials
- `OCI_CONFIG_PROFILE` - OCI configuration
- `OVH_ENDPOINT` + `OVH_APPLICATION_KEY` - OVH credentials

## 📊 Data Standards

All cloud provider data is normalized to a consistent format:

- **Pricing**: Converted to USD with original currency preserved
- **Specifications**: Standardized CPU, memory, and storage units
- **Regions**: Normalized region names and availability zones
- **Types**: Categorized as cloud-server, dedicated-server, load-balancer, volume, etc.
- **Platform**: Clear distinction between cloud and dedicated infrastructure

## 🚀 Deployment Options

CloudPriceFinder generates a static site that can be deployed to:

- **Cloudflare Pages** - Global edge deployment (recommended)
- **Netlify** - Deploy directly from GitHub
- **Vercel** - Zero-config deployment
- **GitHub Pages** - Free static hosting
- **Any Static Host** - Upload the `dist/` folder

### Production Deployment

For production use with automated data collection:

1. **Fork this repository**
2. **Set up Cloudflare Pages** connection to your GitHub repository
3. **Configure GitHub Secrets** with your API tokens
4. **Enable GitHub Actions** workflows
5. **Customize the schedule** in workflow files if needed

The system will automatically:
- Collect fresh pricing data weekly
- Build and deploy the updated site
- Handle errors gracefully with detailed reporting

## 📚 Documentation

- 📖 [API Setup Guide](./docs/API_SETUP.md) - Setting up read-only API credentials
- 🤖 [GitHub Actions Setup](./docs/GITHUB_ACTIONS_SETUP.md) - Automation configuration
- 🏗️ [Development Guide](./CLAUDE.md) - Complete architecture and development workflow
- 📊 [Project Status](./PROJECT_STATUS.md) - Current implementation status and roadmap

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🆘 Support

- 🐛 [Issues](https://github.com/eSKylezZ/CloudPriceFinder/issues) - Bug reports and feature requests
- 💬 [Discussions](https://github.com/eSKylezZ/CloudPriceFinder/discussions) - Community support
- 📧 [Contact](https://github.com/eSKylezZ/CloudPriceFinder) - Project repository

---

**CloudPriceFinder v2.0** - Making cloud cost comparison simple, automated, and transparent.