"""
Catalog readers, one per e-commerce platform. Each reads ONE page of a store's catalog and
returns products in the same shape, so the rest of the collector does not care where they came from.

  fetch(store, cursor) -> ("ok", items, next_cursor)        next_cursor is None on the last page
                        | ("throttled", retry_after_seconds, detail)
                        | ("error", detail)

Shopify      /products.json                      public catalog feed
WooCommerce  /wp-json/wc/store/v1/products       the official public Store API
PrestaShop   listing pages asked for as JSON     the same data the shop's own "load more" uses
             (EXPERIMENTAL: depends on the shop's version and theme)
Magento      /graphql                            the public GraphQL catalog (Marwa)
Zara         /ma/fr/category/<id>/products       the JSON behind zara.com/ma category pages
LC Waikiki   sale listing pages (?page=N)        the `catalogModel` JSON embedded in each listing page
Decathlon    the "Promotions" listing, in a browser   PrestaShop JSON if offered, else the rendered cards
Magento page category pages (?p=N), in a browser     standard Magento price attributes on each card
"""
import html, json, os, re, tempfile
from urllib.parse import quote
import requests

from classify import classify

UA = "HemzaBot/0.1 (+https://hemza.ma/bot; contact@arweb.ma)"
TIMEOUT = 30
MAX_PAGES = 40
DIAGNOSTIC_HEADERS = ("retry-after", "server", "cf-mitigated", "cf-ray", "x-shopify-stage", "x-request-id")


def describe(r):
    """One line explaining an unexpected answer, to tell a rate limit from a bot challenge."""
    seen = [f"{h}={r.headers[h]}" for h in DIAGNOSTIC_HEADERS if h in r.headers]
    kind = (r.headers.get("content-type") or "?").split(";")[0]
    return f"HTTP {r.status_code} [{kind}] " + " ".join(seen)


def get_json(url, headers=None):
    """Returns ("ok", data, response) or a ("throttled" | "error", ...) tuple ready to hand back."""
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})},
                         timeout=TIMEOUT)
    except requests.RequestException as e:
        return ("error", str(e))
    if r.status_code in (429, 430, 503):
        asked = r.headers.get("Retry-After", "")
        return ("throttled", int(asked) if asked.isdigit() else None, describe(r))
    if r.status_code != 200:
        return ("error", describe(r))
    try:
        return ("ok", r.json(), r)
    except ValueError:
        return ("error", "answered with a web page, not data (wrong platform, or a challenge page) " + describe(r))


def get_text(url):
    """Like get_json, for HTML pages. Returns ("ok", text, response) or an error tuple."""
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=TIMEOUT)
    except requests.RequestException as e:
        return ("error", str(e))
    if r.status_code in (429, 430, 503):
        asked = r.headers.get("Retry-After", "")
        return ("throttled", int(asked) if asked.isdigit() else None, describe(r))
    if r.status_code != 200:
        return ("error", describe(r))
    return ("ok", r.text, r)


def item(store, *, external_id, title, url, price, compare_at, in_stock, image=None, brand=None,
         sizes=(), handle=None, text=(), gender=None):
    """Builds the common product record. `text` is extra wording used only for classification.
    `gender` forces the section when the shop states it (e.g. Zara's KID section holds dresses too)."""
    title = html.unescape(title or "").strip()
    guessed, category = classify(title, " ".join(t for t in text if t), [],
                                 store.get("gender_hint"), store.get("category_hint"))
    gender = gender or guessed
    return {
        "external_id": str(external_id), "handle": handle, "title": title, "brand": brand or None,
        "gender": gender, "category": category, "image_url": image or None, "product_url": url,
        "sizes": [str(s) for s in sizes][:12], "in_stock": bool(in_stock),
        "_price": float(price),
        "_compare_at": float(compare_at) if compare_at and float(compare_at) > float(price) else None,
    }


# ------------------------------------------------------------------ Shopify
SHOPIFY_LIMIT = 250

