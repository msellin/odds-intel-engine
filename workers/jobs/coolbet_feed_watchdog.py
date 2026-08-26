"""COOLBET-FEED-WATCHDOG-2026-08-26 — keep the Coolbet odds feed alive, and know
the difference between "restart it" and "a human has to look".

The operator asked for a job that restarts the Coolbet daemon when something
happens to it. Diagnosing the 2026-08-26 outage first turned out to matter,
because a naive restarter would have looped for three days without helping:

    launchctl list
      com.oddsintel.coolbet-mac-daemon      exit 0   <- healthy
      com.oddsintel.coolbet-odds-snapshot   exit 1   <- firing every 30 min,
                                                        failing every time

The launchd job was never dead. It fired on schedule, on time, for 80 hours, and
every run failed the same way: HTTP 403 from Imperva because the cookies had
aged out. Root cause one level down — CDP-Chrome was not running, and CDP-Chrome
is the only thing that can mint fresh Imperva cookies (Imperva trusts the
operator's real browser; FlareSolverr's Chrome fails the challenge).

Fixed by launching local/launch_chrome_for_sync.sh, opening a coolbet.com tab in
it, and harvesting the six Imperva cookies into the DB. The feed came back
immediately: 0.01h stale, from 80h.

So "is the job loaded?" is the wrong question, and "restart it" is the wrong
reflex. This watchdog classifies before it acts:

    NOT_LOADED       launchd lost the job          -> reload it (safe, automatic)
    STALE_COOKIES    Imperva cookies aged out      -> refresh from CDP-Chrome
    CDP_DOWN         no Chrome on :9222            -> alert; needs the operator
    BLOCKED          client gets 4xx on everything -> alert; do NOT loop
    HEALTHY          odds arriving                 -> nothing

Only the first two self-heal. The rest alert once and stop, because retrying a
block is how you turn one outage into a rate-limit ban.

The real signal is OUTPUT, not process state: hours since the last Coolbet row
in odds_snapshots. Everything else is a proxy, and today the proxies were all
green while the feed was dead — the same failure shape as the InplayBot UUID bug
and the coolbet healthcheck that watched heartbeats instead of odds.

Usage:
    python3 -m workers.jobs.coolbet_feed_watchdog            # check + act
    python3 -m workers.jobs.coolbet_feed_watchdog --dry-run  # classify only
"""
from __future__ import annotations

import argparse
import logging
import subprocess
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# The launchd job that actually writes odds rows.
ODDS_JOB = "com.oddsintel.coolbet-odds-snapshot"
PLIST = f"~/Library/LaunchAgents/{ODDS_JOB}.plist"

# Hours without a single Coolbet odds row before we call the feed dead. The job
# runs every 30 min, so 3h is six consecutive silent cycles — well past a blip.
FEED_STALE_H = 3.0

# Imperva cookies are refreshed from CDP-Chrome and rot fast; the session itself
# treats >2h as stale, so anything beyond that is worth acting on.
COOKIE_STALE_H = 2.0

# Alert at most this often per state, so a multi-day block sends a handful of
# messages rather than one every run.
ALERT_DEDUP_H = 6.0


