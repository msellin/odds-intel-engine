"""
Coolbet browser sync (COOLBET-BROWSER-SYNC, 2026-06-12).

Drives a real persistent Chromium via Playwright to fetch the operator's
Coolbet bet history. This is the ONLY path that survives Coolbet's full
Imperva stack (TLS fingerprint + JS challenge + dynamic server cookies) —
FS-based plain-requests hits HTTP 500 with an _Incapsula_Resource JS
challenge at the /s/sbgate/bets/history endpoint specifically. A real
Chromium executes the JS challenge naturally.

ONE-TIME SETUP:
    python3 -m workers.automation.coolbet_browser_sync --login
    # → Opens a Chromium window, you log into Coolbet (SMS verify if asked)
    # → Press ENTER in terminal when logged in. Profile is persisted to
    #   ~/.config/oddsintel/coolbet-playwright-profile/ — survives forever.

RUNTIME (called from Mac daemon):
    bets = fetch_pending_bets()
    # → Returns list of {match_name, market, selection, stake, odds,
    #   placed_at, ticket_id} for everything in PENDING status.

Headless after first login: launch_persistent_context(headless=True). The
session cookies persist; no SMS needed again unless Coolbet invalidates
the trust marker (rare — usually months).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load .env eagerly so cdp_auto_login (and any helper that reads
# COOLBET_USER / COOLBET_PASS / FLARESOLVERR_URL) gets the right values
# regardless of how this module is invoked (`python -m ...` from the
# repo root, launchd plist, ad-hoc shell, etc.).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger(__name__)

PROFILE_DIR = Path.home() / ".config" / "oddsintel" / "coolbet-playwright-profile"
HISTORY_PAGE = "https://www.coolbet.com/et/panuste-ajalugu/sport"
LOGIN_PAGE   = "https://www.coolbet.com/et/login"
API_HISTORY  = "https://www.coolbet.com/s/sbgate/bets/history"

# CDP attach mode: connect to the operator's REAL Chrome (launched with
# --remote-debugging-port=9222) instead of spawning our own. This is the
# only reliable path past Imperva: their Chrome is a long-warmed
# residential-IP browser that Imperva already trusts. Our spawned
# Chromium starts cold and gets challenged → API calls 403 → SPA never
# initialises. Confirmed via direct test 2026-06-12.
CDP_URL = os.getenv("COOLBET_CHROME_CDP_URL", "http://localhost:9222")


def _ensure_profile_dir() -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


def _sync_playwright_factory():
    """Try patchright first (Playwright fork with deeper anti-detection —
    patches navigator.webdriver at the binary level, removes --enable-
    automation entirely, fixes CDP leaks). Falls back to vanilla
    playwright if patchright not installed.

    Playwright-stealth (added via _apply_stealth) is layered on top to
    catch anything patchright doesn't already neutralise."""
    try:
        from patchright.sync_api import sync_playwright
        log.info("Using patchright (anti-detection fork)")
        return sync_playwright
    except ImportError:
        sync_playwright = _sync_playwright_factory()
        log.info("patchright unavailable — using vanilla playwright")
        return sync_playwright


def _launch_context(p, *, headless: bool):
    """Launch Chrome with anti-automation tweaks. Tries the system's real
    Chrome first (channel="chrome") — its fingerprint differs from the
    Playwright-bundled Chromium in ways Imperva probes for. Falls back
    to bundled Chromium with the standard AutomationControlled flag."""
    profile_dir = _ensure_profile_dir()
    common = dict(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport={"width": 1280, "height": 800},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
        # Strip Playwright's default --enable-automation so navigator.webdriver
        # stays false. patchright does this too but belt-and-suspenders.
        ignore_default_args=["--enable-automation"],
    )
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **common)
    except Exception as e:
        log.info("System Chrome unavailable (%s) — falling back to bundled Chromium.", e)
        return p.chromium.launch_persistent_context(**common)


