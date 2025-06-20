import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: process.env.SITE_URL || 'https://cloudpricefinder.com/',
  integrations: [
    tailwind(),
    sitemap()
  ],
  output: 'static',
  build: {
    inlineStylesheets: 'auto'
  },
  vite: {
    optimizeDeps: {
      include: ['./data/*.json']
    }
  }
});