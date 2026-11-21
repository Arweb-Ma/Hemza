import type { Gender } from '@/lib/types';

export const SITE = {
  name: 'Reeklam',
  tagline: 'vraies promos mode',
  title: 'Reeklam — Les vraies promos mode au Maroc',
  description:
    'Reeklam repère chaque jour les vraies baisses de prix sur la mode femme, homme et enfant au Maroc. Historique des prix, fausses promos filtrées.',
  locale: 'fr-MA',
  /** Set once the channel exists, e.g. 'https://t.me/your_channel'. The button is hidden while null. */
  telegramUrl: null as string | null,
};

/**
 * Database the site reads at build time. Both values are public: the publishable key can only
 * read what row-level security allows. Environment variables override them (see .env.example).
 */
export const SUPABASE = {
  url: 'https://tcsjfqkreqceektalogu.supabase.co',
  publishableKey: 'sb_publishable_cCihiXDolnLBydPGaLKm3g_KNX17m2c',
};

/** Sections that get their own page. `unisexe` items are listed in femme and homme. */
export const SECTIONS = ['femme', 'homme', 'enfant'] as const satisfies readonly Gender[];
export type Section = (typeof SECTIONS)[number];

export const SECTION_LABEL: Record<Section, string> = {
  femme: 'Femme',
  homme: 'Homme',
  enfant: 'Enfant',
};

/** Display order and labels for categories (keys match collector/collect.py). */
export const CATEGORY_LABEL = {
  robes: 'Robes',
  traditionnel: 'Caftans & djellabas',
  jeans: 'Jeans',
  pantalons: 'Pantalons',
  't-shirts': 'T-shirts & tops',
  chemises: 'Chemises',
  pulls: 'Pulls & sweats',
  vestes: 'Vestes & manteaux',
  ensembles: 'Ensembles',
  chaussures: 'Chaussures',
  sacs: 'Sacs',
  accessoires: 'Accessoires',
  parfums: 'Parfums',
  autres: 'Autres',
} as const;

export type Category = keyof typeof CATEGORY_LABEL;
export const CATEGORIES = Object.keys(CATEGORY_LABEL) as Category[];

export const isCategory = (value: string): value is Category => value in CATEGORY_LABEL;
