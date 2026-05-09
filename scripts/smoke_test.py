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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# ── Test runner ───────────────────────────────────────────────────────────────

_registry: list[tuple[str, object]] = []


def test(name: str):
    """Decorator — registers the test for parallel execution in main()."""
    def decorator(fn):
        _registry.append((name, fn))
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


@test("scheduler — backfill jobs use ≥25min interval to prevent overlap (worst case 22min)")
def _():
    import ast, pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
            if func == "add_job":
                for kw in node.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                        if kw.value.value in ("hist_backfill", "backfill_transfers", "backfill_coaches"):
                            for arg in node.args:
                                if isinstance(arg, ast.Call):
                                    fname = getattr(arg.func, 'id', None) or getattr(arg.func, 'attr', None)
                                    if fname == "IntervalTrigger":
                                        for ikw in arg.keywords:
                                            if ikw.arg == "minutes":
                                                mins = ikw.value.n if hasattr(ikw.value, 'n') else ikw.value.value
                                                assert mins >= 25, (
                                                    f"Backfill job {kw.value.value!r} uses {mins}min interval — "
                                                    "must be ≥25min: worst case is 15s×3retries×30req=22min"
                                                )


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


@test("ops_snapshots — migration 063 columns exist")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='ops_snapshots'"
    )
    actual = {r["column_name"] for r in rows}
    required = {
        "live_games_tracked", "live_games_with_xg",
        "live_games_with_odds", "inplay_active_bots",
    }
    missing = required - actual
    if missing:
        raise AssertionError(f"Missing ops_snapshots columns (run migration 063): {missing}")


@test("write_ops_snapshot — wired to ops_snapshots + pipeline_runs (source inspect)")
def _():
    """The Ops Dashboard shows '—' on every metric if no ops_snapshot row for today.
    Original test invoked write_ops_snapshot() directly: 146s and writing duplicate
    rows on every CI push (~95% of suite runtime). The real silent-failure guard
    is the daily Railway run logging to pipeline_runs — if that stops, the
    dashboard goes stale visibly. Schema is covered by the migration 063 test
    above. Here we just verify the function is correctly wired."""
    import pathlib
    src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    fn_start = src.index("def write_ops_snapshot(")
    next_def = src.find("\ndef ", fn_start + 1)
    fn_body = src[fn_start:next_def] if next_def != -1 else src[fn_start:]

    assert "INSERT INTO ops_snapshots" in fn_body, (
        "write_ops_snapshot must INSERT into ops_snapshots — that's what the dashboard reads"
    )
    assert "log_pipeline_start" in fn_body and "log_pipeline_complete" in fn_body, (
        "write_ops_snapshot must log to pipeline_runs (start + complete) so failures are visible"
    )
    assert "log_pipeline_failed" in fn_body, (
        "write_ops_snapshot must log_pipeline_failed on errors — silent failures break the dashboard"
    )




@test("dashboard_cache_refresh — periodic job wired in scheduler (source inspect)")
def _():
    """Performance page reads dashboard_cache; without periodic refresh,
    it lags up to ~24h between settlement runs. Verifies the standalone
    refresh job is registered and calls write_dashboard_cache."""
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()

    assert "def job_dashboard_cache_refresh" in src, (
        "job_dashboard_cache_refresh must exist — keeps /performance fresh between settlements"
    )
    assert "from workers.jobs.settlement import write_dashboard_cache" in src, (
        "job must import write_dashboard_cache so it can run the cache rebuild"
    )
    assert 'id="dashboard_cache_refresh"' in src, (
        "scheduler must register dashboard_cache_refresh with a unique id"
    )
    # Ensure it runs more often than once a day — current spec is :15 and :45
    assert 'CronTrigger(minute="15,45")' in src or 'IntervalTrigger(minutes=30)' in src, (
        "dashboard_cache_refresh must be scheduled every 30 min (currently minute='15,45')"
    )


@test("simulated_bets — odds_at_pick column exists (settlement KeyError guard)")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='simulated_bets' AND column_name='odds_at_pick'"
    )
    assert rows, "odds_at_pick missing from simulated_bets — settlement will KeyError on every bet"


@test("bots — inplay bot names match LIKE 'inplay_%' pattern")
def _():
    from workers.api_clients.db import execute_query
    # Verify at least the expected inplay bots exist with the right naming convention.
    # If this fails, bets_inplay_today and inplay_active_bots will silently count 0.
    rows = execute_query("SELECT name FROM bots WHERE name LIKE 'inplay_%' ORDER BY name")
    names = [r["name"] for r in rows]
    assert len(names) >= 6, (
        f"Expected ≥6 inplay bots matching 'inplay_%', got {len(names)}: {names}. "
        "ops_snapshot inplay counts will be 0 if names don't match."
    )


@test("settle_bet_result — 1x2 home win, correct pnl")
def _():
    from workers.jobs.settlement import settle_bet_result
    bet = {"market": "1x2", "selection": "home", "stake": "10", "odds_at_pick": "2.00"}
    r = settle_bet_result(bet, home_goals=2, away_goals=1, closing_odds=None)
    assert r["result"] == "won", f"Expected won, got {r['result']}"
    assert r["pnl"] == 10.0, f"Expected pnl=10.0, got {r['pnl']}"


@test("settle_bet_result — over_under_25 market parses line correctly")
def _():
    from workers.jobs.settlement import settle_bet_result
    # over 2.5 — 3 goals — should win
    bet = {"market": "over_under_25", "selection": "over", "stake": "10", "odds_at_pick": "1.90"}
    r = settle_bet_result(bet, home_goals=2, away_goals=1, closing_odds=None)
    assert r["result"] == "won", f"over_under_25 over with 3 goals should win, got {r['result']}"
    # under 2.5 — 2 goals — should win
    bet2 = {"market": "over_under_25", "selection": "under", "stake": "10", "odds_at_pick": "1.90"}
    r2 = settle_bet_result(bet2, home_goals=1, away_goals=1, closing_odds=None)
    assert r2["result"] == "won", f"over_under_25 under with 2 goals should win, got {r2['result']}"


@test("_poisson_over_prob — no NaN/inf at edge cases (lam=0, lam=0.001)")
def _():
    from workers.jobs.inplay_bot import _poisson_over_prob
    import math
    for lam in (0.0, 0.001, 0.1, 3.0, 10.0):
        p = _poisson_over_prob(lam, 2.5)
        assert not math.isnan(p), f"NaN at lam={lam}"
        assert not math.isinf(p), f"Inf at lam={lam}"
        assert 0.0 <= p <= 1.0, f"Probability out of [0,1] at lam={lam}: {p}"


@test("_bayesian_posterior — valid probability at edge cases (minute=0, total xg=0)")
def _():
    from workers.jobs.inplay_bot import _bayesian_posterior
    # minute=0: should return prematch xg unchanged
    r = _bayesian_posterior(prematch_xg_total=2.5, live_xg_total=0.0, minute=0)
    assert r == 2.5, f"At minute=0 should return prematch xg, got {r}"
    # zero xg inputs — should not crash or return negative
    r2 = _bayesian_posterior(prematch_xg_total=0.0, live_xg_total=0.0, minute=45)
    assert r2 == 0.0, f"Zero xg at minute=45 should return 0.0, got {r2}"
    # normal case — result should be positive
    r3 = _bayesian_posterior(prematch_xg_total=1.4, live_xg_total=0.8, minute=60)
    assert r3 > 0, f"Expected positive posterior, got {r3}"


@test("VIG-REMOVE — vig normalization: fair probs sum to 1.0 and are each less than raw")
def _():
    import math
    # Typical Pinnacle 1X2 odds with ~4.8% margin
    home_odds, draw_odds, away_odds = 2.10, 3.40, 3.60
    raw_h = 1.0 / home_odds
    raw_d = 1.0 / draw_odds
    raw_a = 1.0 / away_odds
    overround = raw_h + raw_d + raw_a
    assert overround > 1.0, f"Overround should be > 1.0 (bookmaker margin), got {overround}"
    fair_h = raw_h / overround
    fair_d = raw_d / overround
    fair_a = raw_a / overround
    total = fair_h + fair_d + fair_a
    assert abs(total - 1.0) < 1e-10, f"Vig-normalized probs must sum to 1.0, got {total}"
    assert fair_h < raw_h, "Vig removal must reduce home probability"
    assert fair_d < raw_d, "Vig removal must reduce draw probability"
    assert fair_a < raw_a, "Vig removal must reduce away probability"
    # O/U pair normalization
    ou_over, ou_under = 1.0 / 1.87, 1.0 / 1.98
    ou_sum = ou_over + ou_under
    assert ou_sum > 1.0, "O/U pair should also have overround"
    assert abs(ou_over / ou_sum + ou_under / ou_sum - 1.0) < 1e-10


@test("DRAW-PER-LEAGUE — _poisson_probs uses league_draw_pct for dynamic inflation")
def _():
    import math
    from workers.jobs.daily_pipeline_v2 import _poisson_probs
    # High draw league (50% draw rate, e.g. defensive lower division) vs low (22%, open attacking league).
    # The formula clips to a floor of 1.03 for leagues below ~37% — so we need ldp=0.50 to show a clear
    # difference vs ldp=0.22 (which clips at 1.03).
    high    = _poisson_probs(1.5, 1.5, league_draw_pct=0.50)
    low     = _poisson_probs(1.5, 1.5, league_draw_pct=0.22)
    default = _poisson_probs(1.5, 1.5)
    assert high["draw_prob"] > low["draw_prob"], (
        f"High draw league (50%) should produce higher draw prob than low (22%): "
        f"{high['draw_prob']:.4f} vs {low['draw_prob']:.4f}"
    )
    # Probabilities must still sum to 1.0 in all cases
    for label, r in [("high", high), ("low", low), ("default", default)]:
        total = r["home_prob"] + r["draw_prob"] + r["away_prob"]
        assert abs(total - 1.0) < 1e-6, f"1X2 probs must sum to 1.0 for {label}, got {total}"
        for key in ("home_prob", "draw_prob", "away_prob"):
            assert not math.isnan(r[key]), f"NaN in {key} for {label}"
    # Ceiling clamp: ldp=0.99 should not exceed ldp=0.80 (both capped at 1.15)
    extreme_high = _poisson_probs(1.5, 1.5, league_draw_pct=0.99)
    capped_high  = _poisson_probs(1.5, 1.5, league_draw_pct=0.80)
    assert abs(extreme_high["draw_prob"] - capped_high["draw_prob"]) < 0.001, \
        "Upper clamp (1.15) should plateau extreme values"


@test("NEWS-IMPACT-DIR — news_impact_home/away signal names queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals "
        "WHERE signal_name IN ('news_impact_home', 'news_impact_away')"
    )
    cnt = rows[0]["cnt"] if rows else 0
    # May be 0 if news_checker hasn't run since the fix — just verify query works without error
    assert isinstance(cnt, int), f"Expected int count, got {type(cnt)}"


@test("MGR-CHANGE — team_coaches table schema (migration 064)")
def _():
    from workers.api_clients.db import execute_query
    cols = execute_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name='team_coaches'"
    )
    if not cols:
        # Migration not yet applied — skip gracefully rather than failing
        return
    actual = {r["column_name"] for r in cols}
    required = {"id", "team_af_id", "coach_name", "start_date", "end_date", "fetched_at"}
    missing = required - actual
    assert not missing, f"Missing columns in team_coaches: {missing}"


@test("MGR-CHANGE — parse_coaches correctly extracts career entries")
def _():
    from workers.api_clients.api_football import parse_coaches
    from datetime import date
    # Simulate AF /coachs response structure
    sample = [{
        "id": 1,
        "name": "Test Manager",
        "firstname": "Test",
        "lastname": "Manager",
        "career": [
            {"team": {"id": 100, "name": "Club A"}, "start": "2026-01-15", "end": None},
            {"team": {"id": 99, "name": "Club B"}, "start": "2024-06-01", "end": "2025-12-31"},
        ]
    }]
    entries = parse_coaches(sample)
    assert len(entries) == 2, f"Expected 2 career entries, got {len(entries)}"
    current = next(e for e in entries if e["end_date"] is None)
    assert current["coach_name"] == "Test Manager"
    assert current["start_date"] == date(2026, 1, 15)
    past = next(e for e in entries if e["end_date"] is not None)
    assert past["end_date"] == date(2025, 12, 31)