def shopify(store, cursor, sample=False):
    page = cursor or 1
    limit = 3 if sample else SHOPIFY_LIMIT
    result = get_json(f"https://{store['domain']}/products.json?limit={limit}&page={page}")
    if result[0] != "ok":
        return result
    data = result[1]
    if not isinstance(data, dict) or "products" not in data:
        return ("error", "no Shopify product feed here")
    items = []
    for p in data["products"]:
        variants = p.get("variants") or []
        if not variants:
            continue
        available = [v for v in variants if v.get("available")]
        best = min(available or variants, key=lambda v: float(v.get("price") or 1e9))
        compare_at = best.get("compare_at_price")
        items.append(item(
            store, external_id=p["id"], handle=p.get("handle"), title=p.get("title"),
            url=f"https://{store['domain']}/products/{p.get('handle')}",
            price=best["price"], compare_at=compare_at if compare_at not in (None, "", "0.00") else None,
            in_stock=bool(available), image=(p.get("images") or [{}])[0].get("src"), brand=p.get("vendor"),
            sizes=sorted({v.get("option1") for v in available if v.get("option1")}),
            text=[p.get("product_type"), " ".join(p.get("tags") or [])],
        ))
    more = len(data["products"]) == SHOPIFY_LIMIT and page < MAX_PAGES
    return ("ok", items, page + 1 if more else None)


# ------------------------------------------------------------------ WooCommerce
WOO_LIMIT = 100
SIZE_WORDS = ("taille", "size", "pointure", "مقاس")

# Store API paths, newest first. Older WooCommerce versions only answer the unversioned one.
WOO_PATHS = ("wp-json/wc/store/v1/products", "wp-json/wc/store/products")

def woocommerce(store, cursor, sample=False):
    page = cursor or 1
    limit = 3 if sample else WOO_LIMIT
    for path in WOO_PATHS:
        result = get_json(f"https://{store['domain']}/{path}?per_page={limit}&page={page}")
        if not (result[0] == "error" and result[1].startswith("HTTP 404")):
            break
    if result[0] != "ok":
        return result
    data = result[1]
    if not isinstance(data, list):
        return ("error", "no WooCommerce Store API here")
    items = []
    for p in data:
        prices = p.get("prices") or {}
        if not prices.get("price"):
            continue
        unit = 10 ** int(prices.get("currency_minor_unit") or 0)     # prices come in minor units: "70000" = 700.00
        sizes = [term.get("name") for attribute in p.get("attributes") or []
                 if any(w in (attribute.get("name") or "").lower() for w in SIZE_WORDS)
                 for term in attribute.get("terms") or []]
        items.append(item(
            store, external_id=p["id"], handle=p.get("slug"), title=p.get("name"), url=p.get("permalink"),
            price=int(prices["price"]) / unit,
            compare_at=int(prices["regular_price"]) / unit if prices.get("regular_price") else None,
            in_stock=p.get("is_in_stock", True), image=(p.get("images") or [{}])[0].get("src"),
            brand=((p.get("brands") or [{}])[0]).get("name"), sizes=sizes,
            text=[c.get("name") for c in (p.get("categories") or []) + (p.get("tags") or [])],
        ))
    total = (getattr(result[2], "headers", None) or {}).get("X-WP-TotalPages")
    # Some shops cap the page size below what we ask for: trust the page count the API announces.
    more = (page < int(total)) if str(total or "").isdigit() else len(data) == WOO_LIMIT
    return ("ok", items, page + 1 if more and page < MAX_PAGES else None)


# ------------------------------------------------------------------ PrestaShop (experimental)
# Listing pages to read. "Home" (category 2) usually holds the whole catalog; "prices-drop" lists
# everything on promotion. A store can override this with "paths" in stores.json.
PRESTA_PATHS = ["index.php?controller=category&id_category=2", "index.php?controller=prices-drop"]

def prestashop(store, cursor, sample=False):
    paths = store.get("paths") or PRESTA_PATHS
    index, page = cursor or (0, 1)
    path = paths[index].lstrip("/")
    url = f"https://{store['domain']}/{path}{'&' if '?' in path else '?'}page={page}&from-xhr"
    result = get_json(url, {"X-Requested-With": "XMLHttpRequest"})
    if result[0] != "ok":
        # A missing listing is not fatal while other listings remain.
        if result[0] == "error" and index + 1 < len(paths) and not sample:
            return ("ok", [], (index + 1, 1))
        return result
    data = result[1]
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        return ("error", "no PrestaShop JSON listing here")
    items = presta_items(store, data["products"])
    if sample:
        return ("ok", items, None)
    pages = int((data.get("pagination") or {}).get("pages_count") or 1)
    if page < min(pages, MAX_PAGES):
        return ("ok", items, (index, page + 1))
    return ("ok", items, (index + 1, 1) if index + 1 < len(paths) else None)


def money_text(text):
    """'1 299,00 MAD' / '1.299,00 DH' / '799 MAD' → 1299.0 / 1299.0 / 799.0 (None when not a price)."""
    if isinstance(text, (int, float)):
        return float(text)
    match = re.search(r"\d[\d\s\u202f\u00a0.,]*", str(text or ""))
    if not match:
        return None
    value = re.sub(r"[\s\u202f\u00a0]", "", match.group(0)).rstrip(".,")
    value = value.replace(".", "").replace(",", ".") if re.search(r",\d{1,2}$", value) else value.replace(",", "")
    try:
        return float(value)
    except ValueError:
        return None


