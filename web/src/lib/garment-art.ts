import type { Category } from '@/config/site';

/** Placeholder illustrations (200x220 viewBox) for items without a photo. */
const SHAPES: Partial<Record<Category, string>> = {
  robes: '<path d="M78 40h44l8 30-12 8 22 92H60l22-92-12-8z"/>',
  traditionnel:
    '<path d="M70 38h60l14 40-14 6 10 96H60l10-96-14-6z"/><path d="M100 50v120" fill="none" stroke="rgb(0 0 0 / .18)" stroke-width="4"/>',
  jeans: '<path d="M66 40h68l6 140h-30l-10-100-10 100H60z"/>',
  pantalons: '<path d="M66 40h68l8 140h-32l-10-100-10 100H58z"/>',
  't-shirts':
    '<path d="M70 44 44 64l14 20 12-8v94h60V76l12 8 14-20-26-20c-6 10-18 14-30 14s-24-4-30-14z"/>',
  chemises:
    '<path d="M72 42 48 60l10 110h84l10-110-24-18-28 20z"/><path d="M100 62v108" fill="none" stroke="rgb(0 0 0 / .18)" stroke-width="3"/>',
  pulls:
    '<path d="M72 42 42 68l10 70 16-4v36h64v-36l16 4 10-70-30-26c-6 8-16 12-28 12s-22-4-28-12z"/>',
  vestes:
    '<path d="M70 40 42 64l6 116h104l6-116-28-24-30 34z"/><path d="M100 74v106" fill="none" stroke="rgb(0 0 0 / .2)" stroke-width="4"/>',
  ensembles:
    '<path d="M72 36 50 54l10 14 10-6v40h60V62l10 6 10-14-22-18c-6 8-14 10-28 10s-22-2-28-10z"/><path d="M70 112h60l4 70h-26l-8-46-8 46H66z"/>',
  chaussures:
    '<path d="M40 130c0-14 10-22 22-22 14 0 22 10 40 12s36 4 50 14c8 6 8 16 0 20H48c-6 0-8-6-8-24z"/>',
  sacs: '<path d="M58 80h84l10 96H48z"/><path d="M78 80c0-26 44-26 44 0" fill="none" stroke="currentColor" stroke-width="7"/>',
  accessoires:
    '<path d="M50 120c0-30 22-50 50-50s50 20 50 50z"/><path d="M40 120h90c10 0 14 8 0 10H40z"/>',
};

const PALETTE = [
  ['#e9c9b5', '#c3a38f'],
  ['#c9dcd3', '#a3b6ad'],
  ['#e7d9b8', '#c1b392'],
  ['#d5cde6', '#afa7c0'],
  ['#f0d3d3', '#caadad'],
  ['#cfe0e8', '#a9bac2'],
  ['#e6e0d4', '#c0baae'],
] as const;

export function garmentArt(category: Category, seed: number) {
  const [background, foreground] = PALETTE[Math.abs(seed) % PALETTE.length]!;
  return { shape: SHAPES[category] ?? SHAPES['t-shirts']!, background, foreground };
}
