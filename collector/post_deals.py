"""
Posts the best new deals to a Telegram channel (and prepares captions for Instagram/Facebook).

  export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
  export TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHANNEL=@your_channel
  export SITE_URL=https://hemza.ma     # optional: posts then link to the deal's page on the site
  python post_deals.py --min-score 40 --max 5          # post up to 5 deals
  python post_deals.py --review                        # print only, post nothing (manual approval)

Never re-posts a product unless its price dropped again since the last post.
"""
import argparse, os, sys, requests

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
    """Deals worth posting now: never posted, or cheaper than the last time we posted them."""
    last = {}
    for p in posts:
        last[p["product_id"]] = min(float(p["price"]), last.get(p["product_id"], float("inf")))
    if not include_unverified:
        deals = [d for d in deals if d.get("verified")]
    return [d for d in deals if float(d["price"]) < last.get(d["id"], float("inf"))][:limit]

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
    a = ap.parse_args()

    only_verified = "" if a.include_unverified else "&verified=is.true"
    deals = sb(f"deals?select=*&score=gte.{a.min_score}{only_verified}&order=score.desc&limit=200")
    posts = sb("posts?select=product_id,price&channel=eq.telegram")
    fresh = select(deals, posts, a.max, a.include_unverified)
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
