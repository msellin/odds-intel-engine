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
    WEDGED_SESSION   sweep's FS session is stuck   -> destroy ONLY that session
    CDP_DOWN         no Chrome on :9222            -> alert; needs the operator
    BLOCKED          Coolbet is challenging us     -> alert; do NOT loop
    HEALTHY          odds arriving                 -> nothing

Only the first three self-heal. The rest alert once and stop, because retrying a
block is how you turn one outage into a rate-limit ban.

WEDGED_SESSION and BLOCKED look identical from the DB (feed dead, cookies fresh)
and have OPPOSITE remedies — cycle the session vs. stop touching Coolbet at all.
They are separated by one fo-tree GET on the sweep's own FS session: HTTP 500
after a fixed ~61s with zero bytes is a dead Chrome tab inside FlareSolverr; a
small-but-real challenge page in ~2s is Imperva. Before 2026-09-18 this branch
printed "check transport first" and refreshed cookies anyway, which is how a
crashed tab cost 9.7 hours of the placement venue's feed.

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
import os
from pathlib import Path
import time
import signal
import subprocess
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# The launchd job that actually writes odds rows.
ODDS_JOB = "com.oddsintel.coolbet-odds-snapshot"
PLIST = f"~/Library/LaunchAgents/{ODDS_JOB}.plist"

# WEDGED-SESSION-SELF-HEAL (2026-09-18). The FS session the odds sweep runs on,
# mirroring COOLBET_FLARE_SESSION in local/launchd/<ODDS_JOB>.plist. The sweep is
# deliberately isolated from `coolbet_prod` (FS-SESSION-ISOLATION 2026-07-05), so
# this watchdog must name the reader's session explicitly: it runs in a different
# process with a different env and would otherwise probe and destroy the REAL-MONEY
# placer's session instead.
ODDS_FS_SESSION = os.getenv("COOLBET_ODDS_FLARE_SESSION", "coolbet_odds_reader")

# Hours without a single Coolbet odds row before we call the feed dead. The job
# runs every 30 min, so 3h is six consecutive silent cycles — well past a blip.
FEED_STALE_H = 3.0

# WEDGE-PROBE-EARLY (2026-09-18, same day as the self-heal that this corrects).
# The self-heal only ran the wedge probe from inside the "feed is stale" branch,
# i.e. after FEED_STALE_H = 3h of silence. Measured the same evening: the sweep's
# session wedged at 16:09 UTC and at 18:05 the watchdog was still printing
# "HEALTHY — last Coolbet odds 2.0h ago" and taking no action, because 2.0 < 3.0.
# Two hours of odds lost to a fault that is DETERMINISTIC (a wedged session never
# recovers on its own), DEFINITIVELY detectable (HTTP 500 at ~61s with 0 bytes)
# and CHEAP to test (0.6s and 199 KB when healthy).
#
# So the wedge probe no longer waits on the staleness clock. One missed sweep is
# enough to justify a single request, which bounds a wedge at roughly one
# watchdog interval (~20 min) instead of 3h+. The 3h threshold stays for the
# SLOWER diagnoses below (job not loaded, CDP down, cookie age), where acting
# early would mean paging a human over a quiet slate.
ODDS_SWEEP_INTERVAL_H = 0.5       # the sweep runs :03/:33
WEDGE_PROBE_AFTER_H = 0.75        # one missed sweep plus margin

# Imperva cookies are refreshed from CDP-Chrome and rot fast; the session itself
# treats >2h as stale, so anything beyond that is worth acting on.
COOKIE_STALE_H = 2.0

# A bulk sweep touches 100+ matches in one minute; the UI placer touches only
# the few it holds picks for. This separates the two writers without a schema
# change — see the note in the odds-age lookup.
BULK_SWEEP_MIN_MATCHES = 25

