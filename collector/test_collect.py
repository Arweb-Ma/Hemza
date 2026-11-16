"""Offline tests for the collector: python -m unittest collector/test_collect.py"""
import io, os, sys, unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(__file__))
import collect


class FakeResponse:
    def __init__(self, status, products=None, headers=None, html=False):
        self.status_code, self._products, self._html = status, products, html
        self.headers = {"content-type": "text/html" if html else "application/json", **(headers or {})}

    def json(self):
        if self._html:
            raise ValueError("not json")
        return {"products": self._products}


def product(i):
    return {"id": i, "title": f"Jean homme {i}", "handle": f"p{i}", "vendor": "X",
            "variants": [{"price": "200.00", "compare_at_price": "300.00", "available": True, "option1": "M"}]}


class World:
    """A fake Shopify: lets one request through every `budget` seconds across ALL stores."""
    def __init__(self, catalogs, budget=0, retry_after="60", blocked=()):
        self.now, self.catalogs, self.budget = 0.0, catalogs, budget
        self.retry_after, self.blocked = retry_after, set(blocked)
        self.last_ok, self.requests = None, []

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now

    def get(self, url, **_):
        domain = url.split("/")[2]
        page = int(url.rsplit("page=", 1)[1])
        self.requests.append((round(self.now), domain, page))
        too_soon = self.last_ok is not None and self.now - self.last_ok < self.budget
        if domain in self.blocked or too_soon:
            return FakeResponse(429, headers={"Retry-After": self.retry_after} if self.retry_after else {})
        self.last_ok = self.now
        items = self.catalogs[domain]
        if items is None:
            return FakeResponse(200, html=True)
        start = (page - 1) * collect.PAGE_LIMIT
        return FakeResponse(200, items[start:start + collect.PAGE_LIMIT])


def run(world, stores, argv=("--dry-run",)):
    import json, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"stores": [{"name": d, "domain": d} for d in stores]}, f)
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
    return out.getvalue()


class PacerTest(unittest.TestCase):
    def test_slows_down_after_refusals_and_recovers(self):
        pacer = collect.Pacer(start=60, low=30, high=900, sleep=lambda s: None, clock=lambda: 0)
        pacer.throttled(None); self.assertEqual(pacer.gap, 120)
        pacer.throttled(600); self.assertEqual(pacer.gap, 600)      # honours Retry-After when longer
        pacer.throttled(5000); self.assertEqual(pacer.gap, 900)     # capped
        for _ in range(30):
            pacer.success()
        self.assertEqual(pacer.gap, 30)                              # never faster than the floor


class CollectorTest(unittest.TestCase):
    def test_reads_every_store_when_nothing_is_refused(self):
        world = World({"a.ma": [product(i) for i in range(53)], "b.ma": [product(i) for i in range(300)]})
        out = run(world, ["a.ma", "b.ma"])
        self.assertIn("Done: 2/2 stores read.", out)
        # a.ma: one short page; b.ma: 250 + 50. No wasted request for an empty page.
        self.assertEqual([(d, p) for _, d, p in world.requests], [("a.ma", 1), ("b.ma", 1), ("b.ma", 2)])
        self.assertGreaterEqual(world.requests[1][0] - world.requests[0][0], collect.MIN_GAP)

    def test_global_budget_is_respected_and_all_stores_are_eventually_read(self):
        stores = [f"s{i}.ma" for i in range(6)]
        world = World({d: [product(i) for i in range(40)] for d in stores}, budget=300)
        out = run(world, stores)
        self.assertIn("Done: 6/6 stores read.", out)
        self.assertLess(len(world.requests), 6 * collect.MAX_ATTEMPTS)   # few refusals, no hammering
        self.assertLess(world.now, 180 * 60)

    def test_a_store_that_always_refuses_does_not_block_the_others(self):
        world = World({"ok.ma": [product(1)], "no.ma": []}, blocked={"no.ma"}, retry_after=None)
        out = run(world, ["no.ma", "ok.ma"])
        self.assertIn("Done: 1/2 stores read.", out)
        self.assertEqual(sum(1 for _, d, _ in world.requests if d == "no.ma"), collect.MAX_ATTEMPTS)
        self.assertRegex(out, r"no\.ma\s+0 products\s+refused")

    def test_a_non_shopify_site_is_reported_not_crashed_on(self):
        out = run(World({"html.ma": None}), ["html.ma"])
        self.assertIn("no product feed here", out)

    def test_time_budget_stops_the_run(self):
        world = World({"no.ma": []}, blocked={"no.ma"}, retry_after="900")
        out = run(world, ["no.ma"], argv=("--dry-run", "--max-minutes", "10"))
        self.assertIn("Time budget of 10 min reached", out)
        self.assertEqual(len(world.requests), 1)

    def test_only_filter(self):
        world = World({"a.ma": [product(1)], "b.ma": [product(2)]})
        run(world, ["a.ma", "b.ma"], argv=("--dry-run", "--only", "b.ma"))
        self.assertEqual([d for _, d, _ in world.requests], ["b.ma"])


class ClassifyTest(unittest.TestCase):
    def test_gender_and_category(self):
        self.assertEqual(collect.classify("Robe longue satin", "", ["Femme"]), ("femme", "robes"))
        self.assertEqual(collect.classify("Ensemble garçon 6 ans", "", []), ("enfant", "ensembles"))
        self.assertEqual(collect.classify("حذاء رجالي جلد", "", []), ("homme", "chaussures"))

    def test_store_hints_fill_the_gaps(self):
        self.assertEqual(collect.classify("Uncloud Fermée Noir", "", [], "homme", "accessoires"), ("homme", "accessoires"))


if __name__ == "__main__":
    unittest.main()
