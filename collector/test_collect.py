"""Offline tests for the collector. Run: python collector/test_collect.py"""
import io, json, os, re, sys, tempfile, unittest
from urllib.parse import unquote
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(__file__))
import collect, platforms


class FakeResponse:
    def __init__(self, status, data=None, headers=None, html=False):
        self.status_code, self._data, self._html = status, data, html
        self.headers = {"content-type": "text/html" if html else "application/json", **(headers or {})}

    def json(self):
        if self._html:
            raise ValueError("not json")
        return self._data


def shopify_product(i, price="200.00", was="300.00"):
    return {"id": i, "title": f"Jean homme {i}", "handle": f"p{i}", "vendor": "X",
            "variants": [{"price": price, "compare_at_price": was, "available": True, "option1": "M"}]}

def woo_product(i, price="70000", regular="130000", unit=2):
    return {"id": i, "name": f"Veste en cuir &amp; daim {i}", "slug": f"veste-{i}",
            "permalink": f"https://woo.ma/produit/veste-{i}/", "is_in_stock": True,
            "prices": {"price": price, "regular_price": regular, "sale_price": price,
                       "currency_code": "MAD", "currency_minor_unit": unit},
            "images": [{"src": "https://woo.ma/a.jpg"}], "categories": [{"name": "Homme"}], "tags": [],
            "attributes": [{"name": "Taille", "terms": [{"name": "M"}, {"name": "L"}]},
                           {"name": "Couleur", "terms": [{"name": "Noir"}]}]}

def presta_product(i, price=700.0, regular=1300.0):
    return {"id_product": str(i), "name": f"Robe longue {i}", "url": f"https://presta.ma/robes/{i}-robe.html",
            "price_amount": price, "regular_price_amount": regular, "has_discount": regular > price,
            "category_name": "Femme", "manufacturer_name": "Marque",
            "cover": {"bySize": {"home_default": {"url": "https://presta.ma/1-home_default/robe.jpg"}}}}