# ZERO-PICKS ALARM (2026-08-28): the feed being healthy does not mean the BOT is
# working. A pipeline that evaluates nothing produces "0 picks", which reads
# exactly like a quiet day with no qualifying prices — the same silent shape as
# the InplayBot UUID bug, which hid behind "0 bets" for 11 days.
#
# The two cases are separable: if Coolbet AND Pinnacle both priced upcoming
# matches in the window and the bot still wrote nothing, that is not a quiet
# day. Below this many priced matches we stay quiet, because then there really
# was nothing to bet.
PICKS_STALE_H = 14.0
PICKS_MIN_PRICED_MATCHES = 20
# The bot whose pick-cadence proxies "is the pipeline evaluating at all?". MUST be
# an ACTIVE, high-volume bot. Was `bot_coolbet_value_v1` — RETIRED 2026-09-08 — which
# made the NO_PICKS check alarm every ~20min about a dead bot that CORRECTLY writes
# nothing (a legacy false-alarm that flooded the ops channel). `bot_v10_all` is the
# live flagship (writes shadow_bets every refresh). The `_picks_bot_active()` guard
# below makes this class of bug impossible to reintroduce silently. [2026-09-10]
PICKS_BOT = "bot_v10_all"

# Alert at most this often per state, so a multi-day block sends a handful of
# messages rather than one every run.
ALERT_DEDUP_H = 6.0


def _hours_since_last_odds() -> float | None:
    """Hours since the newest BULK Coolbet sweep. None on error — a broken
    lookup must not manufacture an incident.

    COOLBET-WATCHDOG-BLINDED (2026-08-28): this used to take the newest Coolbet
    row of any kind. Since COOLBET-UI-PLACER started writing snapshots from the
    match pages it visits, that row is kept fresh by the PLACER even when the
    bulk scraper is dead — so the watchdog reported HEALTHY through a 6-hour
    scraper outage on 8.1h-stale Imperva cookies. Adding a second writer blinded
    the monitor for the first one.

    The two are separable by breadth, not by timestamp: a bulk sweep touches
    100+ matches, while the placer only visits the handful it holds picks for.
    So freshness is measured over sweep-shaped minutes only.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            # Hour buckets, not minutes: a bulk sweep spreads across several
            # minutes touching a few matches each, so a per-minute test never
            # reaches the breadth threshold. Measuring from the bucket START
            # over-estimates staleness by up to an hour, which is the safe
            # direction for a watchdog.
            "SELECT EXTRACT(epoch FROM (now() - max(t))) / 3600.0 AS h FROM ("
            "  SELECT date_trunc('hour', timestamp) AS t"
            "    FROM odds_snapshots WHERE bookmaker = 'Coolbet'"
            "     AND timestamp > now() - interval '48 hours'"
            "   GROUP BY 1 HAVING COUNT(DISTINCT match_id) >= %s"
            ") sweeps",
            [BULK_SWEEP_MIN_MATCHES],
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
            # Column is imperva_cookies_refreshed_at. The old name
            # `imperva_cookies_at` does not exist, so this query raised
            # UndefinedColumn on every call, the except swallowed it, and the
            # helper returned None forever — the watchdog reported "cookie age
            # is unknown" on every run and the "cookies only Xh old" branch of
            # classify() was unreachable dead code. Found 2026-08-28.
            "SELECT EXTRACT(epoch FROM (now() - imperva_cookies_refreshed_at)) "
            "/ 3600.0 AS h FROM coolbet_session_state WHERE id = 1",
            [],
        )
        if rows and rows[0].get("h") is not None:
            return float(rows[0]["h"])
    except Exception as e:
        log.warning("cookie-age lookup failed: %s", e)
    return None


def _picks_bot_active() -> bool:
    """True only if PICKS_BOT is an ACTIVE bot. A retired bot correctly writes no
    picks, so alarming on its silence is a false alarm (the bot_coolbet_value_v1
    ops-channel flood, 2026-09-08→10). Fails CLOSED (returns False → stay quiet)
    on any error — a broken lookup must never manufacture a NO_PICKS incident."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT is_active FROM bots WHERE name = %s", [PICKS_BOT])
        return bool(rows and rows[0].get("is_active"))
    except Exception as e:  # noqa: BLE001
        log.debug("_picks_bot_active check failed (staying quiet): %s", e)
        return False


