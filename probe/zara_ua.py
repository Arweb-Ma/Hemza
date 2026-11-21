import requests
U = "https://www.zara.com/ma/fr/categories?ajax=true"
for name, ua in [("hemzabot", "HemzaBot/0.1 (+https://hemza.ma/bot; contact@arweb.ma)"),
                 ("browser", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"),
                 ("hemzabot", "HemzaBot/0.1 (+https://hemza.ma/bot; contact@arweb.ma)")]:
    r = requests.get(U, headers={"User-Agent": ua, "Accept": "application/json"}, timeout=30)
    print(name, r.status_code, len(r.text))
r = requests.get("https://www.zara.com/robots.txt", timeout=30)
print("robots", r.status_code); print(r.text[:3000])
