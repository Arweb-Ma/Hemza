"""
Hemza.ma collector — reads store catalogs (Shopify, WooCommerce, PrestaShop), classifies items
(femme / homme / enfant + category) and writes prices to Supabase.

Usage (PowerShell: $env:NAME = "value" instead of export):
  export SUPABASE_URL=https://xxxx.supabase.co
  export SUPABASE_SERVICE_KEY=...            # service role key (server side only!)
  python collect.py                          # read every store in stores.json
  python collect.py --dry-run                # read, but save nothing
  python collect.py --detect shop.ma other.ma  # which platform is it, and can we read it?
  python collect.py --only casquetta.ma      # one store
  python collect.py --max-minutes 60         # stop after an hour

It is deliberately slow. Shopify stores share one queue (about one request a minute, slower
when refused); every other store has its own, faster one. Run it once or twice a day.
"""
import argparse, json, os, sys, time
from datetime import datetime, timezone
import requests

import platforms
from classify import classify          # re-exported for tests and scripts

MAX_ATTEMPTS = 4        # tries per store per run; a refused store waits for its next turn
MAX_GAP = 900           # never wait longer than 15 minutes for one request
# (start, minimum) seconds between two requests in the same queue.
# Shopify throttles unknown crawlers across ALL its stores at once, hence one shared slow queue.
PACE = {"shopify": (60, 30), "default": (10, 5)}

class Pacer:
    """Spaces out requests and adapts: slower after every refusal, slowly faster after successes."""
    def __init__(self, start, low, high=MAX_GAP, sleep=None, clock=None):
        self.gap, self.low, self.high = start, low, high
        self._sleep, self._clock, self._last = sleep or time.sleep, clock or time.monotonic, None

    def ready_at(self):
        """Clock time at which this queue may send its next request."""
        return self._clock() if self._last is None else self._last + self.gap

    def wait(self):
        remaining = self.ready_at() - self._clock()
        if remaining > 0:
            if remaining >= 90:
                print(f"  … waiting {remaining / 60:.0f} min before the next request")
            self._sleep(remaining)
        self._last = self._clock()

    def success(self):
        self.gap = max(self.low, self.gap * 0.9)    # speed up gently: refusals are costly

    def throttled(self, retry_after=None):
        self.gap = min(self.high, max(self.gap * 2, retry_after or 0))

# ---------- Supabase REST ----------
class DB:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
        key = os.environ["SUPABASE_SERVICE_KEY"]
        self.h = {"apikey": key, "Authorization": f"Bearer {key}",
                  "Content-Type": "application/json"}

    def upsert(self, table, rows, on_conflict):
        h = dict(self.h, Prefer="resolution=merge-duplicates,return=representation")
        r = requests.post(f"{self.url}/{table}?on_conflict={on_conflict}", headers=h, json=rows, timeout=60)
        r.raise_for_status()
        return r.json()

    def insert(self, table, rows):
        r = requests.post(f"{self.url}/{table}", headers=dict(self.h, Prefer="return=minimal"), json=rows, timeout=60)
        r.raise_for_status()

    def latest_prices(self, product_ids):
        if not product_ids:
            return {}
        ids = ",".join(map(str, product_ids))
        r = requests.get(f"{self.url}/price_history?select=product_id,price,observed_at"
                         f"&product_id=in.({ids})&order=observed_at.desc", headers=self.h, timeout=60)
        r.raise_for_status()
        last = {}
        for row in r.json():
            last.setdefault(row["product_id"], row)
        return last

def save(db, store, items):
    store_row = db.upsert("stores", [{"name": store["name"], "domain": store["domain"], "platform": store.get("platform", "shopify"),
                                       "last_crawl_at": datetime.now(timezone.utc).isoformat()}], "domain")[0]
    now = datetime.now(timezone.utc).isoformat()
    prod_rows = [{k: v for k, v in it.items() if not k.startswith("_")} | {"store_id": store_row["id"], "last_seen_at": now}
                 for it in items]
    saved = []
    for i in range(0, len(prod_rows), 200):
        saved += db.upsert("products", prod_rows[i:i+200], "store_id,external_id")
    by_ext = {s["external_id"]: s["id"] for s in saved}
    last = db.latest_prices(list(by_ext.values()))
    ph = []
    for it in items:
        pid = by_ext[it["external_id"]]
        prev = last.get(pid)
        changed = prev is None or float(prev["price"]) != it["_price"]
        stale = prev is not None and prev["observed_at"][:10] != now[:10]   # 1 heartbeat/day for medians
        if changed or stale:
            ph.append({"product_id": pid, "price": it["_price"], "compare_at": it["_compare_at"], "in_stock": it["in_stock"]})
    for i in range(0, len(ph), 500):
        db.insert("price_history", ph[i:i+500])
    return len(items), len(ph)

def queue_key(store):
    """Stores on the same throttled infrastructure share one pacer."""
    platform = store.get("platform", "shopify")
    return "shopify" if platform == "shopify" else store["domain"]

def detect(domains):
    for domain in domains:
        print(f"→ {domain}")
        found = None
        for name in platforms.ADAPTERS:
            result = platforms.fetch({"domain": domain, "name": domain, "platform": name}, None, sample=True)
            if result[0] == "ok":
                print(f"  {name:12} yes, {len(result[1])} products in the sample")
                found = found or name
                for item in result[1][:2]:
                    was = f" (was {item['_compare_at']:.0f})" if item["_compare_at"] else ""
                    print(f"               {item['_price']:.0f} DH{was}  {item['gender']}/{item['category']}  {item['title'][:50]}")
            else:
                print(f"  {name:12} no: {result[-1][:110]}")
            time.sleep(3)
        if found:
            print(f'  add to stores.json: {{ "name": "{domain}", "domain": "{domain}", "platform": "{found}", "gender_hint": null }}')
        else:
            print("  not readable with the built-in readers; ask the shop for its product feed")

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--detect", nargs="+", metavar="DOMAIN", help="identify the platform of these shops")
    ap.add_argument("--only", nargs="+", metavar="DOMAIN", help="only these stores")
    ap.add_argument("--max-minutes", type=int, default=180, help="stop after this long (default 180)")
    ap.add_argument("--stores", default=os.path.join(os.path.dirname(__file__), "stores.json"))
    a = ap.parse_args()

    if a.detect:
        return detect(a.detect)

    with open(a.stores, encoding="utf-8") as f:
        stores = json.load(f)["stores"]
    if a.only:
        stores = [s for s in stores if s["domain"] in a.only]
    db = None if a.dry_run else DB()

    sleep, clock = (lambda s: time.sleep(s)), (lambda: time.monotonic())
    # Remember the pace that worked last time, so a new run does not re-learn it by being refused.
    state_path = a.stores + ".state"
    try:
        with open(state_path, encoding="utf-8") as f:
            learned = json.load(f).get("gap", {})
    except (OSError, ValueError):
        learned = {}
    pacers = {}
    for s in stores:
        key = queue_key(s)
        start, low = PACE.get(key, PACE["default"])
        start = min(MAX_GAP, max(start, float(learned.get(key, 0))))
        pacers.setdefault(key, Pacer(start, low, sleep=sleep, clock=clock))
    deadline = clock() + a.max_minutes * 60
    totals = {s["domain"]: 0 for s in stores}
    done, gave_up = set(), {}
    queue = [(s, None, 0) for s in stores]             # (store, cursor, attempts)

    while queue:
        # Next: the entry whose queue is ready first (keeps fast shops moving while Shopify cools down).
        index = min(range(len(queue)), key=lambda i: pacers[queue_key(queue[i][0])].ready_at())
        s, cursor, attempts = queue[index]
        pacer = pacers[queue_key(s)]
        if pacer.ready_at() > deadline:
            print(f"\nTime budget of {a.max_minutes} min reached: stopping here.")
            break
        queue.pop(index)
        domain = s["domain"]
        pacer.wait()
        print(f"→ {domain}" + (f" ({platforms.label(cursor)})" if cursor else ""))
        result = platforms.fetch(s, cursor)

        if result[0] == "throttled":
            _, retry_after, detail = result
            pacer.throttled(retry_after)
            print(f"  ! refused: {detail}", file=sys.stderr)
            if attempts + 1 < MAX_ATTEMPTS:
                print(f"  … will come back to it later; next pause {pacer.gap / 60:.0f} min")
                queue.append((s, cursor, attempts + 1))
            else:
                gave_up[domain] = "refused"
            continue
        if result[0] == "error":
            print(f"  ! {result[1]}", file=sys.stderr)
            gave_up[domain] = "error"
            continue

        pacer.success()
        _, items, next_cursor = result
        totals[domain] += len(items)
        if a.dry_run:
            print(f"  {len(items)} products")
        elif items:
            n, changed = save(db, s, items)             # saved page by page: nothing is lost if we stop
            print(f"  {n} products, {changed} price rows written")
        if next_cursor is not None:
            queue.insert(0, (s, next_cursor, 0))        # finish this store before starting another
        else:
            done.add(domain)

    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"gap": {"shopify": round(pacers["shopify"].gap)} if "shopify" in pacers else {}}, f)
    except OSError:
        pass

    print("\nSummary")
    for s in stores:
        d = s["domain"]
        state = "complete" if d in done else ("partial" if totals[d] else gave_up.get(d, "not reached"))
        print(f"  {d:24} {s.get('platform', 'shopify'):12} {totals[d]:5} products  {state}")
    read = sum(1 for d in totals if totals[d])
    print(f"Done: {read}/{len(stores)} stores read.")

if __name__ == "__main__":
    main()