def presta_items(store, products):
    """PrestaShop listing JSON → product records (shared by the plain and browser readers)."""
    items = []
    for p in products:
        if not isinstance(p, dict):
            continue
        # Themes differ: amounts as numbers (price_amount) or only as text ("1 299,00 MAD").
        price = p.get("price_amount")
        if price is None:
            price = money_text(p.get("price"))
        regular = p.get("regular_price_amount")
        if regular is None:
            regular = money_text(p.get("regular_price"))
        product_id = p.get("id_product") or p.get("id")
        product_url = p.get("url") or p.get("link") or p.get("canonical_url")
        if price is None or product_id is None or not product_url:
            continue
        cover = (p.get("cover") or {}) if isinstance(p.get("cover"), dict) else {}
        image = ((cover.get("bySize") or {}).get("home_default") or cover.get("large") or {}).get("url")
        discounted = p.get("has_discount", regular is not None and float(regular) > float(price))
        items.append(item(
            store, external_id=product_id, handle=p.get("link_rewrite"), title=p.get("name"), url=product_url,
            price=price, compare_at=regular if discounted else None,
            in_stock=p.get("availability") != "unavailable", image=image, brand=p.get("manufacturer_name"),
            text=[p.get("category_name")],
        ))
    return items


# ------------------------------------------------------------------ Magento (GraphQL)
MAGENTO_LIMIT = 100
MAGENTO_QUERY = (
    '{products(search:"",pageSize:%d,currentPage:%d){page_info{total_pages} items{'
    'id sku name url_key url_suffix stock_status small_image{url} categories{name} '
    'price_range{minimum_price{regular_price{value} final_price{value}}}}}}'
)

def magento(store, cursor, sample=False):
    page = cursor or 1
    limit = 3 if sample else MAGENTO_LIMIT
    result = shop_json(store, f"https://{store['domain']}/graphql?query={quote(MAGENTO_QUERY % (limit, page))}")
    if result[0] != "ok":
        return result
    data = ((result[1] or {}).get("data") or {}).get("products") if isinstance(result[1], dict) else None
    if not isinstance(data, dict):
        return ("error", "no Magento GraphQL catalog here")
    items = []
    for p in data.get("items") or []:
        prices = ((p.get("price_range") or {}).get("minimum_price") or {})
        price = (prices.get("final_price") or {}).get("value")
        regular = (prices.get("regular_price") or {}).get("value")
        if price is None or not p.get("url_key"):
            continue
        items.append(item(
            store, external_id=p.get("id") or p.get("sku"), handle=p.get("url_key"), title=p.get("name"),
            url=f"https://{store['domain']}/{p['url_key']}{p.get('url_suffix') or '.html'}",
            price=price, compare_at=regular, in_stock=p.get("stock_status") != "OUT_OF_STOCK",
            image=(p.get("small_image") or {}).get("url"), brand=store.get("name"),
            text=[c.get("name") for c in p.get("categories") or []],
        ))
    if sample:
        return ("ok", items, None)
    pages = int((data.get("page_info") or {}).get("total_pages") or 1)
    return ("ok", items, page + 1 if page < min(pages, int(store.get("max_pages") or MAX_PAGES)) else None)


# ------------------------------------------------------------------ Zara
# Plain HTTP from a server is refused by zara.com, so the store can be marked `"via": "browser"`:
# the same JSON is then asked for from inside a Chromium tab opened on zara.com/ma (browser.py).
def shop_json(store, url, headers=None):
    if store.get("via") == "browser":
        return browser_reader().get_json(store, url, headers)
    return get_json(url, headers)


ZARA_BASE = "https://www.zara.com/ma/fr"
ZARA_SECTIONS = {"WOMAN": "femme", "MAN": "homme", "KID": "enfant"}
ZARA_SKIP_FAMILIES = {"PERFUME", "PERFUMES", "FRAGRANCE", "COSMETICS", "MAKE UP", "MAKEUP", "BEAUTY"}
ZARA_LISTINGS = {"TOUT VOIR", "TOUT AFFICHER"}