def _apply_stealth(ctx) -> None:
    """No-op. Was using playwright-stealth, but its non-configurable
    property patches (e.g. offsetHeight) conflicted with Coolbet's own
    JS — `utils.replaceProperty` would throw `Cannot redefine property`
    and the page would crash before the Imperva checkbox rendered.
    Patchright handles automation hiding at the binary level without
    breaking page JS, so no extra layer needed.

    Kept as a function so future stealth toggles have a clean hook."""
    return


def interactive_login() -> int:
    """One-time setup: open Chromium with the persistent profile so the
    operator can log into Coolbet. Profile cookies persist for next runs.

    Why not automate the login: Coolbet's SMS-2FA + occasional CAPTCHA
    is intentionally manual. Doing it once via real Chromium also seeds
    the localStorage with cbauth where Coolbet's frontend JS expects it
    — that's what makes subsequent headless XHRs work."""
    sync_playwright = _sync_playwright_factory()

    profile_dir = _ensure_profile_dir()
    print(f"  Profile dir: {profile_dir}")
    print(f"  Opening Coolbet login page. Sign in (incl. SMS if asked).")
    print(f"  When you're logged in and you see your account/balance, press ENTER here.")

    with sync_playwright() as p:
        ctx = _launch_context(p, headless=False)
        _apply_stealth(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_PAGE)
        try:
            input("  >>> Press ENTER when logged in <<<\n")
        except (EOFError, KeyboardInterrupt):
            pass
        # Snapshot what we learned for diagnostics.
        try:
            page.goto(HISTORY_PAGE, wait_until="domcontentloaded")
            time.sleep(2)
            cookies = ctx.cookies()
            print(f"  Profile now has {len(cookies)} cookies.")
            cb_cookies = [c["name"] for c in cookies if "incap" in c["name"] or c["name"] in ("reese84","cbauth","data")]
            print(f"  Imperva/session markers: {cb_cookies}")
        except Exception as e:
            print(f"  Warm-on-history-page failed: {e}")
        ctx.close()
    print("  ✓ Login flow done — profile saved.")
    return 0


def cdp_auto_login(*, max_wait_s: int = 300) -> int:
    """Fill the Coolbet login form in the CDP-Chrome window + click
    the login button automatically. If SMS is required, the operator
    completes that step in the browser; we poll until the URL leaves
    /login (success) or max_wait_s elapses.

    Idempotent: if already logged in (no login form found), we report
    success and return early."""
    sync_playwright = _sync_playwright_factory()
    email = os.getenv("COOLBET_USER") or os.getenv("COOLBET_EMAIL") or ""
    password = os.getenv("COOLBET_PASS") or os.getenv("COOLBET_PASSWORD") or ""
    if not email or not password:
        print("✗ COOLBET_USER / COOLBET_PASS not set in .env")
        return 2

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL, timeout=10000)
        except Exception as e:
            print(f"✗ Can't connect to CDP at {CDP_URL}: {e}")
            print("  Run: ./local/launch_chrome_for_sync.sh")
            return 3

        ctx = browser.contexts[0]
        # Reuse an existing tab if it's already on Coolbet, else open new.
        page = None
        for pg in ctx.pages:
            if "coolbet.com" in pg.url:
                page = pg
                print(f"  reusing existing Coolbet tab: {pg.url}")
                break
        if page is None:
            page = ctx.new_page()
        page.goto(LOGIN_PAGE, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)

        # If we're already logged in (no login form), report and exit.
        # Heuristic: the login page shows "Logi sisse" as a submit button;
        # if we don't find it after a brief wait, assume logged in.
        try:
            page.wait_for_selector("input[type='email'], input[name='email']", timeout=5000)
        except Exception:
            print("✓ Already logged in (no email field on page)")
            return 0

        print("  Filling login form…")
        try:
            # Coolbet's React form uses controlled inputs — type into them
            # rather than set value directly so the framework picks it up.
            page.fill("input[type='email'], input[name='email']", email)
            page.fill("input[type='password'], input[name='password']", password)
        except Exception as e:
            print(f"✗ Couldn't fill form: {e}")
            return 4

        # Click the green submit button inside the form. The header has a
        # "Logi sisse" link that we must NOT match — it opens the dialog
        # rather than submitting. Strategy: find the password field, then
        # the nearest submit button (always inside the same form).
        print("  Clicking Logi sisse…")
        try:
            # First, try the form-scoped submit button.
            submit = page.locator(
                "form button[type='submit'], "
                "button[type='submit']:has-text('LOGI SISSE'), "
                "button[type='submit']:has-text('Logi sisse')"
            ).first
            try:
                submit.click(timeout=8000)
            except Exception:
                # Fallback: type Enter inside the password field — Coolbet
                # supports that form submission shortcut.
                print("    submit click missed → trying Enter on password field")
                page.locator("input[type='password'], input[name='password']").first.press("Enter")
        except Exception as e:
            print(f"✗ Couldn't submit form: {e}")
            return 5

        print(f"  Submitted. Watching for login success (up to {max_wait_s}s).")
        print("  If SMS appears in the browser, enter the code there.")

        deadline = time.time() + max_wait_s
        while time.time() < deadline:
            try:
                url = page.url
                # Success signal: page navigates away from /login or balance appears.
                if "/login" not in url:
                    print(f"  ✓ logged in — page is now at {url}")
                    return 0
                # Some flows keep /login URL but render account info — check
                # for a logged-in marker (balance badge / account menu).
                has_balance = page.evaluate(
                    "() => !!document.querySelector('[data-test=\"balance\"], [data-testid=\"balance\"], [class*=\"Balance\"]')"
                )
                if has_balance:
                    print("  ✓ logged in — balance widget detected")
                    return 0
            except Exception:
                pass
            time.sleep(2)

        print(f"✗ Login didn't complete within {max_wait_s}s")
        return 6


