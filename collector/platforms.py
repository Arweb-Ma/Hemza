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
Inditex      /itxrest/ catalog JSON, in a browser    Pull&Bear, Bershka: sale listings, then "see all"
Any shop     sale pages (given or found on the home page), in a browser: product links with a price
"""
import html, json, os, re, tempfile
from urllib.parse import quote
import requests

from classify import classify, gender_in

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
         sizes=(), handle=None, text=(), gender=None, gender_hint=None):
    """Builds the common product record. `text` is extra wording used only for classification.
    `gender` forces the section when the shop states it (e.g. Zara's KID section holds dresses too).
    `gender_hint` (from the listing or the product address) is used when the name says nothing."""
    title = html.unescape(title or "").strip()
    guessed, category = classify(title, " ".join(t for t in text if t), [],
                                 gender_hint or store.get("gender_hint"), store.get("category_hint"))
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

def shopify_gender_learn(store, cursor):
    """gender_collections ({"femme": ["k-femme"], …}): reads the first pages of each gender collection
    and learns which tags only ever appear in one gender (Kiabi's 'UNV1000003'-style codes). The whole
    catalogue is then read as usual; products seen in a collection keep its gender, the others get
    the gender their tags point to (unless their name says otherwise)."""
    queue = cursor["queue"]
    gender, handle, page = queue[0]
    result = get_json(f"https://{store['domain']}/collections/{quote(handle)}/products.json?limit={SHOPIFY_LIMIT}&page={page}")
    products = (result[1] or {}).get("products") if result[0] == "ok" and isinstance(result[1], dict) else None
    rest = queue[1:]
    if products:
        for p in products:
            cursor["direct"].setdefault(p["id"], gender)
            for tag in p.get("tags") or []:
                cursor["tags"].setdefault(tag, {}).setdefault(gender, 0)
                cursor["tags"][tag][gender] += 1
        if len(products) == SHOPIFY_LIMIT and page < int(store.get("gender_pages") or 2):
            rest = [(gender, handle, page + 1)] + rest
    elif result[0] == "throttled":
        return result
    if rest:
        return ("ok", [], {**cursor, "queue": rest})
    learned = {}
    for tag, counts in cursor["tags"].items():
        total = sum(counts.values())
        top, n = max(counts.items(), key=lambda kv: kv[1])
        if total >= 10 and n / total >= 0.9:
            learned[tag] = top
    return ("ok", [], {"page": 1, "direct": cursor["direct"], "learned": learned})


def shopify(store, cursor, sample=False):
    if store.get("gender_collections") and not sample:
        if cursor is None:
            queue = [(g, h, 1) for g, handles in store["gender_collections"].items() for h in handles]
            cursor = {"queue": queue, "direct": {}, "tags": {}}
        if "queue" in cursor:
            return shopify_gender_learn(store, cursor)
    genders = cursor if isinstance(cursor, dict) else {}
    page = genders.get("page", 1) if genders else (cursor or 1)
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
        votes = [genders.get("learned", {}).get(t) for t in p.get("tags") or []]
        votes = [v for v in votes if v]
        from_tags = max(set(votes), key=votes.count) if votes else None
        items.append(item(
            store, external_id=p["id"], handle=p.get("handle"), title=p.get("title"),
            gender=genders.get("direct", {}).get(p["id"]), gender_hint=from_tags,
            url=f"https://{store['domain']}/products/{p.get('handle')}",
            price=best["price"], compare_at=compare_at if compare_at not in (None, "", "0.00") else None,
            in_stock=bool(available), image=(p.get("images") or [{}])[0].get("src"), brand=p.get("vendor"),
            sizes=sorted({v.get("option1") for v in available if v.get("option1")}),
            text=[p.get("product_type"), " ".join(p.get("tags") or [])],
        ))
    more = len(data["products"]) == SHOPIFY_LIMIT and page < MAX_PAGES
    if genders:
        return ("ok", items, {**genders, "page": page + 1} if more else None)
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
    # gender_paths: the shop's Homme / Femme / Enfant categories, read before the whole catalogue so
    # each product keeps the gender of the section it was found in (Lacoste names rarely say it).
    gendered = list(store.get("gender_paths") or [])
    paths = gendered + list(store.get("paths") or PRESTA_PATHS)
    index, page, *rest = cursor or (0, 1)
    seen = rest[0] if rest else set()
    path = paths[index].lstrip("/")
    url = f"https://{store['domain']}/{path}{'&' if '?' in path else '?'}page={page}&from-xhr"
    result = get_json(url, {"X-Requested-With": "XMLHttpRequest"})
    if result[0] != "ok":
        # A missing listing is not fatal while other listings remain.
        if result[0] == "error" and index + 1 < len(paths) and not sample:
            return ("ok", [], (index + 1, 1, seen) if gendered else (index + 1, 1))
        return result
    data = result[1]
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        return ("error", "no PrestaShop JSON listing here")
    hint = gender_in(path) if index < len(gendered) else None
    items = [i for i in presta_items(store, data["products"], gender_hint=hint) if i["external_id"] not in seen]
    if gendered:
        seen.update(i["external_id"] for i in items)
    if sample:
        return ("ok", items, None)
    def at(i, p):
        return (i, p, seen) if gendered else (i, p)
    pages = int((data.get("pagination") or {}).get("pages_count") or 1)
    if page < min(pages, MAX_PAGES):
        return ("ok", items, at(index, page + 1))
    return ("ok", items, at(index + 1, 1) if index + 1 < len(paths) else None)


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


def presta_items(store, products, gender_hint=None):
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
            text=[p.get("category_name")], gender_hint=gender_hint,
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

def zara_targets(categories, sections=None):
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
        if sections is not None:
            sections.update({cid: ZARA_SECTIONS[section] for cid in leaves})
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
        sections = {}
        targets = zara_targets((result[1] or {}).get("categories") or [], sections) if isinstance(result[1], dict) else []
        if not targets:
            return ("error", "no Zara category tree here")
        cursor = {"targets": targets[:1] if sample else targets, "index": 0, "seen": set(), "sections": sections}
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
            gender=ZARA_SECTIONS.get(p.get("sectionName")) or (cursor.get("sections") or {}).get(targets[index]),
            text=[p.get("familyName"), p.get("subfamilyName")],
        ))
    if sample:
        return ("ok", items[:3], None)
    return ("ok", items, {**cursor, "index": index + 1} if index + 1 < len(targets) else None)


# ------------------------------------------------------------------ Inditex (Pull&Bear, Bershka…)
# The other Inditex shops show no products in their HTML: the grid arrives as JSON from their own
# catalog API under /itxrest/, which refuses servers (Akamai), so these stores run `"via": "browser"`
# from the PC and the JSON is asked for from inside the shop's tab, like Zara. robots.txt (`*`) only
# forbids /itxrest/1/marketing/, which is never asked for.
#   /itxrest/2/catalog/store/{store}                       currency decimals, catalog id, languages
#   /api/storefront/1/stores/{store}/menu  → grids         (Bershka's newer site)
#   /itxrest/2/catalog/store/{store}/{catalog}/category    → categories  (older sites; the fallback)
#   /itxrest/4/…/grid/{id} or /itxrest/3/…/category/{id}/product?showProducts=false → productIds
#   /itxrest/3/catalog/store/{store}/{catalog}/productsArray?productIds=…   40 at a time
# Sale listings are read first, then the "see all" listings (for price history), up to max_products.
# One product per product id, its cheapest colour (as for Zara).
INDITEX_SALE = re.compile(r"solde|sale\b|promo|special price|prix sp[ée]ciaux|black.?friday|offres?\b|outlet|-\s?\d+\s?%", re.I)
INDITEX_ALL = re.compile(r"voir tout|tout voir|tout afficher|view all|see all|ver todo|^tout$|^all$", re.I)
INDITEX_SKIP = re.compile(r"parfum|perfume|fragrance|beaut|cosm[ée]ti|maquillage|make.?up|gift|carte cadeau|"
                          r"join life|music|editorial|lookbook|nouveaut|new in", re.I)
INDITEX_BATCH = 40
INDITEX_SECTIONS = {"WOMAN": "femme", "WOMEN": "femme", "FEMME": "femme", "MAN": "homme", "MEN": "homme",
                    "HOMME": "homme", "KID": "enfant", "KIDS": "enfant", "ENFANT": "enfant"}


def inditex_base(store):
    return f"https://{store['domain']}"


def inditex_menu_targets(menu):
    """(listing kind, id, trail) for every grid of the menu, skipping the nodes that name themselves."""
    out = []
    def walk(node, trail):
        here = trail + [node.get("name") or ""]
        content = node.get("content") or {}
        if content.get("type") == "grid" and content.get("id") and str(content["id"]) != str(node.get("key")):
            out.append(("grid", str(content["id"]), " / ".join(p for p in here if p)))
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child, here)
    for node in (menu or {}).get("items") or []:
        if isinstance(node, dict):
            walk(node, [])
    return out


def inditex_category_targets(tree):
    """(listing kind, id, trail) for every leaf category of the older category tree."""
    out = []
    def walk(nodes, trail):
        for c in nodes or []:
            here = trail + [c.get("name") or ""]
            subs = c.get("subcategories") or []
            if subs:
                walk(subs, here)
            else:
                out.append(("category", str(c.get("viewCategoryId") or c.get("id")), " / ".join(p for p in here if p)))
    walk((tree or {}).get("categories"), [])
    return out


def inditex_pick(targets):
    """Sale listings first, then "see all" listings; never beauty, gift cards or editorial pages."""
    sale, everything, seen = [], [], set()
    for kind, tid, trail in targets:
        if (kind, tid) in seen or INDITEX_SKIP.search(trail):
            continue
        seen.add((kind, tid))
        last = trail.split(" / ")[-1]
        if INDITEX_SALE.search(trail):
            sale.append((kind, tid, trail))
        elif INDITEX_ALL.search(last.strip()):
            everything.append((kind, tid, trail))
    return sale + everything


def inditex_money(raw, decimals):
    if raw in (None, "", "null"):
        return None
    try:
        return round(float(str(raw)) * 10 ** -abs(int(decimals)), 2)
    except (TypeError, ValueError):
        return None


def inditex_image(color, holder, base):
    for source in (color, holder):
        image = (source or {}).get("image") or {}
        stem = image.get("url")
        if isinstance(stem, str) and stem and base:
            views = [str(v) for v in image.get("type") or [] if str(v).strip()]
            url = f"{base.rstrip('/')}{stem if stem.startswith('/') else '/' + stem}_{views[0] if views else '1'}_1_0.jpg"
            return f"{url}?ts={image['timestamp']}" if image.get("timestamp") else url
        xmedia = (source or {}).get("xmedia") or []
        if xmedia and isinstance(xmedia[0], dict) and xmedia[0].get("url"):
            return xmedia[0]["url"].replace("{width}", "563")
    return None


def inditex_items(store, cursor, products, trail):
    prefix, decimals, images = store.get("prefix") or "/ma/", cursor["decimals"], cursor["image_base"]
    items = []
    for entry in products or []:
        if not isinstance(entry, dict) or entry.get("id") is None or ("key" in entry and "description" in entry):
            continue                                     # errors arrive inside the array (Item not found)
        pid = str(entry["id"])
        if pid in cursor["seen"]:
            continue
        holders = [h for h in entry.get("bundleProductSummaries") or [] if isinstance(h, dict)] or [entry]
        best = None                                      # (price, old, colour, holder)
        for holder in holders:
            for color in ((holder.get("detail") or {}).get("colors") or []):
                for size in color.get("sizes") or []:
                    price = inditex_money(size.get("price"), decimals)
                    if price and (best is None or price < best[0]):
                        best = (price, inditex_money(size.get("oldPrice"), decimals), color, holder)
        if best is None:
            continue
        price, old, color, holder = best
        cursor["seen"].add(pid)
        slug = (holder.get("productUrl") or entry.get("productUrl") or "").strip("/")
        if store.get("url_style") == "c0p":              # Bershka: /ma/<slug>-c0p<id>.html
            stem = re.sub(r"-l\d+$", "", slug)
            path = f"{stem}-c0p{pid}.html" if stem else f"c0p{pid}.html"
        else:                                            # Pull&Bear: /ma/<slug>-l<reference>
            path = slug or f"c0p{pid}.html"
        sizes = color.get("sizes") or []
        section = (holder.get("sectionNameEN") or holder.get("sectionName") or entry.get("sectionNameEN") or "").upper()
        items.append(item(
            store, external_id=pid, handle=slug or None, title=holder.get("name") or entry.get("name"),
            url=f"{inditex_base(store)}{prefix}{path}", price=price, compare_at=old,
            in_stock=any(s.get("isBuyable") for s in sizes) or not sizes,
            image=inditex_image(color, holder, images), brand=store.get("name"),
            sizes=[s.get("name") for s in sizes if s.get("isBuyable") and s.get("name")],
            gender=INDITEX_SECTIONS.get(section), gender_hint=gender_in(trail.split(" / ")[0]),
            text=[holder.get("familyName") or entry.get("familyName"),
                  holder.get("subFamilyName") or entry.get("subFamilyName"), trail],
        ))
    return items


def inditex_setup(store, sample):
    sid, base = store["store_id"], inditex_base(store)
    result = shop_json(store, f"{base}/itxrest/2/catalog/store/{sid}?languageId=-1&appId=1")
    if result[0] != "ok":
        return result
    config = result[1] if isinstance(result[1], dict) else {}
    catalog = next((c.get("id") for c in config.get("catalogs") or [] if isinstance(c, dict) and c.get("type") == 1),
                   None) or store.get("catalog_id")
    language = next((l.get("id") for l in config.get("supportedLanguages") or []
                     if isinstance(l, dict) and str(l.get("code") or l.get("localeName") or "").lower().startswith("fr")),
                    store.get("language_id", -1))
    details = config.get("details") or {}
    decimals = (details.get("locale") or {}).get("currencyDecimals")
    cursor = {"catalog": catalog, "lang": language, "decimals": -2 if decimals is None else decimals,
              "image_base": details.get("imageBaseUrl") or store.get("image_base") or "",
              "seen": set(), "index": 0}
    targets = []
    menu = shop_json(store, f"{base}/api/storefront/1/stores/{sid}/menu")
    if menu[0] == "ok":
        targets = inditex_pick(inditex_menu_targets(menu[1]))
    if not targets:
        tree = shop_json(store, f"{base}/itxrest/2/catalog/store/{sid}/{catalog}/category?languageId={language}&typeCatalog=1&appId=1")
        if tree[0] == "throttled":
            return tree
        if tree[0] == "ok":
            targets = inditex_pick(inditex_category_targets(tree[1]))
    if not targets:
        return ("error", "no sale or 'see all' listing found in the shop's menu")
    print(f"  {sum(1 for t in targets if INDITEX_SALE.search(t[2]))} sale listings, {len(targets)} in all", flush=True)
    cursor["targets"] = targets[:1] if sample else targets
    return ("ok", cursor)


def inditex(store, cursor, sample=False):
    if cursor is None:
        setup = inditex_setup(store, sample)
        if setup[0] != "ok":
            return setup
        cursor = setup[1]
    sid, base, catalog, lang = store["store_id"], inditex_base(store), cursor["catalog"], cursor["lang"]
    kind, tid, trail = cursor["targets"][cursor["index"]]
    following = {**cursor, "index": cursor["index"] + 1} if cursor["index"] + 1 < len(cursor["targets"]) else None
    if kind == "grid":
        url = f"{base}/itxrest/4/catalog/store/{sid}/grid/{quote(tid)}?languageId={lang}&appId=1"
    else:
        url = f"{base}/itxrest/3/catalog/store/{sid}/{catalog}/category/{tid}/product?languageId={lang}&showProducts=false&appId=1"
    result = shop_json(store, url)
    if result[0] == "throttled":
        return result
    if result[0] != "ok":
        return ("ok", [], following) if following and not sample else result   # one broken listing is not fatal
    data = result[1] if isinstance(result[1], dict) else {}
    limit = int(store.get("max_products") or 2500)
    ids = [str(i) for i in data.get("productIds") or data.get("sortedProductIds") or [] if str(i).isdigit()]
    ids = [i for i in dict.fromkeys(ids) if i not in cursor["seen"]][:max(0, limit - len(cursor["seen"]))]
    items = []
    for start in range(0, len(ids), INDITEX_BATCH):
        batch = ids[start:start + INDITEX_BATCH]
        answer = shop_json(store, f"{base}/itxrest/3/catalog/store/{sid}/{catalog}/productsArray"
                                  f"?productIds={','.join(batch)}&languageId={lang}&appId=1")
        if answer[0] == "throttled":
            return answer if not items else ("ok", items, cursor)     # keep what we have; retry later
        if answer[0] == "ok" and isinstance(answer[1], dict):
            items += inditex_items(store, cursor, answer[1].get("products"), trail)
        if sample and items:
            return ("ok", items[:3], None)
    if len(cursor["seen"]) >= limit:
        following = None
    return ("ok", items, following)


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
DECATHLON_JS = r"""(source) => {
  // Product links: Decathlon's /p/<id>-<attr>-slug.html unless another pattern is given.
  const PRODUCT = new RegExp(source || '/p/(\\d+)-(\\d+)-[^/?#]+\\.html');
  // A price element: "99 MAD", possibly after a short label ("Prix avant la réduction 299 MAD").
  const PRICE = /^([^\d]{0,50}?)(\d[\d\s  .,]*)\s*(?:MAD|DHS?|Dh|د\.م\.?)\s*$/i;
  const OLD_WORDS = /avant|ancien|initial|normal|barr|habituel|before|was|r[ée]f[ée]rence|conseill/i;
  const NOT_A_PRICE = /conomi|save|gagn|livraison|offert|d[èe]s\s|remise de/i;   // savings, shipping
  const OLD_CLASS = /old|strike|before|crossed|barr|reference|regular|initial|was/i;
  const keyOf = a => { const m = (a.getAttribute('href') || '').match(PRODUCT); return m ? (m[1] || m[0]) : null; };
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
  for (const a of document.querySelectorAll('a[href]')) {
    const key = keyOf(a);
    if (!key || seen.has(key)) continue;
    // The card: the largest block around the link that links to this product only.
    let card = a;
    while (card.parentElement && card.parentElement !== document.body) {
      const keys = new Set([...card.parentElement.querySelectorAll('a[href]')].map(keyOf).filter(Boolean));
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
    const linkText = [...card.querySelectorAll('a[href]')].filter(keyOf).map(l => (l.innerText || '').trim())
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


def shout_to_title(brand):
    """'QUECHUA' → 'Quechua'; 'DeFacto' stays 'DeFacto'."""
    return brand.title() if brand and brand.isupper() else brand


def decathlon_items(store, products, seen, default_brand="Decathlon", listing_gender=None):
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
        handle = match.group(0)[3:-5] if match else (p.get("url") or "").rsplit("/", 1)[-1].replace(".html", "")
        items.append(item(
            store, external_id=p["id"], handle=handle, title=p.get("title"),
            url=p["url"], price=price, compare_at=was, in_stock=not DECATHLON_GONE.search(p.get("text") or ""),
            image=p.get("image"), brand=shout_to_title(p.get("brand") or default_brand), text=[p.get("text")],
            gender_hint=gender_in(p.get("url")) or listing_gender,
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
    result = reader.render(store, url, MAGENTO_PAGE_JS, wait_for=".product-item, li.item.product, .price")
    if result[0] != "ok":
        return result
    found = result[1] or {}
    section = targets[index].rsplit("/", 1)[-1].replace(".html", "").replace("-", " ")
    if not found.get("products"):
        # Theme without Magento's usual card markup: any link to a product page with a price next to it.
        pattern = r"^(?:https?://[^/]+)?/([a-z0-9][a-z0-9-]*)\.html$"
        cards = reader.evaluate(store, DECATHLON_JS, pattern)
        if cards[0] != "ok":
            return cards
        cards = cards[1] or {}
        debug_dump(store, f"cards-page{page}", cards)
        if not cards.get("products") and page == 1:
            return ("error", f"no product cards found on {url}{reader.snapshot(store, 'no-cards')}")
        items = decathlon_items(store, cards.get("products") or [], seen, default_brand=store.get("name"))
        for i in items:
            i["category"] = i["category"] if i["category"] != "autres" else (store.get("category_hint") or "autres")
        found = {"pages": cards.get("pages") or 0}
        return magento_page_next(store, cursor, items, found, sample)
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
    return magento_page_next(store, cursor, items, found, sample)


def magento_page_next(store, cursor, items, found, sample):
    targets, index, page = cursor["targets"], cursor["index"], cursor["page"]
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


# ------------------------------------------------------------------ Any shop, through a browser
# For shops whose pages are built in the browser and whose markup we cannot see in advance (DeFacto,
# Lefties). Listing pages: `paths` from stores.json, plus — with `discover` — the links on the home
# page named soldes / promo / sale / discount. On each page (scrolled, so lazy grids load) every
# link of the shop with a MAD/DH price next to it is a product card (the Decathlon card reader with
# a generic link pattern). `page_param`: also read ?<param>=2, 3… until no new product appears.
SALE_LINK_JS = r"""async ([prefix, words]) => {
  // Menus often load their links only when opened (☰): open it first, without following any link.
  const opener = [...document.querySelectorAll(
      'button[aria-label*="menu" i], [role="button"][aria-label*="menu" i], button[class*="menu" i], ' +
      'button[class*="burger" i], [class*="hamburger" i]')]
    .find(e => !e.closest('a[href]') && e.offsetParent !== null);
  if (opener) {
    try { opener.click(); } catch (e) {}
    await new Promise(r => setTimeout(r, 2000));
  }
  const re = new RegExp(words, 'i'), out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    let url;
    try { url = new URL(a.href, location.href); } catch (e) { continue; }
    if (url.host !== location.host || !url.pathname.startsWith(prefix)) continue;
    const label = ((a.innerText || '') + ' ' + (a.getAttribute('aria-label') || '') + ' ' + url.pathname).trim();
    if (re.test(label)) out.push(url.pathname);
  }
  return [...new Set(out)];
}"""
SALE_WORDS = r"solde|promo|sale\b|discount|r[ée]duction|baisse de prix|prix bas|outlet|offres?\b|black.?friday|tanzilat|تخفيض"
SALE_EXCLUDE = re.compile(r"/(help|aide|faq|conditions|legal|cgv|store|magasin|account|cart|panier)", re.I)


def browser_cards(store, cursor, sample=False):
    reader = browser_reader()
    prefix = store.get("prefix") or "/"
    if cursor is None:
        targets = list(store.get("paths") or [])
        if store.get("discover"):
            home = store.get("home_url") or f"https://{store['domain']}{prefix}"
            result = reader.render(store, home, SALE_LINK_JS, wait_for="a[href]", arg=[prefix, SALE_WORDS])
            if result[0] == "throttled":
                return result
            found = result[1] if result[0] == "ok" else []
            debug_dump(store, "sale-links", found)
            targets += [p for p in (found or []) if not SALE_EXCLUDE.search(p)][:int(store.get("max_targets") or 8)]
        targets = list(dict.fromkeys(t if t.startswith("/") else prefix + t for t in targets))
        if not targets:
            # Not an error: many shops only open a sale section during sales (Lefties, Nov 2026).
            print("  no sale section on the site right now (home page and menu checked)", flush=True)
            if os.environ.get("HEMZA_DEBUG") in ("1", "true", "yes"):
                print(f"  {reader.snapshot(store, 'no-sale-links').lstrip(' —')}", flush=True)
            return ("ok", [], None)
        cursor = {"targets": targets, "index": 0, "page": 1, "seen": set(), "found_any": False}
    targets, index, page, seen = cursor["targets"], cursor["index"], cursor["page"], cursor["seen"]
    url = f"https://{store['domain']}{targets[index]}"
    if page > 1:
        url += ("&" if "?" in url else "?") + f"{store['page_param']}={page}"
    host = re.escape(store["domain"].removeprefix("www."))
    pattern = store.get("product_pattern") or (
        r"^(?:https?://(?:www\.)?" + host + r")?(" + re.escape(prefix) + r"[^?#]*[^/?#])")
    result = reader.render(store, url, DECATHLON_JS, wait_for="a[href]", arg=pattern)
    if result[0] != "ok":
        return result
    cards = result[1] or {}
    debug_dump(store, f"cards-{index}-{page}", cards)
    listing_paths = set(targets)
    products = [p for p in cards.get("products") or [] if p["id"] not in listing_paths]
    items = decathlon_items(store, products, seen, default_brand=store.get("name"),
                            listing_gender=gender_in(targets[index]))
    cursor = {**cursor, "found_any": cursor["found_any"] or bool(items)}
    following = {**cursor, "index": index + 1, "page": 1} if index + 1 < len(targets) else None
    if not following and not cursor["found_any"]:
        return ("error", f"no product cards found on {url}{reader.snapshot(store, 'no-cards')}")
    if sample:
        return ("ok", items[:3], None)
    if items and store.get("page_param") and page < int(store.get("max_pages") or MAX_PAGES):
        return ("ok", items, {**cursor, "page": page + 1})
    return ("ok", items, following)


ADAPTERS = {"shopify": shopify, "woocommerce": woocommerce, "prestashop": prestashop,
            "magento": magento, "zara": zara, "lcwaikiki": lcwaikiki, "decathlon": decathlon,
            "magento_page": magento_page, "browser_cards": browser_cards, "inditex": inditex}
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
    if isinstance(cursor, dict) and "queue" in cursor:
        gender, handle, page = cursor["queue"][0]
        return f"learning genders: collection {handle}, page {page}"
    if isinstance(cursor, dict) and "targets" not in cursor:
        return f"page {cursor.get('page', 1)}"
    if isinstance(cursor, dict):
        page = f", page {cursor['page']}" if cursor.get("page", 1) > 1 else ""
        return f"listing {cursor['index'] + 1}/{len(cursor['targets'])}{page}"
    return f"listing {cursor[0] + 1}, page {cursor[1]}" if isinstance(cursor, tuple) else f"page {cursor}"