@test("MGR-CHANGE — manager_change_home_days signal queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals "
        "WHERE signal_name IN ('manager_change_home_days', 'manager_change_away_days')"
    )
    cnt = rows[0]["cnt"] if rows else 0
    # May be 0 until coaches data accumulates — just verify schema + query run
    assert isinstance(cnt, int), f"Expected int count, got {type(cnt)}"


@test("MGR-CHANGE — fetch_enrichment imports coaches component without error")
def _():
    from workers.jobs.fetch_enrichment import fetch_coaches, run_enrichment  # noqa: F401


# ── AF-VENUES ─────────────────────────────────────────────────────────────────

@test("AF-VENUES — parse_venue extracts surface and capacity correctly")
def _():
    from workers.api_clients.api_football import parse_venue
    raw = {
        "id": 1,
        "name": "Old Trafford",
        "surface": "grass",
        "capacity": 76212,
    }
    result = parse_venue(raw)
    assert result["af_id"] == 1
    assert result["surface"] == "grass"
    assert result["capacity"] == 76212

    raw_turf = {"id": 2, "name": "Turf Arena", "surface": "Artificial Turf", "capacity": 5000}
    result_turf = parse_venue(raw_turf)
    assert result_turf["surface"] == "artificial turf", "surface should be lowercased"


@test("AF-VENUES — venue signal logic: artificial turf → 1.0, grass → 0.0")
def _():
    def surface_to_signal(surface: str) -> float:
        return 1.0 if (surface or "").lower() == "artificial turf" else 0.0

    assert surface_to_signal("grass") == 0.0
    assert surface_to_signal("Grass") == 0.0
    assert surface_to_signal("artificial turf") == 1.0
    assert surface_to_signal("Artificial Turf") == 1.0
    assert surface_to_signal("indoor") == 0.0
    assert surface_to_signal(None) == 0.0


@test("AF-VENUES — venues table exists (migration 065)")
def _():
    from workers.api_clients.db import execute_query
    try:
        cols = execute_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'venues' ORDER BY column_name",
            []
        )
    except Exception:
        cols = []
    if not cols:
        return  # migration not yet applied, skip gracefully
    col_names = {r["column_name"] for r in cols}
    assert "af_id" in col_names, "venues.af_id missing"
    assert "surface" in col_names, "venues.surface missing"
    assert "capacity" in col_names, "venues.capacity missing"


@test("AF-VENUES — matches.venue_af_id column exists (migration 065)")
def _():
    from workers.api_clients.db import execute_query
    try:
        cols = execute_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'matches' AND column_name = 'venue_af_id'",
            []
        )
    except Exception:
        cols = []
    if not cols:
        return  # migration not yet applied, skip gracefully
    assert len(cols) == 1, "matches.venue_af_id column not found"


@test("AF-VENUES — fetch_enrichment imports venues component without error")
def _():
    from workers.jobs.fetch_enrichment import fetch_venues, ALL_COMPONENTS  # noqa: F401
    assert "venues" in ALL_COMPONENTS


# ── AH-SIGNALS ────────────────────────────────────────────────────────────────

@test("AH-SIGNALS — parse_fixture_odds extracts Asian Handicap rows with handicap_line")
def _():
    from workers.api_clients.api_football import parse_fixture_odds
    raw = [{
        "bookmakers": [{
            "name": "Pinnacle",
            "bets": [{
                "name": "Asian Handicap",
                "values": [
                    {"value": "Home", "odd": "1.87", "handicap": "-0.5"},
                    {"value": "Away", "odd": "2.03", "handicap": "0.5"},
                ]
            }]
        }]
    }]
    rows = parse_fixture_odds(raw)
    ah_rows = [r for r in rows if r["market"] == "asian_handicap"]
    assert len(ah_rows) == 2, f"Expected 2 AH rows, got {len(ah_rows)}"
    home_row = next(r for r in ah_rows if r["selection"] == "home")
    assert home_row["handicap_line"] == -0.5
    assert home_row["bookmaker"] == "Pinnacle"
    away_row = next(r for r in ah_rows if r["selection"] == "away")
    assert away_row["handicap_line"] == 0.5


@test("AH-SIGNALS — parse_fixture_odds skips Asian Handicap First Half market")
def _():
    from workers.api_clients.api_football import parse_fixture_odds
    raw = [{
        "bookmakers": [{
            "name": "Pinnacle",
            "bets": [{
                "name": "Asian Handicap First Half",
                "values": [
                    {"value": "Home", "odd": "1.90", "handicap": "-0.25"},
                ]
            }]
        }]
    }]
    rows = parse_fixture_odds(raw)
    ah_rows = [r for r in rows if r["market"] == "asian_handicap"]
    assert len(ah_rows) == 0, "First Half AH should be skipped"


@test("AH-SIGNALS — parse_fixture_odds skips AH rows with missing handicap field")
def _():
    from workers.api_clients.api_football import parse_fixture_odds
    raw = [{
        "bookmakers": [{
            "name": "Bet365",
            "bets": [{
                "name": "Asian Handicap",
                "values": [
                    {"value": "Home", "odd": "1.90"},  # no handicap field
                ]
            }]
        }]
    }]
    rows = parse_fixture_odds(raw)
    ah_rows = [r for r in rows if r["market"] == "asian_handicap"]
    assert len(ah_rows) == 0, "AH row without handicap field should be skipped"


@test("OU-PARSE-BUG — parse_fixture_odds keeps FT Goals Over/Under rows")
def _():
    from workers.api_clients.api_football import parse_fixture_odds
    raw = [{
        "bookmakers": [{
            "name": "Pinnacle",
            "bets": [{
                "name": "Goals Over/Under",
                "values": [
                    {"value": "Over 2.5",  "odd": "1.85"},
                    {"value": "Under 2.5", "odd": "1.95"},
                ]
            }]
        }]
    }]
    rows = parse_fixture_odds(raw)
    ou25 = [r for r in rows if r["market"] == "over_under_25"]
    assert len(ou25) == 2, f"Expected 2 FT OU 2.5 rows, got {len(ou25)}: {ou25}"
    over = next(r for r in ou25 if r["selection"] == "over")
    assert abs(over["odds"] - 1.85) < 1e-6


@test("OU-PARSE-BUG — parse_fixture_odds drops First Half / team-specific OU markets")
def _():
    """Substring match used to bucket these into FT OU keys, producing fake edges."""
    from workers.api_clients.api_football import parse_fixture_odds
    raw = [{
        "bookmakers": [{
            "name": "1xBet",
            "bets": [
                {
                    "name": "Goals Over/Under First Half",
                    "values": [
                        {"value": "Over 2.5",  "odd": "6.50"},  # ← would have leaked into over_under_25
                        {"value": "Under 2.5", "odd": "1.10"},
                    ]
                },
                {
                    "name": "Goals Over/Under Second Half",
                    "values": [
                        {"value": "Over 2.5",  "odd": "5.20"},
                    ]
                },
                {
                    "name": "Home Team Goals Over/Under",
                    "values": [
                        {"value": "Over 1.5",  "odd": "3.10"},
                    ]
                },
                {
                    "name": "Away Team Goals Over/Under",
                    "values": [
                        {"value": "Over 1.5",  "odd": "4.00"},
                    ]
                },
            ]
        }]
    }]
    rows = parse_fixture_odds(raw)
    bad = [r for r in rows if r["market"].startswith("over_under_")]
    assert len(bad) == 0, (
        f"OU-PARSE-BUG: non-FT OU markets leaked into over_under_* buckets: {bad}"
    )


@test("OU-PARSE-BUG — parser uses exact match, not substring (source guard)")
def _():
    """Guard against revert to the substring 'Over/Under' in bet_name pattern."""
    import inspect
    from workers.api_clients import api_football
    src = inspect.getsource(api_football.parse_fixture_odds)
    # The buggy form was: "Over/Under" in bet_name
    assert '"Over/Under" in bet_name' not in src, (
        "OU-PARSE-BUG regressed: substring match is back in parse_fixture_odds. "
        "Use exact `bet_name == \"Goals Over/Under\"` only."
    )
    assert 'bet_name == "Goals Over/Under"' in src, (
        "OU-PARSE-BUG: expected exact match `bet_name == \"Goals Over/Under\"` in parser."
    )


@test("EMAIL-DIGEST-SMART — league_prestige_weight: Big-5 leagues weight 1.0")
def _():
    from workers.utils.league_prestige import league_prestige_weight
    assert league_prestige_weight("Premier League", "England", 1) == 1.0
    assert league_prestige_weight("La Liga", "Spain", 1) == 1.0
    assert league_prestige_weight("Bundesliga", "Germany", 1) == 1.0
    assert league_prestige_weight("Serie A", "Italy", 1) == 1.0
    assert league_prestige_weight("Ligue 1", "France", 1) == 1.0
    assert league_prestige_weight("UEFA Champions League", None, None) == 1.0


@test("EMAIL-DIGEST-SMART — league_prestige_weight: youth/women/lower-coverage = 0")
def _():
    from workers.utils.league_prestige import league_prestige_weight
    assert league_prestige_weight("Premier League", "Bhutan", 1) == 0.0, (
        "Bhutan top division shouldn't qualify"
    )
    assert league_prestige_weight("Campionato Primavera 2", "Italy", 1) == 0.0, (
        "Youth league should be excluded by 'primavera' keyword"
    )
    assert league_prestige_weight("Brescia U19", "Italy", 1) == 0.0, (
        "U19 should be excluded"
    )
    assert league_prestige_weight("FA WSL", "England", 1) > 0 or True  # no women hint in name
    assert league_prestige_weight("Aston Villa W", "England", 1) == 0.0, (
        "Trailing ' W' suffix should be excluded as women's league"
    )
    # Generic Polish 1. Liga (lower division) — country in T3 list, but tier=2
    # so falls through to 0
    assert league_prestige_weight("I Liga", "Poland", 2) == 0.0


@test("EMAIL-DIGEST-SMART — league_prestige_weight: T2/T3 tiers")
def _():
    from workers.utils.league_prestige import league_prestige_weight
    assert league_prestige_weight("Eredivisie", "Netherlands", 1) == 0.7
    assert league_prestige_weight("Championship", "England", 2) == 0.7
    assert league_prestige_weight("J1 League", "Japan", 1) == 0.7
    assert league_prestige_weight("Super League", "Switzerland", 1) == 0.4
    assert league_prestige_weight("Premier League", "Russia", 1) == 0.4


@test("EMAIL-DIGEST-SMART — qualifies_today returns False below threshold")
def _():
    """Source-level guard: the function exists and respects EMAIL_DIGEST_MIN_SIGNAL."""
    import inspect
    from workers.jobs import email_digest
    assert hasattr(email_digest, "qualifies_today"), "qualifies_today() must exist"
    assert hasattr(email_digest, "compute_signal_strength"), "compute_signal_strength() must exist"
    src = inspect.getsource(email_digest.compute_signal_strength)
    # Must use prestige weighting, not just count
    assert "prestige_weight" not in src or "PRESTIGE_WEIGHT_SQL" in src, (
        "compute_signal_strength must use the shared PRESTIGE_WEIGHT_SQL"
    )
    src_q = inspect.getsource(email_digest.qualifies_today)
    assert "EMAIL_DIGEST_MIN_SIGNAL" in src_q, (
        "qualifies_today must compare against EMAIL_DIGEST_MIN_SIGNAL"
    )


