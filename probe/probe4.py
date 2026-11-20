import re, json, time, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "fr-MA,fr;q=0.9", "Accept": "application/json"}
def get(u, **kw):
    try: return requests.get(u, headers=H, timeout=30, **kw)
    except Exception as e: print("  !!", u, type(e).__name__, str(e)[:100]); return None
BRANDS = ["pullandbear", "bershka", "stradivarius", "massimodutti", "oysho", "lefties"]
for site in BRANDS:
    j = get(f"https://www.{site}.com/itxrest/2/web/seo/config?isMobile=false&locale=fr_MA").json()
    print(f"\n### {site}")
    for mapname in ["seoParamMap", "seoCatalogMap"]:
        m = j.get(mapname, {})
        for key, sub in (m.items() if isinstance(m, dict) else []):
            if isinstance(sub, dict):
                hits = {k: v for k, v in sub.items() if isinstance(v, str) and (v.endswith("ma") or v == "ma" or "maroc" in v.lower() or v.endswith("_ma"))}
                if hits: print(f"  {mapname}.{key}: {dict(list(hits.items())[:12])}")
            elif isinstance(sub, str) and re.search(r'\bma\b', sub): print(f"  {mapname}.{key} = {sub[:120]}")
    print("  seoParamMap keys:", list(j.get("seoParamMap", {}).keys()))
