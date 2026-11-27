"""
Posts the best new deals to a Telegram channel (and prepares captions for Instagram/Facebook).

  export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
  export TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHANNEL=@your_channel
  export SITE_URL=https://reeklam.ma     # optional: posts then link to the deal's page on the site
  python post_deals.py --min-score 40 --max 5          # post up to 5 deals
  python post_deals.py --review                        # print only, post nothing (manual approval)
  python post_deals.py --include-unverified --max 1 --per-day 6 --between 9-22   # what run.py does

Never re-posts a product unless its price dropped again since the last post.
Verified drops always go first; shops that were posted least in the last 24 h come next,
so one shop with hundreds of promos (LC Waikiki) does not fill the whole channel.
"""
import argparse, os, sys, requests
from datetime import datetime, timedelta, timezone

def morocco_hour(now=None):
    """Local hour in Morocco: GMT, permanently, since 20 September 2026 (decree 2.26.530)."""
    return (now or datetime.now(timezone.utc)).hour

def in_window(window, hour):
    """window like "9-22": posting allowed from 9:00 up to 21:59."""
    if not window:
        return True
    start, end = (int(x) for x in window.split("-"))
    return start <= hour < end

def recent(posts, hours=24, now=None):
    now = now or datetime.now(timezone.utc)
    out = []
    for p in posts:
        at = p.get("posted_at")
        if at and datetime.fromisoformat(at.replace("Z", "+00:00")) > now - timedelta(hours=hours):
            out.append(p)
    return out

def sb(path, method="GET", json=None):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    r = requests.request(method, url, json=json, timeout=30,
                         headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json() if r.text else None

def link(d):
    site = os.environ.get("SITE_URL", "").rstrip("/")
    if site:                                   # send people to our own page first
        return f"{site}/promo/{d['id']}/"
    tpl = d.get("affiliate_tpl")
    return tpl.replace("{url}", requests.utils.quote(d["product_url"], safe="")) if tpl else d["product_url"] + "?utm_source=hemza"

GENDER_EMOJI = {"femme": "👗", "homme": "👔", "enfant": "🧸", "unisexe": "🛍️"}

def caption(d):
    was = d.get("median_30d") if (d.get("n_obs") or 0) >= 5 else (d.get("compare_at") or d.get("median_30d"))
    was = float(was) if was else None
    d = dict(d, price=float(d["price"]))
    disc = d.get("real_disc_pct") or d.get("claimed_disc_pct")
    lines = [
        f"{GENDER_EMOJI.get(d['gender'],'🛍️')} {d['title']}",
        (f"🏷️ {was:.0f} DH → {d['price']:.0f} DH  (-{was - d['price']:.0f} DH, -{disc}%)" if was and disc else f"🏷️ {d['price']:.0f} DH"),
    ]
    if not d.get("verified"):
        lines.append("ℹ️ Remise annoncée par la boutique, vérification en cours")
    if d.get("lowest_90d"):
        lines.append("📉 Prix le plus bas depuis 90 jours")
    lines += [f"🏬 {d['store_name']}", f"👉 {link(d)}", "", f"#reeklam #soldes #maroc #mode{d['gender']}"]
    return "\n".join(lines)

def send(token, channel, deal, text):
    """Photo with caption when there is an image; plain message otherwise or if Telegram rejects the photo."""
    api = f"https://api.telegram.org/bot{token}"
    if deal.get("image_url"):
        r = requests.post(f"{api}/sendPhoto", timeout=30,
                          data={"chat_id": channel, "photo": deal["image_url"], "caption": text[:1024]})
        if r.ok:
            return
    r = requests.post(f"{api}/sendMessage", data={"chat_id": channel, "text": text}, timeout=30)
    r.raise_for_status()

def select(deals, posts, limit, include_unverified=False):
    """Deals worth posting now: never posted, or cheaper than the last time we posted them.
    Verified first, then shops posted least in the last 24 h, then the original (score) order."""
    last = {}
    for p in posts:
        last[p["product_id"]] = min(float(p["price"]), last.get(p["product_id"], float("inf")))
    if not include_unverified:
        deals = [d for d in deals if d.get("verified")]
    fresh = [d for d in deals if float(d["price"]) < last.get(d["id"], float("inf"))]
    shop_posts = {}
    for p in recent(posts):
        store = (p.get("products") or {}).get("store_id")
        shop_posts[store] = shop_posts.get(store, 0) + 1
    order = {id(d): i for i, d in enumerate(fresh)}
    fresh.sort(key=lambda d: (not d.get("verified"), shop_posts.get(d.get("store_id"), 0), order[id(d)]))
    picked, seen = [], set()
    for d in fresh:                            # same item in several colours: post it once
        key = (d.get("store_id"), d.get("title"), float(d["price"]))
        if key in seen:
            continue
        seen.add(key)
        picked.append(d)
        shop_posts[d.get("store_id")] = shop_posts.get(d.get("store_id"), 0) + 1
        if len(picked) >= limit:
            break
    return picked

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=40)
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--include-unverified", action="store_true",
                    help="also post promos we could not check against our own price history yet")
    ap.add_argument("--per-day", type=int, default=0, help="at most this many posts in any 24 h (0 = no limit)")
    ap.add_argument("--between", default="", help='only post during these Morocco hours, e.g. "9-22"')
    a = ap.parse_args()

    if not a.review and not in_window(a.between, morocco_hour()):
        print(f"Outside posting hours ({a.between}h Morocco time).")
        return

    only_verified = "" if a.include_unverified else "&verified=is.true"
    deals = sb(f"deals?select=*&score=gte.{a.min_score}{only_verified}&order=score.desc&limit=200")
    posts = sb("posts?select=product_id,price,posted_at,products(store_id)&channel=eq.telegram")
    limit = a.max
    if a.per_day and not a.review:
        limit = min(limit, a.per_day - len(recent(posts)))
        if limit <= 0:
            print(f"Daily limit reached ({a.per_day} posts in 24 h).")
            return
    fresh = select(deals, posts, limit, a.include_unverified)
    if not fresh:
        print("Nothing new to post.")
        return

    failed = 0
    for d in fresh:
        text = caption(d)
        if a.review:
            print(text, "\n" + "-" * 40); continue
        try:
            send(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"], d, text)
        except requests.RequestException as e:
            failed += 1
            print(f"! could not post {d['title']}: {e}", file=sys.stderr)
            continue
        sb("posts", "POST", [{"product_id": d["id"], "channel": "telegram", "price": d["price"]}])
        print("posted:", d["title"])
    if failed:
        sys.exit(1)

if __name__ == "__main__":
    main()
