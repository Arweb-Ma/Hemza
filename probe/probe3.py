import re, json, time, requests, subprocess, sys
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "fr-MA,fr;q=0.9", "Accept": "application/json"}
def get(u):
    try: return requests.get(u, headers=H, timeout=30)
    except Exception as e: print("  !!", u, type(e).__name__, str(e)[:100]); return None

print("### ZARA tree")
cats = get("https://www.zara.com/ma/fr/categories?ajax=true").json()["categories"]
def walk(cs, depth, path):
    for c in cs:
        if depth <= 3 and c.get("sectionName") in ("WOMAN", "MAN", "KID") or depth == 0:
            sub = c.get("subcategories", [])
            print(f"{'  '*depth}{c['id']} {c['name']} [{c.get('sectionName')}] layout={c.get('layout')} subs={len(sub)} redirect={c.get('redirectCategoryId')}")
            walk(sub, depth + 1, path + [c["name"]])
walk(cats, 0, [])
for cid in [2419737]:
    t = time.time(); r = get(f"https://www.zara.com/ma/fr/category/{cid}/products?ajax=true")
    d = r.json()
    groups = d.get("productGroups", [])
    items = [e for g in groups for el in g.get("elements", []) for e in el.get("commercialComponents", [])]
    print(f"\n### zara {cid}: {r.status_code} {len(r.text)//1024}KB {time.time()-t:.1f}s groups={len(groups)} items={len(items)}")
    print("  keys:", list(d.keys())[:10], "| item keys:", list(items[0].keys())[:40] if items else None)
    od = [i for i in items if i.get("oldPrice")]
    print("  with oldPrice:", len(od), [(i["name"], i["price"], i["oldPrice"], i.get("sectionName"), i.get("familyName")) for i in od[:5]])

print("\n### INDITEX store ids")
for site in ["pullandbear", "bershka", "stradivarius", "massimodutti", "oysho", "lefties"]:
    r = get(f"https://www.{site}.com/itxrest/2/web/seo/config?isMobile=false&locale=fr_MA")
    j = r.json()
    print(f"\n{site}: top keys {list(j.keys())[:20]}")
    txt = json.dumps(j)
    for m in re.finditer(r'.{0,120}"(?:ma|MA|fr_MA|ar_MA)".{0,160}', txt):
        print("   ", m.group(0)[:280]); break
    for k, v in j.items():
        if isinstance(v, (dict, list)) and re.search(r'"(?:MA|ma)"', json.dumps(v)):
            s = json.dumps(v); i = max(0, s.find('"MA"') - 200)
            print(f"   [{k}] …{s[i:i+400]}")
    # common guesses
    for path in [f"/itxrest/2/web/seo/config?isMobile=false&locale=fr_MA&country=MA",
                 "/itxrest/2/bam/store/ma", "/itxrest/2/web/geo/countryCode",
                 "/itxrest/1/web/seo/config/store/ma"]:
        rr = get(f"https://www.{site}.com{path}")
        if rr is not None: print(f"   {path} -> {rr.status_code} {rr.text[:200]!r}")

print("\n### skechers wp-json")
for u in ["https://skechers.ma/wp-json/", "https://skechers.ma/wp-json/wc/store/products?per_page=2"]:
    r = get(u)
    if r is not None: print(" ", u, r.status_code, r.text[:400].replace("\n", " "))

print("\n### LCW")
r = get("https://www.lcwaikiki.ma/fr-FR/MA")
if r is not None:
    print("  ", r.status_code, r.url, len(r.text))
    links = sorted(set(re.findall(r'href="(/[a-zA-Z-]+/MA/[^"#?]{3,80})"', r.text)))
    print("   links:", links[:40])
    if links:
        r2 = get("https://www.lcwaikiki.ma" + links[min(10, len(links)-1)])
        if r2 is not None:
            print("   page", r2.status_code, r2.url, len(r2.text))
            for pat in [r'"price"\s*:\s*"?[\d.,]+', r'data-[a-z-]*price[a-z-]*="[^"]+"', r'"discount[A-Za-z]*"\s*:\s*[^,]{0,30}', r'window\.[A-Za-z_]+\s*=\s*\{.{0,100}', r'cart-square-price[^<]{0,80}', r'/(?:api|ajax)/[A-Za-z0-9/_-]{3,60}']:
                print("   ", pat[:24], re.findall(pat, r2.text)[:4])

print("\n### Etam SFCC")
for u in ["https://www.etam.ma/c/soldes_ma/?sz=3&format=ajax", "https://www.etam.ma/c/soldes_ma/"]:
    r = get(u)
    if r is not None:
        print("  ", u, r.status_code, len(r.text))
        for pat in [r'data-gtm[a-z-]*="[^"]{0,300}', r'class="[^"]*price[^"]*"[^>]*>[^<]{0,40}', r'"price"\s*:\s*\{[^}]{0,120}', r'data-pid="[^"]+"', r'(\d[\d ,.]*)\s*(?:MAD|DH|Dhs)']:
            print("    ", pat[:20], re.findall(pat, r.text)[:3])
sys.stdout.flush()
print("\n### detect with existing readers")
subprocess.run([sys.executable, "collector/collect.py", "--detect", "kiabi.ma", "aldoshoes.ma", "lacoste.ma", "skechers.ma"])
