import type { Crumb } from '@/components/Breadcrumb.astro';

export function breadcrumbJsonLd(items: Crumb[], site: URL | undefined, currentPath: string) {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name: item.label,
      item: new URL(item.href ?? currentPath, site).href,
    })),
  };
}
