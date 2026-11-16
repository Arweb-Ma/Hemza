"""
Posts the best new deals to a Telegram channel (and prepares captions for Instagram/Facebook).

  export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
  export TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHANNEL=@hemza_ma
  python post_deals.py --min-score 40 --max 5          # post up to 5 deals
  python post_deals.py --review                        # print only, post nothing (manual approval)

Never re-posts a product unless its price dropped again since the last post.
"""
import argparse, os, requests

def sb(path, method="GET", json=None):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    r = requests.request(method, url, json=json, timeout=30,
                         headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json() if r.text else None

def link(d):
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
    lines += [f"🏬 {d['store_name']}", f"👉 {link(d)}", "", f"#hemza #soldes #maroc #mode{d['gender']}"]
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=40)
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--include-unverified", action="store_true",
                    help="also post promos we could not check against our own price history yet")
    a = ap.parse_args()

    deals = sb(f"deals?select=*&score=gte.{a.min_score}&order=score.desc&limit=50")
    posted = {(p["product_id"]): float(p["price"]) for p in sb("posts?select=product_id,price&channel=eq.telegram")}
    if not a.include_unverified:
        deals = [d for d in deals if d.get("verified")]
    fresh = [d for d in deals if d["id"] not in posted or float(d["price"]) < posted[d["id"]]][: a.max]

    for d in fresh:
        cap = caption(d)
        if a.review:
            print(cap, "\n" + "-" * 40); continue
        tok, ch = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"]
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendPhoto",
                          data={"chat_id": ch, "photo": d["image_url"], "caption": cap}, timeout=30)
        r.raise_for_status()
        sb("posts", "POST", [{"product_id": d["id"], "channel": "telegram", "price": d["price"]}])
        print("posted:", d["title"])

if __name__ == "__main__":
    main()
