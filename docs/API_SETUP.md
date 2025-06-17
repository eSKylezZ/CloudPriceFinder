# API Setup Guide

This guide explains how to create read-only API keys for CloudCosts data collection.

## 🌩️ Hetzner Cloud API (Read-Only)

### Step 1: Access Hetzner Cloud Console
1. Go to [Hetzner Cloud Console](https://console.hetzner.cloud)
2. Log in to your Hetzner account
3. Select your project (or create a new one for CloudCosts)

### Step 2: Create API Token
1. Navigate to **Security** → **API Tokens** in the left sidebar
2. Click **Generate API Token**
3. Configure the token:
   - **Description**: `CloudCosts Data Collection (Read-Only)`
   - **Permissions**: **Read** (this is the most restrictive option)
   - **Expiry**: Choose based on your security policy (1 year recommended)
4. Click **Generate Token**
5. **⚠️ Important**: Copy the token immediately - it won't be shown again!

### Step 3: Verify Token Permissions
The read-only token can access:
- ✅ Server types and pricing
- ✅ Load balancer types and pricing  
- ✅ Volume pricing
- ✅ Floating IP pricing
- ✅ Network pricing
- ❌ Cannot create, modify, or delete resources
- ❌ Cannot access sensitive server information

### Security Notes
- Read-only tokens cannot make any changes to your infrastructure
- Tokens can be revoked at any time from the console
- Consider using a dedicated project for CloudCosts to isolate access

---

## 🖥️ Hetzner Robot API (Read-Only)

### Step 1: Access Robot Interface
1. Go to [Hetzner Robot](https://robot.hetzner.com)
2. Log in with your Hetzner account credentials

### Step 2: Create Robot API Credentials
1. Navigate to **Settings** → **Robot** → **API**
2. If you don't have API access enabled:
   - Click **Enable Robot API**
   - Set a username and password
   - **Note**: These are separate from your main account credentials

### Step 3: Configure Read-Only Access
The Robot API doesn't have granular permission controls, but we only use read-only endpoints:
- ✅ `/order/server/product` - Available server configurations
- ✅ Server specifications and pricing
- ❌ Our script never calls modification endpoints

### Step 4: Limit Access (Recommended)
For additional security:
1. Create a dedicated sub-account for API access
2. Grant minimal permissions to that account
3. Use those credentials for the Robot API

### Security Notes
- Robot API credentials have broader access than Cloud API tokens
- Consider IP restrictions if available in your account settings
- Monitor API usage in the Robot interface
- Rotate credentials periodically

---

## 🔐 GitHub Secrets Configuration

### Required Secrets

Add these secrets to your GitHub repository:

#### For Hetzner Cloud (Required)
```
HETZNER_API_TOKEN = your_cloud_api_token_here
```

#### For Hetzner Dedicated Servers (Optional)
```
HETZNER_ROBOT_USER = your_robot_username
HETZNER_ROBOT_PASSWORD = your_robot_password  
```

### Setting Up GitHub Secrets

1. Go to your GitHub repository
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add each secret with the exact names shown above
5. The values should be the API credentials from Hetzner

### Testing Configuration

You can test your setup by running the workflow manually:
1. Go to **Actions** tab in your repository
2. Select **Data Collection Only** workflow
3. Click **Run workflow**
4. Check the logs to verify successful data collection

---

## 🛡️ Security Best Practices

### API Token Security
- ✅ Use read-only permissions whenever possible
- ✅ Set reasonable expiration dates
- ✅ Monitor token usage in provider consoles
- ✅ Use GitHub's encrypted secrets (never commit tokens to code)
- ✅ Rotate tokens periodically

### Access Monitoring
- Review API usage logs regularly
- Set up alerts for unusual API activity
- Monitor GitHub Actions logs for failed authentication

### Incident Response
If a token is compromised:
1. **Immediately revoke** the token in the provider console
2. Generate a new token with fresh credentials
3. Update GitHub secrets with the new token
4. Review recent API usage for suspicious activity

---

## 🚀 Alternative Providers Setup

### AWS (Future)
- Use IAM with read-only policies
- Recommended policy: `ReadOnlyAccess` or custom pricing-only policy
- Use IAM Access Keys (not root credentials)

### Azure (Future)  
- Create Service Principal with Reader role
- Scope to specific resource groups if desired
- Use Application ID and Secret

### Google Cloud (Future)
- Create Service Account with Viewer role
- Generate and download JSON key file
- Store key content in GitHub secret

### Oracle Cloud (Future)
- Use read-only user with minimal permissions
- Generate API key pair
- Configure OCI CLI profile format

---

## 📞 Support

If you encounter issues:
1. Check the GitHub Actions logs for specific error messages
2. Verify your API credentials in the provider consoles
3. Test API access manually using curl or provider CLI tools
4. Review this documentation for setup steps

For CloudCosts-specific issues, please open an issue in the GitHub repository.