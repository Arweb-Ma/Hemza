"""
Hemza.ma collector — reads public Shopify /products.json feeds, classifies items
(femme / homme / enfant + category) and writes prices to Supabase.

Usage:
  export SUPABASE_URL=https://xxxx.supabase.co
  export SUPABASE_SERVICE_KEY=...            # service role key (server side only!)
  python collect.py                          # crawl all stores in stores.json
  python collect.py --dry-run                # print what would be saved
  python collect.py --sample sample.json     # classify a local products.json (offline test)

It is deliberately slow: one request about every minute, slower when stores refuse.
A full run can take from 15 minutes to 3 hours. Run it once or twice a day.
  python collect.py --only casquetta.ma      # one store
  python collect.py --max-minutes 60         # stop after an hour
"""
import argparse, json, os, re, sys, time, unicodedata
from collections import deque
from datetime import datetime, timezone
import requests

UA = "HemzaBot/0.1 (+https://hemza.ma/bot; contact@arweb.ma)"
PAGE_LIMIT = 250
MAX_PAGES = 20

# Pacing. Shopify throttles unknown crawlers across ALL its stores at once, so the pace is
# global, not per store: one request, then a pause, whichever store comes next.
START_GAP = 60          # seconds between two requests at the start of a run
MIN_GAP = 30            # never go faster than this, even when everything works
MAX_GAP = 900           # never wait longer than 15 minutes for one request
MAX_ATTEMPTS = 3        # tries per store per run; a refused store goes to the back of the queue

# ---------- classification (FR / EN / Darija-AR keywords) ----------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()

GENDER_RULES = [
    ("enfant", r"\b(enfant|enfants|kids?|bebe|baby|fille|garcon|junior|girls?|boys?|ans)\b|اطفال|أطفال|بيبي"),
    ("femme",  r"\b(femme|femmes|women|woman|ladies|dame|robe|jupe|abaya|caftan|kaftan|takchita|soutien|lingerie|legging)\b|نساء|نسائي"),
    ("homme",  r"\b(homme|hommes|men|man|masculin|gentleman)\b|رجال|رجالي"),
]
CATEGORY_RULES = [
    ("chaussures", r"chaussure|basket|sneaker|sandale|mocassin|botte|bottine|escarpin|shoe|mule|loafer|حذاء|صندل|سبرديلة"),
    ("sacs",       r"\bsac|pochette|cabas|bag|cartable|valise|حقيبة|صاك"),
    ("traditionnel", r"caftan|kaftan|takchita|djellaba|jellaba|gandoura|abaya|قفطان|تكشيطة|جلابة|عباية"),
    ("robes",      r"\brobe|dress|فستان|كسوة"),
    ("jeans",      r"\bjean|denim"),
    ("pantalons",  r"pantalon|trouser|chino|jogger|jogging"),
    ("vestes",     r"veste|blouson|manteau|doudoune|parka|blazer|jacket|coat"),
    ("pulls",      r"\bpull|sweat|hoodie|cardigan|gilet"),
    ("t-shirts",   r"t-?shirt|tee\b|polo|debardeur|top\b"),
    ("chemises",   r"chemise|shirt|blouse"),
    ("ensembles",  r"ensemble|set\b|survetement|tracksuit"),
    ("accessoires", r"casquette|ceinture|bijou|montre|lunette|echarpe|foulard|cap\b|bonnet"),
]

def classify(title, product_type, tags, gender_hint=None, category_hint=None):
    text = " ".join([title or "", product_type or "", " ".join(tags or [])])
    t = _norm(text) + " " + text   # keep raw for Arabic keywords
    gender = None
    for g, rx in GENDER_RULES:
        if re.search(rx, t):
            gender = g
            break
    gender = gender or gender_hint or "unisexe"
    category = next((c for c, rx in CATEGORY_RULES if re.search(rx, t)), category_hint or "autres")
    return gender, category

# ---------- Shopify feed ----------
class Pacer:
    """Spaces out requests and adapts: slower after every refusal, slowly faster after successes."""
    def __init__(self, start=START_GAP, low=MIN_GAP, high=MAX_GAP, sleep=None, clock=None):
        self.gap, self.low, self.high = start, low, high
        self._sleep, self._clock, self._last = sleep or time.sleep, clock or time.monotonic, None

    def wait(self):
        if self._last is not None:
            remaining = self._last + self.gap - self._clock()
            if remaining > 0:
                if remaining >= 90:
                    print(f"  … waiting {remaining / 60:.0f} min before the next request")
                self._sleep(remaining)
        self._last = self._clock()

    def success(self):
        self.gap = max(self.low, self.gap * 0.8)

    def throttled(self, retry_after=None):
        self.gap = min(self.high, max(self.gap * 2, retry_after or 0))

DIAGNOSTIC_HEADERS = ("retry-after", "server", "cf-mitigated", "cf-ray", "x-shopify-stage", "x-request-id")

def describe(r):
    """One line explaining a refusal, so we can tell a rate limit from a bot challenge."""
    seen = [f"{h}={r.headers[h]}" for h in DIAGNOSTIC_HEADERS if h in r.headers]
    kind = (r.headers.get("content-type") or "?").split(";")[0]
    return f"HTTP {r.status_code} [{kind}] " + " ".join(seen)

