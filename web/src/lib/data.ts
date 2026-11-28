import { SUPABASE } from '@/config/site';
import { byScore, dropDuplicates } from './deals';
import { buildDemoFeed } from './demo';
import { fetchLiveDeals } from './supabase';
import type { DealFeed } from './types';

/**
 * During a build, every page shares one fetch. In `npm run dev` the result expires after a minute,
 * so new collector runs show up on refresh without restarting the server.
 */
const DEV_TTL_MS = 60_000;
let cached: { feed: Promise<DealFeed>; at: number } | undefined;

async function load(): Promise<DealFeed> {
  const generatedAt = new Date().toISOString();
  const url = import.meta.env.SUPABASE_URL || SUPABASE.url;
  const key = import.meta.env.SUPABASE_PUBLISHABLE_KEY || SUPABASE.publishableKey;

  if (url && key) {
    try {
      const deals = await fetchLiveDeals({ url, key });
      if (deals.length > 0) {
        return {
          deals: dropDuplicates(deals),
          source: 'live',
          fakePromosFiltered: null,
          generatedAt,
        };
      }
      console.warn(
        '[hemza] The database is reachable but has no deals yet: showing demo data until the collector finds some.',
      );
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

  // On the deploy server, demo data must never replace the live site: fail the build instead,
  // and the previous deployment (with real deals) stays online.
  if (import.meta.env.HEMZA_REQUIRE_LIVE_DATA === '1') {
    throw new Error(
      '[hemza] Live deals could not be loaded and HEMZA_REQUIRE_LIVE_DATA=1: not building a demo site.',
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
  const expired =
    import.meta.env.DEV && cached !== undefined && Date.now() - cached.at > DEV_TTL_MS;
  if (cached === undefined || expired) cached = { feed: load(), at: Date.now() };
  return cached.feed;
}
