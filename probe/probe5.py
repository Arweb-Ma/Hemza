import re, json, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "fr-MA,fr;q=0.9", "Accept": "application/json"}
def get(u):
    try: return requests.get(u, headers=H, timeout=30)
    except Exception as e: print("  !!", u, type(e).__name__); return None
MA = ['35009486', '75009436', '45109536', '85009936', '55009586', '65009636', '25009436', '95009036']
SITES = {"pullandbear": "pullandbear", "bershka": "bershka", "stradivarius": "stradivarius", "massimodutti": "massimodutti", "oysho": "oysho", "lefties": "lefties"}
for site, key in SITES.items():
    j = get(f"https://www.{site}.com/itxrest/2/web/seo/config?isMobile=false&locale=fr_MA").json()
    cats = {k: v for k, v in j["seoParamMap"]["catalogId"].items() if v.startswith(key)}
    print(f"\n### {site} catalogs: {dict(list(cats.items())[:14])}")
    print("  brandId:", j["seoParamMap"].get("brandId"))
    for sid in MA:
        for path in [f"/itxrest/2/catalog/store/{sid}?languageId=-2&appId=1", f"/itxrest/2/web/store/{sid}?languageId=-2&appId=1"]:
            r = get(f"https://www.{site}.com{path}")
            if r is not None and r.status_code == 200 and len(r.text) > 50:
                t = r.text
                cat = re.findall(r'"catalogs?"\s*:\s*\[?\{?[^\]]{0,200}', t)[:1]
                print(f"  {sid} {path.split('?')[0]} -> 200 {len(t)}B name={re.findall(r'\"(?:name|countryCode|countryName)\"\s*:\s*\"[^\"]+', t)[:3]} cat={cat}")
                break
