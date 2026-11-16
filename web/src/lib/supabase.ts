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
const HISTORY_DAYS = 30;
const ID_CHUNK = 150;

export interface SupabaseConfig {
  url: string;
  key: string;
}

async function get<T>({ url, key }: SupabaseConfig, path: string): Promise<T> {
  const response = await fetch(`${url.replace(/\/$/, '')}/rest/v1/${path}`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
    signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok) throw new Error(`Supabase ${response.status} on ${path.split('?')[0]}`);
  return (await response.json()) as T;
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
    history: history.length > 1 ? history : [refPrice, price],
    sizes: row.sizes ?? [],
    imageUrl: safeUrl(row.image_url),
    productUrl,
    observedAt: row.observed_at,
  };
}

export async function fetchLiveDeals(config: SupabaseConfig): Promise<Deal[]> {
  const rows = await get<DealRow[]>(config, 'deals?select=*&order=score.desc&limit=1000');
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