def fetch_pending_bets_via_cdp(*, timeout_ms: int = 30000) -> list[dict]:
    """Connect to the operator's real Chrome via CDP and fetch pending
    bets. Chrome must be running with --remote-debugging-port=9222.

    Uses a NEW page in the existing browser so we don't disturb the
    operator's open tabs. The new page inherits all cookies + localStorage
    from the user-data-dir — Imperva sees a fully-warmed residential
    browser, no challenge."""
    sync_playwright = _sync_playwright_factory()

    captured: list[dict] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(CDP_URL, timeout=10000)
            except Exception as e:
                log.error("CDP connect failed at %s — is Chrome running with "
                          "--remote-debugging-port=9222? %s", CDP_URL, e)
                return []

            # Reuse the existing browser context (the operator's profile).
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            # SILENT-SYNC (2026-06-12): reuse an EXISTING Coolbet tab if
            # one is open — opening a new tab brings the CDP-Chrome window
            # to the foreground on macOS, which interrupts the operator's
            # workflow. Only open a fresh tab as a last resort.
            page = None
            for pg in ctx.pages:
                try:
                    if "coolbet.com" in pg.url:
                        page = pg
                        break
                except Exception:
                    continue
            if page is None:
                page = ctx.new_page()

            def _on_response(resp):
                try:
                    url = resp.url
                    if "/s/sbgate/bets/history" not in url:
                        return
                    body = resp.json()
                    # Coolbet's response shape (verified 2026-06-12):
                    #   { tickets: [...], hasNextPage: bool, totals: {...} }
                    if isinstance(body, dict):
                        items = (body.get("tickets") or body.get("data")
                                  or body.get("results") or body.get("items") or [])
                    else:
                        items = body
                    if isinstance(items, list):
                        captured.extend(items)
                        log.info("captured %d bets from %s", len(items), url)
                except Exception as e:
                    log.debug("response intercept failed: %s", e)

            page.on("response", _on_response)
            try:
                # If we're already on the history page, just reload —
                # reload triggers the XHR without changing the tab URL or
                # bringing the window to front. If on another Coolbet
                # page, goto the history URL (still in the SAME tab so
                # no new-tab focus event).
                if HISTORY_PAGE in page.url:
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                else:
                    page.goto(HISTORY_PAGE, wait_until="domcontentloaded",
                               timeout=timeout_ms)
                # XHR fires after DOM ready — wait briefly for capture.
                page.wait_for_timeout(5000)
            except Exception as e:
                log.warning("history page load failed: %s", e)
            # DO NOT close the page — it might be the operator's tab.
            # DO NOT close the browser — it's the operator's Chrome.
    except Exception as e:
        log.error("CDP fetch failed: %s", e)
    return captured


