import re, json, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "fr-MA,fr;q=0.9"}
def get(u, **kw):
    try:
        r = requests.get(u, headers={**H, **kw.pop("headers", {})}, timeout=25, **kw); return r
    except Exception as e:
        print(f"  !! {u}: {type(e).__name__} {str(e)[:120]}"); return None
def show(title, u, n=700):
    r = get(u)
    if r is None: return None
    print(f"\n### {title}: {u}\n  -> {r.status_code} {r.url} [{r.headers.get('content-type','?').split(';')[0]}] {len(r.text)}B")
    print("  " + r.text[:n].replace("\n", " "))
    return r

# 1. Inditex SPA shell: what does it load?
r = show("pull&bear shell", "https://www.pullandbear.com/ma/", 2200)
for site in ["pullandbear", "bershka", "stradivarius", "massimodutti", "oysho", "lefties"]:
    show(site + " seo config", f"https://www.{site}.com/itxrest/2/web/seo/config?isMobile=false&locale=fr_MA", 500)
    show(site + " /ma/fr", f"https://www.{site}.com/ma/fr/", 300)

# 2. Zara: one category listing, find a sale category
cats = get("https://www.zara.com/ma/fr/categories?ajax=true").json()["categories"]
flat = []
def walk(cs, path):
    for c in cs:
        flat.append((c["id"], path + "/" + c["name"], c.get("sectionName")))
        walk(c.get("subcategories", []), path + "/" + c["name"])
walk(cats, "")
print("\n### zara categories:", len(flat))
for f in flat:
    if re.search(r"SOLDE|BAISSE|PROMO|SALE|-%", f[1], re.I): print("  ", f)
print("  top:", [(c["id"], c["name"], c.get("sectionName")) for c in cats])
first = next(f for f in flat if "/FEMME/" in f[1] and "ROBES" in f[1])
r = get(f"https://www.zara.com/ma/fr/category/{first[0]}/products?ajax=true")
print(f"\n### zara products {first}: {r.status_code} {len(r.text)}B")
d = r.json()
def find_products(o, out):
    if isinstance(o, dict):
        if "price" in o and "name" in o and ("id" in o): out.append(o)
        for v in o.values(): find_products(v, out)
    elif isinstance(o, list):
        for v in o: find_products(v, out)
ps = []; find_products(d, ps)
print("  products found:", len(ps))
for p in ps[:3]:
    keep = {k: p.get(k) for k in ["id", "reference", "name", "price", "oldPrice", "displayDiscountPercentage", "sectionName", "familyName", "subfamilyName", "availability", "seo", "detail"]}
    print("  ", json.dumps(keep, ensure_ascii=False)[:900])
disc = [p for p in ps if p.get("oldPrice")]
print("  with oldPrice:", len(disc), [ (p["name"], p["price"], p["oldPrice"]) for p in disc[:3]])

# 3. Marwa (Magento): GraphQL
q = '{products(search:"robe",pageSize:2){total_count items{name sku url_key small_image{url} price_range{minimum_price{regular_price{value currency} final_price{value} discount{percent_off}}}}}}'
show("marwa graphql", "https://www.marwa.com/graphql?query=" + requests.utils.quote(q), 900)

# 4. Kiabi Shopify, Skechers Woo
show("kiabi", "https://kiabi.ma/products.json?limit=2", 600)
show("skechers woo", "https://skechers.ma/wp-json/wc/store/v1/products?per_page=2", 600)

# 5. LC Waikiki .ma: API hints
r = get("https://www.lcwaikiki.ma/")
if r is not None:
    print("\n### lcw hints:", sorted(set(re.findall(r'https?://[a-z0-9.-]*lcw[a-z0-9.-]*/[a-zA-Z0-9/_.-]{0,60}', r.text)))[:25])
    print("  api-ish:", sorted(set(re.findall(r'["\'](/(?:api|ajax|service|product|catalog)[a-zA-Z0-9/_.-]{0,60})', r.text)))[:25])
show("lcw sale page", "https://www.lcwaikiki.ma/fr-FR/MA/etiket/indirim", 300)

# 6. Etam SFCC
r = get("https://www.etam.ma/")
if r is not None:
    print("\n### etam sites:", sorted(set(re.findall(r'Sites-[A-Za-z0-9_-]+-Site', r.text)))[:5])
    print("  links:", sorted(set(re.findall(r'href="(https://www\.etam\.ma/[a-z0-9/_-]{3,50})"', r.text)))[:20])

# 7. more brand guesses
for u in ["https://www.parfois.com/fr/ma/", "https://www.calzedonia.com/ma/", "https://www.intimissimi.com/ma/",
          "https://www.us-polo-assn.ma/", "https://www.guess.ma/", "https://www.aldoshoes.ma/", "https://www.tommy.ma/",
          "https://www.defacto.ma/", "https://www.mango.ma/", "https://www.newbalance.ma/", "https://www.converse.ma/",
          "https://www.sportzone.ma/", "https://www.planetsport.ma/", "https://www.jdsports.ma/", "https://www.charleskeith.ma/",
          "https://www.lacoste.ma/", "https://www.mavi.com/ma", "https://www.colins.ma/", "https://www.celio.com/ma", "https://www.jules.ma/"]:
    r = get(u, allow_redirects=True)
    if r is not None:
        t = re.findall(r"<title>([^<]{0,80})", r.text)
        plat = [h for h in ["cdn.shopify", "woocommerce", "Magento", "demandware", "prestashop", "Just a moment", "captcha"] if h in r.text]
        print(f"  {u} -> {r.status_code} {r.url} {t[:1]} {plat}")
