import re, json, time, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "fr-MA,fr;q=0.9", "Accept": "application/json"}
def get(u):
    try: return requests.get(u, headers=H, timeout=40)
    except Exception as e: print("  !!", u, type(e).__name__); return None
STORES = {"bershka": (45109536, 40259559), "pullandbear": (25009436, 20309459), "stradivarius": (55009586, 50331101),
          "massimodutti": (35009486, 30359498), "oysho": (65009636, 60361120), "lefties": (95009036, 90009051)}
for site, (sid, cid) in STORES.items():
    base = f"https://www.{site}.com/itxrest"
    t = time.time()
    r = get(f"{base}/2/catalog/store/{sid}/{cid}/category?languageId=-2&typeCatalog=1&appId=1")
    print(f"\n### {site}: categories {r.status_code if r is not None else None} {len(r.text) if r is not None else 0}B {time.time()-t:.1f}s")
    if r is None or r.status_code != 200: 
        print("   ", r.text[:300] if r is not None else ""); continue
    cats = r.json().get("categories", [])
    leaves = []
    def walk(cs, path, d):
        for c in cs:
            p = path + "/" + (c.get("name") or "")
            if d <= 1: print(f"   {'  '*d}{c['id']} {c.get('name')} key={c.get('key')} subs={len(c.get('subcategories') or [])} viewCategoryId={c.get('viewCategoryId')}")
            sub = c.get("subcategories") or []
            if not sub: leaves.append((c["id"], p))
            walk(sub, p, d + 1)
    walk(cats, "", 0)
    print(f"   leaves: {len(leaves)}; sale-ish:", [l for l in leaves if re.search(r'SALE|SOLDE|PROMO|BAISSE|REBAJ|OFFRE|-\d+%', l[1], re.I)][:6])
    leaf = next((l for l in leaves if re.search(r'ROBE|DRESS|JEAN|PANTALON', l[1], re.I)), leaves[0])
    r = get(f"{base}/3/catalog/store/{sid}/{cid}/category/{leaf[0]}/product?languageId=-2&appId=1&showProducts=false")
    print(f"   category {leaf}: {r.status_code} keys={list(r.json().keys())[:8] if r.status_code==200 else r.text[:200]}")
    if r.status_code != 200: continue
    ids = r.json().get("productIds") or []
    print(f"   productIds: {len(ids)}")
    r = get(f"{base}/2/catalog/store/{sid}/{cid}/productsArray?productIds={','.join(map(str, ids[:30]))}&languageId=-2&appId=1")
    print(f"   productsArray: {r.status_code} {len(r.text)}B")
    if r.status_code != 200: print("   ", r.text[:300]); continue
    prods = r.json().get("products", [])
    p = prods[0]
    print("   product keys:", list(p.keys())[:30])
    bd = p.get("bundleProductSummaries") or []
    det = (bd[0] if bd else p).get("detail") or {}
    print("   detail keys:", list(det.keys())[:30])
    colors = det.get("colors") or []
    if colors:
        c0 = colors[0]; s0 = (c0.get("sizes") or [{}])[0]
        print("   color keys:", list(c0.keys())[:20], "| size:", {k: s0.get(k) for k in ["name", "price", "oldPrice", "visibilityValue", "sku"]})
        print("   image:", json.dumps(c0.get("image"))[:300] if c0.get("image") else None, "| xmedia:", json.dumps(det.get("xmedia"))[:300])
    print("   name/url:", p.get("name"), p.get("productUrl"), p.get("nameEn"), (bd[0] if bd else p).get("productUrl"))
    disc = 0
    for q in prods:
        for b in (q.get("bundleProductSummaries") or [q]):
            for c in (b.get("detail") or {}).get("colors") or []:
                for s in c.get("sizes") or []:
                    if s.get("oldPrice"): disc += 1
    print(f"   sizes with oldPrice among {len(prods)} products: {disc}")
