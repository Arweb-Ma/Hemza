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
"""
import html
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
    items = []
    for p in data["products"]:
        price, product_id = p.get("price_amount"), p.get("id_product")
        product_url = p.get("url") or p.get("link")
        if price is None or product_id is None or not product_url:
            continue
        cover = (p.get("cover") or {})
        image = ((cover.get("bySize") or {}).get("home_default") or cover.get("large") or {}).get("url")
        items.append(item(
            store, external_id=product_id, handle=p.get("link_rewrite"), title=p.get("name"), url=product_url,
            price=price, compare_at=p.get("regular_price_amount") if p.get("has_discount") else None,
            in_stock=p.get("availability") != "unavailable", image=image, brand=p.get("manufacturer_name"),
            text=[p.get("category_name")],
        ))
    if sample:
        return ("ok", items, None)
    pages = int((data.get("pagination") or {}).get("pages_count") or 1)
    if page < min(pages, MAX_PAGES):
        return ("ok", items, (index, page + 1))
    return ("ok", items, (index + 1, 1) if index + 1 < len(paths) else None)


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
    result = get_json(f"https://{store['domain']}/graphql?query={quote(MAGENTO_QUERY % (limit, page))}")
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
    return ("ok", items, page + 1 if page < min(pages, MAX_PAGES) else None)


# ------------------------------------------------------------------ Zara
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
        result = get_json(f"{ZARA_BASE}/categories?ajax=true")
        if result[0] != "ok":
            return result
        targets = zara_targets((result[1] or {}).get("categories") or []) if isinstance(result[1], dict) else []
        if not targets:
            return ("error", "no Zara category tree here")
        cursor = {"targets": targets[:1] if sample else targets, "index": 0, "seen": set()}
    targets, index, seen = cursor["targets"], cursor["index"], cursor["seen"]
    result = get_json(f"{ZARA_BASE}/category/{targets[index]}/products?ajax=true")
    if result[0] != "ok":
        if result[0] == "error" and index + 1 < len(targets) and not sample:
            return ("ok", [], {**cursor, "index": index + 1})       # one broken listing is not fatal
        return result
    items = []
    for group in (result[1] or {}).get("productGroups") or []:
        for element in group.get("elements") or []:
            for p in element.get("commercialComponents") or []:
                seo, price = p.get("seo") or {}, p.get("price")
                if price is None or not seo.get("keyword") or p.get("id") in seen:
                    continue
                if (p.get("familyName") or "").upper() in ZARA_SKIP_FAMILIES:
                    continue
                seen.add(p.get("id"))
                old = p.get("oldPrice")
                items.append(item(
                    store, external_id=p["id"], handle=seo.get("keyword"), title=p.get("name"),
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


ADAPTERS = {"shopify": shopify, "woocommerce": woocommerce, "prestashop": prestashop,
            "magento": magento, "zara": zara}


def fetch(store, cursor, sample=False):
    adapter = ADAPTERS.get(store.get("platform", "shopify"))
    if adapter is None:
        return ("error", f"unknown platform '{store.get('platform')}' (use: {', '.join(ADAPTERS)})")
    return adapter(store, cursor, sample)


def label(cursor):
    if isinstance(cursor, dict):
        return f"listing {cursor['index'] + 1}/{len(cursor['targets'])}"
    return f"listing {cursor[0] + 1}, page {cursor[1]}" if isinstance(cursor, tuple) else f"page {cursor}"