def zara_targets(categories):
    """Category ids worth reading: every "TOUT VOIR" listing of the women, men and kids sections,
    price-drop listings first. A section without such listings falls back to its leaf categories."""
    sale, listings = [], []
    for root in categories:
        section = root.get("sectionName")
        if section not in ZARA_SECTIONS:
            continue
        found, leaves = [], []
        def walk(nodes, sale_branch):
            for c in nodes:
                name = (c.get("name") or "").strip().upper()
                on_sale = sale_branch or "BAISSE DE PRIX" in name or "SOLDES" in name
                subs = c.get("subcategories") or []
                if subs:
                    walk(subs, on_sale)
                elif c.get("layout") == "products-category-view":
                    leaves.append(c["id"])
                    if on_sale:
                        sale.append(c["id"])
                    elif name in ZARA_LISTINGS:
                        found.append(c["id"])
        walk(root.get("subcategories") or [], False)
        listings += found or leaves
    seen, ordered = set(), []
    for cid in sale + listings:
        if cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    return ordered

def zara_image(product):
    for media in (product.get("xmedia") or []) + [
            m for c in ((product.get("detail") or {}).get("colors") or [])[:1] for m in c.get("xmedia") or []]:
        if media.get("url"):
            return media["url"].replace("{width}", "563")
        if media.get("path") and media.get("name"):
            return f"https://static.zara.net{media['path']}/{media['name']}.jpg?ts={media.get('timestamp', '')}&w=563"
    return None

def zara(store, cursor, sample=False):
    if cursor is None:
        result = shop_json(store, f"{ZARA_BASE}/categories?ajax=true")
        if result[0] != "ok":
            return result
        targets = zara_targets((result[1] or {}).get("categories") or []) if isinstance(result[1], dict) else []
        if not targets:
            return ("error", "no Zara category tree here")
        cursor = {"targets": targets[:1] if sample else targets, "index": 0, "seen": set()}
    targets, index, seen = cursor["targets"], cursor["index"], cursor["seen"]
    result = shop_json(store, f"{ZARA_BASE}/category/{targets[index]}/products?ajax=true")
    if result[0] != "ok":
        if result[0] == "error" and index + 1 < len(targets) and not sample:
            return ("ok", [], {**cursor, "index": index + 1})       # one broken listing is not fatal
        return result
    # Zara lists each colour of a product as its own card. We keep one product per product page
    # (seoProductId, so its id stays the same whatever colour comes first): its cheapest colour.
    best = {}
    for group in (result[1] or {}).get("productGroups") or []:
        for element in group.get("elements") or []:
            for p in element.get("commercialComponents") or []:
                seo, price = p.get("seo") or {}, p.get("price")
                page_id = str(seo.get("seoProductId") or p.get("id"))
                if price is None or not seo.get("keyword") or page_id in seen:
                    continue
                if (p.get("familyName") or "").upper() in ZARA_SKIP_FAMILIES:
                    continue
                if page_id not in best or price < best[page_id]["price"]:
                    best[page_id] = p
    items = []
    for page_id, p in best.items():
        seen.add(page_id)
        seo, price, old = p.get("seo") or {}, p.get("price"), p.get("oldPrice")
        items.append(item(
            store, external_id=page_id, handle=seo.get("keyword"), title=p.get("name"),
            url=f"{ZARA_BASE}/{seo['keyword']}-p{seo.get('seoProductId')}.html?v1={seo.get('discernProductId', p['id'])}",
            price=price / 100, compare_at=old / 100 if old else None,
            in_stock=p.get("availability") not in ("out_of_stock", "coming_soon"),
            image=zara_image(p), brand="Zara",
            gender=ZARA_SECTIONS.get(p.get("sectionName")),
            text=[p.get("familyName"), p.get("subfamilyName")],
        ))
    if sample:
        return ("ok", items[:3], None)
    return ("ok", items, {**cursor, "index": index + 1} if index + 1 < len(targets) else None)


# ------------------------------------------------------------------ LC Waikiki
# Listing pages embed their products as `var catalogModel = {...}`. We read the sale listings the
# home page links to (plus any "paths" from stores.json) and follow ?page=N (robots.txt allows `page`,
# not `PageIndex`).
LCW_MARKER = "var catalogModel = "
LCW_SALE_LINK = re.compile(r'href="(/fr/(?:mvc|campagnes)/[^"?#]*(?:sale|discount|solde|promo)[^"?#]*)"', re.I)
LCW_SECTIONS = (("enfant", r"enfant|fille|gar[cç]on|b[ée]b[ée]|adolescent|kids?|girls?|boys?|baby"),
                ("femme", r"femme|women"), ("homme", r"homme|men"))

def lcw_model(text):
    start = text.find(LCW_MARKER)
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, start + len(LCW_MARKER))[0]
    except ValueError:
        return None

