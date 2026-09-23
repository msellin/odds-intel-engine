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
"""
from __future__ import annotations

# AF books in our feed, used to judge the AF odds refresh by its output.
AF_BOOKS = ("Pinnacle", "Bet365", "1xBet", "Marathonbet", "Betfair", "BetVictor",
            "William Hill", "SBO", "Betano")

FEEDS: list[dict] = [
    # ── our own direct books ────────────────────────────────────────────────
    {"id": "coolbet_prematch", "label": "Coolbet — pre-match odds", "book": "Coolbet",
     "category": "book", "kind": "pre-match", "job": "coolbet_odds_snapshot",
     "units": ["oddsintel-zone-egress.service"], "docker": "oi_hetzner_flaresolverr",
     "schedule": ":03 / :33 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": ["Coolbet"]},
     "runbook": "docs/COOLBET_RUNBOOK.md"},
    {"id": "epicbet_prematch", "label": "Epicbet — pre-match odds", "book": "Epicbet",
     "category": "book", "kind": "pre-match", "job": "epicbet_odds_snapshot",
     "units": ["oddsintel-zone-egress.service"],
     "schedule": ":02 / :32 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": ["Epicbet"]}},
    {"id": "unibet_prematch", "label": "Unibet — pre-match odds (logged-out Chrome)",
     "book": "Unibet-Site", "category": "book", "kind": "pre-match",
     "job": "unibet_site_odds",
     "units": ["oddsintel-unibet-chrome.service", "oddsintel-zone-egress.service"],
     "schedule": ":15 / :45 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": ["Unibet-Site"]},
     "runbook": "docs/COOLBET_RUNBOOK.md#6-unibet-site-odds-feed-stale"},
    {"id": "tonybet_prematch", "label": "Tonybet — pre-match odds", "book": "Tonybet",
     "category": "book", "kind": "pre-match", "job": "tonybet_odds_snapshot",
     "units": ["oddsintel-zone-egress.service"],
     "schedule": ":01 / :31 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": ["Tonybet"]}},
    {"id": "tonybet_live", "label": "Tonybet — live score / corners / cards", "book": "Tonybet",
     "category": "book", "kind": "live", "job": "tonybet_live",
     "schedule": "every 120 s", "interval_min": 2, "stale_after_min": 20,
     "health": "runs", "data": {"table": "book_live_stats", "ts": "captured_at",
                                 "where": "bookmaker = 'Tonybet'"}},
    {"id": "tonybet_results", "label": "Tonybet — results (FT / HT / 2H)", "book": "Tonybet",
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
     "schedule": "every 5 min", "interval_min": 5, "stale_after_min": 180,
     "health": "service", "data": {"table": "odds_snapshots", "ts": "timestamp",
                                    "where": "is_closing AND bookmaker IN ('Epicbet','Unibet-Site','Tonybet','Coolbet')"}},

    # ── API-Football ────────────────────────────────────────────────────────
    {"id": "af_odds", "label": "API-Football — bulk odds (9 books)", "book": None,
     "category": "af", "kind": "pre-match", "job": "odds_refresh",
     "schedule": ":00 / :30 UTC", "interval_min": 30, "stale_after_min": 90,
     "health": "data", "data": {"odds_books": list(AF_BOOKS)}},
    {"id": "af_fixtures", "label": "API-Football — fixtures", "book": None,
     "category": "af", "kind": "fixtures", "job": "fetch_fixtures",
     "schedule": "morning chain + refreshes", "interval_min": 360, "stale_after_min": 1560,
     "health": "runs"},
    {"id": "af_closing", "label": "API-Football — closing snapshots", "book": None,
     "category": "af", "kind": "close", "job": "closing_snap",
     "schedule": "every 5 min", "interval_min": 5, "stale_after_min": 30,
     "health": "runs"},
    {"id": "af_live", "label": "API-Football — live poller (scores for settlement)", "book": None,
     "category": "af", "kind": "live",
     "schedule": "continuous, 45–120 s", "interval_min": 2, "stale_after_min": 240,
     "health": "data", "data": {"table": "live_match_snapshots", "ts": "captured_at"}},

    # ── infrastructure ──────────────────────────────────────────────────────
    {"id": "zone_egress", "label": "Estonian exit (zone.ee SOCKS)", "book": None,
     "category": "infra", "kind": "service", "units": ["oddsintel-zone-egress.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "unibet_chrome", "label": "Unibet Chrome (Xvfb, CDP on loopback)", "book": None,
     "category": "infra", "kind": "service", "units": ["oddsintel-unibet-chrome.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "flaresolverr", "label": "FlareSolverr (VPS)", "book": None,
     "category": "infra", "kind": "service", "docker": "oi_hetzner_flaresolverr",
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
    {"id": "scheduler", "label": "Engine scheduler", "book": None,
     "category": "infra", "kind": "service", "units": ["oddsintel-scheduler.service"],
     "schedule": "always on", "interval_min": None, "stale_after_min": None, "health": "service"},
]

FEEDS_BY_ID = {f["id"]: f for f in FEEDS}

# Books whose "fixtures priced today" is shown on the dashboard.
COVERAGE_BOOKS = ("Coolbet", "Epicbet", "Unibet-Site", "Tonybet", "Pinnacle")
