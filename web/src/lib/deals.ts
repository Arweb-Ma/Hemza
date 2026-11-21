import { CATEGORIES, type Category, type Section } from '@/config/site';
import type { Deal, Tier } from './types';

/** Mirrors the `score` column of the `deals` view in db/schema.sql. */
export function computeScore(discountPct: number, lowest90d: boolean): number {
  return Math.min(100, Math.max(0, Math.round(discountPct * 1.6 + (lowest90d ? 12 : 0))));
}

export type ScoreLevel = 'high' | 'medium' | 'low';
export const scoreLevel = (score: number): ScoreLevel =>
  score >= 70 ? 'high' : score >= 50 ? 'medium' : 'low';

/** Unisex items belong to both adult sections. */
export function inSection(deal: Deal, section: Section): boolean {
  if (deal.gender === section) return true;
  return deal.gender === 'unisexe' && section !== 'enfant';
}

/** Listings show big brands unless asked for the small shops (`tier: 'boutique'`). */
export function filterDeals(
  deals: readonly Deal[],
  {
    section,
    category,
    tier = 'marque',
  }: { section?: Section; category?: Category; tier?: Tier } = {},
): Deal[] {
  return deals.filter(
    (deal) =>
      deal.tier === tier &&
      (!section || inSection(deal, section)) &&
      (!category || deal.category === category),
  );
}

/** Categories that have at least one deal, in display order. */
export function categoriesIn(deals: readonly Deal[]): Category[] {
  const present = new Set(deals.map((deal) => deal.category));
  return CATEGORIES.filter((category) => present.has(category));
}

/** Verified drops always come before promos we only know from the store. */
export const byScore = (a: Deal, b: Deal): number =>
  Number(b.verified) - Number(a.verified) || b.score - a.score || b.savedMad - a.savedMad;

/** Lowercase, no accents, punctuation folded to spaces: "Gris." and "gris" compare equal. */
export function normalizeTitle(title: string): string {
  return title
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** The image file without its cache-busting query (`?v=…`). */
const imageKey = (url: string | null): string | null => (url ? url.split('?')[0]! : null);

/** Placeholder products shops forget to delete ("test", "LOS ANGELES | TEST"). */
export const isTestListing = (title: string): boolean => /\btest\b/.test(normalizeTitle(title));

/**
 * Removes products a shop published twice: same photo (e.g. "Casquette … Gris" and
 * "Casquette … Gris." on a `-1` URL), or, without a photo, same name at the same price.
 * Test listings go too. The cheapest copy is kept.
 * Returns the survivors in `byScore` order.
 */
export function dropDuplicates(deals: readonly Deal[]): Deal[] {
  const seen = new Set<string>();
  const kept: Deal[] = [];
  const cheapestFirst = [...deals].sort((a, b) => a.price - b.price || byScore(a, b));
  for (const deal of cheapestFirst) {
    if (isTestListing(deal.title)) continue;
    // Different photos under one name are different models (handled by collapseModels), so the
    // name + price rule only applies when there is no photo to tell them apart.
    const image = imageKey(deal.imageUrl);
    const keys = image
      ? [`${deal.storeName}\u0000image\u0000${image}`]
      : [`${deal.storeName}\u0000title\u0000${normalizeTitle(deal.title)}\u0000${deal.price}`];
    if (keys.some((key) => seen.has(key))) continue;
    keys.forEach((key) => seen.add(key));
    kept.push(deal);
  }
  return kept.sort(byScore);
}

/** Products a shop sells under the same name (different models, e.g. 8 × "BASKETS … - NOIR"). */
export const modelKey = (deal: Deal): string =>
  `${deal.storeName}\u0000${normalizeTitle(deal.title)}`;

export interface ListedDeal {
  /** The best-ranked model, shown on the card. */
  deal: Deal;
  /** How many models share this name in the listing (1 when the name is unique). */
  models: number;
  /** Lowest price among those models. */
  fromPrice: number;
}

/** One card per shop + name. Keeps the input order, so the best-ranked model represents the group. */
export function collapseModels(deals: readonly Deal[]): ListedDeal[] {
  const groups = new Map<string, ListedDeal>();
  for (const deal of deals) {
    const key = modelKey(deal);
    const group = groups.get(key);
    if (group) {
      group.models += 1;
      group.fromPrice = Math.min(group.fromPrice, deal.price);
    } else {
      groups.set(key, { deal, models: 1, fromPrice: deal.price });
    }
  }
  return [...groups.values()];
}