def lcw_gender(p):
    label = " ".join(str(p.get(k) or "") for k in ("ProductGroup", "Gender"))
    return next((g for g, rx in LCW_SECTIONS if re.search(rx, label, re.I)), None)

def lcwaikiki(store, cursor, sample=False):
    base = f"https://{store['domain']}"
    if cursor is None:
        result = get_text(f"{base}/fr")
        if result[0] != "ok":
            return result
        targets = list(dict.fromkeys((store.get("paths") or []) + LCW_SALE_LINK.findall(result[1])))
        if not targets:
            return ("error", "no LC Waikiki sale listings found on the home page")
        cursor = {"targets": targets[:1] if sample else targets, "index": 0, "page": 1, "seen": set()}
    targets, index, page, seen = cursor["targets"], cursor["index"], cursor["page"], cursor["seen"]
    following = {**cursor, "index": index + 1, "page": 1} if index + 1 < len(targets) else None
    result = get_text(f"{base}{targets[index]}" + (f"?page={page}" if page > 1 else ""))
    if result[0] != "ok":
        if result[0] == "error" and following and not sample:
            return ("ok", [], following)                                # one dead listing is not fatal
        return result
    model = lcw_model(result[1])
    if model is None:
        return ("ok", [], following) if following and not sample else ("error", "no catalogModel on the page")
    products = ((model.get("CatalogList") or {}).get("Items")) or []
    items = []
    for p in products:
        key, url, regular = p.get("OptionId") or p.get("ModelId"), p.get("ModelUrl"), p.get("PriceValue")
        if key in seen or not url or not regular:
            continue
        seen.add(key)
        # The sale price is either on the product or, on sale listings, on its "Produits Soldés" badge.
        offers = [p.get("DiscountedPriceValue") or 0] + [
            b.get("DiscountedPrice") or 0 for b in p.get("CampaignBadges") or [] if b.get("ShowDiscountedPrice")]
        sale = min([o for o in offers if 0 < o < regular] or [0])
        price, was = (sale, regular) if sale else (regular, None)
        crumbs = p.get("BreadCrump") or {}
        items.append(item(
            store, external_id=key, handle=url.rsplit("/", 1)[-1], title=p.get("ProductDescription"),
            url=base + url, price=price, compare_at=was, in_stock=(p.get("AvailableStock") or 0) > 0,
            image=p.get("DefaultOptionImageUrl"), brand=p.get("Brand") or "LC Waikiki", gender=lcw_gender(p),
            text=[p.get("Category"), *crumbs.values()],
        ))
    if sample:
        return ("ok", items[:3], None)
    announced = (model.get("PageInfo") or {}).get("PageCount") or (model.get("CatalogList") or {}).get("PageCount")
    pages = int(announced or 0) or (page + 1 if len(products) >= 100 else page)
    if page < min(pages, MAX_PAGES):
        return ("ok", items, {**cursor, "page": page + 1})
    return ("ok", items, following)


