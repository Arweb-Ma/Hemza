import type { Category } from '@/config/site';
import { computeScore } from './deals';
import type { Deal, Gender } from './types';

/**
 * Sample deals shown until the collector has gathered real prices.
 * Deterministic (seeded) so builds and tests are reproducible. Stores and prices are fictional.
 */
type Template = [title: string, category: Category, min: number, max: number];

const TEMPLATES: Record<Exclude<Gender, 'unisexe'>, Template[]> = {
  femme: [
    ['Robe longue satin', 'robes', 250, 600],
    ['Caftan brodé main', 'traditionnel', 900, 2000],
    ['Takchita deux pièces', 'traditionnel', 1200, 2000],
    ['Jean mom fit', 'jeans', 200, 450],
    ['Blouse en lin', 'chemises', 150, 350],
    ['Cardigan maille', 'pulls', 180, 400],
    ['Trench beige', 'vestes', 450, 1100],
    ['Baskets blanches', 'chaussures', 300, 700],
    ['Sac cabas cuir', 'sacs', 350, 900],
    ['Abaya fluide', 'traditionnel', 300, 700],
    ['Pantalon large', 'pantalons', 200, 450],
    ['Ensemble jogging', 'ensembles', 250, 550],
  ],
  homme: [
    ['Jean slim brut', 'jeans', 250, 550],
    ['Chemise oxford', 'chemises', 200, 450],
    ['Sweat à capuche', 'pulls', 220, 480],
    ['Blouson aviateur', 'vestes', 600, 1400],
    ['Polo piqué', 't-shirts', 150, 320],
    ['Chino stretch', 'pantalons', 220, 480],
    ['Djellaba laine', 'traditionnel', 700, 1800],
    ['Sneakers running', 'chaussures', 400, 1000],
    ['Mocassins cuir', 'chaussures', 450, 1100],
    ['Doudoune légère', 'vestes', 500, 1200],
    ['Casquette brodée', 'accessoires', 100, 220],
    ['Ceinture cuir', 'accessoires', 120, 300],
  ],
  enfant: [
    ['Ensemble garçon 6 ans', 'ensembles', 150, 320],
    ['Robe fille à volants', 'robes', 150, 350],
    ['Jean enfant', 'jeans', 120, 260],
    ['Doudoune enfant', 'vestes', 250, 550],
    ['Baskets enfant lumineuses', 'chaussures', 180, 400],
    ['Pyjama coton', 'ensembles', 100, 220],
    ['Gandoura enfant', 'traditionnel', 200, 450],
    ['Sweat imprimé', 'pulls', 120, 250],
    ['Cartable 2 roues', 'sacs', 200, 450],
  ],
};

const EDITIONS = ['été', 'hiver', 'limitée'];

function storesFor(gender: Gender, category: Category): string[] {
  if (gender === 'enfant') return ['Petit Souk Kids', 'Casa Avenue'];
  if (category === 'chaussures') return ['Medina Shoes', 'Casa Avenue'];
  if (category === 'jeans') return ['Rif Denim', 'Boutique Atlas'];
  if (gender === 'femme') return ['Maison Zellige', 'Boutique Atlas', 'Casa Avenue'];
  return ['Kasbah Style', 'Boutique Atlas', 'Casa Avenue'];
}

function sizesFor(gender: Gender, category: Category): string[] {
  if (category === 'sacs' || category === 'accessoires') return ['Taille unique'];
  if (gender === 'enfant') return ['4A', '6A', '8A', '10A'];
  if (category === 'chaussures') return ['38', '40', '42', '44'];
  return ['S', 'M', 'L', 'XL'];
}

/** Park–Miller generator: small, fast, reproducible. */
function seededRandom(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state * 16807) % 2147483647;
    return state / 2147483647;
  };
}

export interface DemoFeed {
  deals: Deal[];
  fakePromosFiltered: number;
}

export function buildDemoFeed(now: Date = new Date(), seed = 42): DemoFeed {
  const random = seededRandom(seed);
  const pick = <T>(items: readonly T[]): T => items[Math.floor(random() * items.length)]!;
  const deals: Deal[] = [];
  let fakePromosFiltered = 0;
  let id = 1;

  for (const [gender, templates] of Object.entries(TEMPLATES) as [Gender, Template[]][]) {
    for (const [title, category, min, max] of templates) {
      const variants = 1 + Math.floor(random() * 2);
      for (let variant = 0; variant < variants; variant++) {
        const refPrice = Math.round((min + random() * (max - min)) / 10) * 10 - 1;
        const isRealDrop = random() < 0.82;
        const drop = 0.2 + random() * 0.3;
        const price = Math.round((refPrice * (1 - drop)) / 5) * 5 - 1;
        const history = Array.from({ length: 30 }, () =>
          Math.round(refPrice * (1 + (random() - 0.5) * 0.05)),
        );
        const claimedPrice = Math.round(refPrice * (1 + random() * 0.25));
        const lowest90d = random() < 0.55;
        const storeName = pick(storesFor(gender, category));
        const ageHours = Math.floor(random() * 72);
        const dealId = id++;

        // A "promo" whose price never moved: exactly what the scoring view hides.
        if (!isRealDrop) {
          fakePromosFiltered++;
          continue;
        }

        const discountPct = Math.round(100 * (1 - price / refPrice));
        deals.push({
          id: dealId,
          title: variant ? `${title} — édition ${EDITIONS[variant % EDITIONS.length]}` : title,
          storeName,
          gender,
          category,
          price,
          refPrice,
          claimedPrice,
          discountPct,
          claimedDiscountPct: Math.round(100 * (1 - price / claimedPrice)),
          savedMad: refPrice - price,
          score: computeScore(discountPct, lowest90d),
          lowest90d,
          verified: true,
          alwaysOnSale: storeName === 'Casa Avenue',
          history: [...history, price],
          sizes: sizesFor(gender, category),
          imageUrl: null,
          productUrl: '#',
          observedAt: new Date(now.getTime() - ageHours * 3_600_000).toISOString(),
        });
      }
    }
  }
  return { deals, fakePromosFiltered };
}