def _picks_gap() -> tuple[float | None, int]:
    """(hours since the bot's last pick, matches both books priced meanwhile).

    Returns (None, 0) on any error — a broken lookup must not manufacture an
    incident, same rule as the odds-age lookup.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT EXTRACT(epoch FROM (now() - MAX(s.pick_time)))/3600.0 AS h
                 FROM shadow_bets s JOIN bots b ON b.id = s.bot_id
                WHERE b.name = %s""",
            [PICKS_BOT],
        )
        h = float(rows[0]["h"]) if rows and rows[0].get("h") is not None else None
        # How many upcoming matches had BOTH books priced in the window? If this
        # is small, "no picks" is honest rather than broken.
        priced = execute_query(
            """SELECT COUNT(*) AS n FROM (
                 SELECT o.match_id
                   FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                  WHERE o.timestamp > now() - (%s || ' hours')::interval
                    AND m.date > now() AND o.market = '1x2'
                    AND o.bookmaker IN ('Coolbet','Pinnacle')
                  GROUP BY o.match_id
                 HAVING COUNT(DISTINCT o.bookmaker) = 2) x""",
            [str(int(PICKS_STALE_H))],
        )
        return h, int(priced[0]["n"]) if priced else 0
    except Exception as e:
        log.warning("picks-gap lookup failed: %s", e)
        return None, 0


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


def _betting_pipeline_ran_recently() -> bool:
    """True if the betting pipeline has COMPLETED inside the pick-staleness window.

    NO-PICKS-IS-NOT-NO-EVALUATION (2026-09-14). `priced >= 20` was the proxy for
    "this is not a quiet day", but it answers the wrong question: it measures
    whether PRICES exist, not whether the pipeline EVALUATED them. Those came
    apart the moment pick volume legitimately fell — OU-CALIBRATOR-DOMAIN-MISMATCH
    removed a curve that had been manufacturing ~10x the picks, so the honest
    output on a normal day is now sometimes zero (measured: of 161 O/U selections
    on 2026-09-14 the best available edge was +5.3% against an 8% floor, and of 87
    1x2 candidates the Pinnacle veto killed 11 and one passed).

    With prices present and nothing clearing, the old condition fires on a
    correctly-working pipeline. The distinction that actually matters is whether
    the pipeline RAN: if it ran and wrote nothing, the gate rejected everything and
    that is honest silence; if it has not run, that is the outage this check exists
    to catch.

    Fails CLOSED (returns False -> stay alarming) on any error, matching the rule
    used by every other lookup here: a broken lookup must not SILENCE an incident
    any more than it may manufacture one.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT count(*) AS n FROM pipeline_runs
                WHERE job_name IN ('betting_pipeline', 'betting_refresh')
                  AND status = 'completed'
                  AND started_at > now() - (%s || ' hours')::interval""",
            [str(int(PICKS_STALE_H))],
        )
        return bool(rows and rows[0]["n"])
    except Exception as e:  # noqa: BLE001
        log.warning("betting-pipeline recency lookup failed (staying loud): %s", e)
        return False