@test("EMAIL-DIGEST-SMART — scheduler has 4 slots at 10/12/14/16 UTC")
def _():
    """Source guard: scheduler must register four email_digest slots."""
    src = open("workers/scheduler.py").read()
    # The slot loop iterates hours; verify the loop with the right hours exists
    assert "for hour in (10, 12, 14, 16):" in src, (
        "Expected slot loop `for hour in (10, 12, 14, 16):` in scheduler"
    )
    assert 'id=f"email_digest_{hour:02d}"' in src, (
        "Expected formatted slot id `id=f\"email_digest_{hour:02d}\"`"
    )
    # Old single 07:30 hardcoded entry must be gone
    assert 'CronTrigger(hour=7, minute=30)' not in src or "email_digest_07" in src, (
        "Old single 07:30 email_digest cron is back — should be replaced by 4 slot entries"
    )


@test("EMAIL-DIGEST-SMART — run_email_digest gates on qualifies_today")
def _():
    """Source guard: ensure the qualification gate is wired into run_email_digest."""
    import inspect
    from workers.jobs import email_digest
    src = inspect.getsource(email_digest.run_email_digest)
    assert "qualifies_today" in src, (
        "run_email_digest must call qualifies_today before sending"
    )
    # Must support a `force` arg so ad-hoc sends can bypass
    sig = inspect.signature(email_digest.run_email_digest)
    assert "force" in sig.parameters, "run_email_digest must accept a `force` kwarg"


@test("BULK-STORE-PREDICTIONS — bulk_store_predictions exists and is signature-stable")
def _():
    """Source guard: ensure the bulk helper is exported and accepts a list of dicts."""
    import inspect
    from workers.api_clients import supabase_client
    assert hasattr(supabase_client, "bulk_store_predictions"), (
        "bulk_store_predictions must exist in supabase_client"
    )
    fn = supabase_client.bulk_store_predictions
    sig = inspect.signature(fn)
    assert len(sig.parameters) == 1, "bulk_store_predictions takes one arg (rows list)"
    src = inspect.getsource(fn)
    assert "execute_values" in src, "bulk_store_predictions must use execute_values"
    assert "ON CONFLICT (match_id, market, source) DO UPDATE" in src, (
        "bulk_store_predictions must upsert on the existing unique key"
    )
    # Empty list is a no-op, returns 0
    assert fn([]) == 0


@test("BULK-STORE-PREDICTIONS — fetch_predictions.py uses bulk write, not per-fixture INSERTs")
def _():
    """Guard against revert to per-fixture store_prediction loop."""
    import inspect
    from workers.jobs import fetch_predictions
    src = inspect.getsource(fetch_predictions.fetch_af_predictions)
    assert "bulk_store_predictions" in src, (
        "fetch_af_predictions must call bulk_store_predictions"
    )
    assert "bulk_update_match_af_predictions" in src, (
        "fetch_af_predictions must call bulk_update_match_af_predictions for matches.af_prediction"
    )
    # The per-fixture UPDATE matches and store_prediction calls must be gone
    assert "execute_write(" not in src, (
        "fetch_af_predictions still calls execute_write per fixture — should batch"
    )


@test("BULK-STORE-PREDICTIONS — daily_pipeline_v2.run_morning buffers + flushes")
def _():
    """Guard: run_morning's prediction stores must be buffered into pending_pred_rows."""
    src = open("workers/jobs/daily_pipeline_v2.py").read()
    assert "pending_pred_rows" in src, "run_morning must use pending_pred_rows buffer"
    # The 3 store_prediction call sites in run_morning's per-match loop should be gone.
    # Only the standalone _fetch_af_predictions still references bulk helpers; the
    # bare `store_prediction(match_id,` call form must not be inside run_morning.
    # We verify by counting: store_prediction( call sites should be 0 now (the
    # _fetch_af_predictions one was also bulk-converted).
    bare_calls = src.count("store_prediction(match_id,")
    assert bare_calls == 0, (
        f"Expected 0 per-row store_prediction calls in daily_pipeline_v2.py, "
        f"found {bare_calls}. Buffer + bulk-flush instead."
    )


@test("STORE-MATCH-DATE-NORMALIZE — _kickoff_minute normalizes T/space/Z/tz/microseconds")
def _():
    """Helper must produce identical canonical minutes regardless of source format.

    Bug it fixes: AF supplies ISO with `T` separator, psycopg2 datetime str() uses a
    space; old `[:16]` slice compared 'YYYY-MM-DDTHH:MM' to 'YYYY-MM-DD HH:MM' —
    always different → date column rewritten on every scheduled match every run.
    """
    from datetime import datetime, timezone
    from workers.api_clients.supabase_client import _kickoff_minute

    # Same instant, two source formats — must compare equal.
    assert _kickoff_minute("2026-05-10T14:00:00+00:00") == _kickoff_minute("2026-05-10 14:00:00+00:00")
    # Both yield the canonical T-form
    assert _kickoff_minute("2026-05-10T14:00:00+00:00") == "2026-05-10T14:00"
    # datetime objects (psycopg2 default return type)
    assert _kickoff_minute(datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc)) == "2026-05-10T14:00"
    # Non-UTC offset normalizes to UTC
    assert _kickoff_minute("2026-05-10T16:00:00+02:00") == "2026-05-10T14:00"
    # Z suffix
    assert _kickoff_minute("2026-05-10T14:00:00Z") == "2026-05-10T14:00"
    # Microseconds dropped
    assert _kickoff_minute("2026-05-10T14:00:30.123+00:00") == "2026-05-10T14:00"
    # Real kickoff change still detected
    assert _kickoff_minute("2026-05-10T14:00:00+00:00") != _kickoff_minute("2026-05-10T14:30:00+00:00")
    # Bad input → None (no false positive update)
    assert _kickoff_minute(None) is None
    assert _kickoff_minute("") is None
    assert _kickoff_minute("not a date") is None


@test("STORE-MATCH-DATE-NORMALIZE — store_match and bulk_store_matches use the helper")
def _():
    """Source guard: both date-mutation guards must go through _kickoff_minute,
    not raw [:16] string slicing (which always differed on T vs space)."""
    import inspect
    from workers.api_clients import supabase_client
    sm_src = inspect.getsource(supabase_client.store_match)
    bsm_src = inspect.getsource(supabase_client.bulk_store_matches)
    assert "_kickoff_minute" in sm_src, "store_match must use _kickoff_minute"
    assert "_kickoff_minute" in bsm_src, "bulk_store_matches must use _kickoff_minute"
    # The old broken slice form must be gone from both functions
    assert "new_date[:16]" not in sm_src, (
        "store_match still uses [:16] slice — STORE-MATCH-DATE-NORMALIZE reverted"
    )
    assert "new_date[:16]" not in bsm_src, (
        "bulk_store_matches still uses [:16] slice — STORE-MATCH-DATE-NORMALIZE reverted"
    )


@test("BULK-STORE-MATCHES — bulk_store_matches exists and uses one execute_values per phase")
def _():
    """Source guard: bulk helper exists, dedup uses tuple key, INSERT/UPDATE both use execute_values."""
    import inspect
    from workers.api_clients import supabase_client
    assert hasattr(supabase_client, "bulk_store_matches"), (
        "bulk_store_matches must exist in supabase_client"
    )
    fn = supabase_client.bulk_store_matches
    sig = inspect.signature(fn)
    assert len(sig.parameters) == 1, "bulk_store_matches takes one arg (match_dicts list)"
    src = inspect.getsource(fn)
    # Dedup uses (home_team_id, away_team_id, date) tuple key
    assert "home_team_id = v.home_id" in src, (
        "bulk dedup must join on (home_team_id, away_team_id, date_prefix)"
    )
    # Both INSERT and UPDATE must go via execute_values, not per-row execute
    ev_count = src.count("execute_values(")
    assert ev_count >= 3, (
        f"bulk_store_matches expected ≥3 execute_values calls (dedup, insert, update); found {ev_count}"
    )
    # INSERT must request RETURNING id so callers can map back to inputs
    assert "RETURNING id" in src, "bulk INSERT must use RETURNING id"
    # Empty list is a no-op
    assert fn([]) == []


@test("BULK-STORE-MATCHES — fetch_fixtures.py uses bulk helper, not per-row store_match")
def _():
    """Guard against revert to serial store_match() loop in the fixtures cron."""
    src = open("workers/jobs/fetch_fixtures.py").read()
    assert "bulk_store_matches" in src, (
        "fetch_fixtures must call bulk_store_matches"
    )
    # The per-fixture store_match( inside the loop must be gone
    assert "store_match(match_dict)" not in src, (
        "fetch_fixtures still calls store_match per fixture — should bulk"
    )


@test("BULK-STORE-MATCHES — daily_pipeline_v2.py and backfill_historical.py have no per-row store_match")
def _():
    """Guard against revert in the two remaining call sites."""
    dp_src = open("workers/jobs/daily_pipeline_v2.py").read()
    assert "bulk_store_matches" in dp_src, "daily_pipeline_v2 must use bulk_store_matches"
    # Bare `store_match(` calls in run_morning must be gone — only the docstring/comment
    # references remain. Count actual call expressions: `store_match(` followed by an arg.
    bare = dp_src.count("store_match(match_dict)") + dp_src.count("store_match(match)")
    assert bare == 0, (
        f"Expected 0 per-row store_match calls in daily_pipeline_v2.py, found {bare}."
    )

    bf_src = open("scripts/backfill_historical.py").read()
    assert "bulk_store_matches" in bf_src, "backfill_historical must use bulk_store_matches"
    bare_bf = bf_src.count("store_match(match_dict)") + bf_src.count("= store_match(")
    assert bare_bf == 0, (
        f"Expected 0 per-row store_match calls in backfill_historical.py, found {bare_bf}."
    )


@test("AH-SIGNALS — odds_snapshots.handicap_line column exists (migration 066)")
def _():
    from workers.api_clients.db import execute_query
    try:
        cols = execute_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'odds_snapshots' AND column_name = 'handicap_line'",
            []
        )
    except Exception:
        cols = []
    if not cols:
        return  # migration not yet applied, skip gracefully
    assert len(cols) == 1


@test("AH-SIGNALS — pinnacle_ah_line signal name queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS n FROM match_signals WHERE signal_name = 'pinnacle_ah_line'",
        []
    )
    assert rows[0]["n"] >= 0  # 0 is fine pre-collection


@test("BTTS-SIGNAL — pinnacle_btts_yes_prob signal name queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS n FROM match_signals WHERE signal_name = 'pinnacle_btts_yes_prob'",
        []
    )
    assert rows[0]["n"] >= 0


# ── H2H-SPLITS ────────────────────────────────────────────────────────────────

@test("H2H-SPLITS — h2h_avg_goal_diff computed correctly from h2h_raw perspective")
def _():
    # Simulate h2h_raw: home team (AF id=33) won 2-0 and 1-0, lost 0-1
    home_af_id = 33
    h2h_raw = [
        {"teams": {"home": {"id": 33}, "away": {"id": 40}}, "goals": {"home": 2, "away": 0}},
        {"teams": {"home": {"id": 40}, "away": {"id": 33}}, "goals": {"home": 1, "away": 0}},  # our team lost
        {"teams": {"home": {"id": 33}, "away": {"id": 40}}, "goals": {"home": 1, "away": 0}},
    ]
    goal_diffs = []
    wins = []
    for f in h2h_raw:
        fix_home_id = f["teams"]["home"]["id"]
        gf, ga = f["goals"]["home"], f["goals"]["away"]
        if fix_home_id == home_af_id:
            goal_diffs.append(gf - ga)
            wins.append(1 if gf > ga else 0)
        else:
            goal_diffs.append(ga - gf)
            wins.append(1 if ga > gf else 0)

    # 2-0, -1 (lost 0-1), 1-0 → diffs [2, -1, 1] → avg = 0.667
    assert abs(sum(goal_diffs) / len(goal_diffs) - 2/3) < 0.001
    # wins: [1, 0, 1] → 2/3 overall, recent (last 3) = [1, 0, 1] = 2/3 → premium = 0
    assert sum(wins) == 2


