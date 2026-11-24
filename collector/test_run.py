"""Offline tests for the cycle runner and the Telegram poster. Run: python collector/test_run.py"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import post_deals, run

DB = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "k"}
TG = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHANNEL": "@c"}


def summary(env, **kw):
    return {name: ("run" if command else "skip") for name, command, _ in run.plan(env, **kw)}


class PlanTest(unittest.TestCase):
    def test_nothing_configured_runs_nothing(self):
        self.assertEqual(summary({}), {"collect": "skip", "post": "skip", "refresh": "skip"})

    def test_database_only_collects(self):
        self.assertEqual(summary(DB), {"collect": "run", "post": "skip", "refresh": "skip"})

    def test_everything_configured(self):
        env = {**DB, **TG, "SITE_DEPLOY_HOOK": "https://hook"}
        self.assertEqual(summary(env), {"collect": "run", "post": "run", "refresh": "run"})

    def test_skip_collect_and_time_budget(self):
        self.assertEqual(summary({**DB, **TG}, skip_collect=True)["collect"], "skip")
        command = run.plan(DB, max_minutes=45)[0][1]
        self.assertEqual(command[-2:], ["--max-minutes", "45"])

    def test_where_is_passed_to_the_collector(self):
        command = run.plan(DB, tier="marque", where="pc")[0][1]
        self.assertEqual(command[-4:], ["--tier", "marque", "--where", "pc"])
        self.assertNotIn("--where", run.plan(DB)[0][1])


DEAL = {"id": 7, "title": "Veste en cuir", "gender": "homme", "price": "700.00", "median_30d": 1300, "n_obs": 19,
        "compare_at": "1500.00", "real_disc_pct": 46, "claimed_disc_pct": 53, "lowest_90d": True,
        "verified": True, "store_name": "Test", "product_url": "https://t.ma/p", "affiliate_tpl": None}


class PosterTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SITE_URL", None)

    def test_caption_shows_the_drop_in_dirhams(self):
        text = post_deals.caption(DEAL)
        self.assertIn("1300 DH → 700 DH  (-600 DH, -46%)", text)
        self.assertIn("Prix le plus bas depuis 90 jours", text)
        self.assertNotIn("vérification en cours", text)

    def test_unverified_deals_say_so(self):
        text = post_deals.caption({**DEAL, "verified": False, "n_obs": 1})
        self.assertIn("1500 DH → 700 DH", text)                     # the store's own "was" price
        self.assertIn("Remise annoncée par la boutique", text)

    def test_links_go_to_the_site_when_it_is_live(self):
        self.assertEqual(post_deals.link(DEAL), "https://t.ma/p?utm_source=hemza")
        os.environ["SITE_URL"] = "https://hemza.ma/"
        self.assertEqual(post_deals.link(DEAL), "https://hemza.ma/promo/7/")

    def test_only_new_or_cheaper_verified_deals_are_posted(self):
        deals = [DEAL, {**DEAL, "id": 8}, {**DEAL, "id": 9, "verified": False, "title": "Jean"}, {**DEAL, "id": 10, "price": "650.00"}]
        posts = [{"product_id": 7, "price": "700.00"},                      # same price: not again
                 {"product_id": 10, "price": "900.00"}, {"product_id": 10, "price": "700.00"}]  # cheaper now
        self.assertEqual([d["id"] for d in post_deals.select(deals, posts, 5)], [8, 10])
        self.assertEqual([d["id"] for d in post_deals.select(deals, posts, 5, include_unverified=True)], [8, 10, 9])
        self.assertEqual(len(post_deals.select(deals, posts, 1)), 1)


    def test_shops_rotate_and_colours_are_not_repeated(self):
        lcw = {**DEAL, "verified": False, "store_id": 1}
        deals = [{**lcw, "id": 1, "title": "Sweat"}, {**lcw, "id": 2, "title": "Sweat"},
                 {**lcw, "id": 3, "title": "Short"}, {**lcw, "id": 4, "store_id": 2, "title": "Veste"}]
        now = datetime.now(timezone.utc).isoformat()
        posts = [{"product_id": 99, "price": "10", "posted_at": now, "products": {"store_id": 1}}]
        self.assertEqual([d["id"] for d in post_deals.select(deals, posts, 3, include_unverified=True)], [4, 1, 3])

    def test_posting_hours_and_daily_count(self):
        self.assertTrue(post_deals.in_window("9-22", 9))
        self.assertFalse(post_deals.in_window("9-22", 22))
        self.assertTrue(post_deals.in_window("", 3))
        old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        self.assertEqual(len(post_deals.recent([{"posted_at": old}, {"posted_at": now}, {}])), 1)

    def test_runner_posts_unverified_with_a_daily_cap(self):
        command = dict((n, c) for n, c, _ in run.plan({**DB, **TG}))["post"]
        self.assertIn("--include-unverified", command)
        self.assertEqual(command[command.index("--per-day") + 1], "6")


if __name__ == "__main__":
    unittest.main()
