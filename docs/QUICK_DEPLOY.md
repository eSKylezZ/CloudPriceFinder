# 🚀 Quick Deploy Guide

Get CloudCosts running with automated data collection in under 10 minutes!

## ⚡ Super Quick Setup

### 1. Fork Repository (30 seconds)
- Go to the CloudCosts repository
- Click **Fork** in the top right
- Choose your account/organization

### 2. Get Hetzner API Token (2 minutes)
1. Visit [Hetzner Cloud Console](https://console.hetzner.cloud)
2. Go to **Security** → **API Tokens**
3. Click **Generate API Token**
4. Set description: `CloudCosts Data Collection`
5. Permission: **Read** (read-only)
6. Copy the token (you'll need it next!)

### 3. Configure GitHub Secrets (1 minute)
1. In your forked repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `HETZNER_API_TOKEN`
4. Value: Paste your token from step 2
5. Click **Add secret**

### 4. Enable GitHub Actions (30 seconds)
1. Go to **Actions** tab in your repository
2. Click **I understand my workflows, go ahead and enable them**
3. Done! Data collection will start automatically

### 5. Enable GitHub Pages (1 minute)
1. Go to **Settings** → **Pages**
2. Source: **GitHub Actions**
3. Your site will be live at `https://yourusername.github.io/cloudcosts`

## ✅ That's It!

Your CloudCosts instance is now:
- 🔄 **Auto-collecting** pricing data every 6 hours
- 🌐 **Auto-deploying** to GitHub Pages
- 📊 **Validating** data quality automatically
- 📈 **Generating** detailed reports

## 🔍 Verify It's Working

### Check Data Collection (5 minutes after setup)
1. Go to **Actions** tab
2. Look for running/completed workflows
3. Click on latest run to see logs
4. Should show "✅ Collected X instances"

### Check Your Site (10 minutes after setup)
1. Visit `https://yourusername.github.io/cloudcosts`
2. Should show Hetzner pricing data
3. Use filters and sorting to explore

## 🔧 Optional: Add Dedicated Servers

Want Hetzner dedicated server pricing too?

### Get Robot API Credentials
1. Visit [Hetzner Robot](https://robot.hetzner.com)
2. Go to **Settings** → **Robot** → **API**
3. Enable API and set username/password

### Add to GitHub Secrets
```
HETZNER_ROBOT_USER = your_robot_username
HETZNER_ROBOT_PASSWORD = your_robot_password
```

### Enable in Workflow
1. Go to **Actions** → **Data Collection & Deployment**
2. Click **Run workflow**
3. Enable "Enable Hetzner dedicated server collection"
4. Run workflow

## 📋 Troubleshooting

**No data showing?**
- Check Actions logs for errors
- Verify API token is correct
- Wait 10-15 minutes for first run

**Site not deploying?**
- Ensure GitHub Pages is enabled
- Check if Actions have deploy permissions
- Look for deployment errors in Actions logs

**API errors?**
- Verify token hasn't expired
- Check token has read permissions
- Try regenerating token

## 🎯 Next Steps

Once running successfully:

### Monitor Your Instance
- **Actions tab**: Monitor data collection runs
- **Issues tab**: Report problems or request features
- **Settings**: Adjust workflow schedules

### Customize
- Edit `.github/workflows/` to change collection frequency
- Modify `src/` files to customize the UI
- Add more cloud providers as we implement them

### Share
- Share your CloudCosts URL with team members
- Embed pricing data in other tools
- Use as reference for cloud cost decisions

## 🆘 Need Help?

1. **Check the logs**: Actions tab → latest run → job details
2. **Read the guides**: 
   - [API Setup Guide](./API_SETUP.md)
   - [GitHub Actions Setup](./GITHUB_ACTIONS_SETUP.md)
3. **Open an issue**: Include error logs and your configuration

---

**🎉 Congratulations!** You now have a fully automated cloud cost comparison tool that keeps itself updated! No more manual price checking across multiple provider websites.