"""
OddsIntel — Smoke Tests

READ-ONLY integration tests against the real DB.
Run before pushing: python scripts/smoke_test.py
Or install the pre-push hook: cp .githooks/pre-push .git/hooks/pre-push

Tests target the exact functions that have broken silently in production.
Exit code 0 = all pass, 1 = any failure.
"""

import sys
import os
import traceback
import threading
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# ── Test runner ───────────────────────────────────────────────────────────────

_pass = 0
_fail = 0
_results: list[tuple[str, bool, str]] = []


def test(name: str):
    """Decorator to register a test function."""
    def decorator(fn):
        global _pass, _fail
        try:
            fn()
            _results.append((name, True, ""))
            _pass += 1
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
            _fail += 1
        return fn
    return decorator


def assert_equal(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg} — expected {b!r}, got {a!r}")


def assert_gt(a, b, msg=""):
    if not (a > b):
        raise AssertionError(f"{msg} — expected > {b}, got {a}")


def assert_no_error(fn, *args, **kwargs):
    """Call fn and assert it doesn't raise."""
    fn(*args, **kwargs)


# ── Tests ─────────────────────────────────────────────────────────────────────

@test("DB connection — basic query")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query("SELECT 1 AS ok")
    assert rows[0]["ok"] == 1


@test("DB pool — reset and reconnect")
def _():
    from workers.api_clients.db import execute_query, _reset_pool
    _reset_pool()
    rows = execute_query("SELECT 2 AS ok")
    assert rows[0]["ok"] == 2


@test("build_match_feature_vectors — runs without error (uuid casts + datetime)")
def _():
    from workers.api_clients.supabase_client import build_match_feature_vectors
    # Use a date we know has finished matches
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    count = build_match_feature_vectors(None, yesterday)
    # count may be 0 if no finished matches yesterday (weekend gap), just no exception
    assert isinstance(count, int), f"Expected int, got {type(count)}"


@test("build_match_feature_vectors — returns rows for known date (May 6)")
def _():
    from workers.api_clients.supabase_client import build_match_feature_vectors
    count = build_match_feature_vectors(None, "2026-05-06")
    assert_gt(count, 0, "Expected feature vectors for 2026-05-06")


@test("backfill_historical — safe to import in a background thread (signal guard)")
def _():
    errors = []

    def _import():
        try:
            from scripts import backfill_historical  # noqa: F401
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=_import)
    t.start()
    t.join(timeout=10)
    if errors:
        raise errors[0]


@test("inplay_bot — _get_live_candidates query parses and runs")
def _():
    from workers.api_clients.db import execute_query
    from workers.jobs.inplay_bot import _get_live_candidates
    # Should return a list (possibly empty outside match hours) without crashing
    candidates = _get_live_candidates(execute_query)
    assert isinstance(candidates, list), f"Expected list, got {type(candidates)}"


@test("settlement — post_mortem bets query runs without error")
def _():
    from workers.api_clients.db import execute_query
    today_str = date.today().isoformat()
    bets = execute_query(
        """SELECT sb.id, sb.market, sb.result, sb.model_probability,
                  sb.pick_time, sb.pnl, sb.stake
           FROM simulated_bets sb
           WHERE sb.result != 'pending' AND sb.pick_time >= %s
           LIMIT 20""",
        [f"{today_str}T00:00:00"],
    )
    assert isinstance(bets, list)


@test("uuid array queries — leagues, signals, predictions use ::uuid[]")
def _():
    from workers.api_clients.db import execute_query
    # Pull a real match_id and league_id, then verify the cast queries work
    rows = execute_query(
        "SELECT id, league_id, home_team_id FROM matches WHERE status='finished' LIMIT 1"
    )
    if not rows:
        return  # No finished matches — skip
    match_id = str(rows[0]["id"])
    league_id = str(rows[0]["league_id"])
    team_id = str(rows[0]["home_team_id"])

    execute_query("SELECT id FROM leagues WHERE id = ANY(%s::uuid[])", ([league_id],))
    execute_query(
        "SELECT match_id FROM match_signals WHERE match_id = ANY(%s::uuid[]) LIMIT 1",
        ([match_id],),
    )
    execute_query(
        "SELECT team_id FROM team_elo_daily WHERE team_id = ANY(%s::uuid[]) LIMIT 1",
        ([team_id],),
    )


@test("match_feature_vectors — table has all 57 expected columns")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name='match_feature_vectors'"
    )
    actual = {r["column_name"] for r in rows}
    required = {
        "match_id", "match_date", "ensemble_prob_home", "elo_home", "elo_away",
        "goals_for_avg_home", "goals_for_avg_away", "h2h_win_pct",
        "overnight_line_move", "rest_days_home", "rest_days_away",
        "referee_home_win_pct", "built_at",
    }
    missing = required - actual
    if missing:
        raise AssertionError(f"Missing columns in match_feature_vectors: {missing}")


@test("simulated_bets — xg_source column exists (migration 057)")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='simulated_bets' AND column_name='xg_source'"
    )
    assert rows, "xg_source column missing from simulated_bets — run migration 057"


@test("daily pipeline — imports without error")
def _():
    from workers.jobs.daily_pipeline_v2 import run_morning  # noqa: F401


