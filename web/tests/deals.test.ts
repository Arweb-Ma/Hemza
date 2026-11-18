import { describe, expect, it } from 'vitest';
import {
  byScore,
  categoriesIn,
  computeScore,
  filterDeals,
  inSection,
  scoreLevel,
} from '@/lib/deals';
import { buildDemoFeed } from '@/lib/demo';
import { formatMad, pluralize, timeAgo } from '@/lib/format';
import { sparkline } from '@/lib/sparkline';
import { toDeal } from '@/lib/supabase';
import type { Deal } from '@/lib/types';

const deal = (overrides: Partial<Deal>): Deal => ({
  id: 1,
  title: 'Veste',
  storeName: 'Test',
  gender: 'homme',
  category: 'vestes',
  price: 700,
  refPrice: 1300,
  claimedPrice: 1300,
  discountPct: 46,
  claimedDiscountPct: 46,
  savedMad: 600,
  score: 86,
  lowest90d: true,
  verified: true,
  history: [1300, 700],
  sizes: [],
  imageUrl: null,
  productUrl: 'https://example.ma/p',
  observedAt: '2026-11-16T00:00:00Z',
  ...overrides,
});

describe('scoring', () => {
  it('matches the SQL view: 46% off at a 90-day low scores 86', () => {
    expect(computeScore(46, true)).toBe(86);
  });
  it('is capped between 0 and 100', () => {
    expect(computeScore(90, true)).toBe(100);
    expect(computeScore(-5, false)).toBe(0);
  });
  it('maps scores to levels', () => {
    expect([scoreLevel(86), scoreLevel(55), scoreLevel(36)]).toEqual(['high', 'medium', 'low']);
  });
});

describe('sections and categories', () => {
  const deals = [
    deal({ id: 1, gender: 'homme', category: 'vestes' }),
    deal({ id: 2, gender: 'femme', category: 'robes' }),
    deal({ id: 3, gender: 'unisexe', category: 'pulls' }),
    deal({ id: 4, gender: 'enfant', category: 'vestes' }),
  ];
  it('lists unisex items in both adult sections but not in enfant', () => {
    const unisex = deals[2]!;
    expect(inSection(unisex, 'femme') && inSection(unisex, 'homme')).toBe(true);
    expect(inSection(unisex, 'enfant')).toBe(false);
  });
  it('filters by section and category', () => {
    expect(filterDeals(deals, { section: 'homme' }).map((d) => d.id)).toEqual([1, 3]);
    expect(filterDeals(deals, { section: 'enfant', category: 'vestes' }).map((d) => d.id)).toEqual([
      4,
    ]);
  });
  it('returns present categories in display order', () => {
    expect(categoriesIn(deals)).toEqual(['robes', 'pulls', 'vestes']);
  });
});

describe('formatting', () => {
  it('formats dirhams without decimals', () => {
    expect(formatMad(1300).replace(/\s/g, ' ')).toBe('1 300 DH');
    expect(formatMad(699.6)).toBe('700 DH');
  });
  it('pluralizes', () => {
    expect(pluralize(1, 'promo')).toBe('1 promo');
    expect(pluralize(37, 'promo')).toBe('37 promos');
  });
  it('describes elapsed time coarsely', () => {
    const now = new Date('2026-11-16T12:00:00Z');
    expect(timeAgo('2026-11-16T11:40:00Z', now)).toBe('il y a 20 min');
    expect(timeAgo('2026-11-16T10:00:00Z', now)).toBe('il y a 2 h');
    expect(timeAgo('2026-11-13T12:00:00Z', now)).toBe('il y a 3 j');
  });
});

describe('sparkline', () => {
  it('needs at least two points', () => {
    expect(sparkline([5], 100, 20)).toBeNull();
  });
  it('puts the highest price at the top and the lowest at the bottom', () => {
    const geometry = sparkline([1300, 1300, 700], 100, 20, 2)!;
    expect(geometry.path).toBe('M0 2L50 2L100 18');
    expect(geometry.end).toEqual({ x: 100, y: 18 });
  });
  it('handles a flat series', () => {
    expect(sparkline([10, 10], 10, 10)).not.toBeNull();
  });
});

describe('database rows', () => {
  const row = {
    id: 7,
    title: 'Veste en cuir',
    store_name: 'Test',
    gender: 'homme' as const,
    category: 'vestes',
    price: '700.00',
    compare_at: '1500.00',
    median_30d: 1300,
    n_obs: 19,
    real_disc_pct: 46,
    claimed_disc_pct: 53,
    saved_mad: 600,
    score: 86,
    lowest_90d: true,
    sizes: ['M'],
    image_url: 'https://cdn.example.ma/a.jpg',
    product_url: 'https://example.ma/products/veste',
    affiliate_tpl: null,
    observed_at: '2026-11-16T00:00:00Z',
  };
  it('uses our own median once there is enough history', () => {
    const parsed = toDeal(row, [1300, 1300, 700])!;
    expect(parsed).toMatchObject({ price: 700, refPrice: 1300, discountPct: 46, savedMad: 600 });
    expect(parsed.verified).toBe(true);
    expect(parsed.productUrl).toBe('https://example.ma/products/veste?utm_source=hemza');
  });
  it('falls back to the store price while history is thin', () => {
    const parsed = toDeal(
      { ...row, n_obs: 1, median_30d: null, saved_mad: null, score: null },
      [],
    )!;
    expect(parsed.refPrice).toBe(1500);
    expect(parsed.verified).toBe(false);
    expect(parsed.history).toEqual([]);
  });
  it('rejects unsafe links and non-drops', () => {
    expect(toDeal({ ...row, product_url: 'javascript:alert(1)' }, [])).toBeNull();
    expect(toDeal({ ...row, price: '1400.00' }, [])).toBeNull();
    expect(toDeal({ ...row, image_url: 'data:text/html,x' }, [])!.imageUrl).toBeNull();
  });
  it('wraps the product link in the affiliate template', () => {
    const parsed = toDeal({ ...row, affiliate_tpl: 'https://aff.example/?to={url}' }, [])!;
    expect(parsed.productUrl).toBe(
      'https://aff.example/?to=https%3A%2F%2Fexample.ma%2Fproducts%2Fveste',
    );
  });
  it('maps unknown categories to "autres"', () => {
    expect(toDeal({ ...row, category: 'mystere' }, [])!.category).toBe('autres');
  });
});

describe('ordering', () => {
  it('ranks verified drops above unverified store claims, whatever the score', () => {
    const claim = deal({ id: 1, verified: false, score: 95 });
    const checked = deal({ id: 2, verified: true, score: 40 });
    expect([claim, checked].sort(byScore).map((d) => d.id)).toEqual([2, 1]);
  });
});

describe('demo feed', () => {
  it('is deterministic and only contains real drops', () => {
    const now = new Date('2026-11-16T00:00:00Z');
    const a = buildDemoFeed(now);
    const b = buildDemoFeed(now);
    expect(a).toEqual(b);
    expect(a.deals.length).toBeGreaterThan(20);
    expect(a.fakePromosFiltered).toBeGreaterThan(0);
    expect(a.deals.every((d) => d.price < d.refPrice && d.discountPct >= 15)).toBe(true);
  });
});