def fetch_pending_bets(*, headless: bool = True,
                        timeout_ms: int = 30000) -> list[dict]:
    """Open the persistent profile in headless Chromium, navigate to the
    bet-history page, intercept the /s/sbgate/bets/history XHR fired by
    Coolbet's frontend, return the parsed JSON.

    Returns a list of bet dicts as Coolbet renders them — each entry has
    at minimum:
      - id (Coolbet's internal ticket id)
      - status ('PENDING' here)
      - selections: array of {event_name, market_name, selection_name, odds, ...}
      - stake, currency, placed_at

    Returns empty list on any failure — caller treats that as "couldn't
    sync this tick, fall back to user_placed_at-only dedup".

    headless=False is for debugging — shows the browser, you can see what
    Coolbet renders. Set via env COOLBET_BROWSER_HEADFUL=1."""
    sync_playwright = _sync_playwright_factory()

    if os.getenv("COOLBET_BROWSER_HEADFUL"):
        headless = False

    profile_dir = _ensure_profile_dir()
    if not (profile_dir / "Default").exists() and not any(profile_dir.iterdir()):
        log.error("Empty profile dir — run `python3 -m workers.automation.coolbet_browser_sync --login` first.")
        return []

    captured: list[dict] = []
    with sync_playwright() as p:
        ctx = _launch_context(p, headless=headless)
        _apply_stealth(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Hook the response BEFORE navigating so we don't miss the XHR.
        def _on_response(resp):
            try:
                if "/s/sbgate/bets/history" not in resp.url:
                    return
                if "ticketStatus=PENDING" not in resp.url and "ticketStatus=all" not in resp.url:
                    return
                body = resp.json()
                # Coolbet returns either a list directly or a paginated envelope.
                # Normalise to a list so callers don't care.
                if isinstance(body, dict):
                    items = body.get("data") or body.get("results") or body.get("items") or []
                else:
                    items = body
                if isinstance(items, list):
                    captured.extend(items)
                    log.info("captured %d bets from %s", len(items), resp.url)
            except Exception as e:
                log.debug("response intercept failed (non-fatal): %s", e)

        page.on("response", _on_response)
        try:
            page.goto(HISTORY_PAGE, wait_until="domcontentloaded",
                       timeout=timeout_ms)
            # XHR fires after DOM ready. Wait briefly for capture.
            page.wait_for_timeout(5000)
        except Exception as e:
            log.warning("page load failed: %s", e)
        ctx.close()

    return captured


def normalize_for_dedup(raw_bet: dict) -> dict | None:
    """Reduce a raw Coolbet ticket to a (match_name, market, selection,
    placed_at, ticket_id) tuple suitable for fuzzy-matching against our
    simulated_bets queue.

    Coolbet's ticket shape (verified 2026-06-12):
      { id, status, total_stake, ticket_type ('single' | 'combo'),
        total_matches, created_at, currency,
        first_match: { sport_name, match_name, league_name,
                       market_name, outcome_name, sport_icon, sport_category_id },
        first_bet_odds, ... }

    For combos (ticket_type != 'single' OR total_matches > 1) we mark
    is_combo=True so the daemon's combo-single dedup logic can treat
    them separately. We use first_match for the canonical text — combos
    are matched at the ticket level, not per-leg.

    Returns None when the bet shape isn't what we expect — caller falls
    back to user_placed_at dedup for that one."""
    try:
        first = raw_bet.get("first_match") or {}
        if not first:
            return None
        match_name = first.get("match_name") or ""
        market = first.get("market_name") or ""
        selection = first.get("outcome_name") or ""
        if not (match_name and market and selection):
            return None
        ticket_id = raw_bet.get("id")
        placed_at = raw_bet.get("created_at")
        is_combo = (raw_bet.get("ticket_type") != "single"
                     or (raw_bet.get("total_matches") or 1) > 1)
        return {
            "match_name": match_name,
            "market": market,
            "selection": selection,
            "placed_at": placed_at,
            "ticket_id": ticket_id,
            "is_combo": is_combo,
            "status": raw_bet.get("status"),
            "stake": raw_bet.get("total_stake"),
            "odds": raw_bet.get("first_bet_odds"),
            "sport_icon": first.get("sport_icon"),
        }
    except Exception as e:
        log.debug("normalize failed: %s", e)
        return None


# Market-name translation: Coolbet's Estonian/English labels → our DB enum.
# Add new variants here as you spot them; unknown markets resolve to None
# and that ticket falls through to user_placed_at-only dedup.
_MARKET_MAP_PARTIALS: list[tuple[str, str]] = [
    ("lõpptulemus",          "1x2"),
    ("match result",          "1x2"),
    ("aasia händikäp",        "asian_handicap"),
    ("asian handicap",        "asian_handicap"),
    ("väravate arv",          "o/u"),
    ("total goals",           "o/u"),
    ("mõlemad meeskonnad",    "btts"),
    ("both teams to score",   "btts"),
    ("topelt võimalus",       "double_chance"),
    ("double chance",         "double_chance"),
    ("loobu loosist",         "draw_no_bet"),
    ("draw no bet",           "draw_no_bet"),
]


def _coolbet_market_to_key(coolbet_market: str) -> str | None:
    """Coolbet's market name → our market enum value. Case-insensitive
    substring match because Coolbet appends line values to some markets
    (e.g. 'Väravate arv (Üle/Alla) 2.5')."""
    m = (coolbet_market or "").lower()
    for needle, key in _MARKET_MAP_PARTIALS:
        if needle in m:
            return key
    return None


def _coolbet_selection_to_key(market_key: str, cb_selection: str,
                                cb_home: str, cb_away: str) -> str | None:
    """Coolbet outcome name → our selection enum.

    1X2 / DC / DNB / AH: outcome_name is the team name (or 'Viik' for
    draw). Match against cb_home/cb_away with fuzzy comparison to handle
    'FC Dinamo Batumi' vs 'Dinamo Batumi' style differences.
    O/U: outcome_name is 'Üle 2.5' or 'Alla 2.5' → 'over X.Y' / 'under X.Y'."""
    sel = (cb_selection or "").strip()
    sel_lower = sel.lower()
    if market_key == "o/u":
        # Estonian Üle/Alla → English over/under, then preserve the number.
        # Coolbet doesn't always include the line in outcome_name — pull from
        # market name if absent.
        if "üle" in sel_lower or sel_lower.startswith("over"):
            base = "over"
        elif "alla" in sel_lower or sel_lower.startswith("under"):
            base = "under"
        else:
            return None
        # Extract the X.Y line from the selection itself.
        import re
        m = re.search(r"(\d+(?:\.\d+)?)", sel)
        if m:
            return f"{base} {m.group(1)}"
        return base
    # For team-based markets, fuzzy-match selection to home/away
    from rapidfuzz import fuzz
    home_score = fuzz.token_set_ratio(sel.lower(), (cb_home or "").lower())
    away_score = fuzz.token_set_ratio(sel.lower(), (cb_away or "").lower())
    if "viik" in sel_lower or "draw" in sel_lower:
        return "draw"
    if home_score >= 75 and home_score >= away_score:
        return "home"
    if away_score >= 75:
        return "away"
    return None


def match_coolbet_to_simulated(norm_bet: dict, simulated_bets: list[dict]) -> dict | None:
    """Given a normalised Coolbet ticket and our candidates from
    simulated_bets, find the simulated_bet that corresponds to it.

    Strategy:
      - Parse Coolbet's 'match_name' as 'Home - Away'
      - Map Coolbet's market + selection labels to our enum values
      - Fuzzy-match the team names against each simulated_bet's home/away

    Returns the matched simulated_bet dict, or None on no match. We want
    to be CONSERVATIVE — a false positive marks a legit edge as placed
    and the daemon would skip placing it (losing the edge). False
    negatives just mean we re-attempt placement which our existing dedup
    catches (real_bets row prevents duplicate at next tick)."""
    from rapidfuzz import fuzz
    cb_match = norm_bet.get("match_name") or ""
    if " - " not in cb_match:
        return None
    parts = cb_match.split(" - ", 1)
    cb_home = parts[0].strip()
    cb_away = parts[1].strip()

    market_key = _coolbet_market_to_key(norm_bet.get("market", ""))
    if not market_key:
        return None
    selection_key = _coolbet_selection_to_key(
        market_key, norm_bet.get("selection", ""), cb_home, cb_away,
    )
    if not selection_key:
        return None

    best = None
    best_score = 0
    for sb in simulated_bets:
        if sb.get("market") != market_key:
            continue
        # Selection comparison: for AH the selection includes a handicap
        # number that may not match exactly. Loose-equal: starts-with.
        sb_sel = (sb.get("selection") or "").lower()
        sk_lower = (selection_key or "").lower()
        if not (sb_sel.startswith(sk_lower.split()[0]) or sk_lower.startswith(sb_sel.split()[0])):
            continue
        home_score = fuzz.token_set_ratio(cb_home.lower(), (sb.get("home_team") or "").lower())
        away_score = fuzz.token_set_ratio(cb_away.lower(), (sb.get("away_team") or "").lower())
        avg = (home_score + away_score) / 2
        if avg >= 70 and avg > best_score:
            best = sb
            best_score = avg
    return best


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--login", action="store_true", help="One-time interactive login (spawned Chromium — deprecated)")
    p.add_argument("--fetch", action="store_true", help="Fetch + print pending bets (spawned Chromium — deprecated)")
    p.add_argument("--cdp-fetch", action="store_true", help="Fetch via CDP attach to user's real Chrome (requires --remote-debugging-port=9222)")
    p.add_argument("--cdp-login", action="store_true", help="Open the CDP-Chrome on Coolbet login + wait for you to finish logging in")
    p.add_argument("--cdp-auto-login", action="store_true", help="Auto-fill + click login in CDP-Chrome (uses COOLBET_USER/PASS); waits for SMS if asked")
    p.add_argument("--headful", action="store_true", help="Visible browser (debug)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    if args.login:
        return interactive_login()
    if args.cdp_auto_login:
        return cdp_auto_login()
    if args.cdp_fetch:
        bets = fetch_pending_bets_via_cdp()
        print(f"  fetched {len(bets)} bets via CDP:")
        for b in bets[:25]:
            norm = normalize_for_dedup(b)
            keys_preview = sorted(b.keys())[:8] if isinstance(b, dict) else type(b).__name__
            print(f"    raw_keys={keys_preview}…")
            if norm:
                print(f"    → {norm['match_name'][:40]:40s}  {norm['market']:20s}  {norm['selection']:18s}  ticket={norm['ticket_id']}")
        return 0
    if args.fetch:
        if args.headful:
            os.environ["COOLBET_BROWSER_HEADFUL"] = "1"
        bets = fetch_pending_bets()
        print(f"  fetched {len(bets)} bets:")
        for b in bets[:25]:
            norm = normalize_for_dedup(b)
            print(f"    raw_keys={sorted(b.keys())[:8]}…")
            if norm:
                print(f"    → {norm['match_name'][:40]:40s}  {norm['market']:20s}  {norm['selection']:18s}  ticket={norm['ticket_id']}")
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
