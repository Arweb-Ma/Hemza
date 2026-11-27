// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

// Fully static output: every page is prerendered at build time and served as
// static assets by Cloudflare Workers (see wrangler.jsonc). No adapter needed.
export default defineConfig({
  site: 'https://reeklam.ma',
  trailingSlash: 'always',
  integrations: [sitemap()],
});