def fetch_page(domain, page):
    """Returns ("ok", products) | ("throttled", retry_after_seconds, detail) | ("error", detail)."""
    url = f"https://{domain}/products.json?limit={PAGE_LIMIT}&page={page}"
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
    except requests.RequestException as e:
        return ("error", str(e))
    if r.status_code in (429, 430, 503):
        asked = r.headers.get("Retry-After", "")
        return ("throttled", int(asked) if asked.isdigit() else None, describe(r))
    if r.status_code != 200:
        return ("error", describe(r))
    try:
        return ("ok", r.json().get("products", []))
    except ValueError:
        return ("error", "no product feed here (not a Shopify catalog, or a challenge page) " + describe(r))

def normalize(domain, p, gender_hint=None, category_hint=None):
    variants = p.get("variants") or []
    if not variants:
        return None
    avail = [v for v in variants if v.get("available")]
    pool = avail or variants
    best = min(pool, key=lambda v: float(v.get("price") or 1e9))
    price = float(best["price"])
    cmp_ = best.get("compare_at_price")
    cmp_ = float(cmp_) if cmp_ not in (None, "", "0.00") else None
    gender, category = classify(p.get("title"), p.get("product_type"), p.get("tags"), gender_hint, category_hint)
    sizes = sorted({v.get("option1") for v in avail if v.get("option1")})[:12]
    img = (p.get("images") or [{}])[0].get("src")
    return {
        "external_id": str(p["id"]),
        "handle": p.get("handle"),
        "title": p.get("title", "").strip(),
        "brand": p.get("vendor"),
        "gender": gender,
        "category": category,
        "image_url": img,
        "product_url": f"https://{domain}/products/{p.get('handle')}",
        "sizes": sizes,
        "in_stock": bool(avail),
        "_price": price,
        "_compare_at": cmp_,
    }

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
    store_row = db.upsert("stores", [{"name": store["name"], "domain": store["domain"],
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

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sample", help="classify a local products.json file")
    ap.add_argument("--only", nargs="+", metavar="DOMAIN", help="only these stores")
    ap.add_argument("--gap", type=int, default=START_GAP, help="seconds between requests at the start (default 60)")
    ap.add_argument("--max-minutes", type=int, default=180, help="stop after this long (default 180)")
    ap.add_argument("--stores", default=os.path.join(os.path.dirname(__file__), "stores.json"))
    a = ap.parse_args()

    if a.sample:
        data = json.load(open(a.sample, encoding="utf-8"))
        for p in data["products"]:
            n = normalize("example.ma", p)
            if n:
                disc = f"-{round(100*(1-n['_price']/n['_compare_at']))}%" if n["_compare_at"] else ""
                print(f"{n['gender']:8} {n['category']:13} {n['_price']:>8.2f} DH {disc:>5}  {n['title']}")
        return

    stores = json.load(open(a.stores, encoding="utf-8"))["stores"]
    db = None if a.dry_run else DB()
    if a.only:
        stores = [s for s in stores if s["domain"] in a.only]
    pacer = Pacer(start=a.gap, sleep=lambda s: time.sleep(s), clock=lambda: time.monotonic())
    deadline = time.monotonic() + a.max_minutes * 60
    totals = {s["domain"]: 0 for s in stores}
    done, gave_up = set(), {}
    queue = deque((s, 1, 0) for s in stores)          # (store, page, attempts)

    while queue:
        if time.monotonic() + pacer.gap > deadline and pacer._last is not None:
            print(f"\nTime budget of {a.max_minutes} min reached: stopping here.")
            break
        s, page, attempts = queue.popleft()
        domain = s["domain"]
        pacer.wait()
        print(f"→ {domain} (page {page})")
        result = fetch_page(domain, page)

        if result[0] == "throttled":
            _, retry_after, detail = result
            pacer.throttled(retry_after)
            print(f"  ! refused: {detail}", file=sys.stderr)
            if attempts + 1 < MAX_ATTEMPTS:
                print(f"  … will come back to it later; next pause {pacer.gap / 60:.0f} min")
                queue.append((s, page, attempts + 1))
            else:
                gave_up[domain] = "refused"
            continue
        if result[0] == "error":
            print(f"  ! {result[1]}", file=sys.stderr)
            gave_up[domain] = "error"
            continue

        pacer.success()
        raw = result[1]
        items = [n for p in raw if (n := normalize(domain, p, s.get("gender_hint"), s.get("category_hint")))]
        totals[domain] += len(items)
        if a.dry_run:
            print(f"  {len(items)} products")
        elif items:
            n, changed = save(db, s, items)             # saved page by page: nothing is lost if we stop
            print(f"  {n} products, {changed} price rows written")
        if len(raw) == PAGE_LIMIT and page < MAX_PAGES:
            queue.appendleft((s, page + 1, 0))          # finish this store before moving on
        else:
            done.add(domain)

    print("\nSummary")
    for s in stores:
        d = s["domain"]
        state = "complete" if d in done else ("partial" if totals[d] else gave_up.get(d, "not reached"))
        print(f"  {d:22} {totals[d]:5} products  {state}")
    read = sum(1 for d in totals if totals[d])
    print(f"Done: {read}/{len(stores)} stores read.")

if __name__ == "__main__":
    main()