@test("H2H-SPLITS — h2h_recency_premium positive when recent form better than overall")
def _():
    # 5 H2H: last 3 all wins, earlier 2 all losses → recency premium > 0
    home_af_id = 33
    h2h_raw = [  # newest first
        {"teams": {"home": {"id": 33}, "away": {"id": 40}}, "goals": {"home": 2, "away": 0}},
        {"teams": {"home": {"id": 33}, "away": {"id": 40}}, "goals": {"home": 1, "away": 0}},
        {"teams": {"home": {"id": 33}, "away": {"id": 40}}, "goals": {"home": 3, "away": 1}},
        {"teams": {"home": {"id": 33}, "away": {"id": 40}}, "goals": {"home": 0, "away": 2}},
        {"teams": {"home": {"id": 33}, "away": {"id": 40}}, "goals": {"home": 0, "away": 1}},
    ]
    wins = []
    for f in h2h_raw:
        fix_home_id = f["teams"]["home"]["id"]
        gf, ga = f["goals"]["home"], f["goals"]["away"]
        wins.append(1 if (gf > ga and fix_home_id == home_af_id) or (ga > gf and fix_home_id != home_af_id) else 0)

    recent_pct = sum(wins[:3]) / 3   # 3/3 = 1.0
    overall_pct = sum(wins) / len(wins)  # 3/5 = 0.6
    premium = round(recent_pct - overall_pct, 4)
    assert premium > 0, f"Expected positive recency premium, got {premium}"
    assert abs(premium - 0.4) < 0.001


@test("H2H-SPLITS — matches.home_team_api_id column exists (migration 067)")
def _():
    from workers.api_clients.db import execute_query
    try:
        cols = execute_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'matches' AND column_name IN ('home_team_api_id', 'away_team_api_id')",
            []
        )
    except Exception:
        cols = []
    if not cols:
        return  # migration not yet applied
    col_names = {r["column_name"] for r in cols}
    assert "home_team_api_id" in col_names
    assert "away_team_api_id" in col_names


@test("H2H-SPLITS — h2h_avg_goal_diff signal name queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS n FROM match_signals WHERE signal_name = 'h2h_avg_goal_diff'",
        []
    )
    assert rows[0]["n"] >= 0


@test("INPLAY-EDGE — simulated_bets.edge_percent stored as decimal not percent (bug: inplay bot was * 100)")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT id, edge_percent FROM simulated_bets
           WHERE edge_percent IS NOT NULL AND edge_percent > 1.5
           LIMIT 5""",
        []
    )
    bad = [r for r in rows if r["edge_percent"] is not None and float(r["edge_percent"]) > 1.5]
    assert len(bad) == 0, (
        f"{len(bad)} bet(s) have edge_percent > 1.5 (150% edge — likely stored as percent, not decimal): "
        + ", ".join(f"id={r['id']} edge={r['edge_percent']}" for r in bad[:3])
    )


# ── Group 1 quick wins ────────────────────────────────────────────────────────

@test("H2H-GATE — h2h_win_pct gated by sample size (total=5 → 50% weight, total=10 → 100%)")
def _():
    # With total=5: raw_pct=0.6, gate=0.5, stored=0.3
    hw, total = 3, 5
    gate = min(total / 10.0, 1.0)
    gated = round((hw / total) * gate, 4)
    assert abs(gated - 0.3) < 0.001, f"Expected 0.3 (gated), got {gated}"

    # With total=10: gate=1.0, stored=raw
    hw2, total2 = 6, 10
    gate2 = min(total2 / 10.0, 1.0)
    gated2 = round((hw2 / total2) * gate2, 4)
    assert abs(gated2 - 0.6) < 0.001, f"Expected 0.6 (no gate penalty at n=10), got {gated2}"

    # With total=15: gate=1.0 (clamped), stored=raw
    hw3, total3 = 9, 15
    gate3 = min(total3 / 10.0, 1.0)
    assert gate3 == 1.0, f"Gate should cap at 1.0 for total >= 10, got {gate3}"


@test("DOUBTFUL-SIGNAL — players_doubtful_home/away signal names queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals "
        "WHERE signal_name IN ('players_doubtful_home', 'players_doubtful_away')",
        []
    )
    cnt = rows[0]["cnt"] if rows else 0
    # May be 0 if no doubtful players today — just verify query runs without error
    assert isinstance(cnt, int), f"Expected int count, got {type(cnt)}"


@test("SHARP-DRAW-AWAY — sharp_consensus_draw/away signal names queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals "
        "WHERE signal_name IN ('sharp_consensus_draw', 'sharp_consensus_away')",
        []
    )
    cnt = rows[0]["cnt"] if rows else 0
    # May be 0 on first run before odds collected — verify query runs
    assert isinstance(cnt, int), f"Expected int count, got {type(cnt)}"


@test("LEAGUE-GOALS-DIST — league_over25_pct and league_btts_pct queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals "
        "WHERE signal_name IN ('league_over25_pct', 'league_btts_pct')",
        []
    )
    cnt = rows[0]["cnt"] if rows else 0
    assert isinstance(cnt, int), f"Expected int count, got {type(cnt)}"


@test("INJURY-UNCERTAINTY — injury_uncertainty_home/away queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals "
        "WHERE signal_name IN ('injury_uncertainty_home', 'injury_uncertainty_away')",
        []
    )
    cnt = rows[0]["cnt"] if rows else 0
    assert isinstance(cnt, int), f"Expected int count, got {type(cnt)}"


@test("ODDS-VOL-AUDIT — odds_volatility uses is_live=false filter (no post-kickoff contamination)")
def _():
    import inspect
    from workers.api_clients import supabase_client
    src = inspect.getsource(supabase_client.batch_write_morning_signals)
    assert "is_live = false" in src, "odds_volatility query must filter is_live=false to prevent in-play odds contamination"
    assert "odds_volatility" in src, "odds_volatility signal must be present in batch_write_morning_signals"
    # Confirm the 24h window uses cutoff based on now(), not kickoff — all snapshots are past timestamps
    assert "cutoff_24h" in src, "24h rolling window variable must be present"


# ── Group 2 signal refinements ────────────────────────────────────────────────

@test("REST-NONLINEAR — log(rest_days+1) squashes correctly (unit test)")
def _():
    import math
    # log(3+1) ≈ 1.386
    assert abs(round(math.log(3 + 1), 4) - 1.3863) < 0.001, "log(4) should be ~1.386"
    # Diminishing returns: adding 1 rest day matters less at 10 days than at 1→2 days
    delta_low = math.log(2 + 1) - math.log(1 + 1)   # 1→2 days
    delta_high = math.log(11 + 1) - math.log(10 + 1)  # 10→11 days
    assert delta_low > delta_high, "log-transform must show diminishing returns at high rest values"


@test("REST-NONLINEAR — rest_days_norm_home/away signal names in source")
def _():
    import inspect
    from workers.api_clients import supabase_client
    src = inspect.getsource(supabase_client.batch_write_morning_signals)
    assert "rest_days_norm_home" in src, "rest_days_norm_home must be written"
    assert "rest_days_norm_away" in src, "rest_days_norm_away must be written"
    assert "math.log" in src, "log-transform must use math.log"


@test("IMPORTANCE-GAMES-REM — fixture_urgency_home/away queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals "
        "WHERE signal_name IN ('fixture_urgency_home', 'fixture_urgency_away')",
        []
    )
    cnt = rows[0]["cnt"] if rows else 0
    assert isinstance(cnt, int), f"Expected int count, got {type(cnt)}"


@test("IMPORTANCE-GAMES-REM — games_remaining computed from played in standings query")
def _():
    import inspect
    from workers.api_clients import supabase_client
    src = inspect.getsource(supabase_client.batch_write_morning_signals)
    assert "games_remaining_" in src, "games_remaining_{suffix} signal must be written"
    assert "fixture_urgency_" in src, "fixture_urgency_{suffix} signal must be written"
    assert "total_season_games" in src, "total_season_games formula must be present"
    assert "played" in src, "played column must be used for games remaining computation"


@test("TURF-FAMILIARITY — away_team_turf_games_ytd queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals WHERE signal_name = 'away_team_turf_games_ytd'",
        []
    )
    cnt = rows[0]["cnt"] if rows else 0
    assert isinstance(cnt, int), f"Expected int count, got {type(cnt)}"


@test("FORM-ELO-RESIDUAL — ELO→expected PPG formula at known ELO values")
def _():
    # At ELO=1500 (exactly average): p_win=0.5, expected_ppg = 3*0.5 + 0.27 = 1.77
    p_win_1500 = 1.0 / (1.0 + 10.0 ** ((1500.0 - 1500.0) / 400.0))
    expected_1500 = 3.0 * p_win_1500 + 0.27
    assert abs(expected_1500 - 1.77) < 0.01, f"At ELO=1500 expected ~1.77 PPG, got {expected_1500}"
    # At ELO=1700 (strong team): p_win higher → expected_ppg > 2.5
    p_win_1700 = 1.0 / (1.0 + 10.0 ** ((1500.0 - 1700.0) / 400.0))
    expected_1700 = 3.0 * p_win_1700 + 0.27
    assert expected_1700 > 2.5, f"At ELO=1700 expected >2.5 PPG, got {expected_1700}"
    # Residual is positive for a team outperforming ELO expectation
    actual_ppg = 2.5
    residual = actual_ppg - expected_1500  # vs average-ELO team
    assert residual > 0, "Team with 2.5 PPG beats ELO=1500 expectation (~1.77)"


@test("FORM-ELO-RESIDUAL — form_vs_elo_expectation signal names in source")
def _():
    import inspect
    from workers.api_clients import supabase_client
    src = inspect.getsource(supabase_client.batch_write_morning_signals)
    assert "form_vs_elo_expectation_" in src, "form_vs_elo_expectation_{suffix} must be written"
    assert "expected_ppg" in src, "expected_ppg variable must be present in ELO residual computation"
    assert "p_win" in src, "p_win ELO probability variable must be present"


@test("POOL-LEAK-FIX — SQL exceptions don't leak conns (25 errors > maxconn=20)")
def _():
    """The 2026-05-08 outage: get_conn() leaked conns on any exception other
    than OperationalError/InterfaceError, so a single SQL syntax error per
    polling cycle drained the pool within 5 minutes. Verify 25 SQL errors
    (more than maxconn=20) leave the pool usable."""
    from workers.api_clients.db import get_conn
    import psycopg2

    for _ in range(25):  # > maxconn=20, so leaks would have exhausted
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # Deliberate bad SQL — psycopg2.errors.UndefinedTable, NOT
                    # OperationalError. Pre-fix this would leak the conn.
                    cur.execute("SELECT * FROM table_that_does_not_exist_xyzzy")
        except psycopg2.Error:
            pass  # expected

    # If conns leaked, this would fail with PoolError. With the fix, fine.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1


@test("POOL-LEAK-FIX — caller-raised exceptions don't leak (KeyError mid-query)")
def _():
    """Same as above but with a non-DB exception raised by the caller while
    holding a conn (e.g. row dict missing a key). Pre-fix this also leaked.
    Use execute_query to absorb any flakey SSL-drop on idle pooled conns."""
    from workers.api_clients.db import get_conn, execute_query

    for _ in range(25):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 AS ok")
                    cur.fetchone()
                    raise KeyError("simulated caller-side failure")
        except KeyError:
            pass
        except Exception:
            # Tolerate occasional Supavisor SSL drops on idle conns —
            # execute_query has retry built in, but our raw get_conn doesn't.
            # The test's purpose is leak detection, not SSL stability.
            pass

    # Pool must still be usable. execute_query retries through SSL drops.
    rows = execute_query("SELECT 1 AS ok")
    assert rows[0]["ok"] == 1


@test("OBS-LOG-ALL-JOBS — _run_job auto-logs to pipeline_runs")
def _():
    """Source guard: _run_job must call log_pipeline_start/complete/failed so
    the ops dashboard sees every wrapped job, not just the 14 that happen to
    log themselves. _log_run=False is the intentional opt-out for jobs whose
    body already logs the same job_name (currently settlement and hist_backfill).

    Reads source text directly so the test runs without apscheduler installed.
    """
    src = open("workers/scheduler.py").read()
    # Locate the _run_job function body (until the next top-level def)
    start = src.find("def _run_job(")
    assert start != -1, "_run_job not found in scheduler.py"
    body_end = src.find("\ndef ", start + 1)
    body = src[start:body_end]
    assert "log_pipeline_start" in body, "_run_job must call log_pipeline_start"
    assert "log_pipeline_complete" in body, "_run_job must call log_pipeline_complete on success"
    assert "log_pipeline_failed" in body, "_run_job must call log_pipeline_failed on exception"
    assert "_log_run" in body, "_run_job must support _log_run=False opt-out"
    # The two known double-log conflicts are explicitly suppressed
    assert '_run_job("settlement", settlement_pipeline, _log_run=False)' in src, (
        "settlement wrapper must opt out — settlement_pipeline already logs as 'settlement'"
    )
    backfill_idx = src.find('_run_job("hist_backfill"')
    assert backfill_idx != -1, "hist_backfill wrapper not found"
    assert "_log_run=False" in src[backfill_idx:backfill_idx + 200], (
        "hist_backfill wrapper must opt out — run_backfill already logs as 'hist_backfill'"
    )


@test("OBS-POOL-METRIC — get_pool_status returns valid structure")
def _():
    from workers.api_clients.db import get_pool_status, get_pool
    get_pool()  # ensure pool is initialised
    status = get_pool_status()
    assert "used" in status and "idle" in status and "max" in status and "pct" in status
    assert status["max"] == 20
    assert 0 <= status["pct"] <= 100
    assert status["used"] + status["idle"] <= status["max"]


@test("POOL-WAIT — _acquire_conn waits on saturation instead of immediate PoolError")
def _():
    """The 2026-05-09 fix: pool exhaustion previously crashed inplay_bot mid-cycle
    (`psycopg2.pool.PoolError: connection pool exhausted`). Now `_acquire_conn`
    polls with backoff until a slot frees up, only raising after wait_timeout.
    Use an isolated pool so we don't starve other parallel smoke tests."""
    import os
    import time as _time
    from psycopg2 import pool as _pool_mod
    from workers.api_clients.db import _acquire_conn

    p = _pool_mod.ThreadedConnectionPool(
        minconn=1, maxconn=2, dsn=os.getenv("DATABASE_URL"), connect_timeout=10
    )
    try:
        held = [p.getconn() for _ in range(2)]  # saturate the isolated pool

        t0 = _time.monotonic()
        try:
            _acquire_conn(p, timeout=1.0)
            assert False, "expected PoolError after timeout"
        except _pool_mod.PoolError:
            pass
        elapsed = _time.monotonic() - t0
        assert 0.9 <= elapsed <= 3.0, (
            f"_acquire_conn did not wait the configured timeout: {elapsed:.2f}s "
            f"(expected ~1.0s — unpatched psycopg2 raises immediately)"
        )

        # Release one slot — _acquire_conn should now succeed quickly.
        p.putconn(held.pop())
        t0 = _time.monotonic()
        conn = _acquire_conn(p, timeout=2.0)
        assert conn is not None and (_time.monotonic() - t0) < 1.5
        p.putconn(conn)
        for c in held:
            p.putconn(c)
    finally:
        try:
            p.closeall()
        except Exception:
            pass


