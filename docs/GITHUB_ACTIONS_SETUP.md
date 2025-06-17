# GitHub Actions Setup Guide

This guide walks you through setting up automated cloud pricing data collection with GitHub Actions.

## 🚀 Quick Start

### 1. Fork or Clone Repository
```bash
git clone https://github.com/your-username/cloudcosts.git
cd cloudcosts
```

### 2. Set Up API Credentials
Follow the [API Setup Guide](./API_SETUP.md) to create read-only API keys for Hetzner.

### 3. Configure GitHub Secrets
Go to **Settings** → **Secrets and variables** → **Actions** and add:

**Required:**
- `HETZNER_API_TOKEN` - Your Hetzner Cloud API token

**Optional (for dedicated servers):**
- `HETZNER_ROBOT_USER` - Your Robot API username  
- `HETZNER_ROBOT_PASSWORD` - Your Robot API password

### 4. Enable GitHub Actions
1. Go to **Actions** tab in your repository
2. Click **I understand my workflows, go ahead and enable them**
3. The workflows will now run automatically

### 5. Enable GitHub Pages (Optional)
For automatic deployment:
1. Go to **Settings** → **Pages**
2. Source: **GitHub Actions**
3. Your site will be available at `https://username.github.io/cloudcosts`

## 📋 Available Workflows

### 1. Data Collection & Deployment (`data-collection.yml`)
**Purpose:** Complete pipeline with data collection, validation, and deployment

**Triggers:**
- Every 6 hours (automatic)
- Manual trigger with options
- Pushes to main branch

**Features:**
- ✅ Collects data from all enabled providers
- ✅ Validates data structure and content
- ✅ Builds static site with Astro
- ✅ Deploys to GitHub Pages
- ✅ Generates detailed build reports
- ✅ Uploads artifacts for debugging

**Manual Trigger Options:**
```yaml
enable_dedicated: true/false    # Include Hetzner dedicated servers
enable_auction: true/false      # Include auction servers
```

### 2. Data Collection Only (`data-only.yml`)
**Purpose:** Lightweight data collection without deployment

**Triggers:**
- Every 3 hours (automatic)
- Manual trigger

**Features:**
- ✅ Fast data collection
- ✅ Commits updated data to repository
- ✅ Minimal resource usage
- ✅ Perfect for data-only updates

## 🔧 Workflow Configuration

### Environment Variables
Both workflows support these environment variables:

```yaml
env:
  HETZNER_ENABLE_CLOUD: true        # Enable Hetzner Cloud API
  HETZNER_ENABLE_DEDICATED: false   # Enable Hetzner Robot API  
  HETZNER_ENABLE_AUCTION: false     # Enable auction server scraping
```

### Scheduling
You can modify the schedule in the workflow files:

```yaml
# Every 6 hours
- cron: '0 */6 * * *'

# Daily at 6 AM UTC  
- cron: '0 6 * * *'

# Every Monday at 9 AM UTC
- cron: '0 9 * * 1'
```

### Resource Usage
**Data Collection Only:**
- Runtime: ~2-3 minutes
- Resources: Minimal
- Data transfer: ~1-5 MB

**Full Pipeline:**
- Runtime: ~5-10 minutes  
- Resources: Medium
- Includes site building and deployment

## 🔍 Monitoring & Debugging

### Build Status
Check the **Actions** tab for:
- ✅ Successful runs (green checkmark)
- ❌ Failed runs (red X)
- 🟡 In-progress runs (yellow circle)

### Build Reports
Each successful run generates a detailed summary:
- Total instances collected
- Breakdown by provider and service type
- Collection errors and warnings
- Build information

### Logs and Artifacts
For debugging:
1. Click on any workflow run
2. Click on job name (e.g., "collect-data")  
3. Expand steps to see detailed logs
4. Download artifacts for offline analysis

### Common Issues

**❌ "Cloud API token not provided"**
- Check that `HETZNER_API_TOKEN` secret is set
- Verify token hasn't expired in Hetzner console

**❌ "No data was collected"**
- Check API token permissions
- Verify token is read-only (not expired/revoked)
- Check Hetzner service status

**❌ "Build failed"**
- Check for data validation errors
- Verify all required Node.js dependencies
- Review TypeScript compilation errors

## 📊 Data Management

### Data Storage
- Raw data: `data/all_instances.json`
- Summary: `data/summary.json`  
- Built site: `dist/` (after build)

### Data Validation
The workflow automatically validates:
- JSON structure integrity
- Required field presence
- Data type validation
- Provider-specific validation

### Backup Strategy
- Artifacts retained for 30 days
- Consider setting up external backup
- Git history provides version control

## 🚀 Deployment Options

### GitHub Pages (Recommended)
**Pros:**
- Free hosting
- Automatic SSL
- CDN distribution
- Easy setup

**Setup:**
1. Enable in repository settings
2. Workflow handles deployment automatically
3. Site available at `username.github.io/cloudcosts`

### Custom Domain
Add to workflow:
```yaml
- name: 🚀 Deploy to GitHub Pages
  uses: peaceiris/actions-gh-pages@v3
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    publish_dir: ./dist
    cname: your-domain.com
```

### Alternative Deployments
The workflow can be modified for:
- **Netlify:** Use `netlify/actions/deploy`
- **Vercel:** Use `amondnet/vercel-action`
- **AWS S3:** Use `aws-actions/configure-aws-credentials`
- **Cloudflare Pages:** Use direct API deployment

## 🔒 Security Considerations

### Secret Management
- Never commit API keys to code
- Use repository secrets for sensitive data
- Consider organization-level secrets for multiple repos
- Rotate secrets periodically

### Workflow Security
- Use pinned action versions (e.g., `@v4`, not `@main`)
- Review third-party actions before use
- Limit workflow permissions when possible
- Monitor workflow runs for suspicious activity

### API Security
- Use read-only API keys whenever possible
- Monitor API usage in provider consoles
- Set up alerts for unusual activity
- Have incident response plan for compromised keys

## 📈 Optimization Tips

### Performance
- Use Node.js and Python caching
- Minimize data collection frequency if needed
- Use conditional deployment (only on changes)
- Optimize Python dependencies

### Cost Management
- GitHub Actions minutes are free for public repos
- Monitor usage in private repos
- Consider data-only workflow for frequent updates
- Use efficient scheduling

### Reliability
- Set up monitoring alerts
- Have backup plans for API outages
- Use error handling and retries
- Document troubleshooting procedures

## 🛠️ Customization

### Adding New Providers
1. Create provider script in `scripts/`
2. Add to orchestrator imports
3. Update workflow environment variables
4. Add provider secrets to GitHub

### Modifying Collection Frequency
Edit the cron schedule:
```yaml
schedule:
  - cron: '0 */6 * * *'  # Every 6 hours
```

### Custom Notifications
Add notification steps:
```yaml
- name: 📬 Notify on failure
  if: failure()
  uses: 8398a7/action-slack@v3
  with:
    status: failure
    webhook_url: ${{ secrets.SLACK_WEBHOOK }}
```

## 📞 Support

For help with GitHub Actions setup:
1. Check workflow logs for specific errors
2. Review this documentation
3. Test workflows manually before automation
4. Open an issue with logs and configuration details

Happy automating! 🎉