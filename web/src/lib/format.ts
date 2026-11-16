/** fr-FR groups thousands with a space ("1 300"); fr-MA would print "1.300", which reads as a decimal. */
export const NUMBER_LOCALE = 'fr-FR';
const number = new Intl.NumberFormat(NUMBER_LOCALE, { maximumFractionDigits: 0 });

/** 1300 -> "1 300 DH" */
export const formatMad = (amount: number): string => `${number.format(Math.round(amount))} DH`;

export const pluralize = (count: number, singular: string, plural = `${singular}s`): string =>
  `${count} ${count > 1 ? plural : singular}`;

/** "il y a 2 h", "il y a 3 j" — coarse on purpose. */
export function timeAgo(iso: string, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60_000));
  if (minutes < 60) return `il y a ${Math.max(1, minutes)} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `il y a ${hours} h`;
  return `il y a ${Math.round(hours / 24)} j`;
}