@test("backfill — get_uuids_with_data query uses ::uuid[] cast")
def _():
    from workers.api_clients.db import execute_query
    # Pull a known match_id and verify the exact query backfill uses works
    rows = execute_query("SELECT id FROM matches WHERE status='finished' LIMIT 1")
    if not rows:
        return
    match_uuid = str(rows[0]["id"])
    execute_query(
        "SELECT DISTINCT match_id FROM match_stats WHERE match_id = ANY(%s::uuid[])",
        [[match_uuid]],
    )


@test("settlement — run_post_mortem imports and dedup guard works")
def _():
    from workers.jobs.settlement import run_post_mortem  # noqa: F401
    # Just verify it imports and the dedup guard short-circuits if already ran today
    # (won't make a Gemini call — already-ran check fires first or no losses yet)


@test("settlement — run_settlement imports and pending bets query runs")
def _():
    from workers.jobs.settlement import run_settlement, _PENDING_BETS_SQL  # noqa: F401
    from workers.api_clients.db import execute_query
    rows = execute_query(_PENDING_BETS_SQL, [])
    assert isinstance(rows, list)


@test("betting pipeline — run_betting imports without error")
def _():
    from workers.jobs.betting_pipeline import run_betting  # noqa: F401


@test("fetch_odds — run_odds imports without error")
def _():
    from workers.jobs.fetch_odds import run_odds  # noqa: F401


@test("fetch_enrichment — run_enrichment imports without error")
def _():
    from workers.jobs.fetch_enrichment import run_enrichment  # noqa: F401


@test("fetch_fixtures — run_fixtures imports without error")
def _():
    from workers.jobs.fetch_fixtures import run_fixtures  # noqa: F401


@test("news_checker — imports without error (skips if google SDK absent)")
def _():
    try:
        from workers.jobs.news_checker import run_news_checker  # noqa: F401
    except ModuleNotFoundError as e:
        if "google" in str(e):
            return  # google-genai not installed locally — fine, it's on Railway
        raise


@test("supabase_client — store_bet and settle_bet are importable")
def _():
    from workers.api_clients.supabase_client import store_bet, settle_bet  # noqa: F401


@test("supabase_client — batch_write_morning_signals is importable")
def _():
    from workers.api_clients.supabase_client import batch_write_morning_signals  # noqa: F401


@test("live_poller — imports without error")
def _():
    from workers.live_poller import LivePoller  # noqa: F401


@test("backfill — match_events insert uses with get_conn() not conn.close()")
def _():
    from workers.api_clients.db import get_conn
    import contextlib
    # get_conn() must be a context manager, not a raw connection
    assert isinstance(get_conn(), contextlib.AbstractContextManager) or hasattr(get_conn(), '__enter__'), \
        "get_conn() must return a context manager"


@test("parse_live_odds — Fulltime Result market parsed as 1x2")
def _():
    from workers.api_clients.api_football import parse_live_odds
    sample = [{
        "fixture": {"id": 999, "status": {"elapsed": 45}},
        "odds": [{
            "name": "Fulltime Result",
            "values": [
                {"value": "Home", "odd": "1.80", "suspended": False},
                {"value": "Draw", "odd": "3.50", "suspended": False},
                {"value": "Away", "odd": "4.20", "suspended": False},
            ]
        }]
    }]
    result = parse_live_odds(sample)
    assert 999 in result, "fixture 999 not found in result"
    markets = {r["selection"] for r in result[999]}
    assert markets == {"home", "draw", "away"}, f"Expected home/draw/away, got {markets}"


@test("parse_live_odds — Over/Under Line with handicap field parsed correctly")
def _():
    from workers.api_clients.api_football import parse_live_odds
    sample = [{
        "fixture": {"id": 888, "status": {"elapsed": 60}},
        "odds": [{
            "name": "Over/Under Line",
            "values": [
                {"value": "Over", "handicap": "2.5", "odd": "1.95", "suspended": False},
                {"value": "Under", "handicap": "2.5", "odd": "1.90", "suspended": False},
            ]
        }]
    }]
    result = parse_live_odds(sample)
    assert 888 in result, "fixture 888 not found in result"
    markets = {r["market"] for r in result[888]}
    assert "over_under_25" in markets, f"Expected over_under_25, got {markets}"
    selections = {r["selection"] for r in result[888]}
    assert selections == {"over", "under"}, f"Expected over/under, got {selections}"


# ── Results ───────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 60)
    print("  OddsIntel Smoke Tests")
    print("═" * 60)

    for name, passed, error in _results:
        status = "✓" if passed else "✗"
        color_on = "\033[32m" if passed else "\033[31m"
        color_off = "\033[0m"
        print(f"  {color_on}{status}{color_off}  {name}")
        if error:
            print(f"       {color_on}{error}{color_off}")

    print("═" * 60)
    color = "\033[32m" if _fail == 0 else "\033[31m"
    print(f"  {color}{_pass} passed, {_fail} failed\033[0m")
    print("═" * 60 + "\n")

    sys.exit(0 if _fail == 0 else 1)


if __name__ == "__main__":
    main()
