import { byScore } from './deals';
import { buildDemoFeed } from './demo';
import { fetchLiveDeals } from './supabase';
import type { DealFeed } from './types';

let cached: Promise<DealFeed> | undefined;

async function load(): Promise<DealFeed> {
  const generatedAt = new Date().toISOString();
  const url = import.meta.env.SUPABASE_URL;
  const key = import.meta.env.SUPABASE_PUBLISHABLE_KEY;

  if (url && key) {
    try {
      const deals = await fetchLiveDeals({ url, key });
      if (deals.length > 0) {
        return {
          deals: deals.sort(byScore),
          source: 'live',
          fakePromosFiltered: null,
          generatedAt,
        };
      }
      console.warn('[hemza] The database has no deals yet: building with demo data.');
    } catch (error) {
      console.warn(
        `[hemza] Could not load live deals (${String(error)}): building with demo data.`,
      );
    }
  } else {
    console.warn(
      '[hemza] SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY not set: building with demo data.',
    );
  }

  const demo = buildDemoFeed();
  return {
    deals: demo.deals.sort(byScore),
    source: 'demo',
    fakePromosFiltered: demo.fakePromosFiltered,
    generatedAt,
  };
}

/** Deals for this build. Fetched once and shared by every page. */
export function getDealFeed(): Promise<DealFeed> {
  cached ??= load();
  return cached;
}
