# Hemza.ma — vraies promos mode au Maroc

## Structure
- `db/schema.sql` — tables (stores, products, price_history, posts) + views `deal_candidates` and `deals` (scoring, fake-promo filter). Tested on Postgres 16.
- `collector/collect.py` — reads public Shopify `/products.json` feeds, classifies femme/homme/enfant + category (FR/EN/Darija-Arabic), writes prices to Supabase.
- `collector/stores.json` — seed store list (verify each one first).
- `collector/post_deals.py` — posts top deals to a Telegram channel; `--review` prints them for manual approval.
- `web/` — the website (Astro + TypeScript, static, deployed to Cloudflare). See `web/README.md`.

## Go-live
1. Supabase: project `hemza` (ref `tcsjfqkreqceektalogu`, eu-west-1) already has `db/schema.sql` applied.
2. Collector: `pip install requests`, set `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`, run `python collector/collect.py --dry-run`, then for real. Schedule it every 6 h (GitHub Actions cron or a VPS cron).
3. Site: `cd web && cp .env.example .env && npm install && npm run deploy`. It shows demo data until the `deals` view returns rows; rebuild after each collection. Real product images load from the stores' CDNs.
4. Telegram: create the channel and a bot via @BotFather, add the bot as channel admin, then run `post_deals.py --review` and, once you're happy with it, without `--review`.

## Scoring
- Reference price = median of the last 30 days (excluding the last 24 h).
- Published if the real drop is ≥ 20 %, or (if there are < 5 data points yet) the store's advertised discount is ≥ 30 %.
- Fake promo = store claims ≥ 30 % off, but the price is not lower than its 30-day median → hidden.
- Score = real % × 1.6 + 12 if lowest price in 90 days (capped at 100).

Note: deals need ~1 week of collected history before the "real discount" check is meaningful; until then the store's own compare-at price is used, at lower weight.
