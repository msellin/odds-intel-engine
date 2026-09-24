"""FEED REGISTRY (#107 FEEDS-DASHBOARD, 2026-09-23) — every data feed we run, in one
place: which book it serves, where it runs, how often, and how to tell whether it
is ACTUALLY working.

WHY OUTPUT, NOT JOB STATUS. The dashboard's first query found Coolbet had written
no odds for four hours while its job reported `completed` on every run
(#108 COOLBET-IMPERVA-FLAG). The same shape cost six days in EPICBET-403 (277
green runs, zero rows). So a feed is judged by `health`:

    "data"     — age of the newest row it writes (book sweeps: odds keep coming
                 all day, so silence means broken)
    "runs"     — its scheduler job's recent success (live feeds: at 04:00 UTC
                 there may be nothing live to write, and that is not a fault)
    "service"  — a systemd unit / docker container being up (infrastructure)

`workers/jobs/feed_health.py` evaluates every entry every 5 minutes into
`feed_status`, which /admin/feeds renders. Adding a sweeper without an entry here
fails smoke `FEED-REGISTRY-COVERS-SWEEPERS`.

CONTROLS (phase B, 2026-09-23). `controls` lists what /admin/feeds may do:
  "pause"   — `_run_job` skips the feed's job while `feed_controls.paused`
  "run_now" — the 30-s drain submits `wrapper` (a function in workers/scheduler.py)
              as a one-off run
AUTO-PAUSE (2026-09-23, migration 391). `auto_pause: True` on the bot-protected
book sweeps: 2 failed runs in a row → the engine pauses the feed itself and tests
it again after a backoff (1 h, 2 h, 4 h, 8 h, cap 12 h) — see
workers/jobs/feed_control.py. Why a pause and not a restart: #108 showed a full
FlareSolverr restart does not clear a bot-protection flag on our exit IP; only time
without traffic (or another IP) does, and every retry keeps the flag fresh.
Only feeds whose job passes through `_run_job` can be paused that way; feeds run
by systemd (in-play collector, near-kickoff timer, services) get their controls in
phase C (allowlisted restarts).
"""
from __future__ import annotations

# AF books in our feed, used to judge the AF odds refresh by its output.
AF_BOOKS = ("Pinnacle", "Bet365", "1xBet", "Marathonbet", "Betfair", "BetVictor",
            "William Hill", "SBO", "Betano")

