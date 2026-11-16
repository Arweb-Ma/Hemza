import { CATEGORIES, type Category, type Section } from '@/config/site';
import type { Deal } from './types';

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

export function filterDeals(
  deals: readonly Deal[],
  { section, category }: { section?: Section; category?: Category } = {},
): Deal[] {
  return deals.filter(
    (deal) => (!section || inSection(deal, section)) && (!category || deal.category === category),
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
