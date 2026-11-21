"""
Reads pages with a real Chromium (hidden by default), for shops that refuse plain HTTP clients.

Used by the `decathlon` reader (Decathlon.ma). The browser starts on the
first request and is shared by every such store for the whole run; it is closed when the run ends.

What it does: opens the shop's home page once (so the shop's own scripts and cookies run as for any
visitor), then loads listing pages like a visitor would, at the collector's slow pace.
If the shop first shows a "checking your browser" page, its script is given time to run, as for any
visitor. What it does NOT do: solve CAPTCHAs or use "stealth" tricks. If the check never clears, the
store is reported as refused and skipped for this run.

Needs: pip install -r collector/requirements-browser.txt && python -m playwright install --with-deps chromium
(or set HEMZA_CHROME to an installed Chrome, e.g. "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")

Settings (environment variables, all optional — useful on a home PC):
  HEMZA_CHROME   "chrome" = your installed Google Chrome; or the path of a chrome.exe
  HEMZA_HEADED   1 = show the browser window instead of running it hidden
  HEMZA_PROFILE  a folder where the browser keeps its cookies between runs (like a normal Chrome profile)
When a shop refuses, a screenshot of what the browser saw is saved and its path printed.

Needs: pip install -r collector/requirements-browser.txt && python -m playwright install --with-deps chromium
"""
import atexit, json, os, re, sys, tempfile

TIMEOUT_MS = 45_000
CHALLENGE_WAIT_S = 30                   # time given to a shop's own "checking your browser" page
CHALLENGE = re.compile(r"incapsula|_incap_|request unsuccessful|verify you are human|are you a robot|"
                       r"captcha|just a moment|attention required|access denied|checking your browser", re.I)
OS_TOKEN = {"win32": "Windows NT 10.0; Win64; x64", "darwin": "Macintosh; Intel Mac OS X 10_15_7"}

_state = {"playwright": None, "browser": None, "context": None, "pages": {}}


def _settings():
    chrome = os.environ.get("HEMZA_CHROME") or ""
    launch = {"headless": os.environ.get("HEMZA_HEADED", "") not in ("1", "true", "yes")}
    if chrome.lower() == "chrome":
        launch["channel"] = "chrome"                        # the Google Chrome installed on this machine
    elif chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chromium"                      # Playwright's full Chromium, not the "headless shell"
    return launch, os.environ.get("HEMZA_PROFILE") or None


def _user_agent(version):
    """What this same Chrome says when it runs in a window (hidden Chrome adds "Headless")."""
    major = (version or "131").split(".")[0]
    system = OS_TOKEN.get(sys.platform, "X11; Linux x86_64")
    return f"Mozilla/5.0 ({system}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


def _start():
    from playwright.sync_api import sync_playwright
    launch, profile = _settings()
    pw = _state["playwright"] = sync_playwright().start()
    options = {"locale": "fr-MA", "timezone_id": "Africa/Casablanca", "viewport": {"width": 1366, "height": 900}}
    if profile:
        if launch["headless"]:                              # learn the version first, to set an honest UA
            probe = pw.chromium.launch(**launch)
            options["user_agent"] = _user_agent(probe.version)
            probe.close()
        _state["context"] = pw.chromium.launch_persistent_context(profile, **launch, **options)
    else:
        _state["browser"] = pw.chromium.launch(**launch)
        if launch["headless"]:
            options["user_agent"] = _user_agent(_state["browser"].version)
        _state["context"] = _state["browser"].new_context(**options)
    atexit.register(close)


def _page(store):
    """One warmed-up tab per shop."""
    domain = store["domain"]
    if domain in _state["pages"]:
        return _state["pages"][domain], None
    if _state["context"] is None:
        try:
            _start()
        except ImportError:
            return None, "the browser reader needs Playwright: pip install -r collector/requirements-browser.txt"
        except Exception as e:
            close()
            return None, f"could not start the browser: {str(e).splitlines()[0][:200]}"
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
    refused = status in (403, 429, 503)
    if refused or not _looks_normal(page, strict=False):
        # Protection services first answer with a "checking your browser" page whose script reloads the
        # real page after a few seconds. Let it run, as for any visitor (we never click a CAPTCHA).
        for _ in range(CHALLENGE_WAIT_S // 2):
            page.wait_for_timeout(2000)
            if _looks_normal(page, strict=refused):
                return None
        return f"bot challenge or refusal (HTTP {status}) on {url}{_screenshot(page, url)}"
    if status >= 400:
        return f"HTTP {status} on {url}"
    return None


def _looks_normal(page, strict):
    """A check page shows challenge words and few links. After a refusal code we want proof of the
    contrary (no challenge words AND plenty of links): block pages often show their text in a frame."""
    try:
        head = (page.title() or "") + " " + page.evaluate("document.body ? document.body.innerText.slice(0, 600) : ''")
        links = page.evaluate("document.querySelectorAll('a[href]').length")
    except Exception:                                       # page is reloading
        return False
    calm = not CHALLENGE.search(head)
    return (calm and links > 40) if strict else (calm or links > 40)


def _screenshot(page, url):
    try:
        name = re.sub(r"[^a-z0-9]+", "-", url.lower().split("//")[-1]).strip("-")[:60]
        path = os.path.join(tempfile.gettempdir(), f"hemza-refused-{name}.png")
        page.screenshot(path=path)
        title = page.title()
        return f" — page title: {title!r}, screenshot: {path}"
    except Exception:
        return ""


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