# ------------------------------------------------------------------ Decathlon (through a browser)
# decathlon.ma (PrestaShop) refuses plain HTTP clients, so it is read with a headless Chromium
# (browser.py). We read only its "Promotions" listing (robots.txt allows it and `?page=N`).
# First choice: the PrestaShop JSON the listing's own "load more" uses. If the shop does not answer
# it, we read the rendered page instead: each product card's link, name, image and prices.
DECATHLON_PATHS = ["5080-promotions"]
DECATHLON_PRODUCT = re.compile(r"/p/(\d+)-(\d+)-[^/?#]+\.html")
DECATHLON_JS = r"""() => {
  const PRODUCT = /\/p\/(\d+)-(\d+)-[^\/?#]+\.html/;
  // A price element: "99 MAD", possibly after a short label ("Prix avant la réduction 299 MAD").
  const PRICE = /^([^\d]{0,50}?)(\d[\d\s  .,]*)\s*(?:MAD|DHS?|Dh|د\.م\.?)\s*$/i;
  const OLD_WORDS = /avant|ancien|initial|barr|habituel|before|was|r[ée]f[ée]rence|conseill/i;
  const NOT_A_PRICE = /conomi|save|gagn|livraison|offert|d[èe]s\s|remise de/i;   // savings, shipping
  const OLD_CLASS = /old|strike|before|crossed|barr|reference|regular|initial|was/i;
  const keyOf = a => { const m = (a.getAttribute('href') || '').match(PRODUCT); return m ? m[1] : null; };
  const textOf = e => (e.innerText || e.textContent || '').trim();
  const num = t => {
    let v = t.replace(/[\s  ]/g, '');
    v = /,\d{1,2}$/.test(v) ? v.replace(/\./g, '').replace(',', '.') : v.replace(/,/g, '');
    return parseFloat(v);
  };
  const isOld = (e, card) => {
    for (let x = e; x && x !== card; x = x.parentElement) {
      if (/^(DEL|S|STRIKE)$/.test(x.tagName)) return true;
      if (OLD_CLASS.test(typeof x.className === 'string' ? x.className : (x.getAttribute('class') || ''))) return true;
      if ((getComputedStyle(x).textDecorationLine || '').includes('line-through')) return true;
      if (OLD_WORDS.test((x.getAttribute('aria-label') || '') + ' ' + (x.getAttribute('title') || ''))) return true;
      const prev = x.previousElementSibling;
      if (prev && textOf(prev).length < 60 && OLD_WORDS.test(textOf(prev)) && !PRICE.test(textOf(prev))) return true;
    }
    return false;
  };
  const products = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/p/"]')) {
    const key = keyOf(a);
    if (!key || seen.has(key)) continue;
    // The card: the largest block around the link that links to this product only.
    let card = a;
    while (card.parentElement && card.parentElement !== document.body) {
      const keys = new Set([...card.parentElement.querySelectorAll('a[href*="/p/"]')].map(keyOf).filter(Boolean));
      if (keys.size > 1) break;
      card = card.parentElement;
    }
    const now = [], old = [];
    for (const e of card.querySelectorAll('*')) {
      const m = textOf(e).match(PRICE);
      if (!m || [...e.children].some(c => PRICE.test(textOf(c)))) continue;   // the smallest element only
      const value = num(m[2]);
      if (!(value > 0)) continue;
      if (NOT_A_PRICE.test(m[1])) continue;
      (OLD_WORDS.test(m[1]) || isOld(e, card) ? old : now).push(value);
    }
    if (!now.length && !old.length) continue;
    seen.add(key);
    const heading = card.querySelector('h2, h3, h4, [class*="title" i], [class*="name" i]');
    const linkText = [...card.querySelectorAll('a[href*="/p/"]')].map(l => (l.innerText || '').trim())
      .sort((x, y) => y.length - x.length)[0];
    const img = card.querySelector('img');
    const src = img && (img.currentSrc || img.getAttribute('src') || img.getAttribute('data-src')
      || (img.getAttribute('srcset') || '').split(' ')[0]);
    const brand = card.querySelector('[class*="brand" i]');
    products.push({
      id: key, url: a.href.split(/[?#]/)[0],
      title: (heading && heading.innerText.trim()) || linkText || (img && img.alt) || '',
      image: src && !src.startsWith('data:') ? new URL(src, location.href).href : null,
      brand: brand ? brand.innerText.trim() : null,
      now, old, text: card.innerText.slice(0, 400),
    });
  }
  // Page count: "1 sur 55" / "1 of 55" near the pagination, or the highest ?page=N link.
  let pages = 0;
  for (const l of document.querySelectorAll('a[href*="page="]')) {
    const m = l.href.match(/[?&]page=(\d+)/);
    if (m) pages = Math.max(pages, +m[1]);
  }
  const pagers = '[class*="pagination" i], nav, [aria-label*="pagination" i]';
  for (const e of document.querySelectorAll(`${pagers.split(', ').map(p => p + ' *').join(', ')}`)) {
    if (e.children.length) continue;                       // one line of text at a time
    const m = textOf(e).match(/^\D{0,12}\d+\s*(?:sur|of|\/)\s*(\d+)\s*$/i);
    if (m) pages = Math.max(pages, +m[1]);
  }
  return {products, pages};
}"""
DECATHLON_GONE = re.compile(r"rupture|indisponible|[ée]puis[ée]", re.I)

_browser = None                                     # tests put a fake here

def browser_reader():
    global _browser
    if _browser is None:
        import browser
        _browser = browser
    return _browser


def decathlon_items(store, products, seen):
    """Rendered cards → product records. Lowest current price is the price; the struck one the old price."""
    items = []
    for p in products:
        if p["id"] in seen:
            continue
        now, old = p.get("now") or [], p.get("old") or []
        if now:
            price, was = min(now), (max(old) if old else (max(now) if max(now) > min(now) else None))
        else:                                       # only prices we could not tell apart
            price, was = min(old), (max(old) if max(old) > min(old) else None)
        seen.add(p["id"])
        match = DECATHLON_PRODUCT.search(p.get("url") or "")
        items.append(item(
            store, external_id=p["id"], handle=match.group(0)[3:-5] if match else None, title=p.get("title"),
            url=p["url"], price=price, compare_at=was, in_stock=not DECATHLON_GONE.search(p.get("text") or ""),
            image=p.get("image"), brand=(p.get("brand") or "Decathlon").title(), text=[p.get("text")],
        ))
    return items


