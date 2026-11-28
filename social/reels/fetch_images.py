"""Download the product photos a reel needs into img/ (stdlib only, run it from home: some shops block servers).

Usage:  py fetch_images.py [reel-id ...] [--force]
Each scene (or hook) lists "image": "img/<name>.jpg" plus "image_url" (from products.image_url in Supabase).
For PrestaShop shops (Lacoste…) the larger "large_default" size is tried first.
You can also drop your own photo at the same img/ path: render.py uses whatever file is there.
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

def candidates(url):
    out = []
    if "-home_default/" in url:
        out.append(url.replace("-home_default/", "-large_default/"))
    out.append(url)
    return out

def fetch(url, dest):
    for u in candidates(url):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "image/avif,image/webp,image/*,*/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if len(data) < 2000:
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            return u
        except OSError as e:
            print("  échec", u, "→", e)
    return None

def items(reel):
    for sc in reel["scenes"]:
        if sc.get("image") and sc.get("image_url"):
            yield sc["image"], sc["image_url"]
        for rel, url in zip(sc.get("images", []), sc.get("image_urls", [])):
            yield rel, url

if __name__ == "__main__":
    force = "--force" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    reels = json.load(open(os.path.join(HERE, "reels.json"), encoding="utf-8"))
    seen = set()
    for reel in reels:
        if only and reel["id"] not in only:
            continue
        for rel, url in items(reel):
            dest = os.path.join(HERE, rel)
            if dest in seen or (os.path.exists(dest) and not force):
                continue
            seen.add(dest)
            got = fetch(url, dest)
            print(("ok   " if got else "MANQUE ") + rel)
