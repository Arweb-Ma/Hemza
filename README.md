# Hemza.ma — vraies promos mode au Maroc

## Structure
- `db/schema.sql` — tables (stores, products, price_history, posts) + views `deal_candidates` and `deals` (scoring, fake-promo filter). Tested on Postgres 16.
- `collector/collect.py` — the collector: paced queues, saving to Supabase. `collector/platforms.py` holds one catalog reader per platform (Shopify, WooCommerce, PrestaShop experimental, Magento GraphQL, Zara, LC Waikiki, Decathlon); `collector/classify.py` sorts items into femme/homme/enfant + category (FR/EN/Darija-Arabic).
- `collector/stores.json` — the stores to read. Each entry: `name`, `domain`, `tier` (`marque` = big brand, shown on the main pages and posted to Telegram; `boutique` = small shop, shown in the separate "Boutiques" section), optional `platform` (`shopify` by default, `woocommerce`, `prestashop`, `magento`, `zara`, `lcwaikiki`, `decathlon`), `gender_hint`, `category_hint`, and for PrestaShop `paths` (listing URLs to read). Brands that could not be read from a server yet are listed in the file's `_not_readable_yet` note. Find a shop's platform with `python collector/collect.py --detect shop.ma`.
- `collector/browser.py` — reads shops that refuse plain HTTP clients with a headless Chromium (Playwright), at the same slow pace. Used by stores marked `"via": "browser"` (Zara: its usual JSON, asked for from inside a zara.com/ma tab; `home_url` is the page opened first) and by the `decathlon` reader: it reads the "Promotions" listing (`/5080-promotions?page=N`, allowed by robots.txt), first as PrestaShop JSON, else from the rendered product cards; non-clothing items are dropped (`skip_other`). No CAPTCHA solving: a challenge page means the store is skipped for that run. Install with `pip install -r collector/requirements-browser.txt && python -m playwright install chromium` On a home PC you can set `HEMZA_CHROME=chrome` (your installed Google Chrome), `HEMZA_HEADED=1` (visible window) and `HEMZA_PROFILE=<folder>` (keeps the browser's cookies between runs). When a shop refuses, the path of a screenshot of what the browser saw is printed.
- `collector/post_deals.py` — posts top deals to a Telegram channel; `--review` prints them for manual approval.
- `collector/run.py` — one full cycle for a scheduler: collect, then post new verified deals, then (optionally) ask the site to rebuild. Each step runs only if its settings are present.
- `.github/workflows/collect.yml` — collects the brands every two hours and the small shops once a day (`run.py --tier …`), then starts a site rebuild. Needs the `SUPABASE_SERVICE_KEY` repository secret (plus `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHANNEL` to post).
- `.github/workflows/detect.yml` — run by hand (Actions → "Detect a shop's platform") to see whether new shops can be read from a server.
- `.github/workflows/deploy.yml` — rebuilds and deploys the site after every collection (started by `collect.yml`), on changes to `web/`, or on demand. Needs the `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` repository secrets; without them it only builds.
- `web/` — the website (Astro + TypeScript, static, deployed to Cloudflare). See `web/README.md`.

## Go-live
1. Supabase: project `hemza` (ref `tcsjfqkreqceektalogu`, eu-west-1) already has `db/schema.sql` applied.
2. Collector: `pip install requests`, set `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`, run `python collector/collect.py --dry-run`, then for real. It is slow on purpose (about one request a minute, slower when stores refuse) and saves page by page. Run it once or twice a day from a home connection; cloud servers are refused by Shopify. Tests: `python collector/test_collect.py`.
3. Site: `cd web && cp .env.example .env && npm install && npm run deploy`. It shows demo data until the `deals` view returns rows; rebuild after each collection. Real product images load from the stores' CDNs.
4. Telegram: create the channel and a bot via @BotFather, add the bot as channel admin, then run `post_deals.py --review` and, once you're happy with it, without `--review`.

## Scoring
- Reference price = median of the last 30 days (excluding the last 24 h).
- Verified = at least 5 price points of our own. Published if the real drop is ≥ 20 %. Not verified yet: published (labelled "annoncée par la boutique", ranked last, not posted to Telegram) only if the store's advertised discount is ≥ 30 % and no more than 40 % of that store's catalog is "on sale".
- Fake promo = store claims ≥ 30 % off, but the price is not lower than its 30-day median → hidden.
- Score = real % × 1.6 + 12 if lowest price in 90 days (capped at 100).

Note: deals need ~1 week of collected history before the "real discount" check is meaningful; until then the store's own compare-at price is used, at lower weight.
