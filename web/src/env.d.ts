/// <reference path="../.astro/types.d.ts" />

interface ImportMetaEnv {
  readonly SUPABASE_URL?: string;
  readonly SUPABASE_PUBLISHABLE_KEY?: string;
  /** "1" on the deploy server: a build without live deals fails instead of showing demo data. */
  readonly HEMZA_REQUIRE_LIVE_DATA?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
