"""
Reads pages with a real (headless) Chromium, for shops that refuse plain HTTP clients.

Used only by stores marked `"via": "browser"` in stores.json (Decathlon.ma). The browser starts on the
first request and is shared by every such store for the whole run; it is closed when the run ends.

What it does: opens the shop's home page once (so the shop's own scripts and cookies run as for any
visitor), then loads listing pages like a visitor would, at the collector's slow pace.
What it does NOT do: solve CAPTCHAs or use "stealth" tricks. If a shop shows a challenge page, the
store is reported as refused and skipped for this run.

Needs: pip install -r collector/requirements-browser.txt && python -m playwright install --with-deps chromium
(or set HEMZA_CHROME to an installed Chrome, e.g. "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
"""
import atexit, json, os, re

TIMEOUT_MS = 45_000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
CHALLENGE = re.compile(r"incapsula|_incap_|request unsuccessful|verify you are human|are you a robot|"
                       r"captcha|just a moment|attention required|access denied|checking your browser", re.I)

_state = {"playwright": None, "browser": None, "context": None, "pages": {}}


def _page(store):
    """One warmed-up tab per shop."""
    domain = store["domain"]
    if domain in _state["pages"]:
        return _state["pages"][domain], None
    if _state["browser"] is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None, "the browser reader needs Playwright: pip install -r collector/requirements-browser.txt"
        _state["playwright"] = sync_playwright().start()
        # HEMZA_CHROME: use an installed Chrome instead of Playwright's own (e.g. on a home PC).
        _state["browser"] = _state["playwright"].chromium.launch(
            headless=True, executable_path=os.environ.get("HEMZA_CHROME") or None)
        _state["context"] = _state["browser"].new_context(
            user_agent=UA, locale="fr-MA", timezone_id="Africa/Casablanca",
            viewport={"width": 1366, "height": 900})
        atexit.register(close)
    page = _state["context"].new_page()
    blocked = _goto(page, f"https://{domain}/")
    if blocked:
        page.close()
        return None, blocked
    _state["pages"][domain] = page
    return page, None


def _goto(page, url):
    """Loads a page. Returns None when it is a normal page, otherwise why it is not."""
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    except Exception as e:                                  # timeouts, DNS, connection reset…
        return f"browser could not open {url}: {str(e).splitlines()[0][:160]}"
    status = response.status if response else 0
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass                                                # busy pages never go idle; that is fine
    if status in (403, 429, 503) or _is_challenge(page):
        return f"bot challenge or refusal (HTTP {status}) on {url}"
    if status >= 400:
        return f"HTTP {status} on {url}"
    return None


def _is_challenge(page):
    try:
        head = (page.title() or "") + " " + page.evaluate("document.body ? document.body.innerText.slice(0, 600) : ''")
    except Exception:
        return False
    # A real listing has product links; a challenge page has none.
    has_products = page.evaluate("document.querySelectorAll('a[href]').length > 40")
    return bool(CHALLENGE.search(head)) and not has_products


def render(store, url, script, wait_for=None):
    """Opens `url` in the shop's tab and runs `script` (a JS function) on it.
    Returns ("ok", value) or ("throttled", None, detail) when refused, or ("error", detail)."""
    page, problem = _page(store)
    if problem:
        return _refusal(problem)
    problem = _goto(page, url)
    if problem:
        return _refusal(problem)
    if wait_for:
        try:
            page.wait_for_selector(wait_for, timeout=15_000)
        except Exception:
            pass
    # Lazy-loaded cards and images appear as the visitor scrolls.
    try:
        page.evaluate("""async () => {
            for (let y = 0; y < document.body.scrollHeight; y += 700) {
                window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120));
            }
            window.scrollTo(0, 0);
        }""")
    except Exception:
        pass
    try:
        return ("ok", page.evaluate(script))
    except Exception as e:
        return ("error", f"could not read {url}: {str(e).splitlines()[0][:160]}")


def get_json(store, url, headers=None):
    """Asks for `url` from inside the shop's own page (same cookies and origin as the visitor).
    Returns ("ok", data, None) or an error tuple, like platforms.get_json."""
    page, problem = _page(store)
    if problem:
        return _refusal(problem)
    try:
        answer = page.evaluate("""async ([url, headers]) => {
            const r = await fetch(url, {headers, credentials: 'include'});
            return {status: r.status, type: r.headers.get('content-type') || '', body: await r.text()};
        }""", [url, {"Accept": "application/json", **(headers or {})}])
    except Exception as e:
        return ("error", f"browser fetch failed: {str(e).splitlines()[0][:160]}")
    if answer["status"] in (403, 429, 503):
        return ("throttled", None, f"HTTP {answer['status']} [browser]")
    if answer["status"] != 200:
        return ("error", f"HTTP {answer['status']} [browser]")
    try:
        return ("ok", json.loads(answer["body"]), None)
    except ValueError:
        return ("error", "answered with a web page, not data [browser]")


def _refusal(detail):
    return ("throttled", None, detail) if "challenge" in detail or "refus" in detail else ("error", detail)


def close():
    for key in ("context", "browser"):
        try:
            if _state[key]:
                _state[key].close()
        except Exception:
            pass
    try:
        if _state["playwright"]:
            _state["playwright"].stop()
    except Exception:
        pass
    _state.update(playwright=None, browser=None, context=None, pages={})
