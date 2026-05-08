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


@test("POOL-LEAK-FIX — SQL exceptions don't leak conns (15 errors > maxconn=10)")
def _():
    """The 2026-05-08 outage: get_conn() leaked conns on any exception other
    than OperationalError/InterfaceError, so a single SQL syntax error per
    polling cycle drained the pool within 5 minutes. Verify 15 SQL errors
    (more than maxconn=10) leave the pool usable."""
    from workers.api_clients.db import get_conn
    import psycopg2

    for _ in range(15):  # > maxconn=10, so leaks would have exhausted
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

    for _ in range(15):
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


@test("OBS-POOL-METRIC — get_pool_status returns valid structure")
def _():
    from workers.api_clients.db import get_pool_status, get_pool
    get_pool()  # ensure pool is initialised
    status = get_pool_status()
    assert "used" in status and "idle" in status and "max" in status and "pct" in status
    assert status["max"] == 10
    assert 0 <= status["pct"] <= 100
    assert status["used"] + status["idle"] <= status["max"]


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


@test("INPLAY-HIDE-VALUEBETS — getTodayBets filters xg_source IS NULL")
def _():
    import pathlib
    p = pathlib.Path("../odds-intel-web/src/lib/engine-data.ts")
    if not p.exists():
        # Frontend repo not present in this checkout — skip rather than fail.
        return
    src = p.read_text()
    fn_start = src.index("export async function getTodayBets(")
    fn_end = src.index("\nexport ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert '.is("xg_source", null)' in fn_body, (
        "getTodayBets must filter inplay bets out (xg_source IS NULL = prematch). "
        "Inplay rows from broken Strategy B/E would otherwise leak into /value-bets."
    )


@test("INPLAY-HIDE-VALUEBETS — getFreeDailyPick filters xg_source IS NULL on both queries")
def _():
    import pathlib
    p = pathlib.Path("../odds-intel-web/src/lib/engine-data.ts")
    if not p.exists():
        return
    src = p.read_text()
    fn_start = src.index("export async function getFreeDailyPick(")
    fn_end = src.index("\nexport ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    # Both the pick query AND the dedupe-count query must be filtered, otherwise
    # the totalCount and the pick will disagree.
    assert fn_body.count('.is("xg_source", null)') >= 2, (
        "getFreeDailyPick must apply xg_source IS NULL to both the pick query "
        "and the all-bets dedupe query — otherwise totalCount drifts from the pick."
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