def classify() -> tuple[str, str]:
    """Return (state, human-readable reason). Pure — takes no action."""
    odds_h = _hours_since_last_odds()
    if odds_h is None:
        return ("UNKNOWN", "could not read odds_snapshots")
    # A WEDGED SESSION IS DETECTABLE LONG BEFORE THE FEED LOOKS STALE.
    # This runs ahead of the staleness gate on purpose — see WEDGE-PROBE-EARLY.
    # It costs one request and only after a sweep has already been missed, so a
    # healthy feed (rows arriving every 30 min) never pays for it.
    if odds_h > WEDGE_PROBE_AFTER_H:
        early = _probe_odds_session()
        if early["state"] == "wedged":
            return ("WEDGED_SESSION",
                    f"no Coolbet odds for {odds_h:.1f}h — under the {FEED_STALE_H}h "
                    f"staleness threshold, but the FS session {ODDS_FS_SESSION!r} "
                    f"is already provably stuck ({early['detail']}). Destroying it "
                    f"now rather than waiting out the clock.")

    if odds_h <= FEED_STALE_H:
        # Feed is fine — but is the BOT producing? A healthy feed with a silent
        # bot is the failure this check exists for.
        picks_h, priced = _picks_gap()
        if (_picks_bot_active() and picks_h is not None and picks_h > PICKS_STALE_H
                and priced >= PICKS_MIN_PRICED_MATCHES
                and not _betting_pipeline_ran_recently()):
            return ("NO_PICKS",
                    f"feed healthy ({odds_h:.1f}h) but {PICKS_BOT} has written no "
                    f"pick for {picks_h:.1f}h while {priced} upcoming matches had "
                    f"both Coolbet and Pinnacle priced, AND no betting pipeline run "
                    f"completed in that window — the pipeline is not evaluating, "
                    f"this is not a quiet day")
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
    # FRESH COOKIES + DEAD FEED = TRANSPORT, NOT COOKIES (2026-09-10).
    # Twice a transport fault had been misread as a cookie problem while the
    # watchdog re-harvested for hours: COOLBET-GET-NO-TIMEOUT-2026-09-04, and the
    # 5.3h outage on 2026-09-10 whose real cause was COOLBET_NO_FS=true in a stale
    # INSTALLED plist (direct plain-requests is blackholed by Imperva because
    # reese84 is TLS-bound). The 09-10 fix rewrote this branch's MESSAGE to say
    # "check transport first" but left the ACTION as _refresh_cookies().
    #
    # WEDGED-SESSION-SELF-HEAL (2026-09-18): so it happened a third time, and a
    # message is not a remedy. Coolbet odds were dead 9.7h while this watchdog ran
    # 29 times, printed that exact "probably NOT the cookies" sentence every run,
    # and re-harvested cookies it had itself just declared innocent. The cause was
    # a crashed Chrome tab inside the sweep's FS session: every fo-tree GET
    # returned HTTP 500 after a fixed ~61s with zero bytes, while FlareSolverr
    # itself stayed healthy and a FRESH session answered in 2.0s with 190,708
    # bytes of real board. Nothing in the loop could ever have fixed that.
    #
    # probe_coolbet_reachable() has distinguished `wedged` from `challenged` since
    # COOLBET-PROBE 2026-09-11 and its docstring already said "the remedy differs"
    # — it was simply never called from here. One request, on the sweep's own
    # session, is what turns this branch from narration into a diagnosis.
    probe = _probe_odds_session()
    if probe["state"] == "wedged":
        return ("WEDGED_SESSION",
                f"no Coolbet odds for {odds_h:.1f}h; cookies are fresh "
                f"({cookie_h:.1f}h) and Coolbet is NOT challenging us — the FS "
                f"session '{ODDS_FS_SESSION}' is stuck ({probe['detail']}). "
                f"Destroying it so the next sweep builds a clean one.")

    # `challenged` on the sweep's own session is NOT a wedge — Coolbet rendered a
    # real answer, so this is the Imperva flag (runbook §2/§6/§7) and destroying
    # sessions does nothing for it. Reduce footprint and let the flag decay.
    if probe["state"] == "challenged":
        return ("BLOCKED",
                f"no Coolbet odds for {odds_h:.1f}h; cookies are fresh "
                f"({cookie_h:.1f}h) and the FS session is NOT wedged — Coolbet "
                f"answered with a challenge page ({probe['detail']}). The flag is "
                f"live: see runbook §7, reduce footprint "
                f"(scripts/ops/coolbet_pause_resume.sh pause) and let it decay. "
                f"Do NOT cycle sessions at this.")

    # `ok` (feed dead but fo-tree answers → downstream fault) or `down` (our own
    # plumbing). Neither is a cookie problem; a re-harvest is still cheap and
    # idempotent, so it is attempted, but the message must not assert a cause.
    return ("STALE_COOKIES",
            f"no Coolbet odds for {odds_h:.1f}h despite cookies only "
            f"{cookie_h:.1f}h old — so this is probably NOT the cookies, and the "
            f"FS session is not wedged (probe: {probe['state']} — "
            f"{probe['detail']}). Check TRANSPORT (runbook §6): is the sweep "
            f"logging 'NO_FS mode'? is FlareSolverr up on :8191? has the INSTALLED "
            f"plist drifted from local/launchd/? A re-harvest is tried anyway "
            f"because it is cheap, but do not let it mask a transport fault")

    # Unreachable today, kept for the escalation path in run().
    return ("BLOCKED",
            f"no Coolbet odds for {odds_h:.1f}h despite a loaded job, live CDP "
            f"and fresh cookies — upstream is refusing the client. Needs a "
            f"human: check for an Imperva/CDN block or a changed API surface.")