FEEDS: list[dict] = [
    # ── our own direct books ────────────────────────────────────────────────
    {"id": "coolbet_prematch", "auto_pause": True, "wrapper": "_coolbet_odds_snapshot_wrapper", "controls": ["pause", "run_now"], "label": "Coolbet — pre-match odds", "book": "Coolbet",
     "category": "book", "kind": "pre-match", "job": "coolbet_odds_snapshot",
     "units": ["oddsintel-zone-egress.service"], "docker": "oi_hetzner_flaresolverr",
     "schedule": ":03 / :33 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": ["Coolbet"]},
     "runbook": "docs/COOLBET_RUNBOOK.md"},
    {"id": "epicbet_prematch", "auto_pause": True, "wrapper": "_epicbet_odds_snapshot_wrapper", "controls": ["pause", "run_now"], "label": "Epicbet — pre-match odds", "book": "Epicbet",
     "category": "book", "kind": "pre-match", "job": "epicbet_odds_snapshot",
     "units": ["oddsintel-zone-egress.service"],
     "schedule": ":02 / :32 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": ["Epicbet"]}},
    {"id": "unibet_prematch", "auto_pause": True, "wrapper": "_unibet_site_odds_wrapper", "controls": ["pause", "run_now"], "label": "Unibet — pre-match odds (logged-out Chrome)",
     "book": "Unibet-Site", "category": "book", "kind": "pre-match",
     "job": "unibet_site_odds",
     "units": ["oddsintel-unibet-chrome.service", "oddsintel-zone-egress.service"],
     "schedule": ":15 / :45 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": ["Unibet-Site"]},
     "runbook": "docs/COOLBET_RUNBOOK.md#6-unibet-site-odds-feed-stale"},
    {"id": "tonybet_prematch", "auto_pause": True, "wrapper": "_tonybet_odds_snapshot_wrapper", "controls": ["pause", "run_now"], "label": "Tonybet — pre-match odds", "book": "Tonybet",
     "category": "book", "kind": "pre-match", "job": "tonybet_odds_snapshot",
     "units": ["oddsintel-zone-egress.service"],
     "schedule": ":01 / :31 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": ["Tonybet"]}},
    {"id": "tonybet_live", "wrapper": "_tonybet_live_wrapper", "controls": ["pause", "run_now"], "label": "Tonybet — live score / corners / cards", "book": "Tonybet",
     "category": "book", "kind": "live", "job": "tonybet_live",
     "schedule": "every 120 s", "interval_min": 2, "stale_after_min": 20,
     "health": "runs", "data": {"table": "book_live_stats", "ts": "captured_at",
                                 "where": "bookmaker = 'Tonybet'"}},
    {"id": "tonybet_results", "wrapper": "_tonybet_results_wrapper", "controls": ["pause", "run_now"], "label": "Tonybet — results (FT / HT / 2H)", "book": "Tonybet",
     "category": "book", "kind": "results", "job": "tonybet_results",
     "schedule": "every 2 h at :20", "interval_min": 120, "stale_after_min": 300,
     "health": "runs", "data": {"table": "book_match_results", "ts": "captured_at",
                                 "where": "bookmaker = 'Tonybet'"}},
    {"id": "epicbet_inplay", "label": "Epicbet — in-play odds collector", "book": "Epicbet",
     "category": "book", "kind": "live", "units": ["oddsintel-inplay-collector.service"],
     "schedule": "continuous, 45 s", "interval_min": 1, "stale_after_min": 30,
     "health": "service", "data": {"table": "inplay_book_quotes", "ts": "captured_at",
                                    "where": "book = 'Epicbet'"}},
    {"id": "direct_close", "label": "Closing prices — direct books (T-15 min)", "book": None,
     "category": "book", "kind": "close",
     "units": ["oddsintel-near-kickoff-epicbet.timer"],
     # Checks every 5 min, but only CAPTURES when a paired match kicks off within
     # 15 min — so the gap since the last capture follows the kickoff calendar.
     "schedule": "checks every 5 min · captures 15 min before each kickoff",
     "interval_min": 5, "stale_after_min": 180,
     "health": "service", "data": {"table": "odds_snapshots", "ts": "timestamp",
                                    "where": "is_closing AND bookmaker IN ('Epicbet','Unibet-Site','Tonybet','Coolbet')"}},

    # ── API-Football ────────────────────────────────────────────────────────
    {"id": "af_odds", "wrapper": "job_odds_refresh", "controls": ["pause", "run_now"], "label": "API-Football — bulk odds (9 books)", "book": None,
     "category": "af", "kind": "pre-match", "job": "odds_refresh",
     "schedule": ":00 / :30 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": list(AF_BOOKS)}},
    {"id": "af_fixtures", "label": "API-Football — fixtures", "book": None,
     "category": "af", "kind": "fixtures", "job": "fetch_fixtures",
     "schedule": "morning chain + refreshes", "interval_min": 360, "stale_after_min": 1560,
     "health": "runs"},
    {"id": "af_closing", "wrapper": "job_closing_snap", "controls": ["pause", "run_now"], "label": "API-Football — closing snapshots", "book": None,
     "category": "af", "kind": "close", "job": "closing_snap",
     "schedule": "every 5 min", "interval_min": 5, "stale_after_min": 30,
     "health": "runs"},
    {"id": "af_live", "label": "API-Football — live poller (scores for settlement)", "book": None,
     "category": "af", "kind": "live",
     "schedule": "continuous, 45–120 s", "interval_min": 2, "stale_after_min": 240,
     "health": "data", "data": {"table": "live_match_snapshots", "ts": "captured_at"}},

    # ── infrastructure ──────────────────────────────────────────────────────
    {"id": "betfair_exchange", "wrapper": "_betfair_exchange_snapshot_wrapper", "controls": ["pause", "run_now"],
     "label": "Betfair Exchange — back/lay + liquidity (reference, not placeable)", "book": "Betfair-Exchange",
     "category": "book", "kind": "pre-match", "job": "betfair_exchange_snapshot",
     "units": ["oddsintel-egress@betfair.service"],
     "schedule": ":04 / :19 / :34 / :49 UTC", "interval_min": 15, "stale_after_min": 60,
     "health": "data", "data": {"table": "exchange_quotes", "ts": "captured_at"}},
    {"id": "betfair_egress", "label": "London exit for Betfair (DigitalOcean SOCKS :1082)", "book": None,
     "category": "infra", "kind": "service", "units": ["oddsintel-egress@betfair.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "zone_egress", "label": "Estonian exit (zone.ee SOCKS)", "book": None,
     "category": "infra", "kind": "service", "units": ["oddsintel-zone-egress.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "unibet_chrome", "label": "Unibet Chrome (Xvfb, CDP on loopback)", "book": None,
     "category": "infra", "kind": "service", "units": ["oddsintel-unibet-chrome.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "flaresolverr", "label": "FlareSolverr (VPS)", "book": None,
     "category": "infra", "kind": "service", "docker": "oi_hetzner_flaresolverr",
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "database", "label": "Database (PostgreSQL 17)", "book": None,
     "category": "infra", "kind": "service", "units": ["postgresql@17-main.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "data_api", "label": "Data API (PostgREST — what the website reads)", "book": None,
     "category": "infra", "kind": "service", "docker": "oddsintel-postgrest-1",
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "website", "label": "Website (Next.js via pm2 + nginx)", "book": None,
     "category": "infra", "kind": "service", "units": ["pm2-root.service", "nginx.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "disk", "label": "Disk space", "book": None, "category": "infra", "kind": "host",
     "host": "disk", "schedule": "checked every 5 min", "interval_min": None,
     "stale_after_min": None, "health": "service"},
    {"id": "memory", "label": "Memory", "book": None, "category": "infra", "kind": "host",
     "host": "memory", "schedule": "checked every 5 min", "interval_min": None,
     "stale_after_min": None, "health": "service"},
    {"id": "scheduler", "label": "Engine scheduler", "book": None,
     "category": "infra", "kind": "service", "units": ["oddsintel-scheduler.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
]

FEEDS_BY_ID = {f["id"]: f for f in FEEDS}

# Books whose "fixtures priced today" is shown on the dashboard.
COVERAGE_BOOKS = ("Coolbet", "Epicbet", "Unibet-Site", "Tonybet", "Pinnacle", "Betfair-Exchange")
