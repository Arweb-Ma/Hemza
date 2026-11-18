import type { Category } from '@/config/site';

export type Gender = 'femme' | 'homme' | 'enfant' | 'unisexe';

/** A verified price drop, ready to render. Prices are in Moroccan dirhams (MAD). */
export interface Deal {
  id: number;
  title: string;
  storeName: string;
  gender: Gender;
  category: Category;
  /** Current price. */
  price: number;
  /** What the item normally costs: our 30-day median, or the store's "was" price while history is thin. */
  refPrice: number;
  /** The store's own crossed-out price, if it shows one. */
  claimedPrice: number | null;
  /** Discount measured against `refPrice`, in percent. */
  discountPct: number;
  /** Discount the store advertises, in percent. */
  claimedDiscountPct: number | null;
  /** Dirhams saved against `refPrice`. */
  savedMad: number;
  /** 0–100, see db/schema.sql. */
  score: number;
  lowest90d: boolean;
  /** True once our own price history is long enough to confirm the drop ourselves. */
  verified: boolean;
  /** The shop has a large share of its catalog permanently "on sale" (see ALWAYS_ON_SALE_SHARE). */
  alwaysOnSale: boolean;
  /** Our own price readings over 30 days, oldest first. Fewer than 2 while a product is new. */
  history: number[];
  sizes: string[];
  imageUrl: string | null;
  productUrl: string;
  /** ISO timestamp of the latest price check. */
  observedAt: string;
}

export interface DealFeed {
  deals: Deal[];
  /** `demo` means the database returned no deals (or was unreachable) and sample data is shown. */
  source: 'live' | 'demo';
  /** Promos hidden because the price never actually moved. Only known for demo data. */
  fakePromosFiltered: number | null;
  generatedAt: string;
}