def _hours_since_last_odds() -> float | None:
    """Hours since the newest Coolbet row in odds_snapshots. None on error —
    a broken lookup must not manufacture an incident."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT EXTRACT(epoch FROM (now() - max(timestamp))) / 3600.0 AS h "
            "FROM odds_snapshots WHERE bookmaker = 'Coolbet'",
            [],
        )
        if rows and rows[0].get("h") is not None:
            return float(rows[0]["h"])
    except Exception as e:
        log.warning("odds-age lookup failed: %s", e)
    return None


def _hours_since_cookies() -> float | None:
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT EXTRACT(epoch FROM (now() - imperva_cookies_at)) / 3600.0 AS h "
            "FROM coolbet_session_state WHERE id = 1",
            [],
        )
        if rows and rows[0].get("h") is not None:
            return float(rows[0]["h"])
    except Exception as e:
        log.warning("cookie-age lookup failed: %s", e)
    return None


def _job_loaded() -> bool:
    """Is the odds job registered with launchd at all?"""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=15)
        return ODDS_JOB in (out.stdout or "")
    except Exception as e:
        log.warning("launchctl list failed: %s", e)
        return True  # assume loaded rather than reload blindly


def _cdp_up() -> bool:
    """Is CDP-Chrome answering on :9222? Without it, cookies cannot be
    refreshed and every other remedy is moot."""
    import json
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:9222/json/version", timeout=5).read()
        return True
    except Exception:
        return False


def classify() -> tuple[str, str]:
    """Return (state, human-readable reason). Pure — takes no action."""
    odds_h = _hours_since_last_odds()
    if odds_h is None:
        return ("UNKNOWN", "could not read odds_snapshots")
    if odds_h <= FEED_STALE_H:
        return ("HEALTHY", f"last Coolbet odds {odds_h:.1f}h ago")

    # Feed is stale. Work out why, cheapest and most-fixable first.
    if not _job_loaded():
        return ("NOT_LOADED",
                f"no Coolbet odds for {odds_h:.1f}h and {ODDS_JOB} is not "
                f"registered with launchd")

    if not _cdp_up():
        return ("CDP_DOWN",
                f"no Coolbet odds for {odds_h:.1f}h; CDP-Chrome is not "
                f"answering on :9222, so cookies cannot be refreshed. Run "
                f"local/launch_chrome_for_sync.sh and open a coolbet.com tab.")

    # Cookie AGE is a weak signal on its own. Observed 2026-08-26: a run started
    # clean on freshly-harvested cookies, wrote 4,517 rows, and was then
    # re-challenged with a 403 mid-batch — cookies barely minutes old and
    # already rejected. Imperva rotates its challenge far faster than the 2h
    # staleness threshold, so keying only on age would classify a re-challenge
    # as BLOCKED and page a human for something a re-harvest fixes.
    #
    # So: if the feed is stale and CDP is available, re-harvest regardless of
    # age. It is cheap (one CDP read), idempotent, and the single remedy that
    # has actually worked. BLOCKED is reserved for the case where even that has
    # been tried — see run(), which only escalates after a failed refresh.
    cookie_h = _hours_since_cookies()
    if cookie_h is None or cookie_h > COOKIE_STALE_H:
        return ("STALE_COOKIES",
                f"no Coolbet odds for {odds_h:.1f}h and Imperva cookies are "
                f"{cookie_h:.1f}h old" if cookie_h is not None else
                f"no Coolbet odds for {odds_h:.1f}h and cookie age is unknown")
    return ("STALE_COOKIES",
            f"no Coolbet odds for {odds_h:.1f}h despite cookies only "
            f"{cookie_h:.1f}h old — Imperva re-challenges far faster than they "
            f"expire, so re-harvesting is tried before calling this blocked")

    # Unreachable today, kept for the escalation path in run().
    return ("BLOCKED",
            f"no Coolbet odds for {odds_h:.1f}h despite a loaded job, live CDP "
            f"and fresh cookies — upstream is refusing the client. Needs a "
            f"human: check for an Imperva/CDN block or a changed API surface.")


def _reload_job() -> bool:
    import os
    plist = os.path.expanduser(PLIST)
    try:
        subprocess.run(["launchctl", "unload", plist], capture_output=True, timeout=20)
        r = subprocess.run(["launchctl", "load", plist], capture_output=True,
                           text=True, timeout=20)
        return r.returncode == 0
    except Exception as e:
        log.warning("reload failed: %s", e)
        return False


def _refresh_cookies() -> bool:
    """Harvest Imperva cookies from CDP-Chrome. This is the one remedy that
    fixed the real 2026-08-26 403s."""
    try:
        from workers.automation.coolbet_browser_sync import extract_imperva_cookies_from_cdp
        from workers.automation.coolbet_state import persist_imperva_cookies
        c = extract_imperva_cookies_from_cdp(timeout_ms=20000)
        if not c:
            return False
        persist_imperva_cookies(c, source="watchdog_cdp")
        return True
    except Exception as e:
        log.warning("cookie refresh failed: %s", e)
        return False


def _alert(state: str, reason: str) -> None:
    try:
        from workers.notify.telegram import send_telegram
        send_telegram(
            f"🔌 <b>Coolbet feed: {state}</b>\n{reason}",
            dedup_key=f"coolbet-feed-{state}",
            dedup_hours=ALERT_DEDUP_H,
        )
    except Exception as e:
        log.warning("telegram alert failed: %s", e)


def run(dry_run: bool = False) -> dict:
    state, reason = classify()
    result = {"state": state, "reason": reason, "action": "none",
              "checked_at": datetime.now(timezone.utc).isoformat()}
    log.info("coolbet feed watchdog: %s — %s", state, reason)

    if dry_run or state in ("HEALTHY", "UNKNOWN"):
        return result

    if state == "NOT_LOADED":
        result["action"] = "reloaded" if _reload_job() else "reload_failed"
    elif state == "STALE_COOKIES":
        if _refresh_cookies():
            result["action"] = "cookies_refreshed"
        else:
            # The one remedy failed. NOW it is a human problem, and the alert
            # says so rather than reporting a refresh that did not happen.
            result["action"] = "cookie_refresh_failed"
            state = "BLOCKED"
            reason = (f"{reason}; re-harvest from CDP-Chrome failed — check that "
                      f"a coolbet.com tab is open and logged in")
            result["state"], result["reason"] = state, reason
    else:
        # CDP_DOWN / BLOCKED — no safe automatic remedy. Alert and stop.
        result["action"] = "alerted"

    if state != "NOT_LOADED" or result["action"] != "reloaded":
        _alert(state, reason)
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Classify without acting")
    args = ap.parse_args()
    r = run(dry_run=args.dry_run)
    print(f"state  = {r['state']}")
    print(f"reason = {r['reason']}")
    print(f"action = {r['action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