def debug_dump(store, name, data):
    """HEMZA_DEBUG=1: keep what the shop sent, to see why products were (not) read."""
    if os.environ.get("HEMZA_DEBUG") not in ("1", "true", "yes"):
        return
    path = os.path.join(tempfile.gettempdir(), f"hemza-{store['domain']}-{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    print(f"  [debug] saved {path}", flush=True)


def decathlon(store, cursor, sample=False):
    reader = browser_reader()
    if cursor is None:
        cursor = {"targets": store.get("paths") or DECATHLON_PATHS, "index": 0, "page": 1,
                  "seen": set(), "mode": None}
    targets, index, page, seen = cursor["targets"], cursor["index"], cursor["page"], cursor["seen"]
    following = {**cursor, "index": index + 1, "page": 1} if index + 1 < len(targets) else None
    url = f"https://{store['domain']}/{targets[index].lstrip('/')}"
    url += f"?page={page}" if page > 1 else ""
    limit = int(store.get("max_pages") or MAX_PAGES)
    pages, items = 0, None

    if cursor["mode"] in (None, "json"):
        result = reader.get_json(store, url + ("&" if "?" in url else "?") + "from-xhr",
                                 {"X-Requested-With": "XMLHttpRequest"})
        if result[0] == "throttled":
            return result
        data = result[1] if result[0] == "ok" else None
        debug_dump(store, f"json-page{page}", data if data is not None else result)
        parsed = presta_items(store, data["products"]) if (
            isinstance(data, dict) and isinstance(data.get("products"), list)) else []
        if parsed:
            cursor = {**cursor, "mode": "json"}
            items = [p for p in parsed if p["external_id"] not in seen]
            seen.update(p["external_id"] for p in items)
            pages = int((data.get("pagination") or {}).get("pages_count") or 0)
        elif cursor["mode"] == "json":
            items = []                                  # past the last page
        else:
            cursor = {**cursor, "mode": "page"}         # no usable data: read the page itself

    if items is None:
        result = reader.render(store, url, DECATHLON_JS, wait_for='a[href*="/p/"]')
        if result[0] != "ok":
            return result
        found = result[1] or {}
        debug_dump(store, f"cards-page{page}", found)
        if not found.get("products") and page == 1:
            return ("error", f"no product cards found on {url} (page layout changed?)")
        items = decathlon_items(store, found.get("products") or [], seen)
        pages = int(found.get("pages") or 0)

    if sample:
        return ("ok", items[:3], None)
    more = page < limit and items and (page < pages if pages else True)
    if more:
        return ("ok", items, {**cursor, "page": page + 1})
    return ("ok", items, following and {**following, "mode": cursor["mode"]})


# ------------------------------------------------------------------ Magento pages (through a browser)
# For Magento shops whose GraphQL API refuses visitors (Beauty Success): read the category pages
# themselves, `?p=N` only (robots.txt allows it; product_list_limit and sorting are disallowed).
# Magento writes prices in standard attributes: data-price-type="finalPrice" / "oldPrice" with
# data-price-amount, and the product id in data-product-id.
MAGENTO_PAGE_JS = r"""() => {
  const cards = document.querySelectorAll('.product-item, li.item.product');
  const products = [], seen = new Set();
  const amount = (card, type) => [...card.querySelectorAll(`[data-price-type="${type}"]`)]
    .map(e => parseFloat(e.getAttribute('data-price-amount'))).filter(v => v > 0);
  for (const card of cards) {
    const link = card.querySelector('a.product-item-link') || card.querySelector('a.product-item-photo, a[href$=".html"]');
    if (!link) continue;
    const idEl = card.querySelector('[data-product-id]');
    const id = idEl ? idEl.getAttribute('data-product-id') : link.href;
    if (seen.has(id)) continue;
    let now = amount(card, 'finalPrice').concat(amount(card, 'minPrice'));
    let old = amount(card, 'oldPrice');
    if (!now.length) {                       // themes that drop the attributes: read the price texts
      for (const e of card.querySelectorAll('.price')) {
        const v = parseFloat((e.textContent || '').replace(/[^\d,.]/g, '').replace(/\.(?=\d{3})/g, '').replace(',', '.'));
        if (v > 0) (e.closest('.old-price') ? old : now).push(v);
      }
    }
    if (!now.length) continue;
    seen.add(id);
    const img = card.querySelector('img.product-image-photo') || card.querySelector('img');
    const src = img && (img.getAttribute('data-src') || img.currentSrc || img.getAttribute('src'));
    const brand = card.querySelector('[class*="brand" i]');
    products.push({
      id, url: link.href.split(/[?#]/)[0], title: (link.innerText || link.title || '').trim() || (img && img.alt) || '',
      image: src && !src.startsWith('data:') ? new URL(src, location.href).href : null,
      brand: brand ? brand.innerText.trim() : null, now, old,
      in_stock: !card.querySelector('.stock.unavailable, .out-of-stock'),
    });
  }
  let pages = 0;
  for (const l of document.querySelectorAll('a[href*="?p="]')) {
    const m = l.href.match(/[?&]p=(\d+)/);
    if (m) pages = Math.max(pages, +m[1]);
  }
  const total = (document.querySelector('#toolbar-amount, .toolbar-amount') || {}).textContent || '';
  const count = (total.replace(/[\s  ]/g, '').match(/(\d+)articles?|sur(\d+)/i) || []).slice(1).find(Boolean);
  return {products, pages, total: count ? +count : 0, per_page: cards.length};
}"""


def magento_page(store, cursor, sample=False):
    reader = browser_reader()
    if cursor is None:
        cursor = {"targets": store.get("paths") or ["parfum.html"], "index": 0, "page": 1, "seen": set()}
    targets, index, page, seen = cursor["targets"], cursor["index"], cursor["page"], cursor["seen"]
    url = f"https://{store['domain']}/{targets[index].lstrip('/')}" + (f"?p={page}" if page > 1 else "")
    result = reader.render(store, url, MAGENTO_PAGE_JS, wait_for=".product-item, li.item.product")
    if result[0] != "ok":
        return result
    found = result[1] or {}
    if not found.get("products") and page == 1:
        return ("error", f"no product cards found on {url} (page layout changed?)")
    section = targets[index].rsplit("/", 1)[-1].replace(".html", "").replace("-", " ")
    items = []
    for p in found.get("products") or []:
        key = str(p["id"])
        if key in seen:
            continue
        seen.add(key)
        price = min(p["now"])
        was = max(p["old"]) if p.get("old") else None
        items.append(item(
            store, external_id=key, handle=p["url"].rsplit("/", 1)[-1].replace(".html", ""), title=p.get("title"),
            url=p["url"], price=price, compare_at=was if was and was > price else None,
            in_stock=p.get("in_stock", True), image=p.get("image"), brand=p.get("brand") or store.get("name"),
            text=[section],                       # "parfum" → the Parfums category
        ))
    if sample:
        return ("ok", items[:3], None)
    pages = int(found.get("pages") or 0)
    if found.get("total") and found.get("per_page"):
        pages = max(pages, -(-int(found["total"]) // int(found["per_page"])))
    limit = int(store.get("max_pages") or MAX_PAGES)
    if items and page < limit and (page < pages if pages else True):
        return ("ok", items, {**cursor, "page": page + 1})
    following = {**cursor, "index": index + 1, "page": 1} if index + 1 < len(targets) else None
    return ("ok", items, following)


ADAPTERS = {"shopify": shopify, "woocommerce": woocommerce, "prestashop": prestashop,
            "magento": magento, "zara": zara, "lcwaikiki": lcwaikiki, "decathlon": decathlon,
            "magento_page": magento_page}
# Platforms that any shop can run on; `--detect` tries these (site-specific readers like zara are skipped).
GENERIC = ("shopify", "woocommerce", "prestashop", "magento")


def fetch(store, cursor, sample=False):
    adapter = ADAPTERS.get(store.get("platform", "shopify"))
    if adapter is None:
        return ("error", f"unknown platform '{store.get('platform')}' (use: {', '.join(ADAPTERS)})")
    result = adapter(store, cursor, sample)
    # Sports shops also sell tents and paddles: keep only what we can sort into a clothing category.
    if store.get("skip_other") and result[0] == "ok":
        result = ("ok", [i for i in result[1] if i["category"] != "autres"], result[2])
    return result


def label(cursor):
    if isinstance(cursor, dict):
        page = f", page {cursor['page']}" if cursor.get("page", 1) > 1 else ""
        return f"listing {cursor['index'] + 1}/{len(cursor['targets'])}{page}"
    return f"listing {cursor[0] + 1}, page {cursor[1]}" if isinstance(cursor, tuple) else f"page {cursor}"
