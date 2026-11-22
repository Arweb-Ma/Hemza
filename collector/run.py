"""
One full cycle, meant to be launched by a scheduler every two hours:

  1. collect   read the stores and save prices               (collect.py)
  2. post      send new deals to Telegram, ~6/day, verified first (post_deals.py)
  3. refresh   ask the website to rebuild with fresh data    (optional deploy hook)

Each step runs only if it is configured, and a failing step never stops the next one.

  SUPABASE_URL, SUPABASE_SERVICE_KEY          required
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL        enables step 2
  SITE_URL                                    optional, posts then link to the site
  SITE_DEPLOY_HOOK                            enables step 3 (a URL to POST to)

  python run.py                  # full cycle
  python run.py --max-minutes 100
  python run.py --skip-collect   # only post and refresh
  python run.py --tier marque    # collect only the big brands (or: --tier boutique)
"""
import argparse, os, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def plan(env, max_minutes=100, skip_collect=False, tier=None):
    """Returns [(name, command or None, reason when skipped)]. Pure, so it can be tested."""
    steps = []
    have_db = bool(env.get("SUPABASE_URL") and env.get("SUPABASE_SERVICE_KEY"))
    script = lambda name: [sys.executable, os.path.join(HERE, name)]

    if skip_collect:
        steps.append(("collect", None, "skipped on request"))
    elif not have_db:
        steps.append(("collect", None, "SUPABASE_URL / SUPABASE_SERVICE_KEY are not set"))
    else:
        steps.append(("collect", script("collect.py") + ["--max-minutes", str(max_minutes)]
                      + (["--tier", tier] if tier else []), ""))

    if not have_db:
        steps.append(("post", None, "SUPABASE_URL / SUPABASE_SERVICE_KEY are not set"))
    elif not (env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHANNEL")):
        steps.append(("post", None, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL are not set"))
    else:
        # Until our own price history can verify drops (5 days of readings), also post the
        # shops' announced promos, labelled "vérification en cours". About 6 a day, daytime only; 2 per run because GitHub often delays or skips scheduled runs.
        steps.append(("post", script("post_deals.py") + ["--include-unverified", "--max", "2",
                                                         "--per-day", "6", "--between", "9-22"], ""))

    if env.get("SITE_DEPLOY_HOOK"):
        steps.append(("refresh", "hook", ""))
    else:
        steps.append(("refresh", None, "SITE_DEPLOY_HOOK is not set (the site also rebuilds on its own schedule)"))
    return steps


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=int, default=100)
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--tier", choices=["marque", "boutique"])
    a = ap.parse_args()

    stamp = lambda: datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n===== Hemza cycle started {stamp()} =====", flush=True)
    failures = []
    for name, command, reason in plan(os.environ, a.max_minutes, a.skip_collect, a.tier):
        if command is None:
            print(f"[{name}] skipped: {reason}", flush=True)
            continue
        print(f"[{name}] started {stamp()}", flush=True)
        try:
            if command == "hook":
                import requests
                requests.post(os.environ["SITE_DEPLOY_HOOK"], timeout=30).raise_for_status()
            else:
                subprocess.run(command, check=True)
        except Exception as e:                      # keep going: one broken step must not block the rest
            failures.append(name)
            print(f"[{name}] FAILED: {e}", file=sys.stderr, flush=True)
    print(f"===== cycle finished {stamp()}" + (f", failed: {', '.join(failures)}" if failures else "") + " =====", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
