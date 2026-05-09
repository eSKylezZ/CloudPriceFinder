import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';
import sitemap from '@astrojs/sitemap';

// Production URL for cloudpricefinder.com (Stage 9)
const SITE_URL = 'https://cloudpricefinder.com';

export default defineConfig({
  site: SITE_URL,
  integrations: [
    tailwind(),
    sitemap()
  ],
  output: 'static',
  build: {
    inlineStylesheets: 'auto'
  },
  vite: {}
});