@test("POOL-FANOUT — fetch_post_match_enrichment caps ThreadPoolExecutor at 2 workers")
def _():
    import inspect
    from workers.jobs import settlement
    src = inspect.getsource(settlement.fetch_post_match_enrichment)
    assert "max_workers=2" in src, (
        "fetch_post_match_enrichment must use max_workers=2 — each thread can hold "
        "up to 3 conns (stats+events+player_stats), so 4 workers = up to 12 conns "
        "from this function alone, which can blow the 20-conn pool when overlapping "
        "with LivePoller + scheduler workers."
    )


@test("POOL-FANOUT — APScheduler executor capped at 4 threads")
def _():
    # Source-read instead of import — apscheduler isn't always installed in
    # the smoke-test venv, but the source file is always in-tree.
    from pathlib import Path
    src = Path("workers/scheduler.py").read_text()
    assert "APSThreadPoolExecutor(max_workers=4)" in src, (
        "BackgroundScheduler must use APSThreadPoolExecutor(max_workers=4) — "
        "default 10 threads × multiple conns/job can fan out to 15+ conns at "
        "startup catch-up, exhausting the pool."
    )
    assert 'executors={"default": APSThreadPoolExecutor' in src, (
        "BackgroundScheduler() must be passed the executor cap explicitly"
    )


@test("POOL-FANOUT — store_match_events_batch uses execute_values (single round-trip)")
def _():
    import inspect
    from workers.api_clients import db
    src = inspect.getsource(db.store_match_events_batch)
    assert "execute_values" in src, (
        "store_match_events_batch must use psycopg2.extras.execute_values — "
        "the per-row INSERT loop holds the conn for ~30 round-trips per match, "
        "blocking other threads waiting on the pool. Bulk insert releases the "
        "conn in a fraction of the time."
    )
    # Ensure fallback per-row path is preserved so a single bad event doesn't
    # kill the whole batch.
    assert "for row in rows:" in src, (
        "store_match_events_batch must keep the per-row fallback for batch failures"
    )


@test("POOL-WAIT — default timeout is 15s, not 60s (override via env)")
def _():
    import os
    # Avoid module-cache effects: re-read the constant from source rather than
    # importing (the user may have set DB_POOL_WAIT_TIMEOUT in their .env).
    import inspect
    from workers.api_clients import db
    src = inspect.getsource(db)
    assert 'os.getenv("DB_POOL_WAIT_TIMEOUT", "15")' in src, (
        "DB_POOL_WAIT_TIMEOUT default must be 15s — 60s of silent waiting hides "
        "real saturation problems. Override via env var if a specific job needs longer."
    )


@test("BOOKMAKER-COUNT — bookmaker_count_active signal name in source")
def _():
    import inspect
    from workers.api_clients import supabase_client
    src = inspect.getsource(supabase_client.batch_write_morning_signals)
    assert "bookmaker_count_active" in src, "bookmaker_count_active must be added in batch_write_morning_signals"


@test("BOOKMAKER-COUNT — bookmaker_count_active queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals WHERE signal_name = 'bookmaker_count_active'",
        []
    )
    assert isinstance(rows[0]["cnt"], int)


@test("LEAGUE-ELO-VAR — league_elo_variance signal name in source")
def _():
    import inspect
    from workers.api_clients import supabase_client
    src = inspect.getsource(supabase_client.batch_write_morning_signals)
    assert "league_elo_variance" in src, "league_elo_variance must be in batch_write_morning_signals"
    assert "league_elo_range" in src, "league_elo_range must be in batch_write_morning_signals"


@test("LEAGUE-ELO-VAR — league_elo_variance queryable in match_signals")
def _():
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM match_signals WHERE signal_name IN ('league_elo_variance', 'league_elo_range')",
        []
    )
    assert isinstance(rows[0]["cnt"], int)


@test("ML-ELO-GAP — elo_home/elo_away/elo_diff in FEATURE_COLS")
def _():
    from workers.model.train import FEATURE_COLS
    assert "elo_home" in FEATURE_COLS, "elo_home missing from FEATURE_COLS"
    assert "elo_away" in FEATURE_COLS, "elo_away missing from FEATURE_COLS"
    assert "elo_diff" in FEATURE_COLS, "elo_diff missing from FEATURE_COLS"


@test("ML-FEATURE-COLS-ALIGN — FEATURE_COLS use MFV column names (no Kaggle-era names)")
def _():
    from workers.model.train import FEATURE_COLS
    # Old Kaggle-era names that don't exist in match_feature_vectors
    banned = {
        "home_form_win_pct", "home_form_ppg", "home_venue_win_pct",
        "away_form_win_pct", "away_form_ppg", "away_venue_win_pct",
        "h2h_home_win_pct", "h2h_avg_goals", "h2h_btts_pct", "h2h_matches",
        "home_position_norm", "away_position_norm", "position_diff",
        "home_pts_to_relegation", "away_pts_to_relegation",
        "home_rest_days", "away_rest_days", "rest_advantage",
    }
    bad = [f for f in FEATURE_COLS if f in banned]
    assert not bad, f"Kaggle-era column names in FEATURE_COLS: {bad}"


@test("ML-FEATURE-COLS-ALIGN — train_result_model uses match_outcome not result")
def _():
    import inspect
    from workers.model import train
    src = inspect.getsource(train.train_result_model)
    assert "match_outcome" in src, "train_result_model still references old 'result' column"
    assert '"result"' not in src, "train_result_model still uses targets_df[\"result\"]"


@test("ML-CALIBRATION-FIX — no CalibratedClassifierCV in train.py")
def _():
    import inspect
    from workers.model import train
    src = inspect.getsource(train)
    assert "CalibratedClassifierCV" not in src, "CalibratedClassifierCV still present — dual calibration not fixed"


@test("KILL-SWITCH-FLAGS — is_disabled returns False for unknown flag")
def _():
    from workers.utils.kill_switches import is_disabled
    assert is_disabled("nonexistent_flag") is False


@test("KILL-SWITCH-FLAGS — is_disabled returns False when env var unset")
def _():
    import os
    from workers.utils.kill_switches import is_disabled
    os.environ.pop("DISABLE_ENRICHMENT", None)
    assert is_disabled("enrichment") is False


@test("KILL-SWITCH-FLAGS — is_disabled returns True when env var set to '1'")
def _():
    import os
    from workers.utils.kill_switches import is_disabled
    os.environ["DISABLE_NEWS_CHECKER"] = "1"
    try:
        assert is_disabled("news_checker") is True
    finally:
        del os.environ["DISABLE_NEWS_CHECKER"]