class World:
    """Fake shops. Shopify lets one request through every `budget` seconds across ALL its stores."""
    def __init__(self, shopify=None, woo=None, presta=None, budget=0, retry_after="60", blocked=(),
                 woo_legacy=None, magento=None, zara=None):
        self.now = 0.0
        self.shopify, self.woo, self.presta = shopify or {}, woo or {}, presta or {}
        self.woo_legacy, self.magento, self.zara = woo_legacy or {}, magento or {}, zara
        self.budget, self.retry_after, self.blocked = budget, retry_after, set(blocked)
        self.last_ok, self.requests = None, []

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now

    def get(self, url, **_):
        domain, path = url.split("/")[2], url.split("/", 3)[3]
        found = re.search(r"[?&]page=(\d+)", path)
        page = int(found.group(1)) if found else 1
        self.requests.append((round(self.now), domain, page))
        if domain == "www.zara.com" and self.zara is not None:
            if path.startswith("ma/fr/categories"):
                return FakeResponse(200, {"categories": self.zara["tree"]})
            listing = re.match(r"ma/fr/category/(\d+)/products", path)
            if listing and int(listing.group(1)) in self.zara["listings"]:
                products = self.zara["listings"][int(listing.group(1))]
                return FakeResponse(200, {"productGroups": [{"elements": [{"commercialComponents": products}]}]})
            return FakeResponse(404, html=True)
        if path.startswith("graphql?query="):
            if domain not in self.magento:
                return FakeResponse(404, html=True)
            query = unquote(path.split("query=", 1)[1])
            size, current = map(int, re.search(r"pageSize:(\d+),currentPage:(\d+)", query).groups())
            rows = self.magento[domain]
            return FakeResponse(200, {"data": {"products": {
                "page_info": {"total_pages": max(1, -(-len(rows) // size))},
                "items": rows[(current - 1) * size:current * size]}}})
        if path.startswith("wp-json/wc/store/products") and domain in self.woo_legacy:
            limit = int(path.split("per_page=")[1].split("&")[0])
            return FakeResponse(200, self.woo_legacy[domain][(page - 1) * limit:page * limit])
        if path.startswith("products.json"):
            too_soon = self.last_ok is not None and self.now - self.last_ok < self.budget
            if domain in self.blocked or too_soon:
                return FakeResponse(429, headers={"Retry-After": self.retry_after} if self.retry_after else {})
            if domain not in self.shopify:
                return FakeResponse(404, html=True)
            self.last_ok = self.now
            if self.shopify[domain] is None:
                return FakeResponse(200, html=True)
            limit = int(path.split("limit=")[1].split("&")[0])
            return FakeResponse(200, {"products": self.shopify[domain][(page - 1) * limit:page * limit]})
        if path.startswith("wp-json/wc/store/v1/products"):
            if domain not in self.woo:
                return FakeResponse(404, html=True)
            limit = int(path.split("per_page=")[1].split("&")[0])
            return FakeResponse(200, self.woo[domain][(page - 1) * limit:page * limit])
        if "from-xhr" in path:
            listing = self.presta.get(domain, {}).get(re.split(r"[?&]page=", path)[0])
            if listing is None:
                return FakeResponse(404, html=True)
            per_page = 12
            pages = max(1, -(-len(listing) // per_page))
            return FakeResponse(200, {"products": listing[(page - 1) * per_page:page * per_page],
                                      "pagination": {"pages_count": pages, "current_page": page},
                                      "rendered_products": "<div>…</div>"})
        return FakeResponse(404, html=True)


def run(world, stores, argv=("--dry-run",), keep_state=False):
    stores = [s if isinstance(s, dict) else {"name": s, "domain": s} for s in stores]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"stores": stores}, f)
    real = (collect.requests.get, collect.time.sleep, collect.time.monotonic, sys.argv)
    collect.requests.get, collect.time.sleep, collect.time.monotonic = world.get, world.sleep, world.clock
    sys.argv = ["collect.py", "--stores", f.name, *argv]
    out = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(out):
            collect.main()
    finally:
        collect.requests.get, collect.time.sleep, collect.time.monotonic, sys.argv = real
        os.unlink(f.name)
        if os.path.exists(f.name + ".state") and not keep_state:
            os.unlink(f.name + ".state")
    return out.getvalue()


def read_one(world, store, cursor=None):
    real = platforms.requests.get
    platforms.requests.get = world.get
    try:
        return platforms.fetch(store, cursor)
    finally:
        platforms.requests.get = real


class PacerTest(unittest.TestCase):
    def test_slows_down_after_refusals_and_recovers(self):
        pacer = collect.Pacer(60, 30, high=900, sleep=lambda s: None, clock=lambda: 0)
        pacer.throttled(None); self.assertEqual(pacer.gap, 120)
        pacer.throttled(600); self.assertEqual(pacer.gap, 600)      # honours Retry-After when longer
        pacer.throttled(5000); self.assertEqual(pacer.gap, 900)     # capped
        for _ in range(60):
            pacer.success()
        self.assertEqual(pacer.gap, 30)                              # never faster than the floor


class ShopifyQueueTest(unittest.TestCase):
    def test_reads_every_store_when_nothing_is_refused(self):
        world = World(shopify={"a.ma": [shopify_product(i) for i in range(53)],
                               "b.ma": [shopify_product(i) for i in range(300)]})
        out = run(world, ["a.ma", "b.ma"])
        self.assertIn("Done: 2/2 stores read.", out)
        # a.ma: one short page; b.ma: 250 + 50. No wasted request for an empty page.
        self.assertEqual([(d, p) for _, d, p in world.requests], [("a.ma", 1), ("b.ma", 1), ("b.ma", 2)])
        self.assertGreaterEqual(world.requests[1][0] - world.requests[0][0], collect.PACE["shopify"][1])

    def test_global_budget_is_respected_and_all_stores_are_eventually_read(self):
        stores = [f"s{i}.ma" for i in range(6)]
        world = World(shopify={d: [shopify_product(i) for i in range(40)] for d in stores}, budget=300)
        out = run(world, stores)
        self.assertIn("Done: 6/6 stores read.", out)
        self.assertLess(len(world.requests), 6 * collect.MAX_ATTEMPTS)   # few refusals, no hammering
        self.assertLess(world.now, 180 * 60)

    def test_a_store_that_always_refuses_does_not_block_the_others(self):
        world = World(shopify={"ok.ma": [shopify_product(1)], "no.ma": []}, blocked={"no.ma"}, retry_after=None)
        out = run(world, ["no.ma", "ok.ma"])
        self.assertIn("Done: 1/2 stores read.", out)
        self.assertEqual(sum(1 for _, d, _ in world.requests if d == "no.ma"), collect.MAX_ATTEMPTS)
        self.assertRegex(out, r"no\.ma\s+shopify\s+0 products\s+refused")

    def test_a_site_that_answers_with_a_web_page_is_reported_not_crashed_on(self):
        out = run(World(shopify={"html.ma": None}), ["html.ma"])
        self.assertIn("answered with a web page", out)

    def test_time_budget_stops_the_run(self):
        world = World(shopify={"no.ma": []}, blocked={"no.ma"}, retry_after="900")
        out = run(world, ["no.ma"], argv=("--dry-run", "--max-minutes", "10"))
        self.assertIn("Time budget of 10 min reached", out)
        self.assertEqual(len(world.requests), 1)

    def test_only_filter(self):
        world = World(shopify={"a.ma": [shopify_product(1)], "b.ma": [shopify_product(2)]})
        run(world, ["a.ma", "b.ma"], argv=("--dry-run", "--only", "b.ma"))
        self.assertEqual([d for _, d, _ in world.requests], ["b.ma"])


class WooCommerceTest(unittest.TestCase):
    STORE = {"name": "Woo", "domain": "woo.ma", "platform": "woocommerce"}

    def test_converts_minor_units_and_reads_sizes(self):
        status, items, cursor = read_one(World(woo={"woo.ma": [woo_product(1)]}), self.STORE)
        self.assertEqual((status, cursor), ("ok", None))
        product = items[0]
        self.assertEqual((product["_price"], product["_compare_at"]), (700.0, 1300.0))
        self.assertEqual(product["title"], "Veste en cuir & daim 1")            # HTML entities decoded
        self.assertEqual((product["gender"], product["category"]), ("homme", "vestes"))
        self.assertEqual(product["sizes"], ["M", "L"])
        self.assertEqual(product["product_url"], "https://woo.ma/produit/veste-1/")

    def test_no_was_price_when_not_on_sale_and_zero_decimal_currencies(self):
        _, items, _ = read_one(World(woo={"woo.ma": [woo_product(1, "450", "450", unit=0)]}), self.STORE)
        self.assertEqual((items[0]["_price"], items[0]["_compare_at"]), (450.0, None))

    def test_pagination(self):
        world = World(woo={"woo.ma": [woo_product(i) for i in range(130)]})
        out = run(world, [self.STORE])
        self.assertEqual([p for _, _, p in world.requests], [1, 2])
        self.assertRegex(out, r"woo\.ma\s+woocommerce\s+130 products\s+complete")

    def test_not_a_woocommerce_site(self):
        self.assertEqual(read_one(World(), self.STORE)[0], "error")


class PrestaShopTest(unittest.TestCase):
    STORE = {"name": "Presta", "domain": "presta.ma", "platform": "prestashop"}
    HOME, DROPS = platforms.PRESTA_PATHS

    def test_reads_prices_and_was_price(self):
        world = World(presta={"presta.ma": {self.HOME: [presta_product(1), presta_product(2, 300.0, 300.0)]}})
        status, items, cursor = read_one(world, self.STORE)
        self.assertEqual(status, "ok")
        self.assertEqual((items[0]["_price"], items[0]["_compare_at"]), (700.0, 1300.0))
        self.assertEqual(items[1]["_compare_at"], None)
        self.assertEqual((items[0]["gender"], items[0]["category"]), ("femme", "robes"))
        self.assertEqual(cursor, (1, 1))                                         # next: the promotions listing

    def test_walks_every_page_of_every_listing(self):
        world = World(presta={"presta.ma": {self.HOME: [presta_product(i) for i in range(30)],
                                            self.DROPS: [presta_product(i) for i in range(5)]}})
        out = run(world, [self.STORE])
        self.assertEqual([p for _, _, p in world.requests], [1, 2, 3, 1])
        self.assertRegex(out, r"presta\.ma\s+prestashop\s+35 products\s+complete")

    def test_a_missing_listing_is_skipped(self):
        world = World(presta={"presta.ma": {self.DROPS: [presta_product(1)]}})
        out = run(world, [self.STORE])
        self.assertRegex(out, r"presta\.ma\s+prestashop\s+1 products\s+complete")

    def test_custom_paths(self):
        store = {**self.STORE, "paths": ["/3-femmes"]}
        world = World(presta={"presta.ma": {"3-femmes": [presta_product(1)]}})
        self.assertEqual(len(read_one(world, store)[1]), 1)


class MixedRunTest(unittest.TestCase):
    def test_other_platforms_are_not_slowed_down_by_shopify(self):
        world = World(shopify={f"s{i}.ma": [shopify_product(1)] for i in range(3)},
                      woo={"woo.ma": [woo_product(i) for i in range(250)]}, budget=600)
        stores = [{"name": d, "domain": d} for d in world.shopify] + [WooCommerceTest.STORE]
        out = run(world, stores)
        self.assertIn("Done: 4/4 stores read.", out)
        woo_times = [t for t, d, _ in world.requests if d == "woo.ma"]
        self.assertLess(max(woo_times), 60)                                       # done within the first minute

    def test_tier_filter(self):
        world = World(shopify={"brand.ma": [shopify_product(1)], "shop.ma": [shopify_product(2)]})
        stores = [{"name": "B", "domain": "brand.ma", "tier": "marque"}, {"name": "S", "domain": "shop.ma"}]
        out = run(world, stores, argv=("--dry-run", "--tier", "marque"))
        self.assertIn("Done: 1/1 stores read.", out)
        self.assertNotIn("shop.ma", out)

    def test_detect_reports_the_platform(self):
        out = run(World(woo={"woo.ma": [woo_product(1)]}), [], argv=("--detect", "woo.ma"))
        self.assertIn("woocommerce  yes", out)
        self.assertIn('"platform": "woocommerce"', out)

    def test_unknown_platform(self):
        self.assertEqual(platforms.fetch({"domain": "x", "platform": "nope"}, None)[0], "error")


def magento_product(i, final=99.0, regular=299.0, stock="IN_STOCK"):
    return {"id": i, "sku": f"R{i}", "name": f"ROBE LONGUE {i}", "url_key": f"r{i}-robe", "url_suffix": ".html",
            "stock_status": stock, "small_image": {"url": f"https://www.marwa.com/media/r{i}.jpg"},
            "categories": [{"name": "Robes"}],
            "price_range": {"minimum_price": {"regular_price": {"value": regular}, "final_price": {"value": final}}}}


def zara_product(i, price=29900, old=49900, section="WOMAN", family="DRESS", name=None):
    return {"id": i, "name": name or f"ROBE {i}", "price": price, "oldPrice": old, "sectionName": section,
            "familyName": family, "availability": "in_stock",
            "seo": {"keyword": f"robe-{i}", "seoProductId": f"0{i}", "discernProductId": i},
            "xmedia": [{"path": f"/assets/public/aa/{i}-p", "name": f"{i}-p", "timestamp": "1"}]}


ZARA_TREE = [
    {"id": 1, "name": "FEMME", "sectionName": "WOMAN", "subcategories": [
        {"id": 10, "name": "COLLECTION", "layout": "products-category-view", "subcategories": [
            {"id": 11, "name": "ROBES", "subcategories": [
                {"id": 111, "name": "TOUT VOIR", "layout": "products-category-view"},
                {"id": 112, "name": "COURTES", "layout": "products-category-view"}]},
            {"id": 12, "name": "BAISSE DE PRIX", "subcategories": [
                {"id": 121, "name": "TOUT VOIR", "layout": "products-category-view"}]}]},
        {"id": 13, "name": "GUIDE", "layout": "marketing-content-view"}]},
    {"id": 2, "name": "ENFANTS", "sectionName": "KID", "subcategories": [
        {"id": 21, "name": "FILLE", "layout": "products-category-view"}]},
    {"id": 3, "name": "BEAUTY", "sectionName": "BEAUTY", "subcategories": [
        {"id": 31, "name": "TOUT VOIR", "layout": "products-category-view"}]},
]


class BrandReadersTest(unittest.TestCase):
    def test_woocommerce_follows_the_announced_page_count(self):
        world = World(woo_legacy={"old.ma": [woo_product(i) for i in range(60)]})
        real = world.get
        def capped(url, **kw):                     # the shop serves 25 per page whatever we ask
            url = re.sub(r"per_page=\d+", "per_page=25", url)
            response = real(url, **kw)
            response.headers["X-WP-TotalPages"] = "3"
            return response
        world.get = capped
        store = {"name": "Old", "domain": "old.ma", "platform": "woocommerce"}
        status, items, cursor = read_one(world, store)
        self.assertEqual((len(items), cursor), (25, 2))
        status, items, cursor = read_one(world, store, 3)
        self.assertEqual((len(items), cursor), (10, None))

    def test_woocommerce_falls_back_to_the_older_store_api(self):
        world = World(woo_legacy={"old.ma": [woo_product(1)]})
        status, items, _ = read_one(world, {"name": "Old", "domain": "old.ma", "platform": "woocommerce"})
        self.assertEqual((status, len(items)), ("ok", 1))

    def test_magento_reads_prices_and_pages(self):
        world = World(magento={"www.marwa.com": [magento_product(i) for i in range(150)]
                               + [magento_product(999, final=249.0, regular=249.0, stock="OUT_OF_STOCK")]})
        store = {"name": "Marwa", "domain": "www.marwa.com", "platform": "magento", "gender_hint": "femme"}
        status, items, cursor = read_one(world, store)
        self.assertEqual((status, len(items), cursor), ("ok", 100, 2))
        first = items[0]
        self.assertEqual((first["_price"], first["_compare_at"]), (99.0, 299.0))
        self.assertEqual(first["product_url"], "https://www.marwa.com/r0-robe.html")
        self.assertEqual((first["gender"], first["category"]), ("femme", "robes"))
        status, items, cursor = read_one(world, store, cursor)
        self.assertEqual((len(items), cursor), (51, None))
        last = items[-1]
        self.assertEqual((last["_compare_at"], last["in_stock"]), (None, False))

    def test_zara_targets_price_drops_first_then_listings_and_skips_beauty(self):
        self.assertEqual(platforms.zara_targets(ZARA_TREE), [121, 111, 21])

    def test_zara_reads_every_listing_once_with_sections(self):
        zara = {"tree": ZARA_TREE, "listings": {
            121: [zara_product(1)],
            111: [zara_product(1), zara_product(2, old=None), zara_product(3, family="PERFUME")],
            21: [zara_product(4, section="KID", name="ROBE FILLE")]}}
        world = World(zara=zara)
        store = {"name": "Zara", "domain": "zara.com", "platform": "zara"}
        cursor, items = None, []
        for _ in range(5):
            status, page, cursor = read_one(world, store, cursor)
            self.assertEqual(status, "ok")
            items += page
            if cursor is None:
                break
        self.assertEqual([i["external_id"] for i in items], ["1", "2", "4"])          # no repeat, no perfume
        drop = items[0]
        self.assertEqual((drop["_price"], drop["_compare_at"]), (299.0, 499.0))
        self.assertEqual(drop["product_url"], "https://www.zara.com/ma/fr/robe-1-p01.html?v1=1")
        self.assertEqual(drop["image_url"], "https://static.zara.net/assets/public/aa/1-p/1-p.jpg?ts=1&w=563")
        self.assertEqual(items[1]["_compare_at"], None)
        self.assertEqual((items[2]["gender"], items[2]["category"]), ("enfant", "robes"))   # section wins over "robe"

    def test_zara_runs_in_the_collector(self):
        zara = {"tree": ZARA_TREE, "listings": {121: [zara_product(1)], 111: [zara_product(2)], 21: []}}
        out = run(World(zara=zara), [{"name": "Zara", "domain": "zara.com", "platform": "zara"}])
        self.assertIn("Done: 1/1 stores read.", out)
        self.assertRegex(out, r"zara\.com\s+zara\s+2 products\s+complete")


class ClassifyTest(unittest.TestCase):
    def test_gender_and_category(self):
        self.assertEqual(collect.classify("Robe longue satin", "", ["Femme"]), ("femme", "robes"))
        self.assertEqual(collect.classify("Ensemble garçon 6 ans", "", []), ("enfant", "ensembles"))
        self.assertEqual(collect.classify("حذاء رجالي جلد", "", []), ("homme", "chaussures"))

    def test_store_hints_fill_the_gaps(self):
        self.assertEqual(collect.classify("Uncloud Fermée Noir", "", [], "homme", "accessoires"), ("homme", "accessoires"))


if __name__ == "__main__":
    unittest.main()
