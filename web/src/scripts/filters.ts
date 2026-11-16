/**
 * Client-side filtering and sorting of the deal grid on the current page.
 * The grid is server-rendered; this only hides and reorders existing cards.
 */
type SortKey = 'score' | 'saved' | 'discount' | 'price' | 'recent';

const number = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

const comparators: Record<SortKey, (a: HTMLElement, b: HTMLElement) => number> = {
  score: (a, b) =>
    Number('verified' in b.dataset) - Number('verified' in a.dataset) ||
    num(b, 'score') - num(a, 'score') ||
    num(b, 'saved') - num(a, 'saved'),
  saved: (a, b) => num(b, 'saved') - num(a, 'saved'),
  discount: (a, b) => num(b, 'discount') - num(a, 'discount'),
  price: (a, b) => num(a, 'price') - num(b, 'price'),
  recent: (a, b) => (b.dataset.observed ?? '').localeCompare(a.dataset.observed ?? ''),
};

function num(card: HTMLElement, key: string): number {
  return Number(card.dataset[key] ?? 0);
}

export function initFilters(): void {
  const form = document.querySelector<HTMLFormElement>('[data-filters]');
  const grid = document.querySelector<HTMLElement>('[data-deal-grid]');
  if (!form || !grid) return;

  const cards = Array.from(grid.querySelectorAll<HTMLElement>('[data-deal]'));
  const count = document.querySelector<HTMLElement>('[data-deal-count]');
  const empty = document.querySelector<HTMLElement>('[data-deal-empty]');
  const fields = form.elements as unknown as {
    q: HTMLInputElement;
    max: HTMLInputElement;
    maxOut: HTMLOutputElement;
    lowest: HTMLInputElement;
    sort: HTMLSelectElement;
  };

  function apply(): void {
    const query = fields.q.value.trim().toLowerCase();
    const maxPrice = Number(fields.max.value);
    const lowestOnly = fields.lowest.checked;
    fields.maxOut.value = `${number.format(maxPrice)} DH`;

    let visible = 0;
    for (const card of cards) {
      const show =
        num(card, 'price') <= maxPrice &&
        (!lowestOnly || 'lowest' in card.dataset) &&
        (!query || (card.dataset.search ?? '').includes(query));
      card.hidden = !show;
      if (show) visible++;
    }

    const sorted = [...cards].sort(comparators[fields.sort.value as SortKey] ?? comparators.score);
    grid!.append(...sorted);

    if (count) count.textContent = `${visible} promo${visible > 1 ? 's' : ''}`;
    if (empty) empty.hidden = visible > 0;
  }

  form.addEventListener('input', apply);
  form.addEventListener('submit', (event) => event.preventDefault());
  form.hidden = false;
}