def _probe_odds_session() -> dict:
    """ONE fo-tree GET on the SWEEP's FS session. Never raises.

    WEDGED-SESSION-SELF-HEAL (2026-09-18). Deliberately probes
    `ODDS_FS_SESSION`, not the watchdog's ambient default — the default is
    `coolbet_prod`, the real-money placer's session, and a wrong answer here
    would route the destroy remedy at it.

    Returns probe_coolbet_reachable()'s dict; on import/other failure returns
    state "down" so the caller falls through to the cookie path rather than
    destroying a session on no evidence.
    """
    try:
        from workers.automation.coolbet_explorer import probe_coolbet_reachable
        return probe_coolbet_reachable(session_name=ODDS_FS_SESSION)
    except Exception as e:  # noqa: BLE001
        log.warning("wedge probe failed (non-fatal): %s", e)
        return {"state": "down", "detail": f"probe unavailable: {e}",
                "elapsed_s": 0, "bytes": 0}


def _destroy_fs_session(name: str) -> bool:
    """Destroy one named FS session. `coolbet_prod` is refused outright — it is
    the real-money placer's authed session, and no feed problem justifies
    dropping it."""
    if name == "coolbet_prod":
        log.error("refusing to destroy the real-money session %r", name)
        return False
    fs_url = os.getenv("FLARESOLVERR_URL", "http://localhost:8191")
    try:
        import json as _json
        import urllib.request as _url
        req = _url.Request(
            f"{fs_url.rstrip('/')}/v1",
            data=_json.dumps({"cmd": "sessions.destroy", "session": name}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with _url.urlopen(req, timeout=30) as r:
            ok = _json.loads(r.read()).get("status") == "ok"
        log.info("destroy FS session %r → %s", name, "ok" if ok else "failed")
        return ok
    except Exception as e:  # noqa: BLE001
        log.warning("destroying FS session %r failed: %s", name, e)
        return False


def heal_inplay_session(dry_run: bool = False) -> dict:
    """Probe and, if stuck, destroy the IN-PLAY collector's FS session.

    WHY THIS IS SEPARATE FROM THE ODDS PATH. The in-play collector
    (`workers/jobs/inplay_coolbet_collector.py`) is a second long-lived Mac
    session, and on the evening it shipped BOTH it and the odds reader wedged in
    the same minute. The odds reader had a healer; the in-play session had none,
    so it sat dead for two hours emitting `errors 8` every cycle until a human
    destroyed it by hand. A long-lived FS session without a healer is a feed
    outage waiting to happen — this closes that gap for the second one.

    It does NOT key off `odds_snapshots`, because the in-play collector does not
    write there; the probe itself is the evidence."""
    from workers.jobs.inplay_coolbet_collector import FS_SESSION_NAME as INPLAY_FS
    try:
        from workers.automation.coolbet_explorer import probe_coolbet_reachable
        probe = probe_coolbet_reachable(session_name=INPLAY_FS)
    except Exception as e:  # noqa: BLE001
        return {"session": INPLAY_FS, "state": "unknown", "action": f"probe_failed: {e}"}
    if probe.get("state") != "wedged":
        return {"session": INPLAY_FS, "state": probe.get("state"), "action": "none"}
    if dry_run:
        return {"session": INPLAY_FS, "state": "wedged", "action": "would_destroy"}
    ok = _destroy_fs_session(INPLAY_FS)
    return {"session": INPLAY_FS, "state": "wedged",
            "action": f"destroyed={ok}", "detail": probe.get("detail")}


def _destroy_odds_session() -> bool:
    """Destroy ONLY the sweep's FS session; the next sweep recreates it.

    This is the surgical recovery from the FS-sticking pattern: FlareSolverr
    keeps serving `GET /` and `sessions.list` while one session's Chrome tab is
    dead, so every naive health probe reads green. `coolbet_prod` is never a
    target here — destroying it would drop the real-money placer's authed
    session to fix a read-only feed.
    """
    return _destroy_fs_session(ODDS_FS_SESSION)


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
            # send_telegram takes dedup_window_s (seconds), not hours. The
            # wrong keyword raised TypeError inside the try/except, so the
            # watchdog could DETECT an outage and never report it — the alert
            # path had never once fired.
            dedup_window_s=int(ALERT_DEDUP_H * 3600),
        )
    except Exception as e:
        log.warning("telegram alert failed: %s", e)


# COOLBET-STALL-KILL-2026-09-04. Timeouts prevent the hang we found; this
# catches the ones we have not found yet.
#
# On 2026-09-04 the odds job wedged on a GET with no timeout: PID 64881 alive
# 15h15m in state S, log silent 10:15 -> 23:48, no bulk sweep since 07:00,
# Coolbet coverage of upcoming fixtures down to 2.1%. launchd will not restart a
# job whose process is still alive, so it sat there. Meanwhile this watchdog ran
# every 20 minutes, correctly reported STALE, and re-harvested cookies ~40 times
# at a problem that had nothing to do with cookies.
#
# Detecting a stall and being unable to end it is not monitoring, it is
# spectating. A process that has outlived several run cycles with a silent log
# is hung by definition — killing it lets launchd start a clean one.
_STALL_PROC_MATCH = "workers.automation.coolbet_explorer"
_STALL_LOG = Path(__file__).resolve().parents[2] / "dev" / "active" / "coolbet-odds-snapshot.log"
_STALL_MINUTES = float(os.getenv("COOLBET_STALL_MINUTES", "45"))


def _stalled_pids() -> list[tuple[int, float]]:
    """(pid, minutes_running) for odds-job processes older than the stall bound."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,etimes,command"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:                                    # noqa: BLE001
        return []
    hits = []
    for line in out.splitlines():
        if _STALL_PROC_MATCH not in line:
            continue
        parts = line.split(None, 2)
        try:
            pid, etimes = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            continue
        mins = etimes / 60.0
        if mins >= _STALL_MINUTES:
            hits.append((pid, mins))
    return hits


def _log_silent_minutes() -> float | None:
    try:
        return (time.time() - _STALL_LOG.stat().st_mtime) / 60.0
    except OSError:
        return None


def kill_stalled_job(dry_run: bool = False) -> dict:
    """Kill an odds-job process that is running but no longer writing.

    Both conditions are required. A long run that is still logging is doing
    work — the full sweep walks ~2,000 fixtures with deliberate pauses — and
    killing it would turn a slow success into a guaranteed failure.
    """
    silent = _log_silent_minutes()
    pids = _stalled_pids()
    info = {"pids": [p for p, _ in pids], "log_silent_min": silent, "killed": []}
    if not pids or silent is None or silent < _STALL_MINUTES:
        return info
    for pid, mins in pids:
        if dry_run:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            info["killed"].append(pid)
            log.warning("killed stalled coolbet odds job pid=%s (running %.0fmin, "
                        "log silent %.0fmin)", pid, mins, silent)
        except OSError as e:
            log.warning("could not kill stalled pid %s: %s", pid, e)
    return info


def run(dry_run: bool = False) -> dict:
    # COOLBET-DAEMONS-PAUSE: honor the global footprint pause. When the operator
    # has paused Coolbet daemons (to calm Imperva), the odds-snapshot job is not
    # running, so there is nothing to watchdog — skip to avoid adding footprint.
    from workers.automation.coolbet_state import is_daemons_paused
    d_paused, d_reason = is_daemons_paused()
    if d_paused:
        log.info("coolbet feed watchdog: daemons PAUSED (%s) — skipping", d_reason)
        return {"state": "paused", "reason": d_reason or "daemons_paused",
                "action": "none",
                "checked_at": datetime.now(timezone.utc).isoformat()}

    # SESSION-KEEP (DAEMON-RETIREMENT 2026-09-10): heal the placement JWT here, on
    # the :20/:50 cadence, so the ~30-min token can't lapse on a quiet day. This is
    # the permanent home for what the retired coolbet_mac_daemon did per tick; the
    # UI placer (real money) has no heal of its own. Cheap when valid; never raises.
    if not dry_run:
        try:
            from workers.automation.coolbet_browser_sync import ensure_session_live
            result_session = ensure_session_live()
            log.info("coolbet feed watchdog: session-keep → %s", result_session)
        except Exception as e:  # noqa: BLE001
            log.debug("coolbet feed watchdog: session-keep skipped (non-fatal): %s", e)
            result_session = f"error:{e}"
        # OPERATOR-CONTROL (DAEMON-RETIREMENT 2026-09-10): drain Telegram heal-button
        # commands here too (pause/resume are direct webhook DB writes and unaffected).
        # The retired daemon drained these every ~30s; on the :20/:50 cadence a heal
        # tap can take up to that long, but the watchdog also AUTO-heals each run, so
        # the button is now a convenience, not the only recovery path. Never fatal.
        try:
            from workers.automation.coolbet_mac_daemon import _drain_operator_commands
            drained = _drain_operator_commands()
            if drained:
                log.info("coolbet feed watchdog: drained %d operator command(s)", drained)
        except Exception as e:  # noqa: BLE001
            log.debug("coolbet feed watchdog: operator-command drain skipped (non-fatal): %s", e)
    else:
        result_session = "dry_run_skipped"

    state, reason = classify()
    result = {"state": state, "reason": reason, "action": "none",
              "session_keep": result_session,
              "checked_at": datetime.now(timezone.utc).isoformat()}
    log.info("coolbet feed watchdog: %s — %s", state, reason)

    # Do this regardless of state: a wedged process starves the feed whether or
    # not the classifier has noticed yet, and killing it is safe when the log is
    # also silent.
    # The in-play collector's session is healed on its own evidence, whatever the
    # odds feed is doing — the two wedge independently (and, on 2026-09-18, together).
    try:
        inplay = heal_inplay_session(dry_run=dry_run)
        if inplay.get("state") == "wedged":
            result["inplay_session"] = inplay
            _alert("WEDGED_INPLAY",
                   f"in-play FS session {inplay['session']!r} was stuck "
                   f"({inplay.get('detail')}) — {inplay['action']}")
    except Exception as e:  # noqa: BLE001
        log.warning("in-play session heal failed (non-fatal): %s", e)

    stall = kill_stalled_job(dry_run=dry_run)
    if stall["killed"]:
        result["action"] = f"killed_stalled_pids={stall['killed']}"
        result["stall"] = stall
        _alert("STALLED",
               f"odds job pid(s) {stall['killed']} were running with the log "
               f"silent for {stall['log_silent_min']:.0f}min — killed so launchd "
               f"can start a clean run")
        return result

    if dry_run or state in ("HEALTHY", "UNKNOWN"):
        return result

    if state == "NOT_LOADED":
        result["action"] = "reloaded" if _reload_job() else "reload_failed"
    elif state == "WEDGED_SESSION":
        # Self-healing: the next sweep (:03/:33) builds a fresh session, so this
        # bounds a wedge at ~30 min instead of "until a human looks at the
        # /performance page". Alert either way — a wedge that recurs often is a
        # different problem from one that happens once.
        if _destroy_odds_session():
            result["action"] = f"destroyed_fs_session={ODDS_FS_SESSION}"
        else:
            result["action"] = "fs_session_destroy_failed"
            state = "BLOCKED"
            reason = (f"{reason} — but destroying it FAILED; FlareSolverr may be "
                      f"down or unreachable on FLARESOLVERR_URL. Needs a human: "
                      f"see runbook §1.")
            result["state"], result["reason"] = state, reason
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
        # CDP_DOWN / BLOCKED / NO_PICKS — no safe automatic remedy. Restarting a
        # pipeline that is silently evaluating nothing would just hide it again.
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
