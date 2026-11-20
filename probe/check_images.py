import sys, json, requests
sys.path.insert(0, "collector")
import platforms
for name, store in [("zara", {"name": "Zara", "domain": "zara.com", "platform": "zara"}),
                    ("marwa", {"name": "Marwa", "domain": "www.marwa.com", "platform": "magento", "gender_hint": "femme"})]:
    r = platforms.fetch(store, None, sample=True)
    print(name, r[0], [(i["title"], i["_price"], i["_compare_at"], i["gender"], i["category"]) for i in r[1][:3]] if r[0] == "ok" else r)
    if r[0] == "ok" and r[1]:
        i = r[1][0]
        print("  url:", i["product_url"], requests.head(i["product_url"], timeout=20, allow_redirects=True).status_code)
        print("  img:", i["image_url"], requests.get(i["image_url"], timeout=20).status_code if i["image_url"] else None)
