# Hemza web

The public site: a static [Astro](https://astro.build) project in TypeScript. Every page is
prerendered at build time from the `deals` view in Supabase, so pages are plain HTML, fast, and
readable by search engines. The only client-side script is the filter bar.

## Commands

| Command           | What it does                                  |
| ----------------- | --------------------------------------------- |
| `npm install`     | Install dependencies                          |
| `npm run dev`     | Local dev server at `localhost:4321`          |
| `npm run build`   | Build the site into `dist/`                   |
| `npm run preview` | Serve the built site locally                  |
| `npm run check`   | Type-check `.astro` and `.ts` files           |
| `npm test`        | Unit tests (Vitest)                           |
| `npm run format`  | Format with Prettier                          |
| `npm run deploy`  | Build, then deploy to Cloudflare (`wrangler`) |

## Data

Copy `.env.example` to `.env`. Both values are public and read-only. They are read **at build
time**: if they are missing, the database is unreachable, or it has no deals yet, the build falls
back to demo data, shows a "Prototype" banner, and marks pages `noindex`.

Because pages are static, the site must be rebuilt after each price collection (for example with a
Cloudflare deploy hook called at the end of the collector run).

## Structure

```
src/
  config/site.ts        Site name, sections (femme/homme/enfant), category labels
  lib/                  Pure TypeScript, no DOM
    types.ts            Deal and feed types
    data.ts             Loads the feed once per build (live, else demo)
    supabase.ts         Reads the `deals` view and price history; validates rows
    demo.ts             Deterministic sample data
    deals.ts            Scoring, section/category filtering
    format.ts           Dirham and time formatting
    sparkline.ts        Price-chart geometry
  components/           Astro components with scoped styles
  layouts/BaseLayout    <head>, SEO tags, header, footer
  pages/
    index.astro                 /
    [gender]/index.astro        /femme/  /homme/  /enfant/
    [gender]/[category].astro   /femme/robes/ ...
    promo/[id].astro            /promo/123/  (one page per deal)
    comment-ca-marche.astro
    404.astro
  scripts/filters.ts    Search, max price, sort (progressive enhancement)
  styles/               Design tokens and global styles
tests/                  Vitest unit tests for lib/
```

## Deploying

`wrangler.jsonc` serves `dist/` as static assets on Cloudflare Workers, with `404.html` for unknown
URLs. Run `npm run deploy`, or connect the repo in Cloudflare (Workers Builds) with root directory
`web`, build command `npm run build`, deploy command `npx wrangler deploy`, and the two variables
from `.env.example`.
