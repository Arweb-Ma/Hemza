-- =========================================================
-- Hemza.ma — deal engine schema (Supabase / Postgres)
-- =========================================================

create table if not exists stores (
  id            bigserial primary key,
  name          text not null,
  domain        text not null unique,
  platform      text not null default 'shopify',   -- shopify | feed | manual
  affiliate_tpl text,                               -- e.g. 'https://kol.jumia.com/...{url}'
  active        boolean not null default true,
  last_crawl_at timestamptz,
  created_at    timestamptz not null default now()
);

create table if not exists products (
  id            bigserial primary key,
  store_id      bigint not null references stores(id) on delete cascade,
  external_id   text not null,                      -- Shopify product id
  handle        text,
  title         text not null,
  brand         text,
  gender        text check (gender in ('femme','homme','enfant','unisexe')),
  category      text,                               -- robe, chaussures, jean, ...
  image_url     text,
  product_url   text not null,
  sizes         text[],
  in_stock      boolean not null default true,
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  unique (store_id, external_id)
);

-- One row per product per crawl where the price changed (or daily heartbeat)
create table if not exists price_history (
  id            bigserial primary key,
  product_id    bigint not null references products(id) on delete cascade,
  price         numeric(10,2) not null,             -- MAD
  compare_at    numeric(10,2),                      -- store's "was" price
  in_stock      boolean not null,
  observed_at   timestamptz not null default now()
);
create index if not exists ph_product_time on price_history(product_id, observed_at desc);

-- Social posting log (avoid re-posting the same deal)
create table if not exists posts (
  id          bigserial primary key,
  product_id  bigint not null references products(id) on delete cascade,
  channel     text not null,                        -- telegram | instagram | facebook
  price       numeric(10,2) not null,
  posted_at   timestamptz not null default now()
);

-- =========================================================
-- Deal scoring
--  ref_price  = median price over the previous 30 days (excl. last 24h)
--  real_disc  = discount vs our OWN history  -> trusted
--  claimed    = discount vs store compare_at -> shown, not trusted alone
--  fake_promo = store claims ≥30% off but price is not lower than its 30-day median
-- =========================================================
create or replace view deal_candidates with (security_invoker = true) as
with cur as (
  select distinct on (product_id) product_id, price, compare_at, in_stock, observed_at
  from price_history order by product_id, observed_at desc
),
hist as (
  select ph.product_id,
         percentile_cont(0.5) within group (order by ph.price) as median_30d,
         count(*)       as n_obs
  from price_history ph
  where ph.observed_at between now() - interval '30 days' and now() - interval '24 hours'
  group by ph.product_id
),
low90 as (
  select product_id, min(price) as min_90d
  from price_history where observed_at > now() - interval '90 days'
  group by product_id
)
select p.*, s.name as store_name, s.affiliate_tpl,
       c.price, c.compare_at, c.observed_at,
       h.median_30d, h.n_obs, l.min_90d,
       case when h.median_30d > 0 then round(100 * (1 - c.price / h.median_30d))::int end as real_disc_pct,
       case when c.compare_at > c.price then round(100 * (1 - c.price / c.compare_at))::int end as claimed_disc_pct,
       -- only meaningful once we have our own history: with one reading, every price is "the lowest"
       (coalesce(h.n_obs, 0) >= 5 and c.price <= l.min_90d) as lowest_90d,
       (c.compare_at > 0 and c.price <= c.compare_at * 0.7
         and h.n_obs >= 5 and c.price >= h.median_30d * 0.98) as fake_promo,
       -- verified = we have enough of our own history to judge the drop ourselves
       (coalesce(h.n_obs, 0) >= 5) as verified,
       -- share of the store's in-stock catalog that carries a crossed-out price.
       -- When a large part of a shop is permanently "on sale", its claims mean little.
       round((count(*) filter (where c.compare_at > c.price) over (partition by p.store_id))::numeric
             / count(*) over (partition by p.store_id), 2) as store_promo_share
from cur c
join products p on p.id = c.product_id
join stores   s on s.id = p.store_id
left join hist  h on h.product_id = c.product_id
left join low90 l on l.product_id = c.product_id
where c.in_stock and s.active;

-- Columns are listed explicitly so new ones can be appended without recreating the view.
create or replace view deals with (security_invoker = true) as
select id, store_id, external_id, handle, title, brand, gender, category, image_url, product_url,
       sizes, in_stock, first_seen_at, last_seen_at, store_name, affiliate_tpl,
       price, compare_at, observed_at, median_30d, n_obs, min_90d,
       real_disc_pct, claimed_disc_pct, lowest_90d, fake_promo,
  -- dirhams saved vs our reference price (30-day median, else the store's own "was" price)
  round(coalesce(case when n_obs >= 5 then median_30d end, compare_at, median_30d) - price)::int as saved_mad,
  -- score 0–100: trusted discount dominates; store claim counts only when history is thin
  least(100, greatest(0,
      coalesce(real_disc_pct, 0) * 1.6
    + case when n_obs is null or n_obs < 5 then coalesce(claimed_disc_pct,0) * 0.6 else 0 end
    + case when lowest_90d then 12 else 0 end
  ))::int as score,
  verified, store_promo_share
from deal_candidates
where not coalesce(fake_promo, false)
  and ( coalesce(real_disc_pct,0) >= 20
        -- not verified yet: accept the store's claim only if it is big (>= 30 %)
        -- and the store does not have over 40 % of its catalog "on sale"
     or (not verified and coalesce(claimed_disc_pct,0) >= 30 and store_promo_share <= 0.40) );

-- Public read access for the website (anon key), writes only via service role
alter table stores enable row level security;
alter table products enable row level security;
alter table price_history enable row level security;
alter table posts enable row level security;
drop policy if exists "public read stores" on stores;
drop policy if exists "public read products" on products;
drop policy if exists "public read prices" on price_history;
create policy "public read stores"   on stores   for select using (true);
create policy "public read products" on products for select using (true);
create policy "public read prices"   on price_history for select using (true);
