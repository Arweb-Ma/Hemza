/**
 * Client-side filtering and sorting of the deal grid on the current page.
 * The grid is server-rendered; this only hides and reorders existing cards.
 * The visitor's choices (not the search text) are remembered in this browser.
 */
type SortKey = 'score' | 'saved' | 'discount' | 'price' | 'recent';

interface Fields {
  q: HTMLInputElement;
  sort: HTMLSelectElement;
  minDiscount: HTMLSelectElement;
  store?: HTMLSelectElement;
  max: HTMLInputElement;
  maxOut: HTMLOutputElement;
  verifiedOnly: HTMLInputElement;
  hideAlwaysOnSale: HTMLInputElement;
}

const STORAGE_KEY = 'hemza:filters';
const number = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

const num = (card: HTMLElement, key: string): number => Number(card.dataset[key] ?? 0);
const has = (card: HTMLElement, key: string): boolean => key in card.dataset;

const comparators: Record<SortKey, (a: HTMLElement, b: HTMLElement) => number> = {
  score: (a, b) =>
    Number(has(b, 'verified')) - Number(has(a, 'verified')) ||
    num(b, 'score') - num(a, 'score') ||
    num(b, 'saved') - num(a, 'saved'),
  saved: (a, b) => num(b, 'saved') - num(a, 'saved'),
  discount: (a, b) => num(b, 'discount') - num(a, 'discount'),
  price: (a, b) => num(a, 'price') - num(b, 'price'),
  recent: (a, b) => (b.dataset.observed ?? '').localeCompare(a.dataset.observed ?? ''),
};

interface Saved {
  sort?: string;
  minDiscount?: string;
  store?: string;
  verifiedOnly?: boolean;
  hideAlwaysOnSale?: boolean;
}

function load(): Saved {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as Saved;
  } catch {
    return {};
  }
}

function save(fields: Fields): void {
  try {
    const saved: Saved = {
      sort: fields.sort.value,
      minDiscount: fields.minDiscount.value,
      store: fields.store?.value,
      verifiedOnly: fields.verifiedOnly.checked,
      hideAlwaysOnSale: fields.hideAlwaysOnSale.checked,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  } catch {
    // Storage unavailable (private mode, blocked): filters still work for this visit.
  }
}

/** Select an option only if this page offers it (a shop may not be in every listing). */
function restoreSelect(select: HTMLSelectElement | undefined, value: string | undefined): void {
  if (select && value !== undefined && [...select.options].some((o) => o.value === value)) {
    select.value = value;
  }
}

export function initFilters(): void {
  const form = document.querySelector<HTMLFormElement>('[data-filters]');
  const grid = document.querySelector<HTMLElement>('[data-deal-grid]');
  if (!form || !grid) return;

  const cards = Array.from(grid.querySelectorAll<HTMLElement>('[data-deal]'));
  const count = document.querySelector<HTMLElement>('[data-deal-count]');
  const empty = document.querySelector<HTMLElement>('[data-deal-empty]');
  const fields = form.elements as unknown as Fields;

  const saved = load();
  restoreSelect(fields.sort, saved.sort);
  restoreSelect(fields.minDiscount, saved.minDiscount);
  restoreSelect(fields.store, saved.store);
  fields.verifiedOnly.checked = saved.verifiedOnly ?? false;
  fields.hideAlwaysOnSale.checked = saved.hideAlwaysOnSale ?? false;

  function apply(): void {
    const query = fields.q.value.trim().toLowerCase();
    const maxPrice = Number(fields.max.value);
    const minDiscount = Number(fields.minDiscount.value);
    const store = fields.store?.value ?? '';
    const verifiedOnly = fields.verifiedOnly.checked;
    const hideAlwaysOnSale = fields.hideAlwaysOnSale.checked;
    fields.maxOut.value = `${number.format(maxPrice)} DH`;

    let visible = 0;
    for (const card of cards) {
      const show =
        num(card, 'price') <= maxPrice &&
        num(card, 'discount') >= minDiscount &&
        (!store || card.dataset.store === store) &&
        (!verifiedOnly || has(card, 'verified')) &&
        // A verified drop is a real drop, even in a shop that is always "on sale".
        (!hideAlwaysOnSale || !has(card, 'alwaysOnSale') || has(card, 'verified')) &&
        (!query || (card.dataset.search ?? '').includes(query));
      card.hidden = !show;
      if (show) visible++;
    }

    const sorted = [...cards].sort(comparators[fields.sort.value as SortKey] ?? comparators.score);
    grid!.append(...sorted);

    if (count) count.textContent = `${visible} promo${visible > 1 ? 's' : ''}`;
    if (empty) empty.hidden = visible > 0;
  }

  form.addEventListener('input', () => {
    apply();
    save(fields);
  });
  form.addEventListener('submit', (event) => event.preventDefault());
  // "reset" fires before the form restores its defaults: re-apply on the next tick.
  form.addEventListener('reset', () =>
    setTimeout(() => {
      apply();
      save(fields);
    }),
  );
  apply();
  form.hidden = false;
}
