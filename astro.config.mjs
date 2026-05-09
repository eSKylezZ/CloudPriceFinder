import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

const SITE_URL = 'https://cloudpricefinder.com';

// Cloudflare Rocket Loader rewrites <script type="module"> tags, causing a
// credentials mode mismatch with Astro's <link rel="modulepreload" crossorigin>
// and wasting every preload. data-cfasync="false" opts the script out of
// Rocket Loader processing entirely.
function cfRocketLoaderBypass() {
  return {
    name: 'cf-rocket-loader-bypass',
    transformIndexHtml(html) {
      return html.replace(/<script type="module"/g, '<script type="module" data-cfasync="false"');
    },
  };
}

export default defineConfig({
  site: SITE_URL,
  integrations: [sitemap()],
  output: 'static',
  build: {
    inlineStylesheets: 'auto',
  },
  vite: {
    plugins: [tailwindcss(), cfRocketLoaderBypass()],
  },
});
