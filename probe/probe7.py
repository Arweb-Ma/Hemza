import re, json, time, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
def H(site): return {"User-Agent": UA, "Accept-Language": "fr-MA,fr;q=0.9", "Accept": "application/json, text/plain, */*",
                     "Referer": f"https://www.{site}.com/ma/fr/", "Origin": f"https://www.{site}.com", "sec-fetch-site": "same-origin",
                     "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"}
print("### inditex retry with browser headers")
for site, (sid, cid) in {"bershka": (45109536, 40259559), "lefties": (95009036, 90009051)}.items():
    s = requests.Session()
    r = s.get(f"https://www.{site}.com/itxrest/2/catalog/store/{sid}/{cid}/category?languageId=-2&typeCatalog=1&appId=1", headers=H(site), timeout=30)
    print(f"  {site} categories -> {r.status_code} {len(r.text)}B")
print("\n### zara load: 6 categories, 3 s apart")
HZ = {"User-Agent": UA, "Accept": "application/json"}
for cid in [2417772, 2419032, 2420896, 2420795, 2419185, 2443335]:
    t = time.time(); r = requests.get(f"https://www.zara.com/ma/fr/category/{cid}/products?ajax=true", headers=HZ, timeout=40)
    n = sum(len(el.get("commercialComponents", [])) for g in r.json().get("productGroups", []) for el in g.get("elements", [])) if r.status_code == 200 else 0
    print(f"  {cid}: {r.status_code} {len(r.text)//1024}KB {time.time()-t:.1f}s items={n}")
    time.sleep(3)
print("\n### marwa graphql")
def gq(q):
    r = requests.get("https://www.marwa.com/graphql", params={"query": q}, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=40)
    return r.status_code, r.text
for q in ['{categories(filters:{}){items{id name url_path children{id name url_path product_count children{id name url_path product_count}}}}}',
          '{products(search:"",pageSize:3,currentPage:1){total_count page_info{total_pages}}}',
          '{products(filter:{price:{from:"0"}},pageSize:3,currentPage:1){total_count page_info{total_pages} items{name sku url_key categories{name url_path} stock_status}}}']:
    st, txt = gq(q); print(f"  {st} {txt[:1400]}\n")