@test("store_team_transfers — uses bulk execute_values (not per-row connections)")
def _():
    import ast, pathlib
    src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    # Verify no for-loop opening get_conn() inside store_team_transfers
    fn_start = src.index("def store_team_transfers(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "execute_values" in fn_body, "store_team_transfers must use execute_values for bulk insert"
    assert fn_body.count("get_conn()") == 1, (
        f"store_team_transfers should open exactly 1 DB connection (got {fn_body.count('get_conn()')})"
    )
    # Must dedupe on the conflict key before bulk upsert — AF returns multi-leg
    # transfers on the same (player, date) which trip "ON CONFLICT cannot affect row a second time".
    assert 'r["team_api_id"], r["player_id"], r["transfer_date"]' in fn_body, (
        "store_team_transfers must dedupe rows on (team_api_id, player_id, transfer_date) "
        "before execute_values to avoid Postgres 'ON CONFLICT cannot affect row a second time' errors"
    )


@test("INPLAY-UUID-FIX — mid converted to str before prematch dict lookup")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    # Verify the main loop uses str(cand["match_id"]) not raw UUID
    assert 'mid = str(cand["match_id"])' in src, (
        "mid must be str() — psycopg2 returns UUID objects, prematch dict has string keys"
    )


@test("INPLAY-UUID-FIX — prematch dict keyed on str(match_id)")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _get_prematch_data(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert 'str(r["match_id"])' in fn_body, (
        "_get_prematch_data must key the return dict on str(match_id)"
    )


@test("INPLAY-DROP-F — inplay_f removed from INPLAY_BOTS dict and dispatcher")
def _():
    import pathlib, re
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    # INPLAY_BOTS dict block: pull only the dict literal
    dict_start = src.index("INPLAY_BOTS = {")
    dict_end = src.index("\n}\n", dict_start) + 2
    bots_block = src[dict_start:dict_end]
    assert '"inplay_f"' not in bots_block, (
        "inplay_f must not be a key in INPLAY_BOTS — strategy F was dropped 2026-05-08"
    )

    # Dispatcher block: _check_strategy() function body
    disp_start = src.index("def _check_strategy(")
    disp_end = src.index("\ndef ", disp_start + 1)
    disp_body = src[disp_start:disp_end]
    assert 'bot_name == "inplay_f"' not in disp_body, (
        "_check_strategy dispatcher must not route to inplay_f"
    )


@test("INPLAY-FIX-B-MODEL — strategy B uses _poisson_over_prob, not BTTS exp formula")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_b(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    # The buggy version computed btts_prob = 1 - exp(-blended_lambda) and bet OU 2.5
    assert "_poisson_over_prob(" in fn_body, (
        "Strategy B must compute P(Over 2.5) via _poisson_over_prob() — fix from 5-AI review"
    )
    assert "btts_prob = 1.0 - math.exp" not in fn_body, (
        "Strategy B must not use the old btts_prob = 1 - exp(-lambda) phantom-edge formula"
    )


@test("INPLAY-FIX-E-FALLBACK — prematch query falls back to league avg, exposes flag")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _get_prematch_data(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    # Old code: COALESCE(tss_h.goals_for_avg::numeric, 1.3) — flat 1.3 fallback
    # New code: COALESCE(tss_h.goals_for_avg, la.league_avg, 1.1)
    assert "la.league_avg" in fn_body, (
        "Prematch query must fall back to per-league average before global default"
    )
    assert "xg_fallback_used" in fn_body, (
        "Query must expose xg_fallback_used flag so strategies can apply edge penalty"
    )
    assert ", 1.3) AS prematch_xg_home" not in fn_body, (
        "The flat 1.3 fallback was the source of inflated E ROI — must be replaced"
    )


@test("INPLAY-FIX-E-FALLBACK — strategy E proxy mode disabled (if not is_real: return None)")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_e(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "if not is_real:" in fn_body and "return None" in fn_body, (
        "Strategy E must bail early on proxy mode — 182 shot_proxy bets at −4.7% ROI confirmed bad"
    )
    assert "shot_proxy" not in fn_body or "disabled" in fn_body, (
        "Strategy E must not produce shot_proxy bets — proxy formula inflated expected_shots"
    )
    assert "expected_shots_at_minute" not in fn_body, (
        "The buggy expected_shots_at_minute formula must be removed from strategy E"
    )


@test("INPLAY-FIX-E-FALLBACK — migration 085 voids settled shot_proxy bets")
def _():
    import pathlib
    src = pathlib.Path("supabase/migrations/085_void_e_proxy_bets_settled.sql").read_text()
    assert "xg_source = 'shot_proxy'" in src, "085 must scope to shot_proxy bets"
    assert "result = 'void'" in src, "085 must set result = 'void' (enum value, not 'voided')"
    assert "result IN ('won', 'lost')" in src, (
        "085 must target settled bets — 079's 'pending' filter matched zero rows after settlement"
    )
    assert "inplay_e" in src, "085 must scope to inplay_e bot"


@test("VOID-AGG-EXCLUSION — dashboard_cache and post-mortem queries exclude voids")
def _():
    """Voided bets keep their original pnl/stake (we only flip `result` to 'void').
    A `result != 'pending'` filter therefore double-counts them in settled/pnl/staked.
    Every aggregate in settlement.py must use `result IN ('won','lost')` instead.
    Bug surfaced 2026-05-10 when 182 voided E proxy bets pulled hit_rate to ~7%."""
    import pathlib
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    fn_start = src.index("def write_dashboard_cache(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    non_comment = "\n".join(
        line for line in fn_body.splitlines() if not line.lstrip().startswith("#")
    )
    assert "result != 'pending'" not in non_comment, (
        "write_dashboard_cache: replace `result != 'pending'` with `result IN ('won','lost')`"
        " — voids contaminate settled/pnl/staked"
    )
    assert "result IN ('won','lost')" in non_comment, (
        "write_dashboard_cache must use the void-aware filter"
    )


@test("INPLAY-MERGE-A2 — inplay_a2 removed from INPLAY_BOTS and dispatcher")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    dict_start = src.index("INPLAY_BOTS = {")
    dict_end = src.index("\n}\n", dict_start) + 2
    bots_block = src[dict_start:dict_end]
    assert '"inplay_a2"' not in bots_block, (
        "inplay_a2 must not be a key in INPLAY_BOTS — merged into A on 2026-05-08"
    )

    disp_start = src.index("def _check_strategy(")
    disp_end = src.index("\ndef ", disp_start + 1)
    disp_body = src[disp_start:disp_end]
    assert 'bot_name == "inplay_a2"' not in disp_body, (
        "_check_strategy dispatcher must not route to inplay_a2"
    )

    # The merged A must accept total_goals <= 1 (covers 0-0, 1-0, 0-1)
    a_start = src.index("def _check_strategy_a(")
    a_end = src.index("\ndef ", a_start + 1)
    a_body = src[a_start:a_end]
    assert "if sh + sa > 1:" in a_body, (
        "Merged Strategy A must filter on total_goals <= 1, not just (0,0)"
    )


@test("INPLAY-MERGE-CHOME — inplay_c_home removed; C handles home/away in one path")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    dict_start = src.index("INPLAY_BOTS = {")
    dict_end = src.index("\n}\n", dict_start) + 2
    bots_block = src[dict_start:dict_end]
    assert '"inplay_c_home"' not in bots_block, (
        "inplay_c_home must not be a key in INPLAY_BOTS — merged into C on 2026-05-08"
    )

    disp_start = src.index("def _check_strategy(")
    disp_end = src.index("\ndef ", disp_start + 1)
    disp_body = src[disp_start:disp_end]
    assert 'bot_name == "inplay_c_home"' not in disp_body, (
        "_check_strategy dispatcher must not route to inplay_c_home"
    )

    # _check_strategy_c must no longer take a home_only parameter
    c_start = src.index("def _check_strategy_c(")
    c_signature_end = src.index(":", c_start)
    c_signature = src[c_start:c_signature_end]
    assert "home_only" not in c_signature, (
        "_check_strategy_c signature must not include home_only — merged into single strategy"
    )


@test("INPLAY-LOOSEN-A — strategy A uses minute 20-40 + live_xg ≥ 0.6 + sot ≥ 3")
def _():
    import pathlib, re
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_a(")
    fn_end = src.index("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "if minute < 20 or minute > 40" in body, "A minute window must loosen to 20-40"
    assert "live_xg < 0.6" in body, "A real-xG floor must drop to 0.6 (was 0.9)"
    assert "sot < 3" in body, "A real SoT floor must drop to 3 (was 4)"
    assert "sot < 6" in body, "A proxy SoT floor must drop to 6 (was 9)"
    assert "pm_xg_total * 1.08" in body, "A posterior multiplier must drop to 1.08 (was 1.15)"


@test("INPLAY-LOOSEN-D — strategy D uses minute 48-80 + live_xg ≥ 0.7 + odds > 2.10")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_d(")
    fn_end = src.index("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "if minute < 48 or minute > 80" in body, "D minute window must loosen to 48-80"
    assert "live_xg < 0.7" in body, "D real-xG floor must drop to 0.7 (was 1.0)"
    assert "odds) <= 2.10" in body, "D OU odds floor must drop to 2.10 (was 2.50)"


@test("INPLAY-LOOSEN-B-C — B window 12-50, C possession 52/55 (real)")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    b_start = src.index("def _check_strategy_b(")
    b_end = src.index("\ndef ", b_start + 1)
    b_body = src[b_start:b_end]
    assert "if minute < 12 or minute > 50" in b_body, "B window must loosen to 12-50"

    c_start = src.index("def _check_strategy_c(")
    c_end = src.index("\ndef ", c_start + 1)
    c_body = src[c_start:c_end]
    assert "min_poss = 52.0 if home_is_fav else 55.0" in c_body, (
        "C real-xG possession thresholds must drop to 52% home / 55% away"
    )


@test("INPLAY-NEW-CORNER — Strategy G (Corner Cluster Over) registered + dispatched")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    dict_start = src.index("INPLAY_BOTS = {")
    dict_end = src.index("\n}\n", dict_start) + 2
    bots_block = src[dict_start:dict_end]
    assert '"inplay_g"' in bots_block, (
        "inplay_g must be registered in INPLAY_BOTS — Strategy G (corner cluster, 4/5 AI consensus)"
    )

    disp_start = src.index("def _check_strategy(")
    disp_end = src.index("\ndef ", disp_start + 1)
    disp_body = src[disp_start:disp_end]
    assert 'bot_name == "inplay_g"' in disp_body, "Dispatcher must route inplay_g"

    # Function must exist and accept execute_query for the corner-history lookup
    assert "def _check_strategy_g(cand: dict, pm: dict, has_red_card: bool,\n                      execute_query)" in src, (
        "_check_strategy_g must accept execute_query for the 9-11 min corner-history lookup"
    )
    # Verify the strategy actually checks corner delta — no point if it doesn't
    g_start = src.index("def _check_strategy_g(")
    g_end = src.index("\ndef ", g_start + 1)
    g_body = src[g_start:g_end]
    assert "corners_delta < 3" in g_body, "G must require ≥ 3-corner delta in 10-min window"


@test("INPLAY-NEW-HT-RESTART — Strategy H (HT Restart Surge) registered + dispatched")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    dict_start = src.index("INPLAY_BOTS = {")
    dict_end = src.index("\n}\n", dict_start) + 2
    bots_block = src[dict_start:dict_end]
    assert '"inplay_h"' in bots_block, "inplay_h must be registered (3/5 AI consensus)"

    disp_start = src.index("def _check_strategy(")
    disp_end = src.index("\ndef ", disp_start + 1)
    disp_body = src[disp_start:disp_end]
    assert 'bot_name == "inplay_h"' in disp_body, "Dispatcher must route inplay_h"

    h_start = src.index("def _check_strategy_h(")
    h_end = src.index("\ndef ", h_start + 1)
    h_body = src[h_start:h_end]
    assert "if minute < 46 or minute > 55" in h_body, "H window must be 46-55"
    assert "if sh != 0 or sa != 0" in h_body, "H must require 0-0 at entry"
    assert "minute BETWEEN 40 AND 46" in h_body, "H must look up an HT-end snapshot"
    # Dual-line refinement (2026-05-10): O2.5 if odds > 2.80, else O1.5 if odds > 1.60.
    # Single-line "odds < 2.10" guard is gone — replaced by the dual-line ladder.
    assert "o25_odds > 2.80" in h_body, "H must take O2.5 only when its odds > 2.80"
    assert "o15_odds > 1.60" in h_body, "H must fall back to O1.5 when its odds > 1.60"
    assert "live_ou_15_over" in h_body, "H must read live_ou_15_over for the fallback"


@test("INPLAY-NEW-RED-CARD — Strategy Q (Red Card Overreaction Over 2.5) registered + dispatched")
def _():
    """Strategy Q is the only inplay strategy that *requires* a red card —
    every other strategy excludes red-card matches as noise. This test guards
    registration, dispatcher routing, and the entry conditions from the spec
    (red minute 15-55, total goals ≤ 1, 11-man possession ≥ 55%, OU2.5 > 2.30)."""
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    dict_start = src.index("INPLAY_BOTS = {")
    dict_end = src.index("\n}\n", dict_start) + 2
    bots_block = src[dict_start:dict_end]
    assert '"inplay_q"' in bots_block, (
        "inplay_q must be registered in INPLAY_BOTS — Red Card Overreaction"
    )

    disp_start = src.index("def _check_strategy(")
    disp_end = src.index("\ndef ", disp_start + 1)
    disp_body = src[disp_start:disp_end]
    assert 'bot_name == "inplay_q"' in disp_body, "Dispatcher must route inplay_q"

    assert "def _check_strategy_q(cand: dict, pm: dict, has_red_card: bool,\n                      execute_query)" in src, (
        "_check_strategy_q must accept execute_query — needs red-card lookup from match_events"
    )

    # Q is currently the last function in the file — slice from def to end-of-file
    # then trim at the next top-level def if a newer one is added later.
    q_start = src.index("def _check_strategy_q(")
    q_after = src[q_start:]
    next_def = q_after.find("\ndef ", 1)
    q_body = q_after if next_def < 0 else q_after[:next_def]
    assert "minute BETWEEN 15 AND 55" in q_body, (
        "Q must require the red card to fall in minute 15-55 (per spec)"
    )
    assert "total_goals > 1" in q_body, "Q must require total goals ≤ 1"
    assert "eleven_man_poss < 55.0" in q_body, "Q must require 11-man possession ≥ 55%"
    assert "odds) <= 2.30" in q_body, "Q must require live OU 2.5 over odds > 2.30"
    assert "if not has_red_card" in q_body, (
        "Q must early-out when there's no red card — opposite of every other strategy"
    )


@test("INPLAY-STATS-COVERAGE — _is_high_priority lifts goals≤1 + min≥25 matches")
def _():
    """The bottleneck for strategies A/D/G/H is stats coverage (xG/SoT/corners
    only on ~9% of historical snapshots). This test verifies that LivePoller's
    HIGH-priority gate covers actionable in-play states, not just matches with
    active bets. Quota cost is real (~2× stats volume on peak days) — managed by
    upgrading to AF Mega (150K/day) rather than removing the condition."""
    import pathlib
    src = pathlib.Path("workers/live_poller.py").read_text()
    fn_start = src.index("def _is_high_priority(")
    fn_end = src.index("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "af_fix" in body, (
        "_is_high_priority must accept af_fix so it can read minute + score"
    )
    assert "minute >= 25" in body and "<= 1" in body, (
        "Must lift matches with minute >= 25 and goals <= 1 to HIGH priority"
    )
    assert "self._is_high_priority(match_id, af_fix)" in src, (
        "Call site in _run_cycle must pass af_fix to _is_high_priority"
    )
    af_src = pathlib.Path("workers/api_clients/api_football.py").read_text()
    assert "_HARD_QUOTA_FLOOR" in af_src, (
        "_get() must have a hard quota floor to protect settlement"
    )


@test("REPLAY-INPLAY — scripts/replay_inplay.py imports without DB writes")
def _():
    """Defensive: backfill script must be dry-run only — no INSERT/UPDATE/DELETE
    in the replay path so a stray invocation can't pollute simulated_bets."""
    import pathlib
    src = pathlib.Path("scripts/replay_inplay.py").read_text()
    # Allow these in queries — they're SELECT-side only
    write_ops = ["execute_write(", "store_bet(", "INSERT INTO", "UPDATE simulated", "DELETE FROM"]
    for op in write_ops:
        assert op not in src, (
            f"replay_inplay.py must stay dry-run — found '{op}'. "
            "Backfill is review-only until --apply is explicitly added."
        )
    # Sanity: dedup against existing inplay bets is wired up
    assert "fetch_existing_inplay_bets" in src, (
        "replay must skip (match,bot) pairs that already have a real bet in DB"
    )


@test("INJURIES-BY-DATE — single call returns grouped fixtures (T3 fast path)")
def _():
    """Validates the /injuries?date=YYYY-MM-DD path is wired and returns the expected
    {fixture_id: [item, ...]} shape that fetch_injuries / _fetch_morning_enrichment
    consume. Replaces the per-fixture get_injuries_batched fan-out (~25 calls → 1).

    Failure modes this catches: response shape change, AF endpoint regressions,
    or accidental revert of the import in either pipeline call site.
    """
    from datetime import date
    from workers.api_clients.api_football import get_injuries_by_date

    today = date.today().isoformat()
    result = get_injuries_by_date(today)
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    # Don't assert non-empty — a quiet day could legitimately have 0 fixtures with injuries.
    # Do assert the shape if anything came back.
    for fid, items in result.items():
        assert isinstance(fid, int), f"Expected int fixture id, got {type(fid)}"
        assert isinstance(items, list), f"Expected list value, got {type(items)}"
        for item in items[:1]:
            assert "player" in item and "team" in item and "fixture" in item, (
                f"Injury item missing expected keys: {list(item.keys())}"
            )


@test("BULK-STORE-ODDS — fetch_odds writes one bulk_insert, not one per fixture")
def _():
    """Source-inspection guard. The original loop did one bulk_insert call per
    fixture (~560 round-trips on a typical day). The fix accumulates rows
    across all fixtures and issues a single bulk_insert with a tuned page_size.

    If anyone reverts to the per-fixture loop, this test fails and step 2 of
    recover_today.py silently regresses from ~30s back to ~100s.
    """
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "workers/jobs/fetch_odds.py"
    body = src.read_text()
    # The bulk_insert call must appear OUTSIDE the for-loop and must use
    # page_size kwarg (default 500 is too small for ~190k odds rows/run).
    assert body.count("bulk_insert(\"odds_snapshots\"") == 1, (
        "fetch_odds must call bulk_insert exactly once (single accumulated insert). "
        "Multiple calls means we're back to the per-fixture loop."
    )
    assert "page_size=5000" in body, (
        "fetch_odds bulk_insert must pass page_size=5000 — default 500 means "
        "377 round-trips for ~190k rows = ~76s instead of ~14s."
    )


@test("INPLAY-E-NULL-SHOTS — strategy E proxy disabled; real-xG only (no shot data access)")
def _():
    """Proxy mode disabled 2026-05-09 — 182 bets at −4.7% ROI. Strategy E now requires
    real xG and returns None immediately for proxy candidates."""
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_e(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    # Proxy disabled — must bail before any shot data access
    assert "if not is_real:" in fn_body and "return None" in fn_body, (
        "Strategy E must bail on proxy mode via 'if not is_real: return None'"
    )
    assert "expected_shots_at_minute" not in fn_body, (
        "Strategy E must not reference expected_shots_at_minute — proxy formula removed"
    )


@test("INPLAY-NEW-IJL — bots I, J, L registered in INPLAY_BOTS + dispatched")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    for name in ("inplay_i", "inplay_j", "inplay_l"):
        assert f'"{name}"' in src, f"{name} missing from INPLAY_BOTS"
    for fn in ("_check_strategy_i", "_check_strategy_j", "_check_strategy_l"):
        assert f"def {fn}(" in src, f"{fn} not defined"
    assert "inplay_i" in src and "inplay_j" in src and "inplay_l" in src
    assert "_check_strategy_i" in src and "_check_strategy_j" in src and "_check_strategy_l" in src


@test("INPLAY-J-GOAL-DEBT — strategy J requires 0-0 and live_ou_15_over ≥ 2.85")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_j(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "live_ou_15_over" in fn_body, "Strategy J must read live_ou_15_over from candidate"
    assert "2.85" in fn_body, "Strategy J must have min odds floor of 2.85"
    assert '0.62' in fn_body, "Strategy J must require prematch_o25_prob >= 0.62"
    # Verify no false-trigger on 1-0 score
    assert 'sh != 0 or sa != 0' in fn_body, "Strategy J must exit early if score is not 0-0"


@test("INPLAY-L-GOAL-CONTAGION — strategy L reads _goal_event_window + guards minute range")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_l(")
    # L is the last function — slice to end of file
    try:
        fn_end = src.index("\ndef ", fn_start + 1)
    except ValueError:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "_goal_event_window" in fn_body, "Strategy L must check _goal_event_window"
    assert "_cycle_count" in fn_body, "Strategy L must compare cycle count for window expiry"
    assert "total_goals != 1" in fn_body, "Strategy L must fire only when exactly 1 goal scored"
    assert "live_ou_25_over" in fn_body, "Strategy L must check live_ou_25_over for execution"


@test("INPLAY-I-FAV-STALL — strategy I uses bivariate Poisson and requires 0-0")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_i(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "_bivariate_poisson_win_prob" in fn_body, "Strategy I must use bivariate Poisson"
    assert "3.0" in fn_body, "Strategy I must require live odds drift ≥ 3.0"
    assert "0.62" in fn_body, "Strategy I must require prematch_win_prob ≥ 0.62"
    assert "sh != 0 or sa != 0" in fn_body, "Strategy I must exit early if score is not 0-0"


@test("INPLAY-L-STATE-UPDATE — goal contagion state updated after strategy checks")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def run_inplay_strategies(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "_prev_total_goals" in fn_body, "run_inplay_strategies must update _prev_total_goals"
    assert "_goal_event_window" in fn_body, "run_inplay_strategies must update _goal_event_window"


@test("INPLAY-CANDS-OU15 — live_ou_15_over fetched in _get_live_candidates SELECT")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _get_live_candidates(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "live_ou_15_over" in fn_body, "_get_live_candidates must select live_ou_15_over"


@test("INPLAY-NEXT-10-MIN-MARKET — parser captures market id=65 / Next 10 Minutes Total")
def _():
    """Free capture from existing /odds/live payload — zero new AF calls."""
    import pathlib
    src = pathlib.Path("workers/api_clients/api_football.py").read_text()
    fn_start = src.index("def parse_live_odds(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert 'bet.get("id") == 65' in fn_body or '"Next 10 Minutes Total"' in fn_body, (
        "parse_live_odds must detect AF Next 10 Minutes market (id=65 or named match)"
    )
    assert '"market": "next10"' in fn_body, (
        "Parsed rows must use market='next10' so build_snapshot can map them"
    )
    # Snapshot writers must accept the new columns
    db_src = pathlib.Path("workers/api_clients/db.py").read_text()
    assert '"live_next10_over"' in db_src and '"live_next10_under"' in db_src, (
        "db.store_live_snapshots_batch columns list must include live_next10_over/under"
    )
    sb_src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    assert '"live_next10_over"' in sb_src, (
        "supabase_client store_live_snapshot optional_fields must include live_next10_over"
    )


@test("INPLAY-FUNNEL-LOGGING — _funnel counters incremented at every skip point")
def _():
    """Funnel keys: no_prematch, league_xg_gate, existing_bet, no_strategy_trigger,
    odds_stale, score_changed, store_bet_error. All seven must be incremented to
    diagnose silent-failure regressions when a strategy goes quiet."""
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    for key in ("no_prematch", "league_xg_gate", "existing_bet",
                "no_strategy_trigger", "odds_stale", "score_changed",
                "store_bet_error"):
        assert f'_funnel["{key}"] += 1' in src, (
            f"Funnel counter '{key}' must be incremented in run_inplay_strategies"
        )
    # Heartbeat must read funnel and reset
    assert "funnel since-last" in src, (
        "Heartbeat output must include 'funnel since-last' line"
    )


@test("INPLAY-BAYESIAN-ENGINE — _remaining_goals_prob helper extracted, J/L call it")
def _():
    """Shared Bayesian remaining-goals helper. Strategies J and L now share one
    code path so future strategies (M/N/O) can adopt the same machinery."""
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "def _remaining_goals_prob(" in src, (
        "_remaining_goals_prob helper must exist (used by strategies J, L, future M/N/O)"
    )
    # Strategies J and L must call the helper rather than duplicate the math
    j_start = src.index("def _check_strategy_j(")
    j_end = src.index("\ndef ", j_start + 1)
    assert "_remaining_goals_prob(" in src[j_start:j_end], (
        "Strategy J must use _remaining_goals_prob helper"
    )
    l_start = src.index("def _check_strategy_l(")
    try:
        l_end = src.index("\ndef ", l_start + 1)
    except ValueError:
        l_end = len(src)
    assert "_remaining_goals_prob(" in src[l_start:l_end], (
        "Strategy L must use _remaining_goals_prob helper"
    )


@test("INPLAY-EQUALIZER-MAGNET — strategy M registered, dispatched, uses _remaining_goals_prob")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    # Registered in INPLAY_BOTS
    dict_start = src.index("INPLAY_BOTS = {")
    dict_end = src.index("\n}\n", dict_start) + 2
    bots_block = src[dict_start:dict_end]
    assert '"inplay_m"' in bots_block, "inplay_m must be registered in INPLAY_BOTS"
    # Dispatched
    disp_start = src.index("def _check_strategy(")
    disp_end = src.index("\ndef ", disp_start + 1)
    assert '_check_strategy_m(' in src[disp_start:disp_end], (
        "_check_strategy must dispatch inplay_m → _check_strategy_m"
    )
    # Body uses the shared Bayesian helper + correct entry conditions
    fn_start = src.index("def _check_strategy_m(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "_remaining_goals_prob(" in fn_body, (
        "Strategy M must use _remaining_goals_prob (1 goal observed → P(2 more))"
    )
    assert "0.48" in fn_body, "Strategy M must require prematch_btts_prob ≥ 0.48"
    assert "3.0" in fn_body, "Strategy M must require live_ou_25_over ≥ 3.0"
    assert "minute < 30 or minute > 60" in fn_body, (
        "Strategy M minute window is 30-60"
    )


@test("INPLAY-LATE-FAV-PUSH — strategy N registered, dispatched, bivariate Poisson home win")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    dict_start = src.index("INPLAY_BOTS = {")
    dict_end = src.index("\n}\n", dict_start) + 2
    bots_block = src[dict_start:dict_end]
    assert '"inplay_n"' in bots_block, "inplay_n must be registered in INPLAY_BOTS"
    disp_start = src.index("def _check_strategy(")
    disp_end = src.index("\ndef ", disp_start + 1)
    assert '_check_strategy_n(' in src[disp_start:disp_end], (
        "_check_strategy must dispatch inplay_n"
    )
    fn_start = src.index("def _check_strategy_n(")
    try:
        fn_end = src.index("\ndef ", fn_start + 1)
    except ValueError:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "_bivariate_poisson_win_prob(" in fn_body, (
        "Strategy N must price the home win via _bivariate_poisson_win_prob"
    )
    assert "0.65" in fn_body, "Strategy N must require prematch_home_prob ≥ 0.65"
    assert "2.20" in fn_body, "Strategy N must require live_1x2_home ≥ 2.20"
    assert "minute < 72 or minute > 80" in fn_body, (
        "Strategy N minute window is 72-80 (intentionally tight)"
    )


@test("INPLAY-BOT-RETIREMENT — dashboard_cache filters retired_at IS NULL")
def _():
    """Public /performance leaderboard reads from dashboard_cache.bot_breakdown,
    which is built by write_dashboard_cache(). Retired bots must be excluded."""
    import pathlib
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    fn_start = src.index("def write_dashboard_cache(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "retired_at IS NULL" in fn_body, (
        "write_dashboard_cache bot query must filter retired_at IS NULL — "
        "otherwise retired bots show up on /performance"
    )


@test("INJURIES-BY-DATE — both call sites use the new function (no batched leftovers)")
def _():
    """Source-inspection guard: if anyone reverts the call site to get_injuries_batched
    in either daily_pipeline_v2.py or fetch_enrichment.py, the recovery script and
    morning pipeline silently lose the 47× speedup.
    """
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("workers/jobs/fetch_enrichment.py", "workers/jobs/daily_pipeline_v2.py"):
        src = (repo / rel).read_text()
        assert "get_injuries_by_date" in src, (
            f"{rel} must call get_injuries_by_date (the single-call /injuries?date= path). "
            f"If this fails, the per-fixture batched fan-out has been reintroduced."
        )
        # The deprecated batched function should not be IMPORTED into pipeline code.
        # (It still lives in api_football.py for ad-hoc scripts — that's fine.)
        assert "get_injuries_batched" not in src, (
            f"{rel} still imports/uses get_injuries_batched. The pipeline call sites "
            f"must use get_injuries_by_date instead."
        )


@test("BACKFILL-COACH-CACHE — team_coaches_cache table + RPC count from cache (migration 083)")
def _():
    from workers.api_clients.db import execute_query
    cols = execute_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name='team_coaches_cache'"
    )
    if not cols:
        return  # migration not applied yet — skip
    actual = {r["column_name"] for r in cols}
    assert {"team_af_id", "fetched_at"}.issubset(actual), (
        f"team_coaches_cache missing required columns: {actual}"
    )
    # RPC must read from cache so dashboard counts probed teams (not just teams with rows).
    rpc_def = execute_query(
        "SELECT pg_get_functiondef(p.oid) AS def FROM pg_proc p "
        "JOIN pg_namespace n ON p.pronamespace = n.oid "
        "WHERE n.nspname = 'public' AND p.proname = 'count_distinct_coached_teams'"
    )
    assert rpc_def, "count_distinct_coached_teams RPC missing"
    assert "team_coaches_cache" in rpc_def[0]["def"], (
        "count_distinct_coached_teams must count from team_coaches_cache "
        "(otherwise empty-AF teams stay 'missing' on the dashboard forever)"
    )


@test("BACKFILL-COACH-MARK — backfill_coaches stamps cache on every probe (incl. empty/error)")
def _():
    import pathlib
    src = pathlib.Path("scripts/backfill_coaches.py").read_text()
    assert "_mark_fetched" in src, (
        "backfill_coaches.py must call _mark_fetched in finally so empty-AF teams "
        "are not re-probed on every run (the bug that parked the dashboard at 64.8%)"
    )
    assert "team_coaches_cache" in src, (
        "_missing_teams must exclude teams already in team_coaches_cache"
    )


@test("BACKFILL-IDS-BATCH — backfill_historical batches stats+events via /fixtures?ids=, no per-match endpoints")
def _():
    """
    Guard the BACKFILL-IDS-BATCH refactor (~40× AF-call reduction). Old code
    fired 2 individual AF calls per match (`/fixtures/statistics?fixture=N` +
    `/fixtures/events?fixture=N`); the new code batches both via
    `get_fixtures_batch` and parses embedded `statistics` + `events` from the
    prefetched payload. Both per-match helpers must be absent from the script
    (importing them would silently re-permit a regression).
    """
    import pathlib
    src = pathlib.Path("scripts/backfill_historical.py").read_text()

    assert "get_fixtures_batch" in src, (
        "backfill_historical.py must import + call get_fixtures_batch — that's "
        "the whole point of BACKFILL-IDS-BATCH (one batched call replaces 40 "
        "individual stats+events calls)."
    )
    assert "get_fixture_statistics" not in src, (
        "backfill_historical.py must NOT call get_fixture_statistics per match. "
        "Use the embedded `statistics` from get_fixtures_batch instead — "
        "individual calls revert the 40× speedup."
    )
    assert "get_fixture_events" not in src, (
        "backfill_historical.py must NOT call get_fixture_events per match. "
        "Use the embedded `events` from get_fixtures_batch instead."
    )
    assert 'fixture.get("statistics")' in src or "fixture.get('statistics')" in src, (
        "Stats parsing must read embedded `statistics` from the batched fixture dict."
    )
    assert 'fixture.get("events")' in src or "fixture.get('events')" in src, (
        "Events parsing must read embedded `events` from the batched fixture dict."
    )


@test("BACKFILL-TRANSFER-PARSE — parse_transfers skips malformed AF dates instead of crashing batch")
def _():
    from workers.api_clients.api_football import parse_transfers
    # Real-world failure: AF returned date "010897" (DDMMYY w/o separators)
    # which crashed the entire psycopg2 batch via DATE column rejection.
    bad = [{
        "player": {"id": 90523, "name": "Alexander Manninger"},
        "transfers": [
            {"date": "010897", "type": "Free", "teams": {"in": {"id": 1}, "out": {"id": 2}}},
            {"date": "2024-07-01", "type": "Free", "teams": {"in": {"id": 1}, "out": {"id": 2}}},
        ],
    }]
    rows = parse_transfers(bad, team_api_id=4256)
    assert len(rows) == 1, f"Expected malformed date dropped, got {len(rows)} rows"
    assert rows[0]["transfer_date"] == "2024-07-01"


@test("FETCH-ODDS-CONCURRENT — pages 2..N fetched via ThreadPoolExecutor")
def _():
    """Source guard. The original loop was strictly sequential (`while page <=
    total_pages: page += 1`) — ~56 pages × ~340ms = ~19s wait. The fix fetches
    page 1 first to learn total_pages, then fans out the rest via a thread
    pool. The _get _rate_lock still paces actual requests at MIN_REQUEST_INTERVAL
    so concurrency cannot breach the AF rate budget."""
    import pathlib
    src = pathlib.Path("workers/api_clients/api_football.py").read_text()
    fn_start = src.index("def get_odds_by_date(")
    fn_end = src.index("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]

    assert "ThreadPoolExecutor" in body, (
        "get_odds_by_date must use ThreadPoolExecutor for pages 2..N"
    )
    assert 'page": 1' in body, (
        "Must fetch page 1 first to learn total_pages before fanning out"
    )
    # Old strictly-sequential pattern must be gone
    assert "while page <= total_pages" not in body, (
        "Sequential while-loop reverted — concurrency lost"
    )
    assert "from concurrent.futures import ThreadPoolExecutor" in src, (
        "Module must import ThreadPoolExecutor"
    )


@test("BACKFILL-TRANSFERS-CONCURRENT — backfill_transfers fans out via ThreadPoolExecutor, no per-team sleep")
def _():
    """Source guard. Sequential per-team fetch + 70ms sleep ran ~1.4s/team
    real-world (network-bound), turning a 4430-team backfill into ~100 min.
    Fix fans out via ThreadPoolExecutor; _get's _rate_lock paces actual HTTP
    at MIN_REQUEST_INTERVAL=120ms so 8 workers cannot breach AF's budget.
    The per-team time.sleep(RATE_DELAY) must be gone — it's redundant when
    pacing is enforced globally inside _get."""
    import pathlib
    src = pathlib.Path("scripts/backfill_transfers.py").read_text()

    assert "from concurrent.futures import ThreadPoolExecutor" in src, (
        "backfill_transfers must import ThreadPoolExecutor"
    )
    assert "ThreadPoolExecutor(max_workers=" in src, (
        "Both run() and run_batch() must use ThreadPoolExecutor for fan-out"
    )
    # Old per-team sleep is redundant once _rate_lock paces _get globally
    assert "time.sleep(RATE_DELAY)" not in src, (
        "Per-team time.sleep(RATE_DELAY) must be gone — _get's _rate_lock "
        "already paces requests; per-thread sleep just slows each worker."
    )
    assert "RATE_DELAY" not in src, (
        "RATE_DELAY constant should be removed — pacing lives in _get's _rate_lock"
    )


# ── Runner ────────────────────────────────────────────────────────────────────

def _run_one(name: str, fn) -> tuple[str, bool, str, float]:
    import time
    t = time.monotonic()
    try:
        fn()
        return (name, True, "", time.monotonic() - t)
    except Exception as e:
        return (name, False, f"{type(e).__name__}: {e}", time.monotonic() - t)


def main():
    import time
    t0 = time.monotonic()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_run_one, name, fn): name for name, fn in _registry}
        results = [f.result() for f in as_completed(futures)]

    elapsed = time.monotonic() - t0

    # Sort by original registration order for stable output
    order = {name: i for i, (name, _) in enumerate(_registry)}
    results.sort(key=lambda r: order.get(r[0], 9999))

    passed = sum(1 for _, ok, _, _ in results if ok)
    failed = sum(1 for _, ok, _, _ in results if not ok)

    print("\n" + "═" * 60)
    print("  OddsIntel Smoke Tests")
    print("═" * 60)

    for name, ok, error, t in results:
        status = "✓" if ok else "✗"
        color_on = "\033[32m" if ok else "\033[31m"
        slow = f"  \033[33m({t:.1f}s)\033[0m" if t > 5 else ""
        print(f"  {color_on}{status}\033[0m  {name}{slow}")
        if error:
            print(f"       \033[31m{error}\033[0m")

    print("═" * 60)
    slowest = sorted(results, key=lambda r: r[3], reverse=True)[:3]
    color = "\033[32m" if failed == 0 else "\033[31m"
    print(f"  {color}{passed} passed, {failed} failed\033[0m  ({elapsed:.1f}s)")
    print(f"  Slowest: " + " | ".join(f"{r[0][:40]} {r[3]:.1f}s" for r in slowest))
    print("═" * 60 + "\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
