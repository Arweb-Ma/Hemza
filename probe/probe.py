"""Temporary: which big-brand Moroccan shops answer a server, and on what platform."""
import re, sys, json, requests
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "fr-MA,fr;q=0.9"}
URLS = sys.argv[1:] or [l.strip() for l in open("probe/urls.txt") if l.strip() and not l.startswith("#")]
HINTS = ["MAD", "DH", "storeId", "__NEXT_DATA__", "__NUXT__", "graphql", "Magento", "mage/", "cdn.shopify",
         "woocommerce", "prestashop", "demandware", "salesforce", "vtex", "algolia", "akamai", "_abck",
         "cf-chl", "captcha", "Just a moment", "catalogId", "productsApi", "itxrest", "window.__STORE__",
         "application/ld+json", "occ/v2", "hybris", "SAP", "spree", "nuxt", "next/static", "bloomreach", "constructor.io"]
for url in URLS:
    try:
        r = requests.get(url, headers=H, timeout=25, allow_redirects=True)
        body = r.text
        found = [h for h in HINTS if h in body]
        srv = {k: r.headers.get(k) for k in ("server", "x-powered-by", "cf-ray", "x-akamai-transformed", "x-magento-cache-debug") if r.headers.get(k)}
        print(f"\n### {url}\n  -> {r.status_code} {r.url} [{r.headers.get('content-type','?').split(';')[0]}] {len(body)}B {srv}")
        print(f"  hints: {found}")
        for pat in [r'"storeId"\s*:\s*"?(\d+)', r'"catalogId"\s*:\s*"?(\d+)', r'https?://[a-z0-9.-]*/[a-z0-9/_-]*graphql',
                    r'"(?:price|salePrice|oldPrice|originalPrice)"\s*:\s*"?[\d.]+', r'<title>([^<]{0,90})']:
            m = re.findall(pat, body)[:3]
            if m: print(f"  {pat[:28]}: {m}")
        if "json" in (r.headers.get("content-type") or ""):
            print("  json:", body[:600].replace("\n", " "))
    except Exception as e:
        print(f"\n### {url}\n  !! {type(e).__name__}: {str(e)[:150]}")
