import { isCategory } from '@/config/site';
import { computeScore } from './deals';
import type { Deal, Gender } from './types';

/** Row shape of the `deals` view (db/schema.sql). Numerics arrive as numbers or strings. */
interface DealRow {
  id: number;
  title: string;
  store_name: string;
  gender: Gender | null;
  category: string | null;
  price: number | string;
  compare_at: number | string | null;
  median_30d: number | string | null;
  n_obs: number | null;
  real_disc_pct: number | null;
  claimed_disc_pct: number | null;
  saved_mad: number | null;
  store_promo_share: number | string | null;
  store_tier: string | null;
  score: number | null;
  lowest_90d: boolean | null;
  sizes: string[] | null;
  image_url: string | null;
  product_url: string;
  affiliate_tpl: string | null;
  observed_at: string;
}

interface PriceRow {
  product_id: number;
  price: number | string;
}

const MIN_OBSERVATIONS = 5;
/** Above this share of discounted items, a shop's crossed-out prices say little. */
export const ALWAYS_ON_SALE_SHARE = 0.4;
const HISTORY_DAYS = 30;
const ID_CHUNK = 150;

export interface SupabaseConfig {
  url: string;
  key: string;
}

/** Pauses before each new attempt. The `promos` view is heavy: on a cold cache it can pass the
 * public role's 3 s statement timeout (HTTP 500), and the very next try usually succeeds. */
export const RETRY_DELAYS_MS = [2_000, 5_000, 10_000];

export async function get<T>(
  { url, key }: SupabaseConfig,
  path: string,
  delays: number[] = RETRY_DELAYS_MS,
): Promise<T> {
  const name = path.split('?')[0];
  for (let attempt = 0; ; attempt++) {
    let error: Error;
    let retryable = true;
    try {
      const response = await fetch(`${url.replace(/\/$/, '')}/rest/v1/${path}`, {
        headers: { apikey: key, Authorization: `Bearer ${key}` },
        signal: AbortSignal.timeout(20_000),
      });
      if (response.ok) return (await response.json()) as T;
      error = new Error(`Supabase ${response.status} on ${name}`);
      // Other 4xx mean the request itself is wrong: trying again will not help.
      retryable = response.status >= 500 || response.status === 429;
    } catch (caught) {
      error = caught instanceof Error ? caught : new Error(String(caught)); // network error or timeout
    }
    if (!retryable || attempt >= delays.length) throw error;
    console.warn(`[hemza] ${error.message}: trying again in ${delays[attempt] / 1000} s`);
    await new Promise((resolve) => setTimeout(resolve, delays[attempt]));
  }
}

/** Only http(s) links are ever rendered: store data is untrusted input. */
function safeUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const { protocol } = new URL(value);
    return protocol === 'https:' || protocol === 'http:' ? value : null;
  } catch {
    return null;
  }
}

function outboundUrl(row: DealRow): string | null {
  const product = safeUrl(row.product_url);
  if (!product) return null;
  if (row.affiliate_tpl)
    return safeUrl(row.affiliate_tpl.replace('{url}', encodeURIComponent(product)));
  const url = new URL(product);
  url.searchParams.set('utm_source', 'hemza');
  return url.toString();
}

export function toDeal(row: DealRow, history: number[]): Deal | null {
  const price = Number(row.price);
  const median = row.median_30d === null ? null : Number(row.median_30d);
  const claimed = row.compare_at === null ? null : Number(row.compare_at);
  const trusted = (row.n_obs ?? 0) >= MIN_OBSERVATIONS && median !== null;
  const refPrice = trusted ? median : (claimed ?? median);
  const productUrl = outboundUrl(row);
  if (!productUrl || !refPrice || !(refPrice > price)) return null;

  const discountPct = Math.round(100 * (1 - price / refPrice));
  const lowest90d = row.lowest_90d ?? false;
  return {
    id: row.id,
    title: row.title,
    storeName: row.store_name,
    tier: row.store_tier === 'marque' ? 'marque' : 'boutique',
    gender: row.gender ?? 'unisexe',
    category: row.category && isCategory(row.category) ? row.category : 'autres',
    price,
    refPrice,
    claimedPrice: claimed,
    discountPct,
    claimedDiscountPct: row.claimed_disc_pct,
    savedMad: row.saved_mad ?? Math.round(refPrice - price),
    score: row.score ?? computeScore(discountPct, lowest90d),
    lowest90d,
    verified: trusted,
    alwaysOnSale: Number(row.store_promo_share ?? 0) > ALWAYS_ON_SALE_SHARE,
    // Only our own readings: never draw a line we did not measure.
    history,
    sizes: row.sizes ?? [],
    imageUrl: safeUrl(row.image_url),
    productUrl,
    observedAt: row.observed_at,
  };
}

/** The API returns at most this many rows per request (Supabase's default `max-rows`). */
const PAGE_SIZE = 1000;
/** Safety stop, far above today's catalog. */
const MAX_ROWS = 50_000;

/** Every row of the `promos` view, read page by page. */
export async function fetchPromoRows(config: SupabaseConfig): Promise<DealRow[]> {
  const rows: DealRow[] = [];
  for (let offset = 0; offset < MAX_ROWS; offset += PAGE_SIZE) {
    const page = await get<DealRow[]>(
      config,
      `promos?select=*&order=score.desc,id.asc&limit=${PAGE_SIZE}&offset=${offset}`,
    );
    rows.push(...page);
    if (page.length < PAGE_SIZE) break;
  }
  return rows;
}

export async function fetchLiveDeals(config: SupabaseConfig): Promise<Deal[]> {
  const rows = await fetchPromoRows(config);
  if (rows.length === 0) return [];

  const since = new Date(Date.now() - HISTORY_DAYS * 86_400_000).toISOString();
  const history = new Map<number, number[]>();
  for (let i = 0; i < rows.length; i += ID_CHUNK) {
    const ids = rows.slice(i, i + ID_CHUNK).map((row) => row.id);
    const prices = await get<PriceRow[]>(
      config,
      `price_history?select=product_id,price&product_id=in.(${ids.join(',')})` +
        `&observed_at=gte.${since}&order=observed_at.asc`,
    );
    for (const { product_id, price } of prices) {
      const series = history.get(product_id) ?? [];
      series.push(Number(price));
      history.set(product_id, series);
    }
  }
  return rows.flatMap((row) => toDeal(row, history.get(row.id) ?? []) ?? []);
}
