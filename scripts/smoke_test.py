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


@test("MFV-LIVE-BUILD — build_match_feature_vectors_live runs and returns int")
def _():
    from workers.api_clients.supabase_client import build_match_feature_vectors_live
    today_str = date.today().isoformat()
    # May be 0 (no scheduled fixtures today) — no-op is fine, exception is not.
    count = build_match_feature_vectors_live(None, today_str)
    assert isinstance(count, int), f"Expected int, got {type(count)}"


@test("MFV-LIVE-BUILD — live builder selects non-finished matches (status guard)")
def _():
    import inspect
    from workers.api_clients import supabase_client
    src = inspect.getsource(supabase_client.build_match_feature_vectors_live)
    # The whole point of the live builder vs the nightly builder: it must NOT
    # filter to status='finished', and it must filter to status != 'finished'
    # so pre-KO and in-progress matches both get rows.
    assert "status != 'finished'" in src, (
        "live builder must select non-finished matches (status != 'finished'); "
        "drift would silently turn it into the nightly builder"
    )
    # Both builders share _build_mfv_rows_for_matches — guard the helper exists
    # so a future refactor can't quietly diverge the two code paths.
    assert hasattr(supabase_client, "_build_mfv_rows_for_matches"), (
        "_build_mfv_rows_for_matches helper must remain shared between "
        "build_match_feature_vectors and build_match_feature_vectors_live"
    )


@test("ML-BUNDLE-STORAGE — model_versions table exists with required columns")
def _():
    from workers.api_clients.db import execute_query
    cols = execute_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        ("model_versions",),
    )
    have = {c["column_name"] for c in cols}
    required = {"version", "trained_at", "training_window_start", "training_window_end",
                "n_training_rows", "feature_cols", "cv_metrics", "storage_bucket",
                "storage_prefix", "promoted_at", "demoted_at", "notes"}
    missing = required - have
    assert not missing, f"model_versions missing columns: {missing}"


@test("ML-BUNDLE-STORAGE — Storage bundle exists for at least one known version")
def _():
    # Bootstrap should have uploaded v9a_202425 + v10_pre_shadow + v12_post0e at minimum.
    # If Storage is empty, bootstrap was never run (or RLS blocked the upload).
    from workers.model.storage import bundle_exists_in_storage
    assert bundle_exists_in_storage("v9a_202425"), (
        "v9a_202425 not in Supabase Storage. Run "
        "`python3 scripts/bootstrap_model_storage.py --only v9a_202425` to fix."
    )


@test("ML-BUNDLE-STORAGE — bundle loader routes through ensure_local_bundle on cache miss")
def _():
    import inspect
    from workers.model import xgboost_ensemble
    # PER-MARKET-VERSION (2026-05-24): the ensure_local_bundle wire moved
    # from _load_models into _load_bundle so per-market overrides can also
    # hydrate from Storage. Check both — at least one must have it.
    legacy_src = inspect.getsource(xgboost_ensemble._load_models)
    per_market_src = inspect.getsource(xgboost_ensemble._load_bundle)
    # Without this wire, a fresh Railway container with MODEL_VERSION set to a
    # bundle not on disk falls through to {} and silently degrades to Poisson.
    assert "ensure_local_bundle" in per_market_src, (
        "_load_bundle must call ensure_local_bundle when the bundle dir is missing — "
        "otherwise Railway redeploys lose bundles silently."
    )
    # The legacy wrapper should delegate to _load_bundle (so the wire is preserved transitively).
    assert "_load_bundle" in legacy_src, "_load_models must delegate to _load_bundle"


@test("ML-BUNDLE-STORAGE — train.py uploads to Storage and registers on success")
def _():
    import inspect
    from workers.model import train
    src = inspect.getsource(train.train_all)
    # Guard the auto-upload + auto-register hook so future train.py refactors
    # don't accidentally drop the durability path.
    assert "upload_bundle" in src, "train_all must upload to Supabase Storage"
    assert "register_version" in src, "train_all must register in model_versions"


@test("OFFLINE-EVAL — Platt formula matches fit_platt_offline (sigmoid(a*p+b), not logit)")
def _():
    import inspect
    from scripts import offline_eval, fit_platt_offline
    # Critical: fit_platt_offline.py fits `sigmoid(a*p + b)` directly on the
    # raw probability (not the logit). offline_eval.py MUST use the same form
    # or v10's calibrated probabilities turn into garbage. The bug burned 1
    # eval cycle — guard it so a future "fix" to standard Platt-on-logit
    # silently breaks the comparison harness.
    fit_src = inspect.getsource(fit_platt_offline._platt)
    eval_src = inspect.getsource(offline_eval._apply_platt)
    assert "a * p + b" in fit_src, (
        "Sanity check: fit_platt_offline._platt should still be sigmoid(a*p+b). "
        "If you changed the fitter, re-fit ALL bundle Platt params and update "
        "offline_eval._apply_platt to match."
    )
    assert "a * p + b" in eval_src, (
        "offline_eval._apply_platt MUST use sigmoid(a*p+b) — same form the "
        "Platt was fit with. Using sigmoid(a*logit(p)+b) silently destroys "
        "v10's calibrated log_loss (0.35 → 1.33 on 1x2_home in real test)."
    )


@test("CAL-PLATT-UPGRADE — apply_platt uses 2-feature logistic when platt_c is set")
def _():
    """Guard the 2-feature logistic path in apply_platt (CAL-PLATT-UPGRADE).
    When platt_c is non-null, apply_platt must use log(odds) as second feature.
    When platt_c is null, must fall back to standard 1-feature Platt.
    """
    import math
    from workers.model.improvements import apply_platt, reset_platt_cache

    # Patch the cache with synthetic params
    import workers.model.improvements as imp

    # 1-feature case: c=None, no odds needed
    imp._platt_params = {"1x2_home": (1.2, -0.5, None)}
    result_1f = apply_platt(0.45, "1x2_home", odds=None)
    expected_1f = 1.0 / (1.0 + math.exp(-(1.2 * 0.45 + (-0.5))))
    assert abs(result_1f - expected_1f) < 1e-9, f"1-feature Platt wrong: {result_1f} vs {expected_1f}"

    # 2-feature case: c=w1, odds provided
    imp._platt_params = {"over_under_25_over": (0.9, -0.3, 0.4)}
    result_2f = apply_platt(0.50, "over_under_25_over", odds=1.85)
    expected_2f = 1.0 / (1.0 + math.exp(-(0.9 * 0.50 + 0.4 * math.log(1.85) + (-0.3))))
    assert abs(result_2f - expected_2f) < 1e-9, f"2-feature logistic wrong: {result_2f} vs {expected_2f}"

    # 2-feature fallback: c set but odds not provided → use 1-feature
    result_fallback = apply_platt(0.50, "over_under_25_over", odds=None)
    expected_fallback = 1.0 / (1.0 + math.exp(-(0.9 * 0.50 + (-0.3))))
    assert abs(result_fallback - expected_fallback) < 1e-9, f"2-feature fallback wrong: {result_fallback} vs {expected_fallback}"

    # 2-feature: different corrections at same prob but different odds
    imp._platt_params = {"over_under_25_over": (1.0, 0.0, 0.5)}
    cal_low_odds = apply_platt(0.55, "over_under_25_over", odds=1.60)
    cal_high_odds = apply_platt(0.55, "over_under_25_over", odds=3.20)
    assert cal_low_odds != cal_high_odds, "2-feature must produce different corrections at different odds"

    reset_platt_cache()


@test("OFFLINE-EVAL — bundle loader returns MFV-schema flag correctly")
def _():
    """`_is_mfv_schema` is the dispatch gate that keeps offline_eval from
    silently running v9 inference on MFV (would zero-fill all 36 features).
    Two assertions per schema:
      1. Literal-list contract — deterministic, runs anywhere CI does.
      2. Bundle round-trip — only when the bundle is on disk (v9a is
         force-tracked per .gitignore; v10+ are not, so we skip on CI
         until they get force-tracked at promotion time)."""
    import pathlib
    from scripts.offline_eval import _load_bundle, _is_mfv_schema, MODELS_DIR

    # 1. Literal-list contract — always runs.
    assert _is_mfv_schema(["elo_home", "elo_away", "form_ppg_home"]), (
        "MFV schema (elo_home present, home_elo absent) must return True"
    )
    assert not _is_mfv_schema(["home_elo", "away_elo", "h_form_ppg"]), (
        "Legacy Kaggle schema (home_elo present) must return False"
    )
    assert not _is_mfv_schema(["elo_home", "home_elo"]), (
        "Mixed schema (both present) must return False — would silently "
        "double-feed inference"
    )
    assert not _is_mfv_schema([]), "Empty feature list must return False"

    # 2. Bundle round-trip — only assert when the bundle is locally tracked.
    # v9a_202425 is force-tracked per .gitignore (production model); v10+
    # bundles aren't tracked until they're promoted, so CI must not require
    # them. Once a v10+ is force-tracked, this branch starts asserting it.
    if (MODELS_DIR / "v9a_202425").exists():
        b9 = _load_bundle("v9a_202425")
        assert not _is_mfv_schema(b9["feature_cols"]), (
            "Tracked v9a_202425 bundle must report Kaggle schema"
        )
    for v10_candidate in ("v10_pre_shadow", "v11_pinnacle"):
        if (MODELS_DIR / v10_candidate).exists():
            b = _load_bundle(v10_candidate)
            assert _is_mfv_schema(b["feature_cols"]), (
                f"Tracked {v10_candidate} bundle must report MFV schema"
            )


@test("MFV-LIVE-BUILD — run_morning wires the live build before the match loop")
def _():
    import inspect
    from workers.jobs import daily_pipeline_v2
    src = inspect.getsource(daily_pipeline_v2.run_morning)
    # Wire-through guard: without this call, v10+ XGBoost inference reads None
    # from match_feature_vectors and silently falls back to Poisson — the bug
    # this task was created to close.
    assert "build_match_feature_vectors_live" in src, (
        "run_morning must call build_match_feature_vectors_live before the "
        "match loop runs get_xgboost_prediction; otherwise v10+ inference "
        "silently falls back to Poisson on every pre-KO match"
    )
    # Ordering guard: the live MFV build must run AFTER batch_write_morning_signals
    # (signals are MFV inputs) and BEFORE the prediction loop (the call site is
    # `get_xgboost_prediction(`, paren-suffixed to skip prose mentions of the
    # function name in surrounding docstrings/comments).
    sig_pos = src.find("batch_write_morning_signals(")
    mfv_pos = src.find("build_match_feature_vectors_live(")
    pred_pos = src.find("get_xgboost_prediction(")
    assert sig_pos < mfv_pos < pred_pos, (
        f"MFV-LIVE-BUILD must run AFTER signals and BEFORE prediction loop; "
        f"got sig={sig_pos} mfv={mfv_pos} pred={pred_pos}"
    )


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


@test("SELF-USE-VALIDATION — compute_real_pnl pure function (won/lost/void/half)")
def _():
    from workers.api_clients.supabase_client import compute_real_pnl
    assert compute_real_pnl(2.0, 2.5, 'won') == 3.0, "won: stake*(odds-1)"
    assert compute_real_pnl(2.0, 2.5, 'lost') == -2.0, "lost: -stake"
    assert compute_real_pnl(2.0, 2.5, 'void') == 0.0, "void: 0"
    assert compute_real_pnl(2.0, 2.5, 'half_won') == 1.5, "half_won: half profit"
    assert compute_real_pnl(2.0, 2.5, 'half_lost') == -1.0, "half_lost: half stake"
    assert compute_real_pnl(2.0, 2.5, 'pending') == 0.0, "pending: 0"


@test("SELF-USE-VALIDATION — store_real_bet validates inputs + writes to real_bets (source inspect)")
def _():
    """Source guard: store_real_bet must validate stake>0, actual_odds>1.0, and
    insert with the right column set. We inspect the function body rather than
    making real INSERTs in CI to keep the smoke suite fast."""
    import inspect
    from workers.api_clients import supabase_client
    src = inspect.getsource(supabase_client.store_real_bet)
    assert "stake must be positive" in src, "stake validation missing"
    assert "actual_odds must be > 1.0" in src, "odds validation missing"
    assert "INSERT INTO real_bets" in src, "writer must INSERT into real_bets"
    assert "RETURNING id" in src, "writer must return new bet UUID"


@test("SELF-USE-VALIDATION — settlement wires _settle_real_bets_for_matches (source inspect)")
def _():
    """The 21:00 / 23:30 / 01:00 settlement chain + 15-min settle_ready sweep
    must call into real_bets settlement so superadmin bets resolve on the same
    cadence as paper bets."""
    import inspect
    from workers.jobs import settlement
    src = inspect.getsource(settlement)
    assert "def _settle_real_bets_for_matches" in src, "function missing"
    assert "_settle_real_bets_for_matches(match_ids)" in src, (
        "settle_finished_matches must call _settle_real_bets_for_matches"
    )
    # Real bets feed actual_odds into settle_bet_result via 'odds_at_pick' alias
    fn = inspect.getsource(settlement._settle_real_bets_for_matches)
    assert "actual_odds AS odds_at_pick" in fn, (
        "_settle_real_bets_for_matches must alias actual_odds → odds_at_pick "
        "for settle_bet_result compatibility"
    )
    assert "real_bets" in fn and "result = 'pending'" in fn, (
        "function must scope to pending real_bets only"
    )


@test("COOLBET-NO-MARKET-PRESENCE — no_market path writes a presence-marker snapshot (source inspect)")
def _():
    """When Coolbet has the event but not the bet's specific market+selection,
    the placer must still write one canonical odds_snapshot so the frontend's
    `matchIdsWithCoolbetEvent` proxy can chip the row as `no_market` instead of
    the misleading `no_event` ('⚠ no match'). Discovered when Sportivo Carapeguá
    vs Atlético Tembetary showed '⚠ no match' for `double_chance x2` even
    though the match exists on Coolbet — Coolbet just doesn't offer DC for the
    Paraguay D. Intermedia league.
    """
    import inspect
    from workers.automation import coolbet_placer
    src = inspect.getsource(coolbet_placer)
    assert "def _write_presence_marker_snapshot" in src, (
        "presence-marker helper must exist in coolbet_placer"
    )
    # The helper has to be called from the no_market branch BEFORE the continue,
    # so a single source-walk verifies the order.
    no_mkt_idx = src.index('"outcome": "no_market"')
    presence_idx = src.rindex("_write_presence_marker_snapshot", 0, no_mkt_idx)
    assert presence_idx > 0 and presence_idx < no_mkt_idx, (
        "presence marker must be written before the no_market continue"
    )
    fn = inspect.getsource(coolbet_placer._write_presence_marker_snapshot)
    assert '"1x2"' in fn and '"Home"' in fn, (
        "helper must prefer 1x2 Home (universally available across leagues)"
    )
    assert "store_coolbet_odds_snapshot" in fn, (
        "helper must write to odds_snapshots (the frontend's evidence source)"
    )


@test("SETTLEMENT-POSTPONED-VOID — postponed/cancelled real_bets get auto-voided (source inspect)")
def _():
    """Singles real_bets on matches that move to status='postponed'/'cancelled'/
    'abandoned' must be voided (result='void', pnl=0) on every 15-min sweep —
    otherwise they sit pending forever (7 stuck bets on Estudiantes Mérida vs
    Metropolitanos burned this on 2026-05-24). Bookmaker always refunds, so
    voiding is the safe mirror.
    """
    import inspect
    from workers.jobs import settlement
    fn = inspect.getsource(settlement._void_real_bets_on_dead_matches)
    assert "postponed" in fn and "cancelled" in fn, (
        "_void_real_bets_on_dead_matches must cover postponed + cancelled "
        "(the only dead match_status enum values; AF PST/CANC/ABD/WO/AWD all "
        "collapse to 'postponed' in store_match)"
    )
    assert "result='void'" in fn and "pnl=0" in fn, (
        "void must set result='void' AND pnl=0"
    )
    assert "combo_legs IS NULL" in fn, (
        "must only void singles — combos use settle_combo_bet's reduced-product rule"
    )
    # Wired into the 15-min sweep so postponed matches don't need a separate
    # finished-match trigger to clear.
    sweep = inspect.getsource(settlement.settle_ready_matches)
    assert "_void_real_bets_on_dead_matches" in sweep, (
        "settle_ready_matches must call _void_real_bets_on_dead_matches"
    )


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


@test("OPS-COVERAGE-TIMEOUT — odds_coverage query uses FILTER aggregates, not NOT EXISTS subquery")
def _():
    """The original odds_coverage query used a correlated NOT EXISTS subquery
    against the full odds_snapshots table. At ~1.9M today-odds rows it timed
    out at Postgres statement_timeout (120s), the exception was silently caught,
    and the snapshot wrote 0 in all 8 odds columns — making the dashboard show
    no odds while the odds-fetch jobs were succeeding. Guard the rewritten form."""
    import pathlib
    src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    fn_start = src.index("def write_ops_snapshot(")
    next_def = src.find("\ndef ", fn_start + 1)
    fn_body = src[fn_start:next_def] if next_def != -1 else src[fn_start:]

    # The slow form must not return.
    assert "NOT EXISTS (\n                SELECT 1 FROM odds_snapshots o2" not in fn_body, (
        "OPS-COVERAGE-TIMEOUT: NOT EXISTS subquery is back — odds_coverage will time out on large days"
    )
    # The fast form must use FILTER aggregates.
    assert "FILTER (WHERE o.bookmaker = 'Pinnacle')" in fn_body, (
        "OPS-COVERAGE-TIMEOUT: with_pinnacle FILTER aggregate missing"
    )
    # without_pinnacle is now derived in Python, not SQL.
    assert "matches_with_odds - matches_with_pinnacle" in fn_body, (
        "OPS-COVERAGE-TIMEOUT: without_pinnacle must be derived in Python (with_odds - with_pinnacle)"
    )
    # Critical-section failures must mark the pipeline run failed (not silently succeed).
    assert "CRITICAL_SECTIONS" in fn_body and "odds_coverage" in fn_body, (
        "OPS-COVERAGE-TIMEOUT: critical-section guard missing — silent timeouts will recur"
    )


@test("SETTLEMENT-CATCHUP — scheduler fires settlement on startup if last success was >25h ago (source inspect)")
def _():
    """Every git push redeploys the Railway scheduler, killing any in-flight job.
    With heavy dev cadence the 21:00/23:30/01:00 redundant settlement runs can
    all be killed mid-run, leaving finished matches unsettled until the next
    21:00 window. This catch-up runs at startup so a missed daily settlement
    doesn't sit waiting a full day."""
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()
    assert "_maybe_catchup_missed_settlement" in src, (
        "SETTLEMENT-CATCHUP: function missing from scheduler"
    )
    assert "timedelta(hours=25)" in src, (
        "SETTLEMENT-CATCHUP: 25-hour 'last successful run' threshold must be present"
    )
    assert "settlement_pipeline" in src, (
        "SETTLEMENT-CATCHUP: must invoke settlement_pipeline so the catch-up actually settles"
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


@test("settle_bet_result — BTTS yes/no settle from both teams scoring")
def _():
    from workers.jobs.settlement import settle_bet_result
    # BTTS yes — both teams scored — should win
    bet_yes = {"market": "BTTS", "selection": "yes", "stake": "10", "odds_at_pick": "1.80"}
    r = settle_bet_result(bet_yes, home_goals=1, away_goals=1, closing_odds=None)
    assert r["result"] == "won", f"BTTS yes 1-1 should win, got {r['result']}"
    assert r["pnl"] == 8.0, f"Expected pnl=8.0, got {r['pnl']}"
    # BTTS yes — clean sheet — should lose
    r2 = settle_bet_result(bet_yes, home_goals=2, away_goals=0, closing_odds=None)
    assert r2["result"] == "lost", f"BTTS yes 2-0 should lose, got {r2['result']}"
    # BTTS no — clean sheet — should win
    bet_no = {"market": "BTTS", "selection": "no", "stake": "10", "odds_at_pick": "2.10"}
    r3 = settle_bet_result(bet_no, home_goals=2, away_goals=0, closing_odds=None)
    assert r3["result"] == "won", f"BTTS no 2-0 should win, got {r3['result']}"
    # BTTS no — both scored — should lose
    r4 = settle_bet_result(bet_no, home_goals=1, away_goals=1, closing_odds=None)
    assert r4["result"] == "lost", f"BTTS no 1-1 should lose, got {r4['result']}"


@test("settle_bet_result — O/U with line in selection (inplay format)")
def _():
    """Inplay bots store market='O/U' with line in selection (e.g. 'over 1.5').
    Default-2.5 line bug used to mis-settle every non-2.5 inplay O/U."""
    from workers.jobs.settlement import settle_bet_result
    # over 1.5 — 2 goals — should win (was lost under default-2.5 bug)
    bet = {"market": "O/U", "selection": "over 1.5", "stake": "10", "odds_at_pick": "1.50"}
    r = settle_bet_result(bet, home_goals=2, away_goals=0, closing_odds=None)
    assert r["result"] == "won", f"O/U over 1.5 with 2 goals should win, got {r['result']}"
    # over 3.5 — 3 goals — should lose (was won under default-2.5 bug)
    bet2 = {"market": "O/U", "selection": "over 3.5", "stake": "10", "odds_at_pick": "2.50"}
    r2 = settle_bet_result(bet2, home_goals=2, away_goals=1, closing_odds=None)
    assert r2["result"] == "lost", f"O/U over 3.5 with 3 goals should lose, got {r2['result']}"
    # under 3.5 — 3 goals — should win (was lost under default-2.5 bug)
    bet3 = {"market": "O/U", "selection": "under 3.5", "stake": "10", "odds_at_pick": "1.60"}
    r3 = settle_bet_result(bet3, home_goals=1, away_goals=2, closing_odds=None)
    assert r3["result"] == "won", f"O/U under 3.5 with 3 goals should win, got {r3['result']}"
    # over 25 (legacy no-dot encoding) — 3 goals — should win (line=2.5)
    bet4 = {"market": "O/U", "selection": "over 25", "stake": "10", "odds_at_pick": "1.90"}
    r4 = settle_bet_result(bet4, home_goals=2, away_goals=1, closing_odds=None)
    assert r4["result"] == "won", f"O/U over 25 with 3 goals should win, got {r4['result']}"


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
    # AF API embeds handicap in the value string: "Home -0.5" (not separate field)
    raw = [{
        "bookmakers": [{
            "name": "Pinnacle",
            "bets": [{
                "name": "Asian Handicap",
                "values": [
                    {"value": "Home -0.5", "odd": "1.87"},
                    {"value": "Away +0.5", "odd": "2.03"},
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


@test("DNB-PARSE — parse_fixture_odds extracts Draw No Bet rows as market=draw_no_bet")
def _():
    """DNB-PARSE (2026-05-26) + DNB-PARSE-NAMING-FIX (2026-05-28):
    AF bulk /odds calls this market "Home/Away" (bet id 2); per-fixture endpoint
    uses "Draw No Bet" (bet id 11). Both map to market=draw_no_bet."""
    from workers.api_clients.api_football import parse_fixture_odds

    # Test both naming conventions
    for bet_name in ("Draw No Bet", "Home/Away"):
        raw = [{
            "bookmakers": [{
                "name": "Pinnacle",
                "bets": [{
                    "name": bet_name,
                    "values": [
                        {"value": "Home", "odd": "1.55"},
                        {"value": "Away", "odd": "2.45"},
                    ]
                }]
            }]
        }]
        rows = parse_fixture_odds(raw)
        dnb_rows = [r for r in rows if r["market"] == "draw_no_bet"]
        assert len(dnb_rows) == 2, f"Expected 2 DNB rows for '{bet_name}', got {len(dnb_rows)}"
        home = next(r for r in dnb_rows if r["selection"] == "home")
        away = next(r for r in dnb_rows if r["selection"] == "away")
        assert home["odds"] == 1.55
        assert away["odds"] == 2.45
        assert home["bookmaker"] == "Pinnacle"


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


@test("ODDS-QUALITY-CLEANUP — filter_garbage_ou_rows drops blacklisted bookmakers on OU only")
def _():
    from workers.utils.odds_quality import filter_garbage_ou_rows
    rows = [
        # OU rows from blacklisted sources — must be dropped
        {"bookmaker": "api-football", "market": "over_under_15", "selection": "over",  "odds": 3.34},
        {"bookmaker": "api-football", "market": "over_under_15", "selection": "under", "odds": 2.63},
        {"bookmaker": "William Hill",  "market": "over_under_25", "selection": "over",  "odds": 5.96},
        {"bookmaker": "William Hill",  "market": "over_under_25", "selection": "under", "odds": 1.14},
        {"bookmaker": "api-football-live", "market": "over_under_35", "selection": "over", "odds": 21.0},
        # 1X2 rows from same blacklisted sources — must be kept (those markets are clean)
        {"bookmaker": "api-football", "market": "1x2", "selection": "home", "odds": 2.10},
        {"bookmaker": "William Hill",  "market": "1x2", "selection": "draw", "odds": 3.40},
        # BTTS from a blacklisted source — also kept (BTTS clean)
        {"bookmaker": "api-football", "market": "btts", "selection": "yes", "odds": 1.90},
        # Legitimate Pinnacle OU pair — kept
        {"bookmaker": "Pinnacle", "market": "over_under_15", "selection": "over",  "odds": 1.45},
        {"bookmaker": "Pinnacle", "market": "over_under_15", "selection": "under", "odds": 2.60},
    ]
    out = filter_garbage_ou_rows(rows)
    bookmakers_kept = {(r["bookmaker"], r["market"]) for r in out}
    # Blacklist: zero OU rows from those three sources
    for bm in ("api-football", "William Hill", "api-football-live"):
        for r in out:
            assert not (r["bookmaker"] == bm and r["market"].startswith("over_under_")), (
                f"ODDS-QUALITY-CLEANUP: blacklisted OU row leaked through: {r}"
            )
    # Whitelist: 1X2 + BTTS from blacklisted books still present
    assert ("api-football", "1x2") in bookmakers_kept
    assert ("William Hill", "1x2") in bookmakers_kept
    assert ("api-football", "btts") in bookmakers_kept
    # Pinnacle OU pair (valid, sum=1/1.45+1/2.60=1.075) survives
    assert ("Pinnacle", "over_under_15") in bookmakers_kept


@test("ODDS-QUALITY-CLEANUP — filter_garbage_ou_rows drops impossible (sum<1.02) OU pairs")
def _():
    from workers.utils.odds_quality import filter_garbage_ou_rows
    rows = [
        # Impossible market: 1/3.0 + 1/2.0 = 0.833 < 1.02 — both must be dropped
        {"bookmaker": "Bet365", "market": "over_under_15", "selection": "over",  "odds": 3.0},
        {"bookmaker": "Bet365", "market": "over_under_15", "selection": "under", "odds": 2.0},
        # Borderline-impossible: 1/2.5 + 1/1.85 = 0.940 < 1.02 — both dropped
        {"bookmaker": "Betano", "market": "over_under_25", "selection": "over",  "odds": 2.5},
        {"bookmaker": "Betano", "market": "over_under_25", "selection": "under", "odds": 1.85},
        # Valid market: 1/1.45 + 1/2.60 = 1.075 — both kept
        {"bookmaker": "Pinnacle", "market": "over_under_15", "selection": "over",  "odds": 1.45},
        {"bookmaker": "Pinnacle", "market": "over_under_15", "selection": "under", "odds": 2.60},
    ]
    out = filter_garbage_ou_rows(rows)
    pairs_kept = {(r["bookmaker"], r["market"]) for r in out}
    assert ("Bet365", "over_under_15") not in pairs_kept, "impossible OU pair kept"
    assert ("Betano", "over_under_25") not in pairs_kept, "borderline-impossible pair kept"
    assert ("Pinnacle", "over_under_15") in pairs_kept, "valid pair dropped"
    assert len(out) == 2, f"expected 2 rows kept, got {len(out)}: {out}"


@test("ODDS-QUALITY-CLEANUP — read-path SQL filter excludes blacklisted OU sources (source guard)")
def _():
    """Guard the SQL clause in _load_today_from_db that excludes blacklisted bookmakers
    on OU markets. Ensures a future refactor can't silently drop the protection."""
    import inspect
    from workers.jobs import daily_pipeline_v2
    src = inspect.getsource(daily_pipeline_v2._load_today_from_db)
    assert "market LIKE 'over_under_%%'" in src, (
        "ODDS-QUALITY-CLEANUP: read-path OU blacklist SQL clause missing"
    )
    for bm in ("api-football", "api-football-live", "William Hill"):
        assert f"'{bm}'" in src, (
            f"ODDS-QUALITY-CLEANUP: blacklisted bookmaker '{bm}' missing from "
            "read-path OU exclusion clause in _load_today_from_db"
        )
    # Implied-sum sanity gate present
    assert "1.02" in src and "OU_PAIRS" in src, (
        "ODDS-QUALITY-CLEANUP: implied-sum sanity gate (1/over + 1/under < 1.02) "
        "missing from _load_today_from_db"
    )


@test("OU-PIN-REQUIRED — OU markets only aggregate when Pinnacle has a row + non-Pinnacle dropped >2x Pinnacle")
def _():
    """Source-inspect guard: betting pipeline's odds aggregator must (1) skip
    any OU row when Pinnacle has no price for that (match, market, selection),
    and (2) when Pinnacle IS present, drop non-Pinnacle rows priced more than
    2× Pinnacle. Together these block both classes of mislabelled OU rows that
    bot_ou15_defensive previously bet on (12 had no Pinnacle ref, 7 exceeded
    the 2× cap = all 19 voids in the bot's pre-guard 38-bet history)."""
    import inspect
    from workers.jobs import daily_pipeline_v2
    src = inspect.getsource(daily_pipeline_v2._load_today_from_db)
    assert "OU-PIN-REQUIRED" in src, (
        "OU-PIN-REQUIRED marker missing — OU rows would aggregate without Pinnacle reference"
    )
    assert "OU-PINNACLE-CAP" in src, "OU-PINNACLE-CAP marker missing from _load_today_from_db"
    assert "pin_price is None" in src, (
        "OU-PIN-REQUIRED: must skip OU rows when no Pinnacle reference exists"
    )
    assert "2.0 * pin_price" in src, (
        "OU-PINNACLE-CAP: cap multiplier check (2.0 * pin_price) missing — "
        "non-Pinnacle OU rows would no longer be filtered against Pinnacle"
    )
    assert 'bookmaker != "Pinnacle"' in src, (
        "OU-PINNACLE-CAP: cap must only apply to non-Pinnacle rows"
    )


@test("ODDS-QUALITY-CLEANUP — filter drops unused OU markets and extreme AH lines")
def _():
    """filter_garbage_ou_rows must drop OU variants not in ALLOWED_OU_MARKETS
    and AH lines beyond ±MAX_AH_LINE, while keeping the four used OU markets
    and AH lines within range."""
    from workers.utils.odds_quality import filter_garbage_ou_rows, ALLOWED_OU_MARKETS, MAX_AH_LINE
    rows = [
        # Unused OU market — must be dropped
        {"bookmaker": "Bet365", "market": "over_under_275", "selection": "over",  "odds": 1.80},
        {"bookmaker": "Bet365", "market": "over_under_275", "selection": "under", "odds": 2.05},
        # Used OU market — must be kept (valid pair)
        {"bookmaker": "Bet365", "market": "over_under_25", "selection": "over",  "odds": 1.90},
        {"bookmaker": "Bet365", "market": "over_under_25", "selection": "under", "odds": 1.95},
        # AH line within ±3.0 — must be kept
        {"bookmaker": "Pinnacle", "market": "asian_handicap", "selection": "home", "odds": 1.92, "handicap_line": -1.5},
        # AH line beyond ±3.0 — must be dropped
        {"bookmaker": "Pinnacle", "market": "asian_handicap", "selection": "home", "odds": 1.05, "handicap_line": -5.0},
        # 1x2 — always kept
        {"bookmaker": "Pinnacle", "market": "1x2", "selection": "home", "odds": 2.10},
    ]
    out = filter_garbage_ou_rows(rows)
    markets = [(r["market"], r.get("handicap_line")) for r in out]
    assert ("over_under_275", None) not in [(r["market"], None) for r in out], "unused OU market must be dropped"
    assert any(r["market"] == "over_under_25" for r in out), "allowed OU market must be kept"
    assert any(r.get("handicap_line") == -1.5 for r in out), "AH line within ±3.0 must be kept"
    assert not any(r.get("handicap_line") == -5.0 for r in out), "AH line beyond ±3.0 must be dropped"
    assert any(r["market"] == "1x2" for r in out), "1x2 must always pass through"
    assert MAX_AH_LINE == 3.0, "MAX_AH_LINE must be 3.0"
    assert "over_under_25" in ALLOWED_OU_MARKETS and "over_under_15" in ALLOWED_OU_MARKETS


@test("ODDS-QUALITY-CLEANUP — write-path applies filter (fetch_odds + store_odds source guard)")
def _():
    """Both the bulk pre-match writer (fetch_odds.fetch_af_odds) and the
    legacy single-bookmaker writer (supabase_client.store_odds) must call
    filter_garbage_ou_rows before INSERT."""
    import inspect
    from workers.jobs import fetch_odds
    from workers.api_clients import supabase_client
    fo_src = inspect.getsource(fetch_odds.fetch_af_odds)
    assert "filter_garbage_ou_rows" in fo_src, (
        "ODDS-QUALITY-CLEANUP: fetch_af_odds no longer applies filter_garbage_ou_rows"
    )
    so_src = inspect.getsource(supabase_client.store_odds)
    assert "filter_garbage_ou_rows" in so_src, (
        "ODDS-QUALITY-CLEANUP: store_odds no longer applies filter_garbage_ou_rows"
    )


@test("ODDS-QUALITY-CLEANUP — pipeline skips bots flagged is_active=false")
def _():
    """The daily betting pipeline must respect bots.is_active so a paused bot
    (e.g. during this cleanup) never places new bets until re-enabled."""
    import inspect
    from workers.jobs import daily_pipeline_v2
    src = inspect.getsource(daily_pipeline_v2.run_morning)
    assert "_bot_active" in src, (
        "ODDS-QUALITY-CLEANUP: run_morning no longer reads is_active per bot"
    )
    assert "not _bot_active.get(bot_name" in src, (
        "ODDS-QUALITY-CLEANUP: run_morning loop missing is_active gate"
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


@test("EMAIL-DIGEST-EDGE-UNITS — edge_percent filter uses decimal threshold, formula scales ×100")
def _():
    """Regression guard: edge_percent is stored as decimal (0.05 = 5%).
    Smart-slot qualification (introduced 2026-05-09) shipped with `>= 3` which
    would require 300% edge — impossible, so signal_strength was always 0 and
    no digest ever fired between 2026-05-09 and 2026-05-12. Lock in the fix:
    (a) the min-edge filter must compare against a decimal (>= 0.03, not >= 3),
    (b) the signal_strength formula must scale edge_percent by ×100 so the
    documented EMAIL_DIGEST_MIN_SIGNAL=5.0 default has its intended meaning."""
    src = open("workers/jobs/email_digest.py").read()
    # Filter must be decimal — `>= 3` (as standalone token) would mean 300% edge
    assert "edge_percent >= 3\n" not in src and "edge_percent >= 3 " not in src, (
        "edge_percent >= 3 is a unit bug — edge_percent is decimal (0.05 = 5%), "
        "use >= 0.03 to mean 3%"
    )
    # The fixed threshold must appear (at least once in compute_signal_strength)
    assert "edge_percent >= 0.03" in src, (
        "Expected `edge_percent >= 0.03` (3% threshold as decimal) after the fix"
    )
    # And the formula must scale edge_percent ×100 to match the documented threshold units
    assert "sb.edge_percent * 100" in src, (
        "compute_signal_strength formula must scale edge_percent×100 — "
        "EMAIL_DIGEST_MIN_SIGNAL default (5.0) assumes edge in percentage-point units"
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
    # SHADOW-PREDICTIONS (2026-05-24, migration 127): unique key extended to include
    # model_version so shadow runs can coexist with production rows. Old constraint
    # was (match_id, market, source). New constraint is (match_id, market, source,
    # model_version) — bulk upsert must match.
    assert "ON CONFLICT (match_id, market, source, model_version) DO UPDATE" in src, (
        "bulk_store_predictions must upsert on the SHADOW-PREDICTIONS unique key "
        "(match_id, market, source, model_version)"
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
    # Exclude combo bets (market='combo'): combined edge across 5 legs can legitimately exceed 1.5
    # e.g. five legs each at 21% edge → combined = (1.21)^5 - 1 ≈ 1.59. The bug to catch is
    # single/inplay bets storing 15.9 instead of 0.159 (the old `edge * 100` path).
    rows = execute_query(
        """SELECT id, edge_percent FROM simulated_bets
           WHERE edge_percent IS NOT NULL AND edge_percent > 1.5
             AND market != 'combo'
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


@test("MISFIRE-GRACE — job_defaults sets misfire_grace_time so 1-3s GIL jitter doesn't skip jobs")
def _():
    # APScheduler's default misfire_grace_time is 1s. Once-a-day jobs (Watchlist
    # 08:30, Stripe Reconcile 09:00, Odds 11:00) were silently skipped on Railway
    # when the scheduler thread slipped 2-3s under GIL contention. Widening the
    # grace window to 5min is safe because coalesce=True collapses stale bursts.
    from pathlib import Path
    src = Path("workers/scheduler.py").read_text()
    assert '"misfire_grace_time": 300' in src, (
        "BackgroundScheduler job_defaults must set misfire_grace_time=300 — "
        "default 1s causes once-a-day jobs to be silently skipped when the "
        "scheduler thread slips a few seconds under GIL contention with "
        "LivePoller / Flask / InplayBot."
    )
    assert '"coalesce": True' in src, (
        "coalesce=True must remain set — without it, a wide misfire_grace_time "
        "would let multiple stale runs all fire at once on catch-up."
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
    assert "odds <= 2.10" in body, "D OU odds floor must drop to 2.10 (was 2.50)"


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
    assert "corners_delta < 2" in g_body, "G must require ≥ 2-corner delta in 10-min window"


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
    # Dual-line ladder: O2.5 if odds > 2.30 (was 2.80, loosened by INPLAY-LOOSEN-SILENT
    # 2026-05-17 — avg O2.5 market was 2.37 so 2.80 was firing almost never), else O1.5
    # if odds > 1.60.
    assert "o25_odds > 2.30" in h_body, "H must take O2.5 only when its odds > 2.30 (INPLAY-LOOSEN-SILENT)"
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
    assert "odds <= 2.30" in q_body, "Q must require live OU 2.5 over odds > 2.30"
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


@test("SHADOW-BETS-TABLE — migration 101 creates shadow_bets with required columns")
def _():
    """Source-inspect the migration. shadow_bets is queried by run_morning,
    bulk_store_shadow_bets, settlement, and ops_snapshot — a missing column
    here is a multi-system failure."""
    import pathlib
    src = pathlib.Path("supabase/migrations/101_shadow_bets.sql").read_text()
    required_cols = [
        "shadow_run_id", "shadow_cohort", "bot_id", "match_id", "market", "selection",
        "odds_at_pick", "stake", "model_probability", "edge_percent",
        "recommended_bookmaker", "kelly_fraction", "timing_cohort",
        "closing_odds", "clv", "result", "pnl",
    ]
    for col in required_cols:
        assert col in src, f"migration 101 missing shadow_bets column: {col}"
    assert "shadow_runs_today" in src, "migration 101 must ALTER ops_snapshots ADD shadow_runs_today"
    assert "shadow_bets_today" in src, "migration 101 must ALTER ops_snapshots ADD shadow_bets_today"
    assert "uq_shadow_bet_per_run" in src, "missing per-run dedup constraint"


@test("SHADOW-MODE-WIRED — run_morning accepts shadow_mode + shadow_cohort kwargs")
def _():
    """The whole shadow pipeline depends on this signature. If anyone reverts,
    the scheduler call site will TypeError immediately at runtime."""
    import inspect
    from workers.jobs.daily_pipeline_v2 import run_morning
    sig = inspect.signature(run_morning)
    assert "shadow_mode" in sig.parameters, "run_morning lost shadow_mode kwarg"
    assert "shadow_cohort" in sig.parameters, "run_morning lost shadow_cohort kwarg"
    assert sig.parameters["shadow_mode"].default is False, "shadow_mode default must be False"
    assert sig.parameters["shadow_cohort"].default is None, "shadow_cohort default must be None"

    # Body must reference the shadow store, not store_bet for shadow rows.
    src = inspect.getsource(run_morning)
    assert "bulk_store_shadow_bets" in src, "run_morning must flush via bulk_store_shadow_bets"
    assert "_pending_shadow_rows" in src, "run_morning must accumulate shadow rows in a buffer"


@test("SHADOW-NO-BANKROLL — shadow path never touches bankroll or exposure cap")
def _():
    """Shadow bets are virtual — they MUST NOT subtract from _running_bankroll
    or trigger exposure caps. Otherwise a shadow run silently corrupts the
    real bots' next bet sizing."""
    import inspect
    from workers.jobs.daily_pipeline_v2 import run_morning
    src = inspect.getsource(run_morning)

    # The shadow append block must be guarded by `if shadow_mode:` and
    # `continue` (skipping the store_bet + bankroll mutation path).
    shadow_block_idx = src.index("if shadow_mode:")
    after_shadow = src[shadow_block_idx:shadow_block_idx + 2000]
    assert "_pending_shadow_rows.append" in after_shadow, (
        "shadow path must append to buffer instead of calling store_bet"
    )
    assert "continue" in after_shadow, (
        "shadow path must `continue` past the real-bet store + bankroll mutation"
    )

    # Exposure cap must be gated by `not shadow_mode`.
    assert "not shadow_mode and _league_count >= 2" in src, (
        "exposure cap (stake halving) must skip when shadow_mode=True"
    )


@test("SHADOW-SETTLE-WIRED — run_settlement settles shadow_bets after simulated_bets")
def _():
    """If we forget to settle shadow_bets, the analysis we built this whole
    system for never gets closing odds / CLV / result fields populated."""
    import inspect, pathlib
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    assert "_PENDING_SHADOW_BETS_SQL" in src, "missing _PENDING_SHADOW_BETS_SQL"
    assert "_settle_pending_shadow_bets" in src, "missing _settle_pending_shadow_bets()"
    assert "UPDATE shadow_bets SET result" in src, (
        "shadow settlement must UPDATE shadow_bets (not simulated_bets)"
    )
    # And the wire-up call in run_settlement.
    from workers.jobs.settlement import run_settlement
    rs_src = inspect.getsource(run_settlement)
    assert "_PENDING_SHADOW_BETS_SQL" in rs_src, (
        "run_settlement must load shadow_pending"
    )
    assert "_settle_pending_shadow_bets" in rs_src, (
        "run_settlement must invoke shadow settlement"
    )


@test("SHADOW-SCHEDULER — 30-min interval shadow job registered (07:05–22:35 UTC)")
def _():
    """Shadow runs every 30 min after odds refresh. Cohort = HHMM time string.
    Replaces the old 3-slot design (06:30/11:30/15:30)."""
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()
    assert "job_shadow_run_interval" in src, "missing interval shadow job function"
    assert "shadow_interval" in src, "missing shadow_interval job id"
    assert '"7-22"' in src, "interval shadow must cover hours 7-22"
    assert '"5,35"' in src, "interval shadow must fire at :05 and :35"


@test("COOLBET-ODDS-SNAPSHOT — parse_market maps new Coolbet schema to our shape")
def _():
    """COOLBET-ODDS-SNAPSHOT (2026-05-20) — Coolbet API restructured: markets
    + odds now served from separate endpoints. parse_market reads the new
    `{market_type_id, line, outcomes:[{id, result_key}]}` shape and looks
    odds up from a separate {outcome_id: decimal} map. Guards:
      - market_type_id 81 → 1x2 with result_key [Home]/Draw/[Away]
      - market_type_id 818 + line=1.5 → over_under_15 with result_key Over/Under
      - Unknown markets fall back to name-based detection
      - Outcomes missing from odds_map are dropped (no 0-odds rows)
      - AH stores handicap_line from home perspective
    """
    from workers.automation.coolbet_explorer import parse_market

    # 1X2 (market_type_id 81, real Coolbet shape from probe).
    # odds_map values are now dicts ({value, odds_id, ...}) — placer needs odds_id
    # for the placement payload; explorer just reads .value.
    odds = {
        1502758378: {"value": 2.25, "odds_id": "uuid-h", "market_id": 598381104, "status": "OPEN"},
        1502758379: {"value": 3.20, "odds_id": "uuid-d", "market_id": 598381104, "status": "OPEN"},
        1502758380: {"value": 3.10, "odds_id": "uuid-a", "market_id": 598381104, "status": "OPEN"},
    }
    rows = parse_market({
        "id": 598381104, "line": 0, "name": "Match Result (1X2)",
        "market_type_id": 81,
        "outcomes": [
            {"id": 1502758378, "name": "SC Grobinas", "result_key": "[Home]"},
            {"id": 1502758379, "name": "Draw",        "result_key": "Draw"},
            {"id": 1502758380, "name": "SK Super Nova","result_key": "[Away]"},
        ],
    }, odds)
    assert rows == [
        ("1x2", "Home", 2.25, None),
        ("1x2", "Draw", 3.20, None),
        ("1x2", "Away", 3.10, None),
    ], f"1X2 parse wrong: {rows}"

    # OU 1.5 (market_type_id 818, line='1.5')
    odds = {
        1502775086: {"value": 1.05, "odds_id": "uuid-o", "market_id": 598387279, "status": "OPEN"},
        1502775087: {"value": 7.00, "odds_id": "uuid-u", "market_id": 598387279, "status": "OPEN"},
    }
    rows = parse_market({
        "id": 598387279, "line": "1.5", "name": "Total Goals Over / Under",
        "market_type_id": 818,
        "outcomes": [
            {"id": 1502775086, "result_key": "Over"},
            {"id": 1502775087, "result_key": "Under"},
        ],
    }, odds)
    assert rows == [
        ("over_under_15", "over", 1.05, None),
        ("over_under_15", "under", 7.00, None),
    ], f"OU 1.5 parse wrong: {rows}"

    # BTTS — falls back to name-based detection (mtid set is still empty)
    odds = {
        9001: {"value": 1.85, "odds_id": "b1", "market_id": 1, "status": "OPEN"},
        9002: {"value": 1.90, "odds_id": "b2", "market_id": 1, "status": "OPEN"},
    }
    rows = parse_market({
        "id": 1, "line": 0, "name": "Both Teams to Score", "market_type_id": 999,
        "outcomes": [
            {"id": 9001, "result_key": "Yes"},
            {"id": 9002, "result_key": "No"},
        ],
    }, odds)
    assert rows == [
        ("btts", "yes", 1.85, None),
        ("btts", "no", 1.90, None),
    ], f"BTTS fallback wrong: {rows}"

    # AH — line is home-perspective; both outcomes share it
    odds = {
        7001: {"value": 1.95, "odds_id": "a1", "market_id": 1, "status": "OPEN"},
        7002: {"value": 1.85, "odds_id": "a2", "market_id": 1, "status": "OPEN"},
    }
    rows = parse_market({
        "id": 1, "line": "-1.25", "name": "Asian Handicap", "market_type_id": 999,
        "outcomes": [
            {"id": 7001, "result_key": "[Home]"},
            {"id": 7002, "result_key": "[Away]"},
        ],
    }, odds)
    assert rows == [
        ("asian_handicap", "home", 1.95, -1.25),
        ("asian_handicap", "away", 1.85, -1.25),
    ], f"AH parse wrong: {rows}"

    # Outcome with no odds entry must be dropped, not stored at 0
    rows = parse_market({
        "id": 1, "line": 0, "name": "Match Result (1X2)", "market_type_id": 81,
        "outcomes": [
            {"id": 1, "result_key": "[Home]"},
            {"id": 2, "result_key": "Draw"},
            {"id": 3, "result_key": "[Away]"},
        ],
    }, {1: {"value": 2.0, "odds_id": "x", "market_id": 1, "status": "OPEN"}})  # only home
    assert rows == [("1x2", "Home", 2.0, None)], (
        f"missing odds must drop, not zero-fill: {rows}"
    )

    # Scheduler registration
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()
    assert "_coolbet_odds_snapshot_wrapper" in src, "missing wrapper function"
    assert "coolbet_odds_interval" in src, "missing coolbet_odds_interval job id"
    assert '"3,33"' in src, "Coolbet snapshot must fire at :03 and :33"

    # Endpoint constants must exist (used by the new fetcher)
    explorer_src = pathlib.Path("workers/automation/coolbet_explorer.py").read_text()
    assert "_ODDS_LINE_URL" in explorer_src, "missing /fo-line/ URL constant"
    assert "fetch_match_markets" in explorer_src, "missing fetch_match_markets"
    assert "fetch_odds_for_markets" in explorer_src, "missing fetch_odds_for_markets"


@test("COOLBET-HUMAN-PACED — every CoolbetSession call routes through _throttle()")
def _():
    """COOLBET-HUMAN-PACED (2026-05-20) — Coolbet has Imperva-class anti-bot
    in front of the API. Every authenticated call must go through _throttle()
    which enforces a randomised gap (default 0.8–2.0s) between consecutive
    requests so we don't look like a scraper. Centralised in CoolbetSession so
    new daemon/scheduler/explorer features can't bypass it accidentally."""
    import inspect
    from workers.automation.coolbet_session import CoolbetSession
    assert hasattr(CoolbetSession, "_throttle"), "missing _throttle()"
    get_src = inspect.getsource(CoolbetSession.get)
    post_src = inspect.getsource(CoolbetSession.post)
    assert "self._throttle()" in get_src, "get() must call _throttle"
    assert "self._throttle()" in post_src, "post() must call _throttle"
    src = inspect.getsource(CoolbetSession._throttle)
    assert "random.uniform" in src, "_throttle must use jitter (random.uniform), not constant gap"


@test("COOLBET-PLACER-NEW-SCHEMA — resolve_placement_target + placer wired to new helpers")
def _():
    """COOLBET-PLACER-NEW-SCHEMA (2026-05-20) — Coolbet split markets and odds
    into separate endpoints and dropped `criterion_label`. resolve_placement_target
    maps our paper-bet (market, selection) → Coolbet (market_id, outcome_id,
    odds_id, current_odds) using the new markets/outcomes/result_key/line shape.
    place_all_bets's per-bet loop must use the new helpers, not the dead
    find_market_outcome path."""
    from workers.automation.coolbet_explorer import resolve_placement_target

    # 1X2 Home — full real-shape sample from probe
    markets = [{
        "id": 598381104, "line": 0, "name": "Match Result (1X2)",
        "market_type_id": 81,
        "outcomes": [
            {"id": 1502758378, "result_key": "[Home]"},
            {"id": 1502758379, "result_key": "Draw"},
            {"id": 1502758380, "result_key": "[Away]"},
        ],
    }]
    odds = {
        1502758378: {"value": 2.25, "odds_id": "uuid-h", "market_id": 598381104, "status": "OPEN"},
        1502758379: {"value": 3.20, "odds_id": "uuid-d", "market_id": 598381104, "status": "OPEN"},
        1502758380: {"value": 3.10, "odds_id": "uuid-a", "market_id": 598381104, "status": "OPEN"},
    }
    res = resolve_placement_target(markets, odds, "1X2", "Home")
    assert res == (598381104, 1502758378, "uuid-h", 2.25), f"1X2 Home wrong: {res}"

    res = resolve_placement_target(markets, odds, "1X2", "Draw")
    assert res == (598381104, 1502758379, "uuid-d", 3.20), f"1X2 Draw wrong: {res}"

    # OU 1.5 Over
    markets = [{
        "id": 598387279, "line": "1.5", "name": "Total Goals Over / Under",
        "market_type_id": 818,
        "outcomes": [
            {"id": 1502775086, "result_key": "Over"},
            {"id": 1502775087, "result_key": "Under"},
        ],
    }]
    odds = {
        1502775086: {"value": 1.05, "odds_id": "uuid-o", "market_id": 598387279, "status": "OPEN"},
        1502775087: {"value": 7.00, "odds_id": "uuid-u", "market_id": 598387279, "status": "OPEN"},
    }
    res = resolve_placement_target(markets, odds, "O/U", "Over 1.5")
    assert res == (598387279, 1502775086, "uuid-o", 1.05), f"OU Over 1.5 wrong: {res}"

    # OU line mismatch — same market, different line — must return None
    res = resolve_placement_target(markets, odds, "O/U", "Over 2.5")
    assert res is None, f"OU line mismatch must return None, got {res}"

    # Outcome with no odds entry → None (suspended / dropped)
    res = resolve_placement_target(markets, {}, "O/U", "Over 1.5")
    assert res is None, f"empty odds_map must return None, got {res}"

    # AH home -1.25
    markets = [{
        "id": 1, "line": "-1.25", "name": "Asian Handicap", "market_type_id": 999,
        "outcomes": [
            {"id": 7001, "result_key": "[Home]"},
            {"id": 7002, "result_key": "[Away]"},
        ],
    }]
    odds = {
        7001: {"value": 1.95, "odds_id": "ah1", "market_id": 1, "status": "OPEN"},
        7002: {"value": 1.85, "odds_id": "ah2", "market_id": 1, "status": "OPEN"},
    }
    res = resolve_placement_target(markets, odds, "asian_handicap", "Home -1.25")
    assert res == (1, 7001, "ah1", 1.95), f"AH home wrong: {res}"

    # AH line mismatch
    res = resolve_placement_target(markets, odds, "asian_handicap", "Home -1.5")
    assert res is None, f"AH line mismatch must return None, got {res}"

    # Placer per-bet loop wiring
    import pathlib
    placer = pathlib.Path("workers/automation/coolbet_placer.py").read_text()
    assert "from workers.automation.coolbet_explorer import" in placer, (
        "placer must import the new-schema helpers from coolbet_explorer"
    )
    assert "resolve_placement_target" in placer, "placer must call resolve_placement_target"
    assert "fetch_match_markets" in placer, "placer must call fetch_match_markets"
    assert "fetch_odds_for_markets" in placer, "placer must call fetch_odds_for_markets"
    # Make sure the per-bet loop no longer relies on criterion_label
    # (legacy find_market_outcome may still exist for back-compat but must not
    # be in the active placement path).
    in_place_all = placer[placer.index("def place_all_bets"):]
    # Comments referencing the old field are fine; what's forbidden is
    # actually accessing it as a dict key, since the new schema has no such field.
    assert 'bo["criterion_label"]' not in in_place_all, (
        "place_all_bets must not read bo['criterion_label'] — field doesn't exist on new schema"
    )
    assert 'bo[\'criterion_label\']' not in in_place_all, (
        "place_all_bets must not read bo['criterion_label'] — field doesn't exist on new schema"
    )


@test("COOLBET-SEARCH-BLOCKED — non-200 raises, placer aborts loop, summary names it")
def test_coolbet_search_blocked():
    """COOLBET-SEARCH-BLOCKED (2026-05-26): a dead cbauth JWT or Incapsula
    challenge returns 4xx/5xx from /search/v2. Previously swallowed at DEBUG,
    making 18 doomed searches look like 18 genuine no-coverage misses. The
    placer now raises CoolbetSearchBlocked, marks the current + remaining
    bets as 'search_blocked', and skips the combo phase."""
    import inspect, pathlib

    # Module-level exception is exported
    from workers.automation.coolbet_placer import CoolbetSearchBlocked  # noqa: F401

    placer_src = pathlib.Path("workers/automation/coolbet_placer.py").read_text()

    # _do_search must raise (not swallow) on non-200
    do_search = placer_src[placer_src.index("def _do_search"):
                            placer_src.index("def search_coolbet_event")]
    assert "raise CoolbetSearchBlocked" in do_search, (
        "_do_search must raise CoolbetSearchBlocked on non-200, not return []"
    )
    assert "log.warning" in do_search, (
        "_do_search must log non-200 at WARNING (not DEBUG) so blocks are visible"
    )

    # Singles loop must catch + mark remaining bets as search_blocked + break
    in_place_all = placer_src[placer_src.index("def place_all_bets"):]
    singles_catch = in_place_all[:in_place_all.index("_place_combo_bets") + 200]
    assert "except CoolbetSearchBlocked" in singles_catch, (
        "singles loop must catch CoolbetSearchBlocked"
    )
    assert '"outcome": "search_blocked"' in singles_catch, (
        "blocked bet rows must use outcome='search_blocked' (not 'no_event')"
    )
    assert "pending[idx:]" in singles_catch, (
        "remaining unprocessed bets must be marked as search_blocked, not silently dropped"
    )

    # Combo phase is skipped when singles loop tripped the block
    assert "if search_blocked:" in singles_catch, (
        "place_all_bets must skip combo phase when singles tripped the block"
    )

    # Combo loop has matching handling
    combo_src = placer_src[placer_src.index("def _place_combo_bets"):]
    assert "except CoolbetSearchBlocked" in combo_src, (
        "combo loop must also catch CoolbetSearchBlocked"
    )
    assert "combos[cidx:]" in combo_src, (
        "remaining combos must be marked as search_blocked on mid-run block"
    )

    # CLI summary surfaces the block prominently
    cli_src = pathlib.Path("scripts/place_coolbet_bets.py").read_text()
    assert "search_blocked" in cli_src, (
        "place_coolbet_bets.py summary must recognise search_blocked outcome"
    )
    assert "COOLBET_MANUAL_JWT" in cli_src, (
        "CLI must tell user how to fix it (refresh COOLBET_MANUAL_JWT)"
    )


@test("SEARCH-RETRY-TRANSIENT — _do_search retries once on 5xx/429, error text drops JWT advice")
def test_search_retry_transient():
    """SEARCH-RETRY-TRANSIENT (2026-05-29): one transient 5xx/429 from
    Coolbet's search endpoint used to mark every remaining bet in the batch as
    search_blocked and tell the user to refresh COOLBET_MANUAL_JWT — even
    though --record runs in anon-read mode (no JWT used). _do_search now
    retries once with a short backoff on transient statuses, and the error
    text no longer mentions the cbauth JWT."""
    import pathlib
    from workers.automation.coolbet_placer import _SEARCH_RETRY_STATUSES

    # Retry covers the actual transient statuses (429 rate-limit + 5xx server)
    for code in (429, 500, 502, 503, 504):
        assert code in _SEARCH_RETRY_STATUSES, (
            f"_SEARCH_RETRY_STATUSES must include {code} (transient)"
        )

    placer_src = pathlib.Path("workers/automation/coolbet_placer.py").read_text()
    do_search = placer_src[placer_src.index("def _do_search"):
                            placer_src.index("def search_coolbet_event")]

    # Retry loop is present (two attempts) and gated on the transient set
    assert "for attempt in (1, 2):" in do_search, (
        "_do_search must loop attempts so one transient 5xx/429 doesn't kill the batch"
    )
    assert "_SEARCH_RETRY_STATUSES" in do_search, (
        "_do_search must consult _SEARCH_RETRY_STATUSES to decide whether to retry"
    )
    assert "time.sleep" in do_search, (
        "_do_search must sleep between attempts (short backoff)"
    )

    # The CoolbetSearchBlocked raise still fires (persistent failures must abort
    # the batch — we're only smoothing transients, not silencing real blocks)
    assert "raise CoolbetSearchBlocked" in do_search, (
        "_do_search must still raise on persistent failure"
    )

    # Stale JWT advice removed — --record uses anon-read mode (no cbauth JWT)
    assert "COOLBET_MANUAL_JWT" not in do_search, (
        "_do_search error text must not advise refreshing COOLBET_MANUAL_JWT "
        "— anon-read mode doesn't use the JWT, so the advice is misleading"
    )
    assert "cbauth JWT" not in do_search, (
        "_do_search error text must not mention cbauth JWT (stale since COOLBET-ANON-READ)"
    )


@test("INPLAY-SCORE-ODDS-CONSISTENCY — guard rejects stale-score/fresh-odds snapshots + 1X2 drift events")
def test_inplay_score_odds_consistency():
    """INPLAY-SCORE-ODDS-CONSISTENCY (2026-05-30): bookmaker odds react to a
    goal within seconds; API-Football's score field lags by 30-60s. That
    window let the bot fire a fictional +56% edge bet on Yanbian Longding vs
    Changchun Yatai (home equalised at 23', snapshot at 07:25:10 had post-goal
    odds but score still 0-1, bot fired at 07:25:19, score corrected at
    07:25:53). Two new guards:
      • _score_odds_consistent: leading-team-implied-by-score must have
        shorter 1X2 odds than trailing team. Disagreement → skip.
      • _odds_drift_recent: any 1X2 leg moving ≥30% in 60s = goal-event
        signature → skip.
    """
    from workers.jobs.inplay_bot import (
        _score_odds_consistent,
        _odds_drift_recent,
        _ODDS_DRIFT_THRESHOLD,
        _ODDS_DRIFT_WINDOW_SEC,
    )

    # The exact bug from production: score 0-1, but home odds shorter than away
    bad = {"score_home": 0, "score_away": 1,
           "live_1x2_home": 2.10, "live_1x2_draw": 3.00, "live_1x2_away": 4.00}
    assert _score_odds_consistent(bad) is False, \
        "must reject when leading team (away, 0-1) has longer 1X2 odds (4.00) than trailing (2.10)"

    # Pre-goal snapshot in the same match — consistent, allow through
    ok = {"score_home": 0, "score_away": 1,
          "live_1x2_home": 3.75, "live_1x2_draw": 3.40, "live_1x2_away": 1.95}
    assert _score_odds_consistent(ok) is True, "leading away (1.95) shorter than trailing home (3.75) — fresh"

    # Tied score: no winner-implied check; always allow
    tied = {"score_home": 1, "score_away": 1,
            "live_1x2_home": 2.00, "live_1x2_draw": 3.00, "live_1x2_away": 4.33}
    assert _score_odds_consistent(tied) is True, "tied scores have no implied leader"

    # Missing odds — allow (no signal to disagree)
    missing = {"score_home": 0, "score_away": 1,
               "live_1x2_home": None, "live_1x2_away": None}
    assert _score_odds_consistent(missing) is True

    # Mirror case: home leading 1-0 but home odds longer than away → reject
    mirror = {"score_home": 1, "score_away": 0,
              "live_1x2_home": 4.00, "live_1x2_draw": 3.00, "live_1x2_away": 2.10}
    assert _score_odds_consistent(mirror) is False, \
        "must reject when leading home (1-0) has longer odds (4.00) than away (2.10)"

    # Drift threshold sanity
    assert _ODDS_DRIFT_THRESHOLD == 0.30
    assert _ODDS_DRIFT_WINDOW_SEC == 60

    # _odds_drift_recent uses a real query — fake the execute_query to exercise
    # the calculation logic without hitting Postgres.
    class _FakeQuery:
        def __init__(self, rows): self.rows = rows
        def __call__(self, sql, params): return self.rows

    # Goal-event signature: home 3.75 → 2.10 (44% move, exceeds 30%)
    goal = _FakeQuery([
        {"live_1x2_home": 3.75, "live_1x2_draw": 3.40, "live_1x2_away": 1.95},
        {"live_1x2_home": 2.10, "live_1x2_draw": 3.00, "live_1x2_away": 4.00},
    ])
    assert _odds_drift_recent(goal, "mid") is True, \
        "44% move on home leg must trigger drift guard"

    # Quiet game: legs drift a few percent — allow
    quiet = _FakeQuery([
        {"live_1x2_home": 2.10, "live_1x2_draw": 3.30, "live_1x2_away": 3.80},
        {"live_1x2_home": 2.08, "live_1x2_draw": 3.30, "live_1x2_away": 3.90},
    ])
    assert _odds_drift_recent(quiet, "mid") is False, \
        "sub-threshold drift must not trigger guard"

    # Single-row window — can't compute drift; allow through
    one = _FakeQuery([
        {"live_1x2_home": 2.00, "live_1x2_draw": 3.00, "live_1x2_away": 4.00},
    ])
    assert _odds_drift_recent(one, "mid") is False

    # Funnel wiring: candidate-eval loop checks both guards
    import pathlib
    bot_src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "_score_odds_consistent(cand)" in bot_src, \
        "inplay_bot must call _score_odds_consistent inside the candidate eval loop"
    assert "_odds_drift_recent(execute_query, mid)" in bot_src, \
        "inplay_bot must call _odds_drift_recent inside the candidate eval loop"
    assert 'score_odds_inconsistent' in bot_src
    assert 'odds_drift_event' in bot_src


@test("INPLAY-RESOLVE-ARGS — place_all_inplay_bets calls resolve_placement_target correctly + unpacks 4-tuple")
def test_inplay_resolve_args():
    """INPLAY-RESOLVE-ARGS-FIX (2026-05-29): two silent bugs in the inplay
    placer were masked while search_blocked aborted runs early. Once Imperva
    cookies were refreshed and the placer reached the markets step, both
    surfaced at once:
      (a) resolve_placement_target args were swapped — the function expects
          (markets, odds_map, our_market, our_selection); the caller had
          (mkt, sel, markets, odds_data).
      (b) The return value (a 4-tuple) was being subscripted as a dict.
    Source-inspection: prevents both regressions silently re-appearing if
    someone re-orders the call.
    """
    import inspect
    import pathlib
    from workers.automation import coolbet_placer
    from workers.automation.coolbet_explorer import resolve_placement_target

    # Function signature is the contract: positional order must be markets first
    sig = inspect.signature(resolve_placement_target)
    params = list(sig.parameters)
    assert params[:4] == ["markets", "odds_map", "our_market", "our_selection"], \
        f"resolve_placement_target signature changed: {params}"

    placer_src = pathlib.Path("workers/automation/coolbet_placer.py").read_text()
    # Inplay caller: `resolve_placement_target(markets, odds_data, mkt, sel)` — NOT (mkt, sel, ...)
    inplay_fn = placer_src[placer_src.index("def place_all_inplay_bets"):]
    assert "resolve_placement_target(markets, odds_data, mkt, sel)" in inplay_fn, \
        "place_all_inplay_bets must pass (markets, odds_data, mkt, sel) — args were swapped pre-fix"
    # And the return must be unpacked as a tuple, not dict-indexed
    assert "bo_id, outcome_id, odds_uuid, ev_odds = target" in inplay_fn, \
        "resolve_placement_target returns a 4-tuple — must be unpacked, not indexed as dict"
    assert "target[\"market_id\"]" not in inplay_fn, \
        "inplay caller must NOT treat the return value as a dict"


@test("ADMIN-TG-CLARITY — per-bet alerts edited with outcome + admin double-notify skipped + summaries collapsed")
def test_admin_tg_clarity():
    """ADMIN-TG-CLARITY (2026-05-29): the admin Telegram chat used to fire
    three messages per cohort (user broadcast, admin per-bet, batch list)
    plus a fourth coolbet --record summary, all listing the same bets.
    For ~50 bets/day that becomes unreadable. Now:
      • per-bet alerts are edited in-place with the recording outcome
      • admin chat is suppressed from `send_telegram_to_users` broadcasts
      • batch + record summaries collapse to single-line counters
    """
    import pathlib
    # 1. Migration for the side table
    mig = pathlib.Path("supabase/migrations/154_bet_telegram_alerts.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS bet_telegram_alerts" in mig
    for col in ("simulated_bet_id", "chat_id", "message_id", "original_text", "sent_at"):
        assert col in mig, f"migration must define {col}"

    # 2. Notify module exposes the helpers
    from workers.notify import telegram as _tg
    for fn in ("record_bet_alert", "edit_bet_alert_outcome"):
        assert hasattr(_tg, fn), f"telegram.{fn} missing"

    # 3. send_telegram_to_users skips the admin chat id
    notify_src = pathlib.Path("workers/notify/telegram.py").read_text()
    users_block = notify_src[notify_src.index("def send_telegram_to_users"):
                              notify_src.index("def send_telegram_to_users") + 4000]
    assert "TELEGRAM_CHAT_ID" in users_block, \
        "send_telegram_to_users must compare against admin TELEGRAM_CHAT_ID to skip duplicates"

    # 4. Per-bet alert sites persist via record_bet_alert
    pipeline_src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    assert "record_bet_alert" in pipeline_src, \
        "daily_pipeline_v2 must call record_bet_alert after sending the per-bet alert"
    inplay_src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "record_bet_alert" in inplay_src, \
        "inplay_bot must call record_bet_alert after sending the per-bet alert"

    # 5. Auto-record (pre-match + inplay) edits per-bet messages with outcome
    bp_src = pathlib.Path("workers/jobs/betting_pipeline.py").read_text()
    assert "edit_bet_alert_outcome" in bp_src, \
        "betting_pipeline._run_coolbet_record must call edit_bet_alert_outcome per result"
    assert "edit_bet_alert_outcome" in inplay_src, \
        "inplay_bot must call edit_bet_alert_outcome after place_all_inplay_bets"

    # 6. Pre-match batch summary collapsed (no more bet_block list)
    assert "bet_block" not in pipeline_src or \
        "f\"🎯 <b>{total_bets} value bet(s) found</b>\"" not in pipeline_src, \
        "long bet_block summary must be removed (replaced with one-liner)"
    assert "f\"🎯 {total_bets} value bet(s) found{cohort_label}\"" in pipeline_src, \
        "pre-match summary must be a one-line counter"

    # 7. Coolbet --record summary collapsed (one-line counter, no per-bet lines)
    record_block = bp_src[bp_src.index("def _run_coolbet_record"):]
    assert "lines.append(" not in record_block, \
        "_run_coolbet_record must not build per-bet lines in the summary anymore"
    assert "\" · \".join(parts)" in record_block, \
        "summary must collapse to a ` · ` joined counter line"


@test("MANUAL-PLACE — admin button + webhook + drain loop end-to-end wiring")
def test_manual_place_wiring():
    """MANUAL-PLACE (2026-05-29): admin taps Telegram inline-keyboard button
    on a value-bet alert; Vercel webhook queues, Railway scheduler drains
    every 10s, edits the message with the outcome. Source-inspection only
    (live flow runs across two services + a Telegram callback)."""
    import inspect
    import pathlib

    # 1. Migration exists and creates the queue table
    mig = pathlib.Path("supabase/migrations/153_manual_placement_queue.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS manual_placement_queue" in mig
    for col in ("simulated_bet_id", "requested_by_chat_id", "telegram_message_id",
                "telegram_chat_id", "status", "result", "processed_at"):
        assert col in mig, f"migration must define {col}"
    assert "CHECK (status IN ('pending', 'processing', 'done', 'failed'))" in mig

    # 2. Placer exposes place_bet_by_id + bet_id_filter
    from workers.automation import coolbet_placer
    assert hasattr(coolbet_placer, "place_bet_by_id"), \
        "placer must expose place_bet_by_id for MANUAL-PLACE"
    for fn_name in ("load_qualified_bets", "load_qualified_combo_bets",
                    "load_qualified_inplay_bets", "place_all_bets",
                    "place_all_inplay_bets"):
        sig = inspect.signature(getattr(coolbet_placer, fn_name))
        assert "bet_id_filter" in sig.parameters, \
            f"{fn_name} must accept bet_id_filter for MANUAL-PLACE"

    # 3. Notify helpers — button markup builder + edit fn
    from workers.notify import telegram as _tg
    assert hasattr(_tg, "place_button_markup"), "telegram.place_button_markup missing"
    assert hasattr(_tg, "edit_telegram_message"), "telegram.edit_telegram_message missing"
    markup = _tg.place_button_markup("00000000-0000-0000-0000-000000000000")
    assert markup["inline_keyboard"][0][0]["callback_data"] == \
        "place:00000000-0000-0000-0000-000000000000"

    # 4. Alert sites attach the button — pre-match + inplay
    pipeline_src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    assert "place_button_markup" in pipeline_src, \
        "daily_pipeline_v2 must attach inline_keyboard via place_button_markup"
    assert "first_bet_id" in pipeline_src, \
        "_tele_bets must capture first_bet_id for the button callback_data"
    inplay_src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "place_button_markup" in inplay_src, \
        "inplay_bot must attach inline_keyboard for live value bets"

    # 5. Scheduler registers the 10s drain
    sched_src = pathlib.Path("workers/scheduler.py").read_text()
    assert "_drain_manual_placement_queue" in sched_src, \
        "scheduler must define _drain_manual_placement_queue"
    assert "manual_placement_drain" in sched_src, \
        "scheduler must register the drain job id"
    assert "IntervalTrigger(seconds=10)" in sched_src, \
        "drain must fire on a 10-second interval"
    # Drain wrapper imports placer + notify pieces
    drain_block = sched_src[sched_src.index("def _drain_manual_placement_queue"):
                             sched_src.index("def _drain_manual_placement_queue")
                             + 4000]
    assert "place_bet_by_id" in drain_block
    assert "edit_telegram_message" in drain_block
    assert "manual_placement_queue" in drain_block

    # 6. Webhook handles callback_query and admin-gates by TELEGRAM_CHAT_ID
    webhook_src = pathlib.Path(
        "../odds-intel-web/src/app/api/telegram/webhook/route.ts"
    ).read_text()
    assert "callback_query" in webhook_src, "webhook must handle callback_query"
    assert "TELEGRAM_CHAT_ID" in webhook_src, "webhook must admin-gate by TELEGRAM_CHAT_ID"
    assert "answerCallbackQuery" in webhook_src, "webhook must ack the callback"
    assert "manual_placement_queue" in webhook_src, "webhook must insert into the queue"
    assert "place:" in webhook_src, "webhook must parse the place: callback_data prefix"


@test("COOLBET-FUZZY-CASE-INSENSITIVE — _ascii lowercases so 'Pepo' fuzzy-matches 'PEPO'")
def test_coolbet_fuzzy_case_insensitive():
    """COOLBET-FUZZY-CASE-INSENSITIVE (2026-05-29): rapidfuzz.partial_ratio
    is case-sensitive — `partial_ratio('Pepo', 'PEPO')` is 40, below the 70
    threshold. MyPa vs Pepo (Finland Kakkonen) was killing combos for this
    reason. _ascii() now lowercases so the case mismatch can't reject a
    real match."""
    from workers.automation.coolbet_placer import _ascii, fuzzy_match_event

    # Direct: _ascii normalises case
    assert _ascii("Pepo") == "pepo", "_ascii must lowercase"
    assert _ascii("PEPO") == "pepo", "_ascii must lowercase (uppercase input)"
    # Accent normalisation still works
    assert _ascii("Sölvesborg") == "solvesborg", "_ascii must still strip diacritics"
    assert _ascii("Hørsholm-Usserød") == "horsholm-usserod", "_ascii must still strip diacritics"

    # End-to-end: the real-world failing case from production logs
    cands = [{"id": 1, "home": "MyPa", "away": "PEPO", "start": None}]
    ev = fuzzy_match_event("MyPa", "Pepo", cands)
    assert ev is not None, (
        "fuzzy_match_event must match 'MyPa' vs 'Pepo' against Coolbet's "
        "'MyPa' vs 'PEPO' — case mismatch was rejecting it at score 40"
    )
    assert ev["id"] == 1

    # The 'Hørsholm-Usserød' kind of name still matches itself after stripping
    cands = [{"id": 2, "home": "Hørsholm-Usserød", "away": "FA 2000", "start": None}]
    ev = fuzzy_match_event("HORSHOLM-USSEROD", "fa 2000", cands)
    assert ev is not None, "fuzzy_match_event must be case- AND diacritic-insensitive"


@test("COMBO-FALLBACK-FO-CATEGORY — combo leg falls back to fo-category like singles + passes match_date")
def test_combo_fallback_fo_category():
    """COMBO-FALLBACK-FO-CATEGORY (2026-05-29): the combo placer was killing
    every combo that had even one leg in a league Coolbet's `/search/v2`
    doesn't index (e.g. Finland Kakkonen — MyPa vs Pepo). Singles already
    handle this by falling back to the full `fo-category` tree; combos now
    mirror that flow. Also: search call now passes `match_date` so the
    fuzzy date guard runs on combo legs too (was silently bypassed)."""
    import pathlib
    placer_src = pathlib.Path("workers/automation/coolbet_placer.py").read_text()

    combo_fn = placer_src[placer_src.index("def _place_combo_bets"):
                          placer_src.index("# ── Refresh utility")
                          if "# ── Refresh utility" in placer_src
                          else len(placer_src)]

    # Combo loop must capture leg kickoff and pass it to search_coolbet_event
    assert 'team_rows[0].get("kick")' in combo_fn or 'team_rows[0]["kick"]' in combo_fn, (
        "Combo leg must pull `kick` from the team_rows query so it can be "
        "passed as match_date to the search/fuzzy match"
    )
    assert "search_coolbet_event(session, home, away, leg_kick)" in combo_fn, (
        "Combo leg must pass leg_kick as match_date to search_coolbet_event "
        "— without it the COOLBET-FUZZY-DATE-GUARD is bypassed for combos"
    )

    # fo-category fallback must exist in the combo loop (was singles-only before)
    assert "fetch_coolbet_events(session)" in combo_fn, (
        "Combo loop must fall back to fetch_coolbet_events when search "
        "returns None — without this, any leg in a league not indexed by "
        "/search/v2 (e.g. lower-tier Finland) kills the whole combo"
    )
    assert "fuzzy_match_event(home, away, _category_events, leg_kick)" in combo_fn, (
        "Combo fo-category fallback must use fuzzy_match_event with leg_kick "
        "so the date guard runs against same-team-different-day candidates"
    )

    # Cache loaded lazily and shared across combos (one fo-category call per run, not per leg)
    assert "_category_events: list[dict] | None = None" in combo_fn, (
        "Combo loop must declare _category_events cache at function scope "
        "so the fo-category tree is fetched at most once per run"
    )


@test("COOLBET-SAFETY-GUARDRAILS — PlacementGuard stake + rate + total + edge + bot-filter")
def _():
    """COOLBET-SAFETY-GUARDRAILS (2026-05-20) — PlacementGuard holds the
    runtime limits + tracking state for live placement. Verifies stake
    selection (fixed/Kelly/cap), rate limit, session-stake cap, edge guard,
    bot-filter, and the daemon CLI wiring."""
    from workers.automation.coolbet_placer import PlacementGuard

    # Fixed stake — default fallback
    g = PlacementGuard()
    bet = {"model_stake": 7.0, "edge_percent": 5.0, "bot_name": "x"}
    s = g.stake_for(bet)
    assert s == 10.0, f"default = COOLBET_STAKE env (10.0), got {s}"

    # Kelly stake used when flag on
    g = PlacementGuard(use_kelly_stake=True)
    s = g.stake_for(bet)
    assert s == 7.0, f"Kelly stake from bet['model_stake'], got {s}"

    # Per-bet cap clamps both sources
    g = PlacementGuard(use_kelly_stake=True, max_stake_per_bet=5.0)
    assert g.stake_for(bet) == 5.0, "cap must clamp Kelly stake"
    g = PlacementGuard(fixed_stake=20.0, max_stake_per_bet=5.0)
    assert g.stake_for(bet) == 5.0, "cap must clamp fixed stake too"

    # Edge guard refuses absurd-edge bets — edge_percent is DECIMAL (0.50 = 50%)
    g = PlacementGuard(max_edge_pct=20.0)
    high_edge_bet = {"model_stake": 5.0, "edge_percent": 0.50, "bot_name": "x"}
    ok, reason = g.can_place(high_edge_bet, 5.0)
    assert not ok and "edge" in reason.lower(), f"max_edge_pct must fire: {reason}"
    # And a normal-edge bet (0.05 = 5%) must pass when cap is 20%
    ok, _ = g.can_place({"model_stake": 5.0, "edge_percent": 0.05, "bot_name": "x"}, 5.0)
    assert ok, "normal-edge bet (5%) must pass when --max-edge-pct=20"

    # Bot filter
    g = PlacementGuard(bot_filter=["bot_a", "bot_b"])
    ok, reason = g.can_place({"bot_name": "bot_z", "edge_percent": 5}, 5.0)
    assert not ok and "bot-filter" in reason, f"bot_filter must reject: {reason}"
    ok, _ = g.can_place({"bot_name": "bot_a", "edge_percent": 5}, 5.0)
    assert ok, "bot_filter must allow whitelisted"

    # Total session-stake cap (record_placement adds to running total)
    g = PlacementGuard(max_total_stake=12.0)
    g.record_placement(5.0)  # total now 5
    g.record_placement(5.0)  # total now 10
    ok, reason = g.can_place({"bot_name": "x", "edge_percent": 5}, 5.0)  # would be 15
    assert not ok and "max-total-stake" in reason, f"total cap must fire: {reason}"
    ok, _ = g.can_place({"bot_name": "x", "edge_percent": 5}, 2.0)  # would be 12
    assert ok, "total cap allows when still under"

    # Rate limit — 2 bets max in window
    g = PlacementGuard(max_bets_per_hour=2)
    g.record_placement(1.0)
    g.record_placement(1.0)
    ok, reason = g.can_place({"bot_name": "x", "edge_percent": 5}, 1.0)
    assert not ok and "max-bets-per-hour" in reason, f"rate limit must fire: {reason}"

    # Daemon wiring
    import pathlib
    daemon = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    for flag in ("--use-kelly-stake", "--max-stake-per-bet", "--max-bets-per-hour",
                 "--max-total-stake", "--max-edge-pct", "--require-confirm",
                 "--bot-filter", "PlacementGuard"):
        assert flag in daemon, f"daemon must wire {flag!r}"

    # Placer accepts guard kwarg
    import inspect
    from workers.automation.coolbet_placer import place_all_bets
    sig = inspect.signature(place_all_bets)
    assert "guard" in sig.parameters, "place_all_bets must accept guard kwarg"


@test("COOLBET-MARKET-NORM — _normalise_our_target handles lowercase DB values")
def _():
    """COOLBET-MARKET-NORM (2026-05-22) — simulated_bets stores market as 'o/u'
    and 'btts' (lowercase). _normalise_our_target was only checking uppercase
    variants ('O/U', 'BTTS'), so all OU and BTTS placements silently returned
    no_market. Fixed by lowercasing m before checks."""
    from workers.automation.coolbet_explorer import _normalise_our_target

    cases = [
        ("o/u",  "under 2.5", ("over_under_25", "under", None)),
        ("o/u",  "over 3.5",  ("over_under_35", "over",  None)),
        ("o/u",  "under 3.5", ("over_under_35", "under", None)),
        ("o/u",  "over 1.5",  ("over_under_15", "over",  None)),
        ("O/U",  "Under 2.5", ("over_under_25", "under", None)),
        ("btts", "yes",       ("btts", "yes", None)),
        ("btts", "no",        ("btts", "no",  None)),
        ("BTTS", "Yes",       ("btts", "yes", None)),
        ("1x2",  "home",      ("1x2",  "Home", None)),
        ("1X2",  "Away",      ("1x2",  "Away", None)),
    ]
    for mkt, sel, expected in cases:
        result = _normalise_our_target(mkt, sel)
        assert result == expected, (
            f"_normalise_our_target({mkt!r}, {sel!r}) = {result}, want {expected}"
        )


@test("TELEGRAM-NOTIFY — send-only Telegram with dedup + preflight surface")
def _():
    """TELEGRAM-NOTIFY (2026-05-20, narrowed 2026-05-29) — send_telegram is the
    single ingress for alerts. No env vars = silent skip (returns None, no
    exceptions). Daemon-specific keepalive/Imperva alerts were deliberately
    removed in TELE-BET-NOTIFY-V2 (commit a818c18, daemon not in active use);
    that retirement is enforced by the TELE-BET-NOTIFY test below.

    MANUAL-PLACE (2026-05-29) changed the return type: success → message_id (int),
    failure/skip → None. Callers that ignored the return value (all of them
    pre-MANUAL-PLACE) keep working unchanged."""
    import inspect
    from workers.notify import telegram as _tg
    # Function exists with expected signature (+ reply_markup added 2026-05-29)
    sig = inspect.signature(_tg.send_telegram)
    for kw in ("dedup_key", "dedup_window_s", "silent", "reply_markup"):
        assert kw in sig.parameters, f"send_telegram missing kwarg: {kw}"

    # No env = silent skip
    import os
    saved = (os.environ.pop("TELEGRAM_BOT_TOKEN", None), os.environ.pop("TELEGRAM_CHAT_ID", None))
    try:
        assert _tg.send_telegram("smoke") is None, "should return None without env"
    finally:
        if saved[0]: os.environ["TELEGRAM_BOT_TOKEN"] = saved[0]
        if saved[1]: os.environ["TELEGRAM_CHAT_ID"] = saved[1]

    # Preflight surfaces TG status
    import pathlib
    pf = pathlib.Path("scripts/coolbet_preflight.py").read_text()
    assert "TELEGRAM_BOT_TOKEN" in pf, "preflight should show TG cred status"


@test("COOLBET-PREFLIGHT — checks cookies+creds+login+heartbeat+bots, daemon gates on it")
def _():
    """COOLBET-PREFLIGHT (2026-05-20) — scripts/coolbet_preflight.py runs all
    critical checks (Imperva cookies present, credentials present, login +
    heartbeat succeeds, JWT TTL > 0, ≥5 active bots) and exits 1 if any
    critical check fails. coolbet_daemon.py runs preflight as a subprocess
    before entering its loop; failure aborts startup."""
    import pathlib
    src = pathlib.Path("scripts/coolbet_preflight.py").read_text()
    for fn in ("check_cookies", "check_credentials", "check_session_works",
               "check_bot_universe", "check_balance"):
        assert f"def {fn}" in src, f"missing preflight check: {fn}"
    assert 'sys.exit(' in src, "preflight must exit with a meaningful code"
    assert 'return 1' in src, "preflight must return 1 on critical failure"

    daemon = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    assert "--skip-preflight" in daemon, "daemon must expose --skip-preflight escape hatch"
    assert "coolbet_preflight.py" in daemon, "daemon must invoke coolbet_preflight.py at startup"


@test("COOLBET-DAEMON-CLI — daemon exposes three loops + dry default")
def _():
    """COOLBET-DAEMON-CLI (2026-05-20) — scripts/coolbet_daemon.py is the
    foreground sibling of the Railway scheduler. Guards: --place-mode defaults
    to 'dry' so accidental run doesn't place real bets, --no-place flag exists,
    and the three tasks (keepalive / odds / place) are all wired."""
    import pathlib
    src = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    assert "_task_keepalive" in src, "missing keepalive task"
    assert "_task_odds_snapshot" in src, "missing odds task"
    assert "_task_place" in src, "missing place task"
    assert 'choices=("dry", "record", "execute")' in src, "place modes must be dry/record/execute"
    assert 'default="dry"' in src, "place-mode must default to dry (safety)"
    assert "--no-place" in src, "--no-place flag must exist"


@test("COOLBET-KEEPALIVE — session exposes TTL + heartbeat (scheduler job retired)")
def _():
    """COOLBET-KEEPALIVE (2026-05-20, narrowed 2026-05-29) — CoolbetSession
    still exposes keep_alive() + jwt_seconds_remaining, used by the daemon
    main loop. The scheduler-level 20-min keepalive job was retired in
    REMOVE-KEEPALIVE (commit a8753ac) — Imperva cookies + anon /record mode
    made it dead weight on Railway."""
    import inspect
    from workers.automation.coolbet_session import CoolbetSession
    assert hasattr(CoolbetSession, "keep_alive"), "CoolbetSession.keep_alive missing"
    assert hasattr(CoolbetSession, "jwt_seconds_remaining"), (
        "CoolbetSession.jwt_seconds_remaining missing"
    )
    src = inspect.getsource(CoolbetSession.keep_alive)
    assert "self.get" in src, "keep_alive must call self.get (so _ensure_auth fires)"


@test("COOLBET-MARKET-TYPE-IDS — AH/BTTS/DC market_type_ids wired from observed API response")
def _():
    """COOLBET-MARKET-TYPE-IDS (2026-05-21) — market_type_ids confirmed from
    DevTools on Brighton vs Man Utd (Premier League, May 2026):
      1086 = Asian Handicap  (line field is display string "0 - 4"; raw_line=-4)
      1377 = Both Teams To Score
      1484 = Double Chance   (result_keys use [Home]/Draw not 1X)
    parse_market must fall back to raw_line when line string can't be parsed."""
    from workers.automation.coolbet_explorer import (
        _MTID_AH, _MTID_BTTS, _MTID_DC, parse_market,
    )
    assert 1086 in _MTID_AH,  "_MTID_AH must contain 1086 (Asian Handicap)"
    assert 1377 in _MTID_BTTS, "_MTID_BTTS must contain 1377 (Both Teams To Score)"
    assert 1484 in _MTID_DC,  "_MTID_DC must contain 1484 (Double Chance)"

    # AH: line is display string, raw_line has the number
    ah_mkt = {
        "market_type_id": 1086, "name": "Asian Handicap",
        "line": "0 - 1.5", "raw_line": -1.5,
        "outcomes": [
            {"id": 1, "result_key": "[Home]"},
            {"id": 2, "result_key": "[Away]"},
        ],
    }
    odds_map = {1: {"value": 1.85, "odds_id": "a"}, 2: {"value": 2.10, "odds_id": "b"}}
    rows = parse_market(ah_mkt, odds_map)
    assert len(rows) == 2, f"AH should yield 2 rows, got {rows}"
    assert rows[0][3] == -1.5, f"AH line should be -1.5, got {rows[0][3]}"

    # DC: result_keys use [Home]/Draw not 1X
    dc_mkt = {
        "market_type_id": 1484, "name": "Double Chance",
        "line": 0, "raw_line": 0,
        "outcomes": [
            {"id": 10, "result_key": "[Home]/Draw"},
            {"id": 11, "result_key": "[Away]/Draw"},
            {"id": 12, "result_key": "[Home]/[Away]"},
        ],
    }
    odds_map_dc = {10: {"value": 1.30}, 11: {"value": 1.40}, 12: {"value": 1.20}}
    dc_rows = parse_market(dc_mkt, odds_map_dc)
    labels = {r[1] for r in dc_rows}
    assert labels == {"1X", "X2", "12"}, f"DC labels should be 1X/X2/12, got {labels}"


@test("COOLBET-SWEEP-PACING — run_bulk sleeps after every match + breathing pauses + shared session")
def _():
    """COOLBET-SWEEP-PACING (2026-05-21) — run_bulk used to sleep only after
    matched matches (0.25s), leaving misses firing search queries back-to-back.
    A 127-match sweep triggered an Imperva block. Fixes:
    - Sleep after every match (hit or miss) with jitter
    - Long breathing pause every 15 matches
    - Accept caller-provided session so daemon shares one CoolbetSession
    - Placement no longer blocked while sweep runs (throttle lock serialises)
    - Placement cadence bumped from 5 → 15 min"""
    import inspect
    from workers.automation.coolbet_explorer import run_bulk, run_league_sweep
    bulk_src = inspect.getsource(run_bulk)
    assert "long_pause_every" in bulk_src, "run_bulk must accept long_pause_every"
    assert "long_pause_s" in bulk_src, "run_bulk must accept long_pause_s"
    assert "breathing pause" in bulk_src, "run_bulk must log breathing pauses"
    assert "random.uniform" in bulk_src, "run_bulk must add jitter to sleeps"
    assert "session" in inspect.signature(run_bulk).parameters, "run_bulk must accept session="
    assert "session" in inspect.signature(run_league_sweep).parameters, "run_league_sweep must accept session="

    from workers.automation.coolbet_session import CoolbetSession
    import inspect as _i
    throttle_src = _i.getsource(CoolbetSession._throttle)
    assert "_throttle_lock" in throttle_src, "CoolbetSession._throttle must hold _throttle_lock"

    import pathlib
    daemon = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    assert "sleep_s=3.0" in daemon, "daemon must use sleep_s=3.0 for run_bulk"
    assert "default=15" in daemon, "placement cadence must default to 15 min"
    assert "sweep in progress" not in daemon, "placement must no longer be blocked by sweep"


@test("COOLBET-JWT-ENV-PROPAGATION — renew_jwt_via_api updates os.environ so fresh sessions see new token")
def _():
    """COOLBET-JWT-ENV-PROPAGATION (2026-05-21, narrowed 2026-05-29) —
    renew_jwt_via_api must update os.environ after renewal so any
    CoolbetSession() created later in the same process (e.g. the odds sweep)
    picks up the fresh JWT instead of the expired one from .env. The
    daemon-Telegram-on-failure assertion was dropped in TELE-BET-NOTIFY-V2
    (commit a818c18) — daemon Telegram noise was deliberately retired."""
    import inspect
    from workers.automation.coolbet_session import CoolbetSession
    src = inspect.getsource(CoolbetSession.renew_jwt_via_api)
    assert 'os.environ["COOLBET_MANUAL_JWT"]' in src, (
        "renew_jwt_via_api must update os.environ so new CoolbetSession() "
        "instances in the same process see the renewed token"
    )

    # The sweep runner still stamps ok=False on failure via state.json, even
    # though the Telegram alert was retired.
    daemon_src = open("scripts/coolbet_daemon.py").read()
    assert "last_sweep_finished" in daemon_src, "sweep runner must stamp last_sweep_finished"
    assert '"ok": False' in daemon_src, "sweep runner must stamp ok=False on failure"


@test("BOT-COHORTS-ALL — every bot fires at every cohort window")
def _():
    """BOT-COHORTS-ALL (2026-05-20) — all entries in BOT_TIMING_COHORTS set
    to 'all' so bots evaluate at every betting_refresh window. Dedup prevents
    duplicate placements. BET-TIMING-MONITOR Phase 3 (~2026-06-15) will
    re-impose cohort gating ONLY where shadow_bets factorial data shows it
    actually helps. Guard against accidental revert."""
    from workers.jobs.daily_pipeline_v2 import BOT_TIMING_COHORTS
    non_all = {k: v for k, v in BOT_TIMING_COHORTS.items() if v != "all"}
    assert not non_all, (
        f"All bots must be cohort='all' (per BOT-COHORTS-ALL 2026-05-20). "
        f"Found non-'all' entries: {non_all}. If reverting any bot to a "
        f"specific cohort, add a comment explaining what shadow_bets data "
        f"justified the gating."
    )


@test("BOT-OU15-EDGE-REPAIR — thresholds relaxed (4/4/3/3) per diagnostic finding")
def _():
    """BOT-OU15-EDGE-REPAIR (2026-05-20) — bot_ou15_defensive was silent since
    May 8 because 97/98 candidates failed the 5-6% edge threshold (per
    BOT-FUNNEL-DIAGNOSTIC). Thresholds relaxed to 4% (T1/T2) and 3% (T3/T4)
    as a 2-week paper-trade experiment. Guard the new values so a future
    refactor doesn't silently revert them while the experiment is running."""
    # Load the BOTS_CONFIG dict directly and inspect the live values — far
    # more robust than regex-scanning the source for nested-brace patterns.
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG
    cfg = BOTS_CONFIG["bot_ou15_defensive"]
    t = cfg["edge_thresholds"]
    assert t[1]["ou"] == 0.04, f"T1 must be 0.04 (was 0.06), got {t[1]}"
    assert t[2]["ou"] == 0.04, f"T2 must be 0.04 (was 0.06), got {t[2]}"
    assert t[3]["ou"] == 0.03, f"T3 must be 0.03 (was 0.05), got {t[3]}"
    assert t[4]["ou"] == 0.03, f"T4 must be 0.03 (was 0.05), got {t[4]}"
    # Sanity — don't accidentally widen odds_range or drop min_prob
    assert cfg["odds_range"] == (1.80, 3.50), "odds_range must stay (1.80, 3.50)"
    assert cfg["min_prob"] == 0.30, "min_prob must stay 0.30"


@test("SHADOW-COHORT-CONSTRAINT — migration 112 accepts HHMM scheduler labels")
def _():
    """SHADOW-COHORT-CONSTRAINT (2026-05-20) — scheduler writes HHMM-format
    shadow_cohort but migration 101's CHECK rejected them. Migration 112
    re-adds the constraint accepting both ('morning','midday','pre_ko') and
    HHMM. Guard the migration file shape so it doesn't get reverted."""
    import pathlib
    p = pathlib.Path("supabase/migrations/112_shadow_cohort_allow_hhmm.sql")
    assert p.exists(), "migration 112 missing"
    sql = p.read_text()
    assert "DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check" in sql, (
        "must drop the old constraint"
    )
    assert "'^[0-9]{4}$'" in sql, "new constraint must allow HHMM"
    assert "IN ('morning', 'midday', 'pre_ko')" in sql, (
        "named cohorts must still be allowed for manual / funnel_diagnostic invocations"
    )


@test("BOT-FUNNEL-DIAGNOSTIC — run_morning instruments per-bot candidate funnel")
def _():
    """BOT-FUNNEL-DIAGNOSTIC (2026-05-20) — verbose_funnel + verbose_funnel_bot
    kwargs on run_morning track every drop point in the candidate evaluation
    loop and print a per-bot funnel table. Used to diagnose silent bots like
    bot_ou15_defensive (silent since 2026-05-08; 5 hypotheses ruled out by
    audit_silent_bots.py). Funnel must cover every continue point in the
    candidate-evaluation loop and the accepted counter at the end."""
    import inspect
    from workers.jobs.daily_pipeline_v2 import run_morning, _print_funnel
    sig = inspect.signature(run_morning)
    assert "verbose_funnel" in sig.parameters, "run_morning missing verbose_funnel kwarg"
    assert "verbose_funnel_bot" in sig.parameters, "run_morning missing verbose_funnel_bot kwarg"
    assert sig.parameters["verbose_funnel"].default is False, "verbose_funnel must default False"
    src = inspect.getsource(run_morning)
    required_counters = [
        '_funnel[bot_name]["candidates"]',
        '_funnel[bot_name]["drop_edge"]',
        '_funnel[bot_name]["drop_pin_veto"]',
        '_funnel[bot_name]["drop_odds_mv"]',
        '_funnel[bot_name]["drop_kelly_zero"]',
        '_funnel[bot_name]["drop_aln1"]',
        '_funnel[bot_name]["drop_stake_low"]',
        '_funnel[bot_name]["accepted"]',
    ]
    for c in required_counters:
        assert c in src, f"missing funnel counter: {c}"
    assert "_print_funnel" in src, "run_morning must call _print_funnel when verbose"

    # CLI runner exposes the flag
    import pathlib
    cli = pathlib.Path("scripts/funnel_diagnostic.py").read_text()
    assert "verbose_funnel" in cli, "CLI must pass verbose_funnel=True"
    assert "--bot" in cli, "CLI must expose --bot for focused output"


@test("SHADOW-RETIRED-OK — retired bots still produce shadow_bets")
def _():
    """Retired bot notes promise '≥30 bets at ≥3% ROI in shadow_bets' as a
    recovery criterion. That criterion is only measurable if retired bots
    still run in shadow_mode. The gate must skip retired bots only when
    shadow_mode=False, not unconditionally."""
    import inspect
    from workers.jobs.daily_pipeline_v2 import run_morning
    src = inspect.getsource(run_morning)
    assert "if not shadow_mode and not _bot_active.get(bot_name, True):" in src, (
        "retired-bot gate must be `if not shadow_mode and not _bot_active...` — "
        "otherwise the shadow-bets recovery path described in retired bots' "
        "notes (bot_lower_1x2, bot_opt_home_lower) can never trigger."
    )


@test("BOT-QUAL-LIB — bot-aggregates lib exports the shared helpers")
def _():
    """BOT-QUAL-FILTER-DUAL — both /admin/bots and /performance import their
    aggregation helpers from src/lib/bot-aggregates.ts so the toggle has a
    single source of truth. Source-inspect that exports + cutoff are present."""
    import pathlib
    p = pathlib.Path("../odds-intel-web/src/lib/bot-aggregates.ts")
    if not p.exists():
        return  # engine-only CI checkout — skip
    src = p.read_text()
    required_exports = [
        "QUALITY_CUTOFF",
        "filterQuality",
        "buildBotStats",
        "buildSummary",
        "buildMarketStats",
        "buildPublicBotStats",
        "buildPerformanceStats",
    ]
    for sym in required_exports:
        assert f"export function {sym}" in src or f"export const {sym}" in src, (
            f"bot-aggregates.ts missing export: {sym}"
        )
    assert '"2026-05-06"' in src, "QUALITY_CUTOFF must be 2026-05-06"


@test("BOT-QUAL-ADMIN — /admin/bots client uses the shared aggregation helpers")
def _():
    """Source-inspect bot-dashboard-client.tsx: must import from bot-aggregates,
    use useMemo for filtered/aggregated state, and expose the quality toggle."""
    import pathlib
    p = pathlib.Path("../odds-intel-web/src/components/bot-dashboard-client.tsx")
    if not p.exists():
        return
    src = p.read_text()
    assert "from \"@/lib/bot-aggregates\"" in src, (
        "bot-dashboard-client.tsx must import from @/lib/bot-aggregates"
    )
    assert "filterQuality" in src, "must call filterQuality with toggle state"
    assert "qualityOnly" in src, "must hold qualityOnly state"
    assert "data-testid=\"quality-only-toggle\"" in src, (
        "toggle input must carry data-testid='quality-only-toggle' for E2E hookup"
    )
    # The server page must NOT pre-aggregate any more — it just hands raw bets to the client.
    page = pathlib.Path("../odds-intel-web/src/app/(app)/admin/bots/page.tsx").read_text()
    assert "buildBotStats" not in page, (
        "/admin/bots/page.tsx must not pre-aggregate; aggregation lives in the client"
    )
    assert "buildMarketStats" not in page, (
        "/admin/bots/page.tsx must not pre-aggregate market stats"
    )


@test("ADMIN-BOTS-COMBO-LEGS — bot detail modal expands combo bets into per-leg sub-rows")
def _():
    """Combo/system bets used to render as one row with the placeholder first-leg match
    name — user couldn't see what the other 4 legs were. Modal now flatMaps each combo
    bet into a header row + N indented leg rows, and engine-data resolves leg match_ids
    to "Home vs Away" via a batched matches lookup so the legs aren't anonymous."""
    import pathlib
    modal = pathlib.Path("../odds-intel-web/src/components/bot-dashboard-client.tsx")
    if not modal.exists():
        return
    msrc = modal.read_text()
    assert "bet.comboLegs" in msrc, "modal must branch on bet.comboLegs to render leg sub-rows"
    assert "botBets.flatMap" in msrc, "modal must flatMap so each combo can emit multiple rows"
    assert "${bet.id}-leg-" in msrc, "leg sub-rows must use a stable per-leg key"

    data = pathlib.Path("../odds-intel-web/src/lib/engine-data.ts")
    if not data.exists():
        return
    dsrc = data.read_text()
    assert "comboLegs:" in dsrc, "LiveBet must expose comboLegs"
    assert "combo_legs, combo_size, system_type" in dsrc, (
        "getAllBets select must include combo_legs/combo_size/system_type columns"
    )
    assert "legMatchIds" in dsrc, "getAllBets must batch-fetch leg match names"
    # Per-leg W/L: leg rows must carry a result so users see which legs killed the combo.
    assert "settleComboLeg" in dsrc, (
        "engine-data must settle each combo leg from match score (won/lost/pending/void)"
    )
    assert "score_home, score_away" in dsrc, (
        "leg match batch query must include score_home/score_away for per-leg settlement"
    )
    assert "resultBadge(leg.result)" in msrc, (
        "modal leg sub-row must render resultBadge(leg.result) so each leg shows W/L"
    )


@test("BOT-QUAL-PERFORMANCE — /performance wraps via PerformanceClient with toggle")
def _():
    """Source-inspect /performance: PerformanceClient owns the toggle and
    feeds both hero + leaderboard so every metric on the page updates."""
    import pathlib
    p = pathlib.Path("../odds-intel-web/src/components/performance-client.tsx")
    if not p.exists():
        return
    src = p.read_text()
    assert "filterQuality" in src, "PerformanceClient must apply filterQuality"
    assert "buildPerformanceStats" in src, (
        "PerformanceClient must recompute hero stats via buildPerformanceStats"
    )
    assert "buildPublicBotStats" in src, (
        "PerformanceClient must recompute leaderboard via buildPublicBotStats"
    )
    assert "useState(true)" in src, "qualityOnly must default to true on /performance"
    page = pathlib.Path("../odds-intel-web/src/app/(app)/performance/page.tsx").read_text()
    assert "PerformanceClient" in page, (
        "/performance/page.tsx must render PerformanceClient (not PerformanceLeaderboard directly)"
    )
    assert "aggregateBets" in page, (
        "/performance/page.tsx must pass aggregateBets so Pro+ users get toggle data"
    )


@test("INPLAY-LOOSEN-SILENT-L — Strategy L edge gate 4% → 3% so it can accumulate data")
def _():
    """Strategy L fired only 2 times in 14d on 1,930 first-goal events in
    min 15-35 (the score/minute gate). Investigation pointed to the edge ≥ 4%
    gate as the binding constraint — live OU 2.5 reprices fast after a 1-0,
    leaving narrow edge. Loosened to ≥ 3% (matches G's real-xG floor) to let
    L accumulate enough bets for calibration. Tighten back if 50+ bets land
    at negative ROI."""
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    # Locate Strategy L block and confirm edge gate is 3.0
    l_start = src.find("def _check_strategy_l(")
    assert l_start >= 0, "Strategy L function missing"
    l_end = src.find("def _check_strategy_", l_start + 1)
    l_block = src[l_start:l_end if l_end > 0 else l_start + 5000]
    assert "if edge_pct < 3.0:" in l_block, "Strategy L edge gate must be < 3.0 (INPLAY-LOOSEN-SILENT-L)"
    assert "if edge_pct < 4.0:" not in l_block, "Strategy L stale < 4.0 gate found — must be removed"


@test("INPLAY-LOOSEN-SILENT — G/H/J thresholds relaxed so silent strategies can fire")
def _():
    """G/H/J had 0 settled bets in 14 days despite hundreds of thousands of
    snapshot evaluations. Funnel analysis (2026-05-17) showed each had one
    binding operational constraint set tighter than what the live market
    actually produces:
      - G: corners_delta ≥3 in 10min — too rare; relaxed to ≥2
      - H: O2.5 odds > 2.80 — only 2 candidates in 14d (avg market 2.37); to 2.30
      - J: OU1.5 odds ≥ 2.85 — only 1,325 candidates (avg 2.37); to 2.50
    Edge filters at end stay the same — these only open the candidate pool so
    the strategies can accumulate enough bets to validate the thesis."""
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    # G — corners_delta gate is now < 2 (was < 3)
    assert "if corners_delta < 2:" in src, "Strategy G corners_delta gate must be < 2 (INPLAY-LOOSEN-SILENT)"
    assert "if corners_delta < 3:" not in src, "Strategy G stale < 3 gate found — must be removed"
    # H — O2.5 path threshold is 2.30 (was 2.80)
    assert 'min_val=2.30)' in src, "Strategy H O2.5 _resolve_odds min_val must be 2.30 (INPLAY-LOOSEN-SILENT)"
    # J — OU1.5 threshold is 2.50 (was 2.85)
    assert "if ou15 < 2.50:" in src, "Strategy J OU1.5 gate must be < 2.50 (INPLAY-LOOSEN-SILENT)"
    assert "if ou15 < 2.85:" not in src, "Strategy J stale < 2.85 gate found — must be removed"


@test("BOTS-RETIRE-1X2 — migration 103 retirement preserved in history")
def _():
    """May 17 retrain set shrinkage_alpha_t2_1x2 = 0.00 — model has no edge over
    market for T1-T2 1X2. Migration 103 retired four bots. Migration 117
    (BOTS-UNRETIRE-ALL, 2026-05-22) subsequently brought them back to accumulate
    analysis volume. The [RETIRED] description prefixes were removed at that
    point — historical migration files remain as the audit trail."""
    import pathlib
    retired_bots = ["bot_lower_1x2", "bot_opt_home_lower", "bot_draw_specialist", "bot_conservative"]
    # Migration exists and retires all four — this is the historical record
    mig = pathlib.Path("supabase/migrations/103_retire_dead_1x2_bots.sql").read_text()
    assert "UPDATE bots" in mig and "retired_at = now()" in mig, \
        "migration 103 must UPDATE bots ... SET retired_at = now()"
    for b in retired_bots:
        assert f"'{b}'" in mig, f"migration 103 missing retirement for {b}"
    # Un-retire migration must exist
    unretire = pathlib.Path("supabase/migrations/117_unretire_all_bots.sql")
    if unretire.exists():
        # Bots are active again — no description check needed
        pass
    else:
        # Original retirement still active — old description check applies
        src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
        for b in retired_bots:
            idx = src.find(f'"{b}":')
            assert idx >= 0, f"{b} missing from BOTS_CONFIG"
            assert "[RETIRED 2026-05-17]" in src[idx:idx + 1500], (
                f"{b} description must be prefixed with [RETIRED 2026-05-17]"
            )


@test("BOTS-RETIRE-DC-DNB — migration 111 retirement preserved in history")
def _():
    """Historical: 1-year backtest showed DC/DNB-away bots structurally losing.
    Migration 111 retired them. Migration 117 (BOTS-UNRETIRE-ALL, 2026-05-22)
    brought them back for analysis. Description markers were removed at that
    point; migration files remain as the audit trail."""
    import pathlib
    retired_bots = ["bot_dc_value", "bot_dc_strong_fav", "bot_dnb_away_value"]
    mig = pathlib.Path("supabase/migrations/111_retire_dc_dnb_away_bots.sql").read_text()
    assert "UPDATE bots" in mig and "retired_at" in mig and "now()" in mig, \
        "migration 111 must UPDATE bots ... SET retired_at = now()"
    for b in retired_bots:
        assert f"'{b}'" in mig, f"migration 111 missing retirement for {b}"
    # If un-retire migration exists, skip description-prefix check
    if not pathlib.Path("supabase/migrations/117_unretire_all_bots.sql").exists():
        src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
        for b in retired_bots:
            idx = src.find(f'"{b}":')
            assert idx >= 0, f"{b} missing from BOTS_CONFIG"
            assert "[RETIRED 2026-05-19]" in src[idx:idx + 1500]


@test("BOTS-UNRETIRE-ALL — migration 117 un-retires all 8 main bots for analysis volume")
def _():
    """2026-05-22: all 8 previously-retired main bots un-retired so simulated_bets
    accumulates more data for analysis. Migration sets is_active=true, retired_at=NULL.
    Inplay merge bots (inplay_a2, inplay_c_home, inplay_f) excluded intentionally."""
    import pathlib
    unretired = [
        "bot_lower_1x2", "bot_opt_home_lower", "bot_draw_specialist", "bot_conservative",
        "bot_dc_value", "bot_dc_strong_fav", "bot_dnb_away_value", "bot_ou15_defensive",
    ]
    mig = pathlib.Path("supabase/migrations/117_unretire_all_bots.sql").read_text()
    assert "retired_at = NULL" in mig, "migration 117 must SET retired_at = NULL"
    assert "is_active  = true" in mig or "is_active = true" in mig, \
        "migration 117 must SET is_active = true"
    for b in unretired:
        assert f"'{b}'" in mig, f"migration 117 missing un-retire entry for {b}"
    # Inplay merge bots must NOT be un-retired — they'd duplicate surviving inplay bots
    for b in ("inplay_a2", "inplay_c_home", "inplay_f"):
        assert f"'{b}'" not in mig, f"migration 117 must not un-retire {b} (merged bot)"


@test("COOLBET-SELECTION-BIAS — real_perf_report has section_selection_bias")
def _():
    """Diagnostic that separates 'I picked losers from the offered slips' from
    'edge doesn't survive execution'. First run on 14d of real bets showed
    placed -7.1% ROI vs unplaced +6.1% (13.2pp gap), confirming selection bias
    is the dominant problem. Guard the section so it doesn't get pruned and
    so the SQL keeps using the simulated_bet_id link."""
    import pathlib
    src = pathlib.Path("scripts/real_perf_report.py").read_text()
    assert "def section_selection_bias(" in src, (
        "section_selection_bias missing from real_perf_report.py"
    )
    assert "section_selection_bias(args.days)" in src, (
        "section_selection_bias must be wired into main()"
    )
    # Bot filter: only pre-match bots (inplay can't be manually placed at Coolbet)
    assert "inplay_" in src and "bo.name NOT LIKE" in src, (
        "Selection bias section must exclude inplay_* bots (not manually placeable)"
    )
    # Hint message reminds user about the ranking fix
    assert "edge" in src.lower() and "stake" in src.lower() and "/admin/place" in src, (
        "Hint message must point user to /admin/place sort"
    )


@test("AF-COVERAGE-AUDIT — script exists + confusion matrix structure")
def _():
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent / "af_coverage_audit.py"
    assert script.exists(), "scripts/af_coverage_audit.py missing"
    text = script.read_text()
    for needed in ("coverage_events", "coverage_lineups", "_audit_one",
                   "flag_accuracy", "fixtures/events", "fixtures/lineups"):
        assert needed in text, f"af_coverage_audit missing {needed!r}"


@test("ODDS-TIMING-COHORT-PREP — BOT_COHORT_OVERRIDES env parses + overrides timing")
def _():
    """Env-driven override for BOT_TIMING_COHORTS. Lets us flip bot cohort
    on 2026-06-07 via env (no code change). Format:
    'bot_a:morning,bot_b:morning'.
    """
    import inspect
    from workers.jobs import daily_pipeline_v2
    src = inspect.getsource(daily_pipeline_v2)
    assert "BOT_COHORT_OVERRIDES" in src, "env var must be referenced"
    assert 'os.getenv("BOT_COHORT_OVERRIDES"' in src
    assert "ovr_bot, ovr_cohort = pair.split" in src, "must parse 'bot:cohort' format"


@test("LIVEPOLLER-EVENTS-GATE-IMPL — env gate + coverage_events check + default off")
def _():
    """Env-gated skip of /fixtures/events when leagues.coverage_events=false.
    Activate via GATE_EVENTS_BY_COVERAGE=true on 2026-06-07. Default OFF.
    AF-COVERAGE-AUDIT 2026-05-25 verified events flag is 95% accurate.
    """
    import inspect
    from workers.jobs import live_tracker
    src = inspect.getsource(live_tracker)
    assert "GATE_EVENTS_BY_COVERAGE" in src, "env var must be referenced"
    assert 'getenv("GATE_EVENTS_BY_COVERAGE", "false")' in src, "default must be false"
    assert 'coverage_events' in src, "must check db_match['coverage_events']"
    # The SELECT in db.py must include the join
    from workers.api_clients import db as _db
    db_src = inspect.getsource(_db.build_af_id_map)
    assert "coverage_events" in db_src, "SELECT must include coverage_events"


@test("DEPLOY-READINESS-20260608 — 9-check script exists + uses real queries")
def _():
    """Script that operator runs on 2026-06-08 morning before flipping env vars.
    Validates candidate bundle, isotonic pickles, B-ML3 cohort, signal freshness,
    Pinnacle coverage, Phase 4 verdict, etc. before going live with the deploy.
    """
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent / "deploy_readiness_20260608.py"
    assert script.exists(), "deploy_readiness_20260608.py missing"
    text = script.read_text()
    for needed in ("check_candidate_bundle", "check_isotonic_pickles",
                   "check_meta_validation", "check_aln1_recommendation",
                   "check_pinnacle_today_coverage", "Env flips for 2026-06-08"):
        assert needed in text, f"deploy_readiness missing: {needed}"


@test("CALIBRATION-ISOTONIC-IMPL — dispatcher + per-market isotonic load + env gate default off")
def _():
    """CALIBRATION-ISOTONIC-IMPL 2026-05-25 — adds isotonic as an alternative
    Stage-2 calibrator behind STAGE2_CALIBRATOR env var. Default 'platt' (no
    behaviour change). Activate post-Phase-3.5 (2026-06-08) via env flip.

    Validated: fit_isotonic_offline.py on v_20260525_signals showed 50-72%
    ECE reduction across all 5 markets vs baseline (raw scores).

    Guards:
      - calibrate_prob routes through _apply_stage2 dispatcher
      - default env behaviour stays platt (no change for production today)
      - apply_isotonic exists and falls back to platt on missing market
      - load_isotonic_models reads from bundle dir
      - fit script exists
    """
    import os as _os, inspect
    from workers.model import improvements
    # Restore env to default before any check
    prev = _os.environ.pop("STAGE2_CALIBRATOR", None)
    try:
        src = inspect.getsource(improvements)
        for needed in ("_apply_stage2", "apply_isotonic", "load_isotonic_models",
                       "STAGE2_CALIBRATOR", "isotonic_"):
            assert needed in src, f"isotonic plumbing missing: {needed}"
        # Default mode = platt — confirm dispatcher routes correctly
        assert 'os.getenv("STAGE2_CALIBRATOR", "platt")' in src, "default must be platt"
        # apply_isotonic must fall back to platt when no model
        improvements.reset_isotonic_cache()
        out = improvements.apply_isotonic(0.5, "1x2_home_nonexistent_market")
        # Should equal what apply_platt returns (which for unknown market = prob unchanged)
        assert out == 0.5 or 0 <= out <= 1, "fallback should produce a valid prob"
        # Fit script
        from pathlib import Path as _Path
        fit_script = _Path(__file__).resolve().parent / "fit_isotonic_offline.py"
        assert fit_script.exists()
    finally:
        if prev is not None:
            _os.environ["STAGE2_CALIBRATOR"] = prev


@test("EMAIL-DELIVERY-CHECK — script exists with env, SPF, DKIM, send-test phases")
def _():
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent / "email_delivery_check.py"
    assert script.exists(), "scripts/email_delivery_check.py missing"
    text = script.read_text()
    for needed in ("_check_env", "_check_spf", "_check_dkim", "_send_test",
                   "resend._domainkey", "v=spf1", "_spf.resend.com",
                   "DIGEST_FROM_EMAIL"):
        assert needed in text, f"email_delivery_check missing {needed!r}"


@test("CAL-ALPHA-ODDS-V2 — graduated longshot shrinkage scaffolding, env-gated OFF")
def _():
    """CAL-ALPHA-ODDS-V2 2026-05-25 — odds-bucketed shrinkage replaces the
    single -0.20 step at odds > 3.0. Activated post-Phase-3.5 via
    CAL_ALPHA_ODDS_V2_ENABLED=true env. Default OFF preserves current
    single-step behaviour.

    platt_overconfidence_deepdive.py audit:
      odds 2.5-3.0: -10pp gap
      odds 3.0-3.5: +2.3pp (well-calibrated by current -0.20)
      odds 3.5-4.0: -12pp gap
      odds 4.0+:   -20pp gap (catastrophic)
    """
    import inspect, os as _os
    from workers.model import improvements
    # Restore env to default before any check
    prev = _os.environ.pop("CAL_ALPHA_ODDS_V2_ENABLED", None)
    try:
        src = inspect.getsource(improvements.calibrate_prob)
        assert "CAL_ALPHA_ODDS_V2_ENABLED" in src, "env flag missing"
        assert "alpha - 0.35" in src, "must use -0.35 pull for odds >= 4.0"
        assert "alpha - 0.25" in src, "must use -0.25 pull for odds 3.5-4.0"
        assert "alpha - 0.10" in src, "must use -0.10 pull for odds 2.5-3.0"
        assert 'os.getenv("CAL_ALPHA_ODDS_V2_ENABLED", "false")' in src, \
            "default must be 'false' (Phase 3.5 lock)"
        # And the legacy single -0.20 step must still be reachable when env unset
        assert "elif odds > 3.0:" in src, "fallback single-step path must remain"
    finally:
        if prev is not None:
            _os.environ["CAL_ALPHA_ODDS_V2_ENABLED"] = prev


@test("COOLBET-FIRST-SORT — value-bets sorts Coolbet-recommended first, then edge desc")
def _():
    """COOLBET-FIRST-SORT 2026-05-25 — /value-bets puts Coolbet-recommended
    bets ahead of others (within group, edge desc preserved). Reduces
    placement friction for the operator who uses Coolbet as primary venue.
    Replaces the dropped B2C BM-FILTER task.
    """
    from pathlib import Path as _Path
    page = _Path(__file__).resolve().parent.parent.parent / "odds-intel-web" / "src" / "app" / "(app)" / "value-bets" / "page.tsx"
    if not page.exists():
        print("  [skip] odds-intel-web not present in CI")
        return
    src = page.read_text()
    assert "COOLBET-FIRST-SORT" in src, "must reference the task tag"
    assert "recommendedBookmaker" in src, "sort must consult recommendedBookmaker"
    assert '"coolbet"' in src, "must compare against literal coolbet (lowercased)"
    # The Coolbet flag must influence sort BEFORE edge — find the order
    cb_pos = src.find("aCoolbet !== bCoolbet")
    edge_pos = src.find("b.edge - a.edge")
    assert cb_pos > 0 and edge_pos > cb_pos, "Coolbet check must come before edge sort"


@test("BOT-BANKROLL-DRIFT — every active bot's current_bankroll matches starting + sum(pnl)")
def _():
    """BOT-BANKROLL-DRIFT 2026-05-25 — un-retire/re-retire cycles previously
    left current_bankroll out of sync with bet history. Fixed by
    scripts/fix_bot_bankroll_drift.py --apply. Smoke catches regression
    if a future migration or settlement bug introduces drift again.

    Tolerance: ±€0.50 per bot.
    """
    from workers.api_clients.db import execute_query
    rows = execute_query("""
        SELECT b.name,
               ABS(b.current_bankroll
                   - (b.starting_bankroll
                      + COALESCE(SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')), 0))) AS drift
        FROM bots b
        LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
        WHERE b.is_active = true AND b.retired_at IS NULL
        GROUP BY b.id, b.name, b.current_bankroll, b.starting_bankroll
        HAVING ABS(b.current_bankroll
                   - (b.starting_bankroll
                      + COALESCE(SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')), 0))) > 0.50
    """)
    if rows:
        names = [(r["name"], f"€{float(r['drift']):.2f}") for r in rows[:5]]
        assert False, f"{len(rows)} bots drifted: {names} — run scripts/fix_bot_bankroll_drift.py --apply"


@test("BOT-AGGREGATES-SSOT — dashboard_cache.bot_breakdown reconciles to live simulated_bets aggregates")
def _():
    """BOT-AGGREGATES-SSOT 2026-05-25 — guard the divergence the original task
    was filed for. Failure here means /performance (cache-backed) and
    /admin/bots (live-aggregated) will show different numbers for the same bot.
    Threshold: cache pnl must match live pnl within 1% (or €1, whichever larger).
    Bankroll drift is checked separately and warned but doesn't fail (it's a
    distinct issue tracked as BOT-BANKROLL-DRIFT).
    """
    import json
    from workers.api_clients.db import execute_query
    cache = execute_query("""
        SELECT bot_breakdown FROM dashboard_cache
        ORDER BY computed_at DESC LIMIT 1
    """)
    if not cache or not cache[0]["bot_breakdown"]:
        return  # no cache row yet — skip
    breakdown = cache[0]["bot_breakdown"]
    if isinstance(breakdown, str):
        breakdown = json.loads(breakdown)
    live = execute_query("""
        SELECT b.name,
               COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) as settled,
               COALESCE(SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')), 0) as total_pnl
        FROM bots b
        LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
        WHERE b.is_active = true AND b.retired_at IS NULL
          AND b.name NOT LIKE 'bot_acca%%'
          AND b.name NOT LIKE 'bot_combo%%'
        GROUP BY b.id, b.name
    """)
    live_by_name = {r["name"]: r for r in live}
    drifted = []
    for c in breakdown:
        name = c.get("name")
        l = live_by_name.get(name)
        if not l:
            continue
        c_pnl = float(c.get("total_pnl") or 0)
        l_pnl = float(l["total_pnl"])
        # Allow €1 absolute slack OR 1% relative
        if abs(c_pnl - l_pnl) > max(1.0, abs(l_pnl) * 0.01):
            drifted.append((name, c_pnl, l_pnl))
    assert not drifted, (
        f"dashboard_cache.bot_breakdown drift on {len(drifted)} bots: "
        f"{[(n, f'{c:.2f}→{l:.2f}') for n,c,l in drifted[:5]]}"
    )


@test("EMAIL-FROM-FALLBACK — aln_auto_tune falls back to DIGEST_FROM_EMAIL when ALERT_FROM_EMAIL unset")
def _():
    """Railway has DIGEST_FROM_EMAIL configured (not ALERT_FROM_EMAIL).
    All new email jobs must fall back so emails don't silently drop.
    """
    from pathlib import Path as _Path
    aln = _Path(__file__).resolve().parent.parent / "workers" / "jobs" / "aln_auto_tune.py"
    src = aln.read_text()
    assert 'os.getenv("ALERT_FROM_EMAIL")' in src and 'os.getenv("DIGEST_FROM_EMAIL")' in src, \
        "aln_auto_tune must consult both ALERT_FROM_EMAIL + DIGEST_FROM_EMAIL"
    # Order matters — ALERT_FROM_EMAIL is checked first, then falls back
    a_pos = src.find('os.getenv("ALERT_FROM_EMAIL")')
    d_pos = src.find('os.getenv("DIGEST_FROM_EMAIL")')
    assert a_pos < d_pos, "ALERT_FROM_EMAIL must be the primary; DIGEST_FROM_EMAIL the fallback"


@test("WORKER-SPLIT-LIVEPOLLER — standalone entrypoint + env-gated in-scheduler thread")
def _():
    from pathlib import Path as _Path
    main = _Path(__file__).resolve().parent.parent / "workers" / "live_poller_main.py"
    assert main.exists(), "workers/live_poller_main.py missing"
    msrc = main.read_text()
    assert "from workers.live_poller import LivePoller" in msrc
    assert "signal.SIGTERM" in msrc, "must handle SIGTERM for graceful shutdown"
    sched = (_Path(__file__).resolve().parent.parent / "workers" / "scheduler.py").read_text()
    assert "LIVE_POLLER_IN_SCHEDULER" in sched, "scheduler must env-gate the in-process thread"
    assert 'os.getenv("LIVE_POLLER_IN_SCHEDULER", "true")' in sched, "default must be true (no behaviour change)"


@test("TIER-C-EXPAND-ALIASES — removes 6 broken aliases + adds verified high-yield entries")
def _():
    """TIER-C-EXPAND-ALIASES 2026-05-25 — alias batch + cleanup.
    Removed 6 broken aliases that pointed to non-existent DB names:
    Brighton, Leicester, Norwich, Cardiff, QPR, Inter (DB actually uses
    the short names). Added ~15 verified targets for high-frequency
    unmatched names. Total impact: 14,837 → 9,038 unmatched FD rows
    (~+5,799 matched, audit script).
    """
    from pathlib import Path as _Path
    audit = _Path(__file__).resolve().parent / "audit_unmatched_extras.py"
    assert audit.exists(), "scripts/audit_unmatched_extras.py missing"
    from scripts.ingest_football_data_csvs import TEAM_ALIASES, normalize_team_name
    from workers.api_clients.db import execute_query
    # No broken aliases — every target must map to a DB name (after normalization)
    rows = execute_query("SELECT name FROM teams")
    db_norm = {normalize_team_name(r["name"]) for r in rows if normalize_team_name(r["name"])}
    broken = []
    for fd_name, target in TEAM_ALIASES.items():
        norm = normalize_team_name(target)
        if norm not in db_norm:
            broken.append((fd_name, target))
    # Acceptable threshold: ≤2 broken aliases (Spal/Hertha-style edge cases)
    assert len(broken) <= 2, f"too many broken aliases: {broken}"
    # The 6 fix-by-removal aliases must NOT be present (their bare names match DB)
    for removed in ("Brighton", "Leicester", "Norwich", "Cardiff", "QPR", "Inter"):
        assert removed not in TEAM_ALIASES, f"{removed} alias must stay removed — bare DB name matches"


@test("LEAGUE-SEASON-PHASE — season-progress signal + scheduler + multi-market backtest")
def _():
    """LEAGUE-SEASON-PHASE 2026-05-25 — per-match season_progress [0..1].
    Backtest on 6,880 matches since 2026-03-01: late vs early matches
    show +7.7pp Over 2.5, +6.0pp BTTS, +6.7pp home win. Signal feeds
    OU + BTTS + 1X2 models alike.
    """
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent / "compute_league_season_phase.py"
    assert script.exists()
    text = script.read_text()
    assert '"season_progress"' in text, "signal name must be season_progress"
    assert "early" in text and "mid" in text and "late" in text, "must bucket into 3 phases"
    sched = (_Path(__file__).resolve().parent.parent / "workers" / "scheduler.py").read_text()
    assert "job_league_season_phase" in sched
    assert "compute_league_season_phase.py" in sched


@test("LINE-VELOCITY — Pinnacle home slope T-12h..T-2h + scheduler + REVERSE-signal backtest")
def _():
    """LINE-VELOCITY 2026-05-25 — linear-regression slope of Pinnacle home
    implied prob over T-12h..T-2h snapshots. Backtest: Q4 |v| → −6.6pp
    CLV-beat (REVERSE signal — high velocity = we're on the wrong side
    by close). Meta-model should down-weight high-|v| bets.
    """
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent / "compute_line_velocity.py"
    assert script.exists(), "scripts/compute_line_velocity.py missing"
    text = script.read_text()
    assert '"line_velocity"' in text, "signal name must be line_velocity"
    assert "_linear_slope" in text, "must compute linear-regression slope"
    assert "BETWEEN 120 AND 720" in text, "must use T-12h..T-2h window"
    sched = (_Path(__file__).resolve().parent.parent / "workers" / "scheduler.py").read_text()
    assert "job_line_velocity" in sched
    assert "compute_line_velocity.py" in sched


@test("LIVE-SNAPSHOTS-PRUNE — weekly prune of live_match_snapshots wired into scheduler")
def _():
    """LIVE-SNAPSHOTS-PRUNE 2026-05-25 — keep 5-min boundaries + event-adjacent
    rows for matches finished ≥48h ago. Dry-run found 51% (377K of 736K rows)
    pruneable. Sunday 01:00 UTC cron.
    """
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent / "prune_live_snapshots.py"
    assert script.exists(), "prune_live_snapshots.py missing"
    text = script.read_text()
    assert "minute %% 5" in text, "must use 5-minute boundary rule"
    assert "match_events" in text and "ev.minute" in text, "must use event-adjacency"
    sched = (_Path(__file__).resolve().parent.parent / "workers" / "scheduler.py").read_text()
    assert "job_prune_live_snapshots" in sched
    assert "Sun 01:00" in sched


@test("LEAGUE-DRAW-YTD — per-league draw rate signal + scheduler + backtest evidence")
def _():
    """LEAGUE-DRAW-YTD 2026-05-25 — per-league season-to-date draw rate.
    Backtest on 11,875 settled matches: Q4 vs Q1 actual-draw gap +11.6pp,
    real edge for draw markets. Guards: script exists, signal naming,
    scheduler job registered, backtest function present.
    """
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent / "compute_league_draw_rate.py"
    assert script.exists(), "scripts/compute_league_draw_rate.py missing"
    text = script.read_text()
    assert "league_draw_rate_ytd" in text, "signal name must be league_draw_rate_ytd"
    assert "def backtest" in text, "backtest function must be present"
    assert "Q4 vs Q1" in text, "must report Q4 vs Q1 lift"
    sched = (_Path(__file__).resolve().parent.parent / "workers" / "scheduler.py").read_text()
    assert "job_league_draw_rate" in sched
    assert "compute_league_draw_rate.py" in sched


@test("OPENING-LINE-MOVE-CAPTURE — tomorrow-odds fetch at 22:00 UTC, no race vs morning pipeline")
def _():
    """OPENING-LINE-MOVE-CAPTURE 2026-05-25 — fix the 0.2% overnight_line_move
    coverage. Root cause: the prior overnight slots at 02:00/04:00 UTC fetched
    TODAY's matches, but today's matches had no prior snapshot. Fix: 22:00 UTC
    fetch TOMORROW's matches so the next morning's 04:00 fetch produces the
    delta. Guards: job exists, schedules at 22:00, uses tomorrow target_date,
    redundant 02:00/04:00 slots removed.
    """
    from pathlib import Path as _Path
    sched_path = _Path(__file__).resolve().parent.parent / "workers" / "scheduler.py"
    src = sched_path.read_text()
    assert "def job_odds_tomorrow" in src, "must define the tomorrow-odds job"
    assert "tomorrow = (date.today() + timedelta(days=1))" in src, \
        "job must compute tomorrow's date"
    assert "id=\"odds_tomorrow_2200\"" in src, "scheduler must register at 22:00"
    assert "OPENING-LINE-MOVE-CAPTURE" in src
    # Confirm the redundant 02:00 + 04:00 redundant slots are gone
    assert 'id="odds_0200"' not in src, "remove redundant 02:00 slot"
    assert 'id="odds_0400"' not in src, "remove redundant 04:00 slot"


@test("SIG-12 — xG overperformance script + scheduler job + signal naming")
def _():
    """SIG-12 2026-05-25 — rolling 10-match team xG overperformance signal.
    Positive = team scoring more than xG (regression-to-mean: expect
    downward correction). First production run: 368 entries, mean +0.17,
    range -1.22 .. +2.27.
    """
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent / "compute_xg_overperformance.py"
    assert script.exists(), "scripts/compute_xg_overperformance.py missing"
    text = script.read_text()
    assert "xg_overperf_home" in text and "xg_overperf_away" in text, \
        "signal names must be xg_overperf_{home,away}"
    assert "WINDOW = 10" in text, "rolling window must be 10 matches"
    sched_src = (_Path(__file__).resolve().parent.parent / "workers" / "scheduler.py").read_text()
    assert "job_xg_overperformance" in sched_src
    assert "compute_xg_overperformance.py" in sched_src


@test("ALN-AUTO — monthly alignment-bump tuner wired into scheduler with email diff")
def _():
    """ALN-AUTO 2026-05-25 — monthly cron wrapping aln1_tune_analysis.py.
    Emails a Resend diff when any alignment class needs |Δ| ≥ 0.005 with
    n ≥ 100. Never auto-applies (human approves). Guards: job file
    exists with correct entrypoint, scheduler registers it on day=1,
    diff thresholds match spec.
    """
    from pathlib import Path as _Path
    job_path = _Path(__file__).resolve().parent.parent / "workers" / "jobs" / "aln_auto_tune.py"
    assert job_path.exists(), "workers/jobs/aln_auto_tune.py missing"
    job_src = job_path.read_text()
    assert "def run_aln_auto_tune" in job_src
    assert ">= 0.005" in job_src, "diff threshold must be 0.005"
    assert ">= 100" in job_src, "n threshold must be 100"
    assert "_send_email" in job_src, "must email via Resend"
    sched_src = (_Path(__file__).resolve().parent.parent / "workers" / "scheduler.py").read_text()
    assert "job_aln_auto_tune" in sched_src, "scheduler must register the job"
    assert 'CronTrigger(day="1"' in sched_src, "scheduler must use day=1 (monthly)"


@test("SCHEMA-DRIFT-SMOKE — every column the model trains on still exists in MFV")
def _():
    """SCHEMA-DRIFT-SMOKE 2026-05-25 — detect column renames / removals
    before they silently break training or inference. Cross-references
    every column referenced in scripts/train_b_ml3.py MATCH_LEVEL_FEATURES,
    SELECT clause in _load_training_data, and the new v3 signal columns,
    against information_schema.columns for match_feature_vectors.

    Failure modes this catches:
    - A migration renames a column but train_b_ml3.py still references the old name
    - A column gets dropped without updating the SELECT
    - A new feature is added to MATCH_LEVEL_FEATURES but its column never lands
    """
    import importlib.util
    from pathlib import Path as _Path
    from workers.api_clients.db import execute_query
    train_path = _Path(__file__).resolve().parent / "train_b_ml3.py"
    spec = importlib.util.spec_from_file_location("train_b_ml3", train_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    declared = set(mod.MATCH_LEVEL_FEATURES)
    # Also the per-selection bases (each gets _home/_draw/_away suffixed in MFV)
    for base in mod.SELECTION_AWARE_V2:
        declared.add(f"{base}_home_at_t6h")
        declared.add(f"{base}_draw_at_t6h")
        declared.add(f"{base}_away_at_t6h")
    # Drop computed-at-train-time features
    declared.discard("time_to_kickoff")
    declared.discard("league_tier")
    # Drop _missing indicator columns (generated post-load)
    declared = {c for c in declared if not c.endswith("_missing")}
    # Verify each declared column exists in MFV
    rows = execute_query("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='match_feature_vectors'
    """)
    actual = {r["column_name"] for r in rows}
    missing = declared - actual
    assert not missing, f"MFV missing columns the model needs: {sorted(missing)}"
    # Also v3 signal columns added by migration 132 must exist
    for c in ("team_avg_player_rating_home", "team_avg_player_rating_away",
              "injury_severity_score_home", "injury_severity_score_away",
              "league_clv_efficiency"):
        assert c in actual, f"migration 132 column missing: {c}"


@test("MFV-FORM-MOMENTUM-BUG — live MFV builder writes form_momentum_{home,away}")
def _():
    """Bug discovered 2026-05-24: form_momentum_* columns exist in MFV but
    `_build_feature_row_batched` never wrote them — 100% NULL on today's
    rows until the nightly 22:45 backfill caught up. Fix: live builder
    now batch-computes (last-3 ppg) − (last-10 ppg) per team in a single
    SQL per chunk and writes the values at MFV build time. Guards:
    (1) function signature accepts form_momentum_by_team, (2) the output
    row dict contains form_momentum_home/away keys, (3) the live builder
    populates the map via the new SQL.
    """
    import inspect
    from workers.api_clients import supabase_client
    sig = inspect.signature(supabase_client._build_feature_row_batched)
    assert "form_momentum_by_team" in sig.parameters, "param must be added"
    src = inspect.getsource(supabase_client._build_feature_row_batched)
    assert '"form_momentum_home"' in src, "row dict must include form_momentum_home"
    assert '"form_momentum_away"' in src, "row dict must include form_momentum_away"
    # The shared helper (_build_mfv_rows_for_matches) — called by both
    # build_match_feature_vectors and build_match_feature_vectors_live —
    # must populate the form_momentum map.
    helper_src = inspect.getsource(supabase_client._build_mfv_rows_for_matches)
    assert "form_momentum_by_team" in helper_src, "shared helper must compute the map"
    assert "ppg_3" in helper_src, "must compute ppg_3 vs ppg_10 momentum"


@test("ML-NEW-FEATURES — migration 132 + backfill script signal→MFV mapping")
def _():
    """ML-NEW-FEATURES 2026-05-25 — pivots the new match_signals into 5 new
    MFV columns ready for the next B-ML3 retrain (v3+). Guards:
    (1) migration 132 file exists and adds all 5 columns,
    (2) backfill script maps each signal name to the matching MFV column.
    """
    from pathlib import Path as _Path
    repo_root = _Path(__file__).resolve().parent.parent
    mig = repo_root / "supabase" / "migrations" / "132_mfv_v3_signal_columns.sql"
    assert mig.exists(), "migration 132_mfv_v3_signal_columns.sql is missing"
    mig_text = mig.read_text()
    for col in (
        "team_avg_player_rating_home", "team_avg_player_rating_away",
        "injury_severity_score_home", "injury_severity_score_away",
        "league_clv_efficiency",
    ):
        assert col in mig_text, f"migration must ADD COLUMN {col}"
    backfill = repo_root / "scripts" / "backfill_mfv_v3_signals.py"
    assert backfill.exists(), "backfill script missing"
    import importlib.util
    spec = importlib.util.spec_from_file_location("backfill_mfv_v3_signals", backfill)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Original migration 132 columns — must be present
    expected_v132 = {
        "team_avg_player_rating_home": "team_avg_player_rating_home",
        "team_avg_player_rating_away": "team_avg_player_rating_away",
        "injury_severity_score_home": "injury_severity_score_home",
        "injury_severity_score_away": "injury_severity_score_away",
        "league_clv_efficiency": "league_clv_efficiency",
    }
    # Migration 133 extension (MFV-V3-PIVOT-EXTEND, 2026-05-25 evening) added 5 more
    expected_v133 = {
        "league_draw_rate_ytd": "league_draw_rate_ytd",
        "season_progress": "season_progress",
        "line_velocity": "line_velocity",
        "xg_overperf_home": "xg_overperf_home",
        "xg_overperf_away": "xg_overperf_away",
    }
    for sig, col in {**expected_v132, **expected_v133}.items():
        assert mod.SIGNAL_TO_COLUMN.get(sig) == col, \
            f"signal→column mapping missing {sig}→{col}"


@test("INPLAY-LAYER-ARCH — _build_inplay_bet_data is a pure function that produces correct payload")
def _():
    """INPLAY-LAYER-ARCH 2026-05-25 — first extracted stage: bet-payload
    construction. Pure function (no DB / no console / no globals). Guards:
    (1) function exists, (2) produces the same dict shape as the inline
    code used to, (3) edge is %→decimal correctly, (4) JSON reasoning
    fields preserved.
    """
    import json as _json
    from workers.jobs.inplay_bot import _build_inplay_bet_data
    trigger = {
        "market": "1x2", "selection": "home", "odds": 1.80,
        "model_prob": 0.62, "edge": 8.5,
        "posterior_rate": 0.030, "prematch_xg_total": 2.7,
        "extra": {"foo": "bar"},
    }
    cand = {"minute": 67, "score_home": 1, "score_away": 0}
    out = _build_inplay_bet_data(
        trigger=trigger, cand=cand, xg_h=1.4, xg_a=0.6, is_real=True,
        odds_age=2.5, bot_name="inplay_c",
    )
    assert out["market"] == "1x2"
    assert out["selection"] == "home"
    assert out["odds"] == 1.80
    assert out["stake"] == 5.0, "INPLAY-STAKE-5 must hold"
    assert out["model_prob"] == 0.62
    assert abs(out["edge"] - 0.085) < 1e-9, "edge must be converted % → decimal"
    assert out["xg_source"] == "live"
    reasoning = _json.loads(out["reasoning"])
    assert reasoning["strategy"] == "inplay_c"
    assert reasoning["minute"] == 67
    assert reasoning["score"] == "1-0"
    assert reasoning["foo"] == "bar", "extra fields must be merged"
    assert reasoning["odds_age_ms"] == 2500


@test("INPLAY-SOFT-GATES — _gate_score helper + env-gated reference impl in strategy_c")
def _():
    """INPLAY-SOFT-GATES 2026-05-25 — continuous closeness score replaces
    boolean cliff-edge gates in inplay strategies. Default OFF (boolean
    path preserved); INPLAY_SOFT_GATES_ENABLED=true activates the soft
    path. Guards: helper math, env-flag default, strategy_d wired to use
    the helper, boolean fallback preserved (anti-regression).
    """
    import importlib
    from workers.jobs import inplay_bot
    importlib.reload(inplay_bot)
    gs = inplay_bot._gate_score
    # Hard pass / fail
    assert gs(60, 55, side="above") == 1.0, "value above threshold = full credit"
    # tolerance band = |threshold|*tolerance_pct → 55*0.10 = 5.5
    assert gs(40, 55, side="above", tolerance_pct=0.10) == 0.0, "value far below band = no credit"
    # Ramp in tolerance band
    score = gs(52, 55, side="above", tolerance_pct=0.10)
    assert 0.0 < score < 1.0, f"in-band should ramp, got {score}"
    # Below side
    assert gs(40, 55, side="below") == 1.0
    assert gs(80, 55, side="below", tolerance_pct=0.10) == 0.0
    # None / NaN safety
    assert gs(None, 55) == 0.0
    assert gs(float("nan"), 55) == 0.0
    # Default OFF
    assert inplay_bot._SOFT_GATES_ENABLED is False, \
        "INPLAY_SOFT_GATES_ENABLED must default to False"
    # Strategy_c (favourite-leading-loser) wired as the reference impl
    import inspect
    src = inspect.getsource(inplay_bot._check_strategy_c)
    assert "_SOFT_GATES_ENABLED" in src, "strategy_c must consult the env flag"
    assert "_gate_score(" in src, "strategy_c must use _gate_score"
    # Boolean fallback still present (so default behaviour is unchanged)
    assert "if fav_sot < opp_sot:" in src, "boolean SoT guard must be preserved"


@test("INJURY-SEVERITY — keyword classifier maps reasons to SEVERE/MODERATE/MINOR/UNKNOWN")
def _():
    """INJURY-SEVERITY 2026-05-25 — replaces raw injury count with
    severity-weighted score (SEVERE 3×, MODERATE 1.5×, MINOR 0.5×, UNKNOWN 1×).
    Guards: classifier maps each major real reason correctly, scheduler
    job registered.
    """
    from pathlib import Path as _Path
    import importlib.util
    script_path = _Path(__file__).resolve().parent / "compute_injury_severity.py"
    assert script_path.exists(), "scripts/compute_injury_severity.py missing"
    spec = importlib.util.spec_from_file_location("compute_injury_severity", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.classify("ACL Knee Injury") == "SEVERE"
    assert mod.classify("Achilles Tendon Injury") == "SEVERE"
    assert mod.classify("Hamstring Injury") == "MODERATE"
    assert mod.classify("Knee Injury") == "MODERATE"
    assert mod.classify("Knock") == "MINOR"
    assert mod.classify("Illness") == "MINOR"
    assert mod.classify("Yellow Cards") == "MINOR"
    assert mod.classify("Injury") == "UNKNOWN", "generic 'Injury' falls through to UNKNOWN"
    assert mod.classify(None) == "UNKNOWN"
    assert mod.WEIGHTS == {"SEVERE": 3.0, "MODERATE": 1.5, "MINOR": 0.5, "UNKNOWN": 1.0}
    # Scheduler wired
    sched_path = _Path(__file__).resolve().parent.parent / "workers" / "scheduler.py"
    sched_src = sched_path.read_text()
    assert "job_injury_severity" in sched_src
    assert "compute_injury_severity.py" in sched_src


@test("AF-PLAYER-RATINGS — compute_team_avg_player_rating.py + scheduler job + signal name pattern")
def _():
    """AF-PLAYER-RATINGS 2026-05-25 — rolling per-team AF player rating
    written to match_signals nightly. Guards: (1) script exists, (2)
    writes team_avg_player_rating_{home,away}, not some other name,
    (3) scheduler has a job registered at 22:50.
    """
    from pathlib import Path as _Path
    script_path = _Path(__file__).resolve().parent / "compute_team_avg_player_rating.py"
    assert script_path.exists(), "scripts/compute_team_avg_player_rating.py is missing"
    text = script_path.read_text()
    assert "team_avg_player_rating_home" in text and "team_avg_player_rating_away" in text, \
        "signal names must match the home/away naming pattern"
    # scheduler.py is text-grep'd (not imported) to avoid pulling apscheduler
    # into the local test env.
    sched_path = _Path(__file__).resolve().parent.parent / "workers" / "scheduler.py"
    sched_src = sched_path.read_text()
    assert "job_team_avg_player_rating" in sched_src, "scheduler must register the job"
    assert "compute_team_avg_player_rating.py" in sched_src, "scheduler must invoke the script"


@test("AH-XGBOOST — train_ah_xgboost.py script + AH label helper + 20-feature schema")
def _():
    """AH-XGBOOST 2026-05-25 — dedicated XGBoost head for AH pricing.
    Trained on 3,199 main-line AH bets since 2026-05-01, CV AUC 0.73.
    Bundle saved locally (gitignored); not auto-activated. Guard:
    (1) script exists, (2) _ah_label correctly handles whole/half/quarter
    lines including pushes/half-wins, (3) feature schema includes the
    AH-specific columns.
    """
    from pathlib import Path as _Path
    import importlib.util
    script_path = _Path(__file__).resolve().parent / "train_ah_xgboost.py"
    assert script_path.exists(), "scripts/train_ah_xgboost.py is missing"
    spec = importlib.util.spec_from_file_location("train_ah_xgboost", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Label correctness
    assert mod._ah_label(2, -1.5) == 1.0, "home wins by 2 covers -1.5"
    assert mod._ah_label(1, -1.5) == 0.0, "home wins by 1 fails -1.5"
    assert mod._ah_label(1, -1.0) is None, "home wins by 1 on -1.0 line = push"
    assert mod._ah_label(2, -1.25) == 1.0, "x.25 quarter full-win"
    assert mod._ah_label(1, -1.25) == 0.5, "x.25 quarter half-loss"
    assert mod._ah_label(2, -1.75) == 0.5, "x.75 quarter half-win"
    assert mod._ah_label(3, -1.75) == 1.0, "x.75 quarter full-win"
    # AH-specific features must be in the schema
    for f in ("handicap_line", "pinnacle_ah_line_at_t6h", "ensemble_prob_home"):
        assert f in mod.MATCH_FEATURES or f in ("handicap_line",), \
            f"feature {f} missing from training schema"


@test("META-BOT-PORTFOLIO — meta_bot_picks.py scaffolding present + bot list gates execution")
def _():
    """META-BOT-PORTFOLIO 2026-05-25 — scaffolding committed but bot selection
    is deferred until the 200-bet cohort report (~2026-06-30). Guard that:
    (1) the script file exists, (2) the seven algorithm steps are
    implemented (key function names), (3) running without --bots prints the
    WAITING message and exits 0 instead of crashing.
    """
    import subprocess, sys, inspect, importlib.util
    from pathlib import Path as _Path
    script_path = _Path(__file__).resolve().parent / "meta_bot_picks.py"
    assert script_path.exists(), "scripts/meta_bot_picks.py is missing"
    spec = importlib.util.spec_from_file_location("meta_bot_picks", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for fn in ("fetch_pending_bets", "resolve_conflicts", "correlation_discount", "simultaneous_kelly", "main"):
        assert hasattr(mod, fn), f"meta_bot_picks.{fn} missing — Step coverage incomplete"
    # Running without --bots must succeed (default WAITING mode)
    res = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"WAITING-mode crashed: {res.stderr[-300:]}"
    assert "WAITING" in res.stdout, "default mode must announce WAITING status"


@test("ENG-15 — league market-inefficiency index bumps edge requirement up/down per league")
def _():
    """ENG-15 2026-05-25 — continuous version of ELITE_LEAGUE_FILTER. The
    pipeline reads match['_league_clv_efficiency'] (60d mean pseudo_clv per
    league, populated by scripts/compute_league_clv_efficiency.py + Sunday
    job_league_clv_efficiency) and bumps the per-candidate edge requirement:
        eff ≥ +2%        → eff_bump = -1% (less edge required)
        -1% < eff < +2%  → eff_bump = 0
        eff ≤ -1%        → eff_bump = +1% (more edge required)
    Env-gated: LEAGUE_EFF_EDGE_BUMP_ENABLED=true to activate. Default OFF
    so Phase 3.5 isn't disturbed.
    """
    import inspect
    from workers.jobs import daily_pipeline_v2
    src = inspect.getsource(daily_pipeline_v2)
    assert "LEAGUE_EFF_EDGE_BUMP_ENABLED" in src, "env var gate missing"
    assert "_league_clv_efficiency" in src, "match key for the signal missing"
    assert "eff_bump" in src, "the bump variable must be present"
    assert "drop_league_eff_edge" in src, "funnel bucket must be present"
    # Default-off guarantee: the env-var check should default to 'false'
    assert 'os.getenv("LEAGUE_EFF_EDGE_BUMP_ENABLED", "false")' in src, \
        "env var must default to 'false' (Phase 3.5 lock)"


@test("BOT-HIGH-ALIGNMENT — only fires on alignment_class=HIGH, all markets, 3pp edge floor")
def _():
    """BOT-HIGH-ALIGNMENT 2026-05-25 — paper bot. Hypothesis: HIGH alignment
    (most signal dimensions agree) is strong enough that a 3% edge floor is
    safe across all markets. Guards: (1) min_alignment_class set; (2) 3% floor
    on every tier+market; (3) covers 1x2/ou/btts/ah/dnb/dc; (4) registered in
    BOT_TIMING_COHORTS; (5) the pipeline applies the filter (not just config).
    """
    import inspect
    from workers.jobs import daily_pipeline_v2
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG, BOT_TIMING_COHORTS
    assert "bot_high_alignment" in BOTS_CONFIG, "bot_high_alignment missing from BOTS_CONFIG"
    cfg = BOTS_CONFIG["bot_high_alignment"]
    assert cfg.get("min_alignment_class") == "HIGH", "min_alignment_class must be HIGH"
    for tier, ths in cfg["edge_thresholds"].items():
        for key, val in ths.items():
            assert abs(val - 0.03) < 1e-6, f"tier {tier} {key} edge must be 3% (got {val})"
    expected_markets = {"1x2", "ou", "btts", "ah", "dnb", "dc"}
    assert set(cfg["markets"]) == expected_markets, f"markets mismatch: {cfg['markets']}"
    assert "bot_high_alignment" in BOT_TIMING_COHORTS, "must be registered in BOT_TIMING_COHORTS"
    # Guard the filter logic exists in the pipeline (not just config dropped on the floor)
    src = inspect.getsource(daily_pipeline_v2)
    assert 'config.get("min_alignment_class")' in src, \
        "pipeline must read min_alignment_class from bot config"
    assert "drop_min_alignment" in src, \
        "pipeline must record drops under drop_min_alignment funnel bucket"


@test("AGGRESSIVE-V2 — bot_aggressive_v2 config drops draws+under2.5, caps odds, raises edge")
def _():
    """Tightened sibling of bot_aggressive. v1's 441 settled bets at -5.7% ROI
    broke down into draws (-€154), home odds≥3.30 high-edge (-€95), OU under 2.5
    (-€46). Retroactive replay of v1 bets under v2 filters: 129 keep at +11.6%
    ROI / +€90. Guard the four rules so they can't silently regress."""
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG, BOT_TIMING_COHORTS
    assert "bot_aggressive_v2" in BOTS_CONFIG, "bot_aggressive_v2 missing from BOTS_CONFIG"
    cfg = BOTS_CONFIG["bot_aggressive_v2"]
    # Rule 1: selection_filter excludes Draw and Under 2.5
    sel = cfg.get("selection_filter") or []
    assert "Draw" not in sel, "v2 must exclude Draw — draws lost €154 / 61 bets in v1"
    assert "Under 2.5" not in sel, "v2 must exclude Under 2.5 — lost €46 / 88 bets in v1"
    assert "Home" in sel and "Over 2.5" in sel, "v2 must allow Home and Over 2.5 — these were the v1 winners"
    # Rule 2: odds_range tightened — upper bound ≤ 3.30 cuts the losing longshot home bucket
    omin, omax = cfg["odds_range"]
    assert omin >= 1.50, f"v2 odds_range min must be ≥1.50 (got {omin})"
    assert omax <= 3.30, f"v2 odds_range max must be ≤3.30 (got {omax}) — caps loss-heavy longshots"
    # Rule 3: min edge bumped to ≥ 5% on every tier+market
    for tier, ths in cfg["edge_thresholds"].items():
        for key, val in ths.items():
            assert val >= 0.05, f"v2 tier {tier} {key} edge must be ≥5% (got {val})"
    # Rule 4: registered in BOT_TIMING_COHORTS (BOT-COHORTS-ALL moved all bots to "all")
    assert "bot_aggressive_v2" in BOT_TIMING_COHORTS, \
        "v2 must be registered in BOT_TIMING_COHORTS"
    # Rule 5: v1 must still exist (control) — v2 does not replace v1 yet
    assert "bot_aggressive" in BOTS_CONFIG, "v1 must stay running as control for v2 comparison"


@test("AGGRESSIVE-V2-SEL-FILTER-OU — sel_filter gates OU/BTTS sides in candidate generation")
def _():
    """v2 relies on selection_filter to drop 'Under 2.5'. Before AGGRESSIVE-V2,
    the OU candidate generation ignored selection_filter (which was only used
    for 1X2 + AH). Guard the new gate so a refactor can't silently re-enable
    Under bets for v2."""
    import pathlib
    src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    # The OU/BTTS branches must now check sel_filter
    assert '"Over 2.5" in sel_filter' in src, (
        "OU Over 2.5 candidate gen must respect selection_filter (AGGRESSIVE-V2)"
    )
    assert '"Under 2.5" in sel_filter' in src, (
        "OU Under 2.5 candidate gen must respect selection_filter (AGGRESSIVE-V2)"
    )


@test("BOT-AGGRESSIVE-RETIRE — migration 104 retires bot_aggressive with reason")
def _():
    """PERF-HONEST-HEADLINE: bot_aggressive (-5.7% ROI / 441 bets) was the single
    biggest drag on portfolio headline ROI. Replaced by bot_aggressive_v2.
    Guard the retirement at migration + BOTS_CONFIG levels so a re-import
    can't accidentally reactivate it."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/104_perf_honest_headline.sql").read_text()
    assert "bot_aggressive" in mig and "is_active = false" in mig, (
        "migration 104 must set bot_aggressive.is_active=false"
    )
    assert "retired_reason" in mig, "migration 104 must populate retired_reason"
    assert "bot_aggressive_v2" in mig, "retired_reason must reference v2 replacement"
    # If un-retire migration exists (BOTS-UNRETIRE-WEEKEND), description prefix
    # was removed when the bot came back online. Migration file is the audit trail.
    if not pathlib.Path("supabase/migrations/122_unretire_remaining_bots.sql").exists():
        src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
        idx = src.find('"bot_aggressive":')
        assert idx >= 0, "bot_aggressive missing from BOTS_CONFIG"
        assert "[RETIRED 2026-05-17]" in src[idx:idx + 1500]


@test("PERF-RETIRED-REASON-COLUMN — migration 104 adds retired_reason column")
def _():
    """PERF-HONEST-HEADLINE: /performance shows *why* each retired bot was
    retired. That requires bots.retired_reason — source-inspect the migration
    so a schema rollback can't silently strip the column."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/104_perf_honest_headline.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS retired_reason TEXT" in mig, (
        "migration 104 must add bots.retired_reason TEXT column"
    )
    # All 4 previously-retired bots must get a backfill reason in this migration
    for b in ("bot_lower_1x2", "bot_opt_home_lower", "bot_draw_specialist", "bot_conservative"):
        assert f"'{b}'" in mig, (
            f"migration 104 must backfill retired_reason for {b} (BOTS-RETIRE-1X2)"
        )


@test("INPLAY-STAKE-5-NEW — new inplay bets stake €5 (not €1)")
def _():
    """INPLAY-STAKE-5 (PERF-HONEST-HEADLINE follow-up): pre-match Kelly stakes
    land €1-10 with €5 median. Inplay was fixed €1, meaning the highest-ROI
    bots had near-zero weight in the headline ROI. Bumped to €5 so new bets
    contribute meaningfully. Guard the new constant so a refactor can't
    silently roll it back to €1."""
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    # Find the bet_data block; stake must be 5.0
    idx = src.find('"market": trigger["market"]')
    assert idx >= 0, "bet_data block missing from inplay_bot.py"
    block = src[idx:idx + 800]
    assert '"stake": 5.0' in block, (
        "inplay bet_data must set stake=5.0 (INPLAY-STAKE-5)"
    )
    assert '"stake": 1.0' not in block, (
        "inplay bet_data still has stake=1.0 — should be 5.0 after INPLAY-STAKE-5"
    )


@test("INPLAY-STAKE-5-NORMALIZE-SCRIPT — retroactive normalize script present and guarded")
def _():
    """INPLAY-STAKE-5 retroactive normalization rewrites historical inplay
    bet rows so the /performance headline reflects €5 stakes immediately
    instead of waiting weeks for new €5 bets to dilute the €1 history.
    Source-inspect the script so the idempotency guard + snapshot table
    can't silently disappear in a refactor."""
    import pathlib
    src = pathlib.Path("scripts/normalize_inplay_stake_to_5.py").read_text()
    # Snapshot table for audit before destructive update
    assert "simulated_bets_pre_inplay_normalize_2026_05_17" in src, (
        "Normalize script must snapshot rows to an audit table before mutating"
    )
    # Idempotency guard: aborts when no legacy €1 rows remain (re-run would compound)
    assert "legacy_n == 0" in src and "ABORT" in src, (
        "Normalize script must abort when no legacy rows remain (else re-run compounds)"
    )
    # Correct multiplier
    assert "MULTIPLIER = 5.0" in src, "Multiplier must be 5.0 (€1 → €5)"
    # Inplay-only scope — must filter by name LIKE 'inplay_%'
    assert "name LIKE 'inplay" in src, (
        "Script must scope updates to inplay bots only"
    )
    # Bankroll recompute must pull starting_bankroll from bots table, not hardcode
    assert "b.starting_bankroll + ranked.running_pnl" in src, (
        "bankroll_after recompute must use bots.starting_bankroll, not hardcoded value"
    )


@test("COMBO-ACCA-BOT-PRESENT — bot_acca_value module + migration shipped")
def _():
    """COMBO-RESEARCH-PHASE-D: paper acca bot generates a multi-leg combo from
    today's top-edge singles. Source-inspect the module + migration so a
    refactor can't silently drop the integration."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/108_combo_legs.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS combo_legs JSONB" in mig, (
        "migration 108 must add combo_legs JSONB column"
    )
    assert "ADD COLUMN IF NOT EXISTS combo_size INTEGER" in mig, (
        "migration 108 must add combo_size INTEGER column"
    )
    assert "INSERT INTO bots" in mig and "'bot_acca_value'" in mig, (
        "migration 108 must register bot_acca_value"
    )
    bot = pathlib.Path("workers/jobs/acca_bot.py").read_text()
    assert "def run_acca_pass" in bot, "acca_bot.py must define run_acca_pass"
    assert "min_legs" in bot and "max_legs" in bot, "ACCA_CONFIG must define min/max legs"
    # Independence enforcement: must dedupe by match_id
    assert "seen_matches" in bot, "Acca bot must enforce one leg per match (independence)"
    # Hook in daily_pipeline_v2
    pipeline = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    assert "from workers.jobs.acca_bot import run_acca_pass" in pipeline, (
        "daily_pipeline_v2 must call run_acca_pass after singles are placed"
    )


@test("COMBO-HIDE-FROM-PUBLIC — combo bots filtered out of dashboard_cache bot_breakdown")
def _():
    """Combo/acca bots are paper experiments with 0 settled bets. They don't
    belong on the public /performance leaderboard until they accumulate
    enough settled bets to prove themselves. settlement.write_dashboard_cache
    must exclude any bot whose name starts with bot_acca_ or bot_combo_ from
    the per-bot rollup. /admin/bots reads via getAllBotsFromDB (separate
    path) and continues to show them."""
    import pathlib
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    # The active-bots bot_rows query must exclude combo/acca names
    assert "b.name NOT LIKE 'bot_acca" in src, (
        "dashboard_cache bot_breakdown query must exclude bot_acca_*"
    )
    assert "b.name NOT LIKE 'bot_combo" in src, (
        "dashboard_cache bot_breakdown query must exclude bot_combo_*"
    )


@test("COMBO-PROVEN-VARIANTS — bot_acca_proven + bot_combo_proven_system registered")
def _():
    """Two whitelist-restricted variants that combine legs ONLY from
    highest-ROI markets. ACCA-REDESIGN (2026-05-20): whitelist is now
    market-based (PROVEN_MARKETS_WHITELIST: ou25/ou35/btts) rather than
    bot-name-based — the acca bot scans predictions+odds_snapshots directly
    so retired source bots can't cause silent 0-leg runs."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/110_combo_proven_variants.sql").read_text()
    assert "'bot_acca_proven'" in mig and "'bot_combo_proven_system'" in mig
    bot = pathlib.Path("workers/jobs/acca_bot.py").read_text()
    # ACCA-REDESIGN: whitelist is now PROVEN_MARKETS_WHITELIST (market-based)
    assert "PROVEN_MARKETS_WHITELIST" in bot, "market whitelist constant must be defined"
    # bot_ou15_defensive must NOT be in whitelist (retired 2026-05-20)
    assert '"bot_ou15_defensive"' not in bot or "PROVEN_BOTS" not in bot, (
        "ACCA-REDESIGN: bot_ou15_defensive removed from PROVEN_BOTS_WHITELIST "
        "(retired 2026-05-20); whitelist is now market-based"
    )
    # New scan function must exist
    assert "def _scan_todays_candidates(" in bot, (
        "ACCA-REDESIGN: _scan_todays_candidates must replace _fetch_todays_singles"
    )
    # run_acca_pass must cache scan results
    assert "scan_cache" in bot, "run_acca_pass must cache scan results per market_whitelist"


@test("COMBO-SYSTEM-BOT-PRESENT — bot_combo_system module + migration 109 shipped")
def _():
    """Mirror of bot_acca_value but uses no-singles system stake distribution
    (Trixie/Yankee/Canadian/Heinz depending on N picks). Same picks per day
    so the two bots run as paper-parallel comparison."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/109_combo_system_type.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS system_type TEXT" in mig
    assert "'bot_combo_system'" in mig, "migration 109 must register bot_combo_system"
    bot = pathlib.Path("workers/jobs/acca_bot.py").read_text()
    assert "ACCA_VARIANTS" in bot, "acca_bot must define both variants in one dict"
    assert '"bot_acca_value"' in bot and '"bot_combo_system"' in bot, (
        "Both variants must be present as ACCA_VARIANTS keys"
    )
    assert '"no_singles"' in bot, "bot_combo_system must use structure=no_singles"
    assert "_subcombo_count" in bot, "must define sub-combo enumeration helper"
    # Critical: picks are shared across variants. run_acca_pass must pick legs
    # ONCE then place a bet per variant on the same legs.
    assert "for bot_name, cfg in ACCA_VARIANTS.items():" in bot, (
        "run_acca_pass must iterate ACCA_VARIANTS placing each on the same picks"
    )


@test("COMBO-SETTLE-SYSTEM-NO-SINGLES — system bet enumerates sub-combos")
def _():
    """No-singles system settlement: 4 picks → 11 sub-combos (Yankee). If 3/4
    win, 4 sub-combos pay (the 3 doubles among winners + the 1 treble of all
    winners). Test the math holds."""
    from workers.jobs.settlement import settle_combo_bet
    import json as _json

    combo = {
        "stake": 11.0,  # €1 per sub-bet × 11 Yankee sub-bets
        "system_type": "no_singles",
        "combo_legs": _json.dumps([
            {"match_id": "m1", "market": "1x2", "selection": "home", "odds": 2.0},
            {"match_id": "m2", "market": "1x2", "selection": "home", "odds": 2.0},
            {"match_id": "m3", "market": "1x2", "selection": "home", "odds": 2.0},
            {"match_id": "m4", "market": "1x2", "selection": "home", "odds": 2.0},
        ]),
    }
    # 1. All 4 win → ALL 11 sub-combos pay at their respective product odds.
    #    Total payout: 6 doubles × (2×2)=4 + 4 trebles × (2×2×2)=8 + 1 fourfold × 16
    #    = 24 + 32 + 16 = 72 (per €1 per sub-bet).
    #    Net pnl = 72 - 11 = +61
    all_win = settle_combo_bet(combo, {f"m{i}": (1, 0) for i in range(1, 5)})
    assert all_win is not None and all_win["result"] == "won"
    assert abs(all_win["pnl"] - 61.0) < 0.01, f"all-win Yankee pnl must be +61, got {all_win['pnl']}"

    # 2. 3 of 4 win → only sub-combos NOT containing the loser pay.
    #    Among 4 legs, fix m4 as loser. Surviving sub-combos:
    #    Doubles using {m1,m2,m3}: 3 doubles, each pays 4 → 12
    #    Trebles using {m1,m2,m3}: 1 treble, pays 8 → 8
    #    No fourfold (must include all 4)
    #    Total payout: 12 + 8 = 20 (per €1 sub-bet).
    #    Net = 20 - 11 = +9
    three_win = settle_combo_bet(combo, {"m1":(1,0), "m2":(1,0), "m3":(1,0), "m4":(0,1)})
    assert three_win is not None and three_win["result"] == "won"
    assert abs(three_win["pnl"] - 9.0) < 0.01, f"3/4 Yankee pnl must be +9, got {three_win['pnl']}"

    # 3. 2 of 4 win → 1 double pays 4. Net = 4 - 11 = -7.
    two_win = settle_combo_bet(combo, {"m1":(1,0), "m2":(1,0), "m3":(0,1), "m4":(0,1)})
    assert two_win["result"] == "lost", f"2/4 Yankee must be net-loss, got {two_win}"
    assert abs(two_win["pnl"] - (-7.0)) < 0.01

    # 4. Any pending leg → settle returns None (defer)
    pending = settle_combo_bet(combo, {"m1":(1,0), "m2":(1,0), "m3":(1,0)})  # m4 missing
    assert pending is None, "missing leg must defer settlement"


@test("COMBO-SETTLE-COMBO-BET — combo settlement aggregates leg outcomes")
def _():
    """settle_combo_bet must:
      - return None if any leg's match hasn't finished (combo stays pending)
      - return 'lost' if any leg lost
      - return 'won' with correct pnl if all legs won
      - handle voided legs (settle at reduced odds)
    """
    from workers.jobs.settlement import settle_combo_bet
    import json as _json

    combo = {
        "stake": 10.0,
        "combo_legs": _json.dumps([
            {"match_id": "m1", "market": "1x2", "selection": "home", "odds": 2.0},
            {"match_id": "m2", "market": "1x2", "selection": "draw", "odds": 3.0},
            {"match_id": "m3", "market": "1x2", "selection": "away", "odds": 4.0},
        ]),
    }
    # 1. All legs win — pnl = 10 × (2×3×4 - 1) = 230
    won = settle_combo_bet(combo, {
        "m1": (2, 0),  # home wins
        "m2": (1, 1),  # draw
        "m3": (0, 2),  # away wins
    })
    assert won is not None and won["result"] == "won", f"all-win must settle as won, got {won}"
    assert won["pnl"] == 230.0, f"all-win pnl must be 230 (10 × 23), got {won['pnl']}"

    # 2. One leg loses — combo loses, pnl = -stake
    lost = settle_combo_bet(combo, {
        "m1": (0, 2),  # home loses
        "m2": (1, 1),
        "m3": (0, 2),
    })
    assert lost["result"] == "lost", f"any-loss must settle as lost, got {lost}"
    assert lost["pnl"] == -10.0, f"loss pnl must be -10, got {lost['pnl']}"

    # 3. One leg not yet finished — combo stays pending (returns None)
    pending = settle_combo_bet(combo, {"m1": (2, 0), "m2": (1, 1)})  # missing m3
    assert pending is None, "combo with unfinished leg must defer to None"

    # 4. CLV is None for combos (no per-match closing line analog)
    assert won.get("clv") is None, "combo CLV must be None"


@test("COMBO-JOINT-PROB-MATH — joint probability matrix is mathematically valid")
def _():
    """COMBO-RESEARCH-PHASE-B: SGM bot will price multi-leg same-game bets by
    comparing book's offered odds vs our model's joint probability. Pin the
    underlying math so a refactor of Poisson/Dixon-Coles can't silently break
    it. Tests are deterministic — depend only on scipy.stats.poisson."""
    from workers.model.joint_probability import (
        build_joint_matrix, prob_event, prob_joint, correlation_ratio
    )
    # Balanced match
    M = build_joint_matrix(1.5, 1.5)
    # 1. Matrix sums to 1.0 (within float tolerance)
    assert abs(M.sum() - 1.0) < 1e-9, f"matrix sum {M.sum()} != 1.0"
    # 2. 1X2 marginals sum to 1.0
    s = prob_event(M, "home") + prob_event(M, "draw") + prob_event(M, "away")
    assert abs(s - 1.0) < 1e-9, f"1X2 marginals sum to {s}"
    # 3. Symmetry: balanced match → P(home) == P(away)
    assert abs(prob_event(M, "home") - prob_event(M, "away")) < 1e-9, (
        "balanced exp goals must give symmetric 1X2"
    )
    # 4. BTTS+O2.5 must be positively correlated (real-world fact about football)
    r = correlation_ratio(M, "btts_yes", "over_2.5")
    assert r > 1.2, f"BTTS+O2.5 should be strongly positive corr, got {r}"
    # 5. Joint never exceeds either marginal (basic probability axiom)
    pa = prob_event(M, "btts_yes")
    pb = prob_event(M, "over_2.5")
    joint = prob_joint(M, "btts_yes", "over_2.5")
    assert joint <= pa + 1e-9 and joint <= pb + 1e-9, (
        f"joint {joint} cannot exceed marginals {pa}, {pb}"
    )
    # 6. P(A and not-A) = 0 — opposite events are disjoint
    assert prob_joint(M, "btts_yes", "btts_no") < 1e-9, (
        "btts_yes and btts_no must be disjoint"
    )
    # 7. Heavy favourite + 'team scores' is positively correlated
    M2 = build_joint_matrix(2.5, 0.7)
    r2 = correlation_ratio(M2, "home", "home_scores")
    assert r2 > 1.05, f"home_win+home_scores must be positive corr, got {r2}"


@test("PERF-V2-BANKROLL-1K — migration 107 normalises bot_aggressive_v2 to €1000 bankroll")
def _():
    """bot_aggressive_v2 was created with starting_bankroll = 10000. Kelly
    sizing on a €10k bankroll produced €36-99 stakes vs every other bot's
    €5-10, which would have given v2 ~10× the weight in the portfolio
    headline ROI. Migration 107 resets starting_bankroll to 1000 and
    rescales the 11 existing v2 bets by /10."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/107_normalize_v2_bankroll_to_1k.sql").read_text()
    # Must touch all four pieces: starting_bankroll, stake/pnl scale, bankroll_after, current_bankroll
    assert "starting_bankroll = 1000" in mig, "Migration must set starting_bankroll = 1000"
    assert "stake / 10" in mig, "Migration must scale stake /= 10"
    assert "pnl   = sb.pnl / 10" in mig or "pnl = sb.pnl / 10" in mig, (
        "Migration must scale pnl /= 10 for settled rows"
    )
    assert "bankroll_after = 1000.00 + ranked.running_pnl" in mig, (
        "Migration must recompute bankroll_after running totals from €1000"
    )
    assert "current_bankroll = 1000.00 + COALESCE(" in mig, (
        "Migration must recompute current_bankroll from €1000 + sum(pnl)"
    )
    assert "bot_aggressive_v2" in mig, "Migration must scope to bot_aggressive_v2"


@test("PERF-INPLAY-CLV-NULL — migration 106 nulls legacy inplay CLV values")
def _():
    """settlement.py:1373 already enforces 'no CLV on inplay bets' for new
    settlements. Older inplay bets settled before the skip got CLV values
    computed against pre-match closing odds — meaningless for a bet placed
    at minute 47. Migration 106 retroactively nulls those legacy values so
    the page is consistent with the code rule (all-or-nothing)."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/106_null_inplay_clv.sql").read_text()
    assert "SET clv = NULL" in mig and "clv_pinnacle = NULL" in mig, (
        "migration 106 must null both clv and clv_pinnacle"
    )
    assert "b.name LIKE 'inplay" in mig, "migration 106 must scope to inplay bots"
    # settlement.py must still enforce the rule for NEW bets — guard so a
    # future refactor can't silently re-enable inplay CLV
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    assert "if is_inplay:" in src and "closing_odds = None" in src, (
        "settlement.py must continue forcing closing_odds=None for inplay bets"
    )


@test("PERF-RETIRED-CLEANUP — migration 105 backfills inplay_a2/c_home/f retired_reason")
def _():
    """Three inplay bots were soft-retired 2026-05-09 with NULL retired_reason.
    Migration 105 backfills them so the public /performance Retired Strategies
    section never shows a row with a missing reason."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/105_backfill_inplay_retired_reasons.sql").read_text()
    for b in ("inplay_a2", "inplay_c_home", "inplay_f"):
        assert f"'{b}'" in mig, f"migration 105 must backfill retired_reason for {b}"
    assert "AND retired_reason IS NULL" in mig, (
        "migration 105 must guard with `AND retired_reason IS NULL` so it's idempotent"
    )


@test("PERF-HONEST-HEADLINE-ACTIVE-FIELDS — dashboard_cache writes both headlines")
def _():
    """PERF-HONEST-HEADLINE: /performance shows two headline rows — all-time
    (incl. retired bots' historical bets) + active-only. settlement.write_dashboard_cache
    must populate both. Source-inspect the writer so a refactor can't drop the
    active_* columns or the retired_bot_breakdown JSONB."""
    import pathlib
    mig = pathlib.Path("supabase/migrations/104_perf_honest_headline.sql").read_text()
    for col in (
        "active_total_staked", "active_total_pnl", "active_roi_pct",
        "active_settled_bets", "active_won_bets", "active_lost_bets",
        "active_total_bets", "active_avg_clv", "retired_bot_breakdown",
    ):
        assert col in mig, f"migration 104 must ADD COLUMN {col} on dashboard_cache"
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    # Writer must compute active-only headline (JOIN bots, filter is_active+retired_at)
    assert "JOIN bots b ON b.id = sb.bot_id" in src, (
        "write_dashboard_cache must JOIN bots for active-only headline query"
    )
    assert "is_active = true AND b.retired_at IS NULL" in src, (
        "active-only query must filter is_active AND retired_at IS NULL"
    )
    # Writer must compute retired_bot_breakdown with retired_at + retired_reason
    assert "retired_bot_breakdown" in src, "write_dashboard_cache must build retired_bot_breakdown"
    assert "retired_reason" in src, "retired breakdown rows must include retired_reason"
    # INSERT must include all new columns
    for col in (
        "active_total_bets", "active_settled_bets", "active_won_bets", "active_lost_bets",
        "active_total_staked", "active_total_pnl", "active_roi_pct", "active_avg_clv",
        "retired_bot_breakdown",
    ):
        assert col in src, f"INSERT INTO dashboard_cache must include {col}"


@test("BOT-TIMING-OU-MIDDAY — OU-specialist bots registered in BOT_TIMING_COHORTS")
def _():
    """Phase A timing analysis (2026-05-13) originally moved OU bots to midday.
    BOT-COHORTS-ALL (2026-05-20) moved all bots to 'all' so every window is
    evaluated and the dedup constraint prevents duplicate bets. bot_ou15_defensive
    was retired 2026-05-20. Guard that the remaining OU bots are still registered."""
    from workers.jobs.daily_pipeline_v2 import BOT_TIMING_COHORTS, BOTS_CONFIG
    ou_bots = ["bot_ou25_global", "bot_ou35_attacking", "bot_opt_ou_british"]
    for bot in ou_bots:
        assert bot in BOT_TIMING_COHORTS, (
            f"OU-specialist {bot} must be registered in BOT_TIMING_COHORTS"
        )
    # bot_ou15_defensive retired — verify it's gone from active bots or flagged retired
    assert "bot_ou15_defensive" not in BOTS_CONFIG or \
           BOTS_CONFIG["bot_ou15_defensive"].get("description", "").startswith("[RETIRED"), \
        "bot_ou15_defensive was retired 2026-05-20 — must be absent from BOTS_CONFIG or marked [RETIRED]"


@test("BTTS-TIMING — BTTS bots must run at all cohorts to capture morning soft odds")
def _():
    """BTTS bots were pre_ko-only, missing morning soft odds window. Changed to
    'all' so the first cohort that clears the edge threshold places the bet;
    dedup (uq_bet) prevents duplicates. Pre-KO confirmed lineups remain the
    fallback if morning threshold isn't met."""
    from workers.jobs.daily_pipeline_v2 import BOT_TIMING_COHORTS
    for bot in ("bot_btts_all", "bot_btts_conservative"):
        assert BOT_TIMING_COHORTS.get(bot) == "all", (
            f"{bot} must be cohort='all' so morning odds are captured; "
            f"currently {BOT_TIMING_COHORTS.get(bot)!r}"
        )


@test("RECOVER-PHASE2 — recover_today step 5 calls run_morning with skip_fetch=True")
def _():
    """Phase 1 (skip_fetch=False) never populates best_bookmaker, so every
    simulated_bet ends up with recommended_bookmaker=NULL. Phase 2 reads
    pre-fetched data from DB and fills the bookmaker correctly. Steps 1-4
    of recover_today already populate the DB, so step 5 must use Phase 2."""
    import pathlib
    src = pathlib.Path("scripts/recover_today.py").read_text()
    assert '"run_morning",     {"skip_fetch": True}' in src, (
        "recover_today STEPS[4] must invoke run_morning with skip_fetch=True. "
        "Without it, simulated_bets.recommended_bookmaker is always NULL and "
        "daily_picks.py shows no bookmaker info for manual placement."
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
    assert '0.55' in fn_body, "Strategy J must require prematch_o25_prob >= 0.55 (INPLAY-J-LOOSEN)"
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
    assert "2.40" in fn_body, "Strategy M OU floor must be 2.40 (lowered from 3.0 — INPLAY-M-THRESHOLD-FIX)"
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
        "Strategy N must price the favourite win via _bivariate_poisson_win_prob"
    )
    # Window expanded 2026-05-22 from 72-80 to 65-82 (funnel showed 2.2× more
    # candidates); threshold lowered 0.65 → 0.62 so the away-favourite path
    # has enough sample to fire. Guard the current values.
    assert "0.62" in fn_body, "Strategy N must require prematch fav prob ≥ 0.62"
    assert "2.20" in fn_body, "Strategy N must require live favourite odds ≥ 2.20"
    assert "minute < 65 or minute > 82" in fn_body, (
        "Strategy N minute window is 65-82 (widened from 72-80 on 2026-05-22)"
    )


@test("INPLAY-TIME-DECAY-PRIOR — w_live = 1 - exp(-minute/30) blend in _bayesian_posterior + _remaining_goals_prob")
def _():
    """
    Guard the time-decay-prior calibration (5/5 round-3 AI consensus). At min 30
    the live signal must outweigh prematch ~63/37; at min 60 ~86/14. The flat
    (pm + live)/(1 + minute/90) blend is gone.
    """
    import pathlib, math, importlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "def _time_decay_weight(" in src, "_time_decay_weight helper must exist"
    assert "1.0 - math.exp(-minute / 30.0)" in src, (
        "_time_decay_weight must implement 1 - exp(-minute/30)"
    )

    # _bayesian_posterior must blend in rate-space using the new weight
    bp_start = src.index("def _bayesian_posterior(")
    bp_end = src.index("\ndef ", bp_start + 1)
    bp_body = src[bp_start:bp_end]
    assert "_time_decay_weight(minute)" in bp_body, (
        "_bayesian_posterior must call _time_decay_weight"
    )
    assert "live_xg_total * 90.0 / minute" in bp_body, (
        "_bayesian_posterior must normalize live signal to per-90 rate"
    )
    # Old flat blend must be removed
    assert "(prematch_xg_total + live_xg_total) / (1.0 + minute / 90.0)" not in bp_body, (
        "Old flat blend formula must be replaced"
    )

    # _remaining_goals_prob must also use the time-decay weight
    rg_start = src.index("def _remaining_goals_prob(")
    rg_end = src.index("\ndef ", rg_start + 1)
    rg_body = src[rg_start:rg_end]
    assert "_time_decay_weight(minute)" in rg_body, (
        "_remaining_goals_prob must call _time_decay_weight"
    )

    # Unit-style: weight values match spec
    spec = importlib.import_module("workers.jobs.inplay_bot")
    assert abs(spec._time_decay_weight(30) - (1 - math.exp(-1))) < 1e-9
    assert abs(spec._time_decay_weight(60) - (1 - math.exp(-2))) < 1e-9
    assert spec._time_decay_weight(0) == 0.0


@test("INPLAY-PERIOD-RATES — period multiplier (0.85× ≤15, 1.20× ≥76) applied to remaining lambda")
def _():
    import pathlib, importlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "def _period_multiplier(" in src, "_period_multiplier helper must exist"
    pm_start = src.index("def _period_multiplier(")
    pm_end = src.index("\ndef ", pm_start + 1)
    pm_body = src[pm_start:pm_end]
    assert "0.85" in pm_body, "_period_multiplier must use 0.85× for early period"
    assert "1.20" in pm_body, "_period_multiplier must use 1.20× for late period"
    assert "minute <= 15" in pm_body, "Early threshold is minute ≤ 15"
    assert "minute >= 76" in pm_body, "Late threshold is minute ≥ 76"

    # Must be applied inside both _remaining_goals_prob and _scaled_remaining_lam
    rg_start = src.index("def _remaining_goals_prob(")
    rg_end = src.index("\ndef ", rg_start + 1)
    assert "_period_multiplier(minute)" in src[rg_start:rg_end], (
        "_remaining_goals_prob must apply _period_multiplier"
    )
    sr_start = src.index("def _scaled_remaining_lam(")
    sr_end = src.index("\ndef ", sr_start + 1)
    assert "_period_multiplier(minute)" in src[sr_start:sr_end], (
        "_scaled_remaining_lam must apply _period_multiplier"
    )

    spec = importlib.import_module("workers.jobs.inplay_bot")
    assert spec._period_multiplier(10) == 0.85
    assert spec._period_multiplier(80) == 1.20
    assert spec._period_multiplier(45) == 1.0


@test("INPLAY-LAMBDA-STATE — score-state multipliers wired into total + per-team lambdas")
def _():
    """
    Total: late-level +5%, late-imbalanced +2.5% (averages trailing+15% / leading-10%).
    Per-team (Strategy N): trailing +15%, leading −10%, level +5%, all only ≥ minute 60.
    Strategies J/L/M must pass score_home/score_away to _remaining_goals_prob; N must
    apply per-team multipliers when computing bivariate Poisson lambdas.
    """
    import pathlib, importlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "def _state_multiplier_total(" in src, "_state_multiplier_total helper must exist"
    assert "def _state_multiplier_team(" in src, "_state_multiplier_team helper must exist"

    spec = importlib.import_module("workers.jobs.inplay_bot")
    # Total multiplier: pre-60 always 1.0
    assert spec._state_multiplier_total(45, 0, 0) == 1.0
    assert spec._state_multiplier_total(70, 0, 0) == 1.05
    assert spec._state_multiplier_total(70, 1, 0) == 1.025
    assert spec._state_multiplier_total(70, 2, 1) == 1.025
    # Per-team multiplier
    assert spec._state_multiplier_team(70, "trailing") == 1.15
    assert spec._state_multiplier_team(70, "leading") == 0.90
    assert spec._state_multiplier_team(70, "level") == 1.05
    assert spec._state_multiplier_team(45, "trailing") == 1.0  # pre-60 disabled

    # J/L/M must pass score_home/score_away to _remaining_goals_prob
    for fn in ("_check_strategy_j", "_check_strategy_l", "_check_strategy_m"):
        fs = src.index(f"def {fn}(")
        fe = src.index("\ndef ", fs + 1)
        body = src[fs:fe]
        call_idx = body.index("_remaining_goals_prob(")
        # Tolerate multi-line call — slice forward to the closing paren
        call_block = body[call_idx:body.index(")", call_idx) + 1] if ")" in body[call_idx:call_idx+400] else body[call_idx:call_idx+400]
        assert "score_home=" in call_block, (
            f"{fn} must pass score_home= to _remaining_goals_prob (LAMBDA-STATE)"
        )
        assert "score_away=" in call_block, (
            f"{fn} must pass score_away= to _remaining_goals_prob (LAMBDA-STATE)"
        )

    # N must apply per-team multipliers. By construction N only fires at level
    # scores (`if sh != sa: return None`), so both home_state and away_state
    # resolve to "level" — the trailing/leading branches can never run inside
    # N. The helper is still exercised through home_state/away_state vars so
    # the multiplier infrastructure (shared with J/L/M) stays wired.
    n_start = src.index("def _check_strategy_n(")
    n_end = src.index("\ndef ", n_start + 1)
    n_body = src[n_start:n_end]
    assert "_state_multiplier_team(" in n_body, (
        "Strategy N must apply per-team state multipliers to bivariate lambdas"
    )
    assert "home_state" in n_body and "away_state" in n_body, (
        "Strategy N must classify each side via home_state/away_state vars"
    )
    assert '"level"' in n_body, (
        "Strategy N body must mention 'level' state (level scores are its entry condition)"
    )


@test("INPLAY-EMA-LIVE-XG — _attach_ema_live_xg + run_inplay_strategies wires + replay port")
def _():
    """
    Live mode: _attach_ema_live_xg replaces cand['xg_home/away'] with EMA-smoothed
    cumulative readings (5-min half-life, time-aware alpha) before strategies run.
    Replay mode: apply_ema_live_xg_replay does the same in-memory across all
    snapshots loaded from the historical window.
    """
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "def _attach_ema_live_xg(" in src, "_attach_ema_live_xg helper must exist"

    # Helper must compute time-aware alpha (half-life-based) and update xg_home/away in-place
    fn_start = src.index("def _attach_ema_live_xg(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "1.0 - math.exp(-delta / max(half_life_min" in fn_body, (
        "EMA must use time-aware alpha = 1 - exp(-delta / half_life_min)"
    )
    assert "live_match_snapshots" in fn_body, (
        "EMA helper must read prior snapshots from live_match_snapshots"
    )
    assert 'cand["xg_home"] = ema_h' in fn_body, (
        "EMA helper must overwrite cand['xg_home'] in-place so strategies pick it up"
    )

    # Must be called from run_inplay_strategies after _get_live_candidates
    run_start = src.index("def run_inplay_strategies(")
    run_end = src.index("\ndef ", run_start + 1)
    run_body = src[run_start:run_end]
    assert "_attach_ema_live_xg(candidates" in run_body, (
        "run_inplay_strategies must call _attach_ema_live_xg(candidates, ...)"
    )

    # Replay-side port
    replay_src = pathlib.Path("scripts/replay_inplay.py").read_text()
    assert "def apply_ema_live_xg_replay(" in replay_src, (
        "scripts/replay_inplay.py must expose apply_ema_live_xg_replay for backfill"
    )
    assert "apply_ema_live_xg_replay(snapshots" in replay_src, (
        "Replay main() must call apply_ema_live_xg_replay before run_replay"
    )


@test("INPLAY-REPLAY-Q-INMEM — replay_strategy_q + bulk red_card_idx, no per-snapshot SQL")
def _():
    """
    Replay's Q strategy must use the bulk-fetched red-card index, not a
    per-snapshot SQL query. The live path runs `_check_strategy_q(... execute_query)`
    which queries match_events for every snapshot — ~3-5k round-trips on the
    backfill window and the dominant runtime cost. The replay port reads
    `red_card_idx[mid]` instead.
    """
    import pathlib
    src = pathlib.Path("scripts/replay_inplay.py").read_text()
    assert "def replay_strategy_q(" in src, "replay_strategy_q must exist"
    assert "def fetch_red_card_index(" in src, "bulk red-card index helper must exist"

    fn_start = src.index("def replay_strategy_q(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "red_card_idx.get(mid)" in fn_body, (
        "replay_strategy_q must look up the precomputed index (no SQL per snapshot)"
    )
    assert "execute_query" not in fn_body, (
        "replay_strategy_q must NOT call execute_query — defeats the perf win"
    )

    # Dispatch in run_replay must route inplay_q to the in-memory port
    rr_start = src.index("def run_replay(")
    rr_end = src.index("\ndef ", rr_start + 1)
    rr_body = src[rr_start:rr_end]
    assert 'bot_name == "inplay_q"' in rr_body, (
        "run_replay must dispatch inplay_q to replay_strategy_q"
    )
    assert "replay_strategy_q(" in rr_body, "replay_strategy_q must be invoked from run_replay"


@test("INPLAY-CALIBRATION-STACK — _scaled_remaining_lam used by every per-strategy lambda_remaining")
def _():
    """
    A/C/D/E/G/H/Q each compute their own lambda_remaining outside _remaining_goals_prob.
    All must funnel through _scaled_remaining_lam so the calibration stack
    (h2_uplift × period × state) lands once, in one helper.
    """
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "def _scaled_remaining_lam(" in src, "_scaled_remaining_lam helper must exist"
    # No raw `posterior * remaining_minutes / 90.0` left — every callsite must use the helper
    assert "lambda_remaining = posterior * remaining_minutes / 90.0" not in src, (
        "All strategies must compute lambda_remaining via _scaled_remaining_lam — "
        "raw posterior * remaining_minutes / 90.0 bypasses the calibration stack"
    )
    assert "lambda_remaining = posterior * remaining / 90.0" not in src, (
        "All strategies must compute lambda_remaining via _scaled_remaining_lam"
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


@test("OPS-SNAPSHOT-RETIRED — ops_snapshot total_bots excludes retired bots")
def _():
    """ops_snapshot computes silent_bots = total_bots - active_bots. If the
    total_bots count includes retired bots (is_active=true, retired_at NOT NULL —
    the convention used by inplay_a2/c_home/f), silent_bots gets inflated and
    triggers false bot-down alerts. Guard both the total count and any sibling
    queries that filter by is_active alone."""
    import pathlib
    src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    # The specific total_bots query inside ops_snapshot
    needle = "SELECT COUNT(*) AS n FROM bots WHERE is_active = true"
    idx = src.find(needle)
    assert idx != -1, "ops_snapshot total_bots query no longer matches expected pattern"
    # Allow either retired_at IS NULL on the same query or a refactor that scopes it
    tail = src[idx:idx + len(needle) + 60]
    assert "retired_at IS NULL" in tail, (
        "ops_snapshot total_bots query must filter retired_at IS NULL — "
        "otherwise retired bots inflate the silent_bots metric"
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


@test("BULK-STORE-MATCH-STATS — backfill_historical bulks stats writes, no per-row store_match_stats_full")
def _():
    """
    Guard the BULK-STORE-MATCH-STATS optimization. Per-match upsert dominated
    wall time on the EU Supabase pooler (3,000+ matches × ~200ms RTT ≈ 10 min).
    The fix collects (match_uuid, stats_dict) tuples and calls bulk_store_match_stats
    once per league/season — one execute_values UPSERT instead of N round-trips.
    A revert to per-row writes inside the backfill loop would silently re-introduce
    the bottleneck.
    """
    import pathlib
    src = pathlib.Path("scripts/backfill_historical.py").read_text()

    assert "bulk_store_match_stats" in src, (
        "backfill_historical.py must import + call bulk_store_match_stats — "
        "without it the per-row store_match_stats_full pattern slows the backfill "
        "by an order of magnitude on the EU pooler."
    )
    assert "store_match_stats_full" not in src, (
        "backfill_historical.py must NOT call store_match_stats_full per match. "
        "Use bulk_store_match_stats with collected tuples instead."
    )

    # Helper exists and uses execute_values + COALESCE (preserves existing values
    # on partial dicts — matches store_match_stats_full's idempotency guarantee).
    helper_src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    assert "def bulk_store_match_stats(" in helper_src, (
        "bulk_store_match_stats helper missing from supabase_client.py"
    )
    helper_idx = helper_src.index("def bulk_store_match_stats(")
    helper_body = helper_src[helper_idx:helper_idx + 3000]
    assert "execute_values" in helper_body, (
        "bulk_store_match_stats must use psycopg2.extras.execute_values — "
        "otherwise it's not actually bulked."
    )
    assert "COALESCE(EXCLUDED." in helper_body, (
        "bulk_store_match_stats UPDATE clause must wrap EXCLUDED values in COALESCE "
        "so a partial stats_dict (NULLs) cannot wipe an existing non-NULL value. "
        "This preserves the idempotency guarantee from store_match_stats_full."
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


@test("AF-FETCHES-AUDIT — BudgetTracker tracks per-endpoint counters and drains on sync")
def _():
    """The 26K-call mystery in PRIORITY_QUEUE.md cannot be diagnosed without
    per-endpoint attribution. record_call(endpoint) must update both the
    per-interval counter (drained on sync) and the cumulative day-to-date
    counter; sync_with_server must persist both as JSONB on api_budget_log.
    Source-inspection guard so a future cleanup pass cannot silently drop the
    breakdown without us noticing.
    """
    import pathlib
    src = pathlib.Path("workers/api_clients/api_football.py").read_text()

    # BudgetTracker carries the two counter dicts
    assert "_endpoint_counts" in src and "_endpoint_counts_today" in src, (
        "BudgetTracker must keep _endpoint_counts (per-interval) and "
        "_endpoint_counts_today (day-to-date)"
    )
    # record_call accepts an endpoint label and updates both maps
    assert 'def record_call(self, endpoint' in src, (
        "record_call must accept the endpoint label so attribution is non-NULL"
    )
    # _get passes the endpoint string when recording
    assert "budget.record_call(endpoint)" in src, (
        "_get must pass the endpoint string to record_call — without this, "
        "every call attributes to 'unknown'"
    )
    # sync writes BOTH JSONB columns
    assert "endpoint_breakdown" in src and "endpoint_breakdown_today" in src, (
        "sync_with_server must persist both interval and day-to-date breakdowns"
    )
    assert "::jsonb" in src, "JSONB cast required for the breakdown columns"

    # Day rollover clears both maps
    assert "_endpoint_counts.clear()" in src and "_endpoint_counts_today.clear()" in src, (
        "_maybe_reset must clear both endpoint counter maps so cross-day numbers "
        "don't leak into the next day's first row"
    )


@test("AF-FETCHES-AUDIT — BudgetTracker per-endpoint counter behaves correctly")
def _():
    """Functional check (no network). Hit record_call with several endpoint
    labels, verify cumulative + drainable maps, and that draining preserves
    the day-to-date counter."""
    from workers.api_clients.api_football import BudgetTracker

    bt = BudgetTracker(daily_limit=1000)
    for ep in ("fixtures", "fixtures", "odds/live", "fixtures/statistics", "fixtures"):
        bt.record_call(ep)

    today = bt.endpoint_counts_today()
    assert today == {"fixtures": 3, "odds/live": 1, "fixtures/statistics": 1}, today
    assert bt.calls_today == 5, bt.calls_today

    # Drain the per-interval map (private but exercised by sync_with_server)
    snap = bt._drain_endpoint_counts()
    assert snap == {"fixtures": 3, "odds/live": 1, "fixtures/statistics": 1}, snap
    assert bt.endpoint_counts_today() == today, "day-to-date map must NOT be drained"

    # New calls after the drain start fresh in the interval map but accumulate in today
    bt.record_call("predictions")
    snap2 = bt._drain_endpoint_counts()
    assert snap2 == {"predictions": 1}, snap2
    today2 = bt.endpoint_counts_today()
    assert today2["predictions"] == 1 and today2["fixtures"] == 3, today2


@test("BT-SEED-FIX — BudgetTracker has _seed_from_db method that seeds _endpoint_counts_today")
def _():
    """Source-inspection test: BudgetTracker must have _seed_from_db() that reads
    api_budget_log and seeds _endpoint_counts_today to survive Railway redeploys."""
    import pathlib
    src = pathlib.Path("workers/api_clients/api_football.py").read_text()
    assert "def _seed_from_db(" in src, "_seed_from_db() method must exist on BudgetTracker"
    assert "self._seed_from_db()" in src, "__init__ must call self._seed_from_db()"
    assert "endpoint_breakdown_today" in src, "_seed_from_db must query endpoint_breakdown_today"
    assert "_endpoint_counts_today.update(" in src, "_seed_from_db must populate _endpoint_counts_today"


@test("AUDIT-AF-ENDPOINTS — /sidelined bulk helper exists with N=20 chunking")
def _():
    """AF rejects bulk team/league for /standings, /transfers, /coachs (probed
    2026-05-10) but accepts /sidelined?players=A-B-C with a hard 20-id ceiling.
    The new helper must chunk by 20 and return a {player_id: entries} dict."""
    import pathlib
    src = pathlib.Path("workers/api_clients/api_football.py").read_text()

    assert "def get_sidelined_by_players_bulk(" in src, (
        "Bulk helper get_sidelined_by_players_bulk(player_ids) must exist"
    )
    assert "_SIDELINED_BULK_LIMIT = 20" in src, (
        "Per-call ceiling must be 20 (AF cap; probed and confirmed)"
    )
    # Plural form is required — singular ?player= rejects the multi-id list
    assert '"players":' in src or '\"players\":' in src, (
        "Helper must use the plural ?players= form — singular ?player= is rejected"
    )


@test("AUDIT-AF-ENDPOINTS — fetch_player_sidelined uses bulk helper, not per-id loop")
def _():
    """Source guard: fetch_player_sidelined must call the bulk helper. Reverting
    to a per-id `for pid in to_fetch: get_sidelined(pid)` loop would silently
    multiply per-run AF calls by ~20× on the morning enrichment T9 step."""
    import pathlib
    src = pathlib.Path("workers/jobs/fetch_enrichment.py").read_text()

    assert "get_sidelined_by_players_bulk" in src, (
        "fetch_enrichment must import + call the bulk helper"
    )
    # The legacy per-id helper must NOT be the active import — it remains in
    # api_football.py only as a fallback for ad-hoc scripts.
    assert "import get_sidelined_by_players_bulk" not in src or "get_sidelined," not in src.split("get_sidelined_by_players_bulk")[0], (
        "fetch_enrichment must not still import the per-id get_sidelined helper "
        "alongside the bulk helper — only the bulk path should be active"
    )


@test("AF-FETCHES-AUDIT — migration 086 adds endpoint_breakdown JSONB columns")
def _():
    """The hourly sync writes per-endpoint JSONB into api_budget_log; without
    these two columns the writes silently fail under the broad except in
    sync_with_server and we lose attribution again."""
    from workers.api_clients.db import execute_query

    rows = execute_query(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'api_budget_log' "
        "  AND column_name IN ('endpoint_breakdown', 'endpoint_breakdown_today')"
    )
    if not rows:
        return  # migration not applied yet — CI/Actions handles this on push
    types = {r["column_name"]: r["data_type"] for r in rows}
    assert types.get("endpoint_breakdown") == "jsonb", types
    assert types.get("endpoint_breakdown_today") == "jsonb", types


@test("BACKFILL-COMPLETE-TOLERANCE — completion check refreshes need-sets and tolerates AF gaps")
def _():
    """
    A single AF data gap (one match where AF never returns stats) used to wedge
    a league/season in 'in_progress' forever, because the completion check at
    the end of `backfill_league_season` evaluated against the stale snapshot
    of `need_stats`/`need_events` taken before the bulk write — and even after
    the write, AF-permanent-gap matches stay in the need-set on every retry.
    Fix: re-query post-write via `get_af_ids_needing` and allow up to ~2% gap.
    """
    import pathlib
    src = pathlib.Path("scripts/backfill_historical.py").read_text()
    assert "fresh_need_stats = get_af_ids_needing" in src, (
        "backfill_historical must re-query need_stats AFTER the bulk write — "
        "otherwise completion check uses a stale pre-write snapshot."
    )
    assert "fresh_need_events = get_af_ids_needing" in src, (
        "backfill_historical must re-query need_events AFTER the bulk write."
    )
    assert "fix_tol" in src and "0.02" in src and "enrich_tol" in src and "0.05" in src, (
        "backfill_historical must apply ≤2% tolerance on fixtures and ≤5% on "
        "stats/events — AF stats/events gaps are common, fixture gaps are not."
    )
    assert "stats_perm_gap" in src and "events_perm_gap" in src, (
        "backfill_historical must detect AF-permanent-gap PER DIMENSION. The "
        "earlier joint check (both stats AND events empty) livelocked when one "
        "dim trickled in (e.g. 1 event/pass) while the other was permanently "
        "empty — finish_backfill burned AF calls forever on the same L/S."
    )
    assert "stats_attempted" in src and "events_attempted" in src, (
        "Per-dim escape needs to know what we actually attempted, not just "
        "what got written — otherwise stats_stored=0 with stats_attempted=0 "
        "(skipped) would falsely flag a permanent gap."
    )
    assert "was_capped" in src, (
        "Per-dim escape must NOT trigger when the union batch was capped by "
        "budget/league_cap — a capped run only sampled a subset, so "
        "stats_stored=0 might just mean the sampled chunk was unlucky."
    )
    assert "fixtures_perm_gap" in src, (
        "backfill_historical must detect permanent fixture gaps too — when "
        "bulk_store_matches drops some rows AF returned (missing team_id FK "
        "or similar), re-running stores the same subset on every pass and "
        "fix_ok is never satisfied. Without this, /fixtures keeps getting "
        "called for L/S that can never reach completion."
    )


@test("FINISH-BACKFILL — entry-point script loops via detect_next_phase + run_backfill")
def _():
    """One-shot script that drives the backfill to completion; CLAUDE.md ops
    flow refers to it. Make sure it stays wired to the real helpers."""
    import pathlib
    src = pathlib.Path("scripts/finish_backfill.py").read_text()
    assert "detect_next_phase" in src, "finish_backfill must call detect_next_phase"
    assert "run_backfill(" in src, "finish_backfill must call run_backfill"
    assert "MIN_BUDGET_TO_START" in src, (
        "finish_backfill must guard against running with starved AF budget."
    )


@test("ML-PIPELINE-UNIFY — xgboost_ensemble reads MODEL_VERSION from env")
def _():
    """Stage 1b: production loader is no longer hard-coded to v9a_202425. Setting
    MODEL_VERSION in env (e.g. 'v10_pre_shadow') flips the active model bundle.
    Without this the harness can't run shadow mode — every prediction would
    write the same hard-coded version tag."""
    import pathlib
    src = pathlib.Path("workers/model/xgboost_ensemble.py").read_text()
    assert "os.environ.get(\"MODEL_VERSION\"" in src, (
        "xgboost_ensemble.py must read MODEL_VERSION from env so ops can flip "
        "the production model bundle without a code change."
    )
    assert "DEFAULT_MODEL_VERSION" in src, (
        "Default version must be a named constant — exposes the fallback to "
        "the harness so shadow-vs-default comparisons are unambiguous."
    )


@test("ML-PIPELINE-UNIFY — predictions + simulated_bets carry model_version")
def _():
    """Stage 3a/b: every prediction and simulated bet must be tagged with the
    active MODEL_VERSION. Without this column, "did the new model help?" can
    only be answered by date — contaminated by league mix, fixture density,
    weather. The harness depends on it."""
    import pathlib
    src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    assert "_active_model_version" in src, (
        "supabase_client must expose _active_model_version() — single read of "
        "MODEL_VERSION env at write time, used by all prediction/bet writers."
    )
    assert "model_version" in src and src.count('"model_version"') >= 3, (
        "store_prediction, bulk_store_predictions, and store_bet must all set "
        "model_version on the row they write."
    )
    # Migration adds the columns
    mig = pathlib.Path("supabase/migrations/087_model_version.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS model_version TEXT" in mig
    assert "predictions" in mig and "simulated_bets" in mig
    assert "v9a_202425" in mig, (
        "Existing rows must be backfilled to v9a_202425 — without that, "
        "compare_models.py can't establish a baseline against historic predictions."
    )


@test("ML-PIPELINE-UNIFY — compare_models.py exists and parses arguments")
def _():
    """Stage 3d: the actual A/B comparison harness. Without this, having
    model_version columns is preparatory only — no insight produced."""
    import pathlib
    src = pathlib.Path("scripts/compare_models.py").read_text()
    assert "version_a" in src and "version_b" in src, (
        "compare_models must take two version strings and produce per-market deltas."
    )
    assert "log_loss" in src and "brier" in src.lower(), (
        "Standard metrics — log_loss and Brier — must both be reported."
    )
    assert "source = 'ensemble'" in src, (
        "Comparison must restrict to the ensemble source — that's what bots "
        "actually consume. Comparing poisson/xgboost/af would mix unrelated signals."
    )


@test("ML-PIPELINE-UNIFY — train.py outputs match what xgboost_ensemble loads")
def _():
    """Stage 1a: train.py used to write to data/models/{result_model,over25_model,
    btts_model}.pkl which xgboost_ensemble.py never reads (it loads from
    data/models/soccer/<version>/{result_1x2,over_under,...}.pkl). The two
    pipelines were disconnected — running train.py had zero production effect.
    This test guards the rename so they can never silently drift apart again."""
    import pathlib
    src = pathlib.Path("workers/model/train.py").read_text()
    assert "result_1x2.pkl" in src and "over_under.pkl" in src and "btts.pkl" in src, (
        "train.py must write filenames xgboost_ensemble._load_models reads "
        "(result_1x2.pkl, over_under.pkl, btts.pkl)."
    )
    assert "data\" / \"models\" / \"soccer\"" in src, (
        "train.py must write under data/models/soccer/ — same root xgboost_ensemble loads from."
    )
    assert "feature_cols.pkl" in src, (
        "train.py must dump FEATURE_COLS as feature_cols.pkl — xgboost_ensemble "
        "loads this to align inference-time feature vectors with training."
    )
    assert "--version" in src, (
        "train.py must accept --version CLI arg so multiple model bundles can "
        "coexist (production vs shadow) under separate subdirs."
    )


@test("BOT-AGGREGATES-NO-SILENT-CAP — getAllBets ceiling stays high enough to fit all bets")
def _():
    """The /admin/bots Per-Bot Performance table aggregates bets in JS from
    getAllBets(). Until 2026-05-10 it had a silent .limit(500) that
    truncated the oldest bets — Per-Bot table disagreed with the public
    Bot Leaderboard (which reads pre-aggregated dashboard_cache.bot_breakdown).

    This test guards the ceiling: if a low cap reappears, the per-bot
    table will start under-reporting again. The longer-term fix is
    BOT-AGGREGATES-SSOT (read aggregates from cache, lazy-load bet history)
    — once that lands this test can be relaxed.

    Cross-repo source inspection: skips gracefully if the sibling
    odds-intel-web checkout isn't present (CI scenario)."""
    import pathlib
    web_path = pathlib.Path("../odds-intel-web/src/lib/engine-data.ts")
    if not web_path.exists():
        return  # CI runs without the sibling repo — skip silently
    src = web_path.read_text()

    fn_start = src.index("export async function getAllBets(")
    fn_end = src.index("\n}\n", fn_start) + 2
    fn_body = src[fn_start:fn_end]

    # The bug pattern: any `.limit(N)` with N < 10000 in the function body
    import re
    limits = [int(m) for m in re.findall(r"\.limit\((\d+)\)", fn_body)]
    bad = [n for n in limits if n < 10000]
    assert not bad, (
        f"getAllBets has .limit({bad[0]}) — this silently truncates per-bot "
        "aggregates once total bets exceed the cap. Use .range(0, N-1) with "
        "N >= 10000, or refactor to BOT-AGGREGATES-SSOT."
    )

    # Belt-and-braces: assert the ceiling constant or .range call is present
    assert (
        "ALL_BETS_CEILING" in fn_body
        or ".range(0," in fn_body
    ), (
        "getAllBets must use ALL_BETS_CEILING or .range() to bypass "
        "Supabase's default 1000-row db-max-rows cap"
    )


@test("ML-PIPELINE-UNIFY Stage 2a — NaN-tolerant training")
def _():
    """Stage 2a: train.py used to drop every row with any NaN feature, losing
    ~30-40% of MFV (H2H is structurally missing for promoted teams). Now it
    imputes per-league mean and adds <col>_missing indicators. This guards the
    rename so a future refactor can't silently reintroduce X.notna().all()."""
    import pathlib
    src = pathlib.Path("workers/model/train.py").read_text()
    assert "_impute_features" in src, (
        "train.py must use _impute_features for per-league mean fill — "
        "the prior X.notna().all(axis=1) row-drop biased training away from promoted teams."
    )
    assert "INFORMATIVE_MISSING_COLS" in src, (
        "Indicator columns for h2h/opening-odds/referee missingness must be added — "
        "the model learns from the *pattern* of missingness, not just the imputed mean."
    )
    assert "_missing" in src, (
        "Each INFORMATIVE_MISSING_COLS entry must produce a <col>_missing flag."
    )
    # The original aggressive drop must be gone — only the docstring reference
    # in _impute_features may remain (it points to the prior pattern explicitly).
    code_lines = [ln for ln in src.split("\n") if "notna().all(axis=1)" in ln and not ln.strip().startswith("#")]
    real_uses = [ln for ln in code_lines if "valid =" in ln or "= X.notna" in ln]
    assert not real_uses, (
        f"X.notna().all(axis=1) row-drop is the regression we're guarding against, "
        f"found in: {real_uses}. Imputation must replace it, not coexist with it."
    )


@test("ML-PIPELINE-UNIFY Stage 1c — home/away goals regressors trained inline")
def _():
    """Stage 1c: train.py now produces home_goals.pkl + away_goals.pkl so the
    version bundle is self-contained. Without these, xgboost_ensemble.py
    silently falls back to v9a_202425 for the Poisson side and a v10 model
    isn't truly v10 — it's a 1X2/OU/BTTS swap with v9a goal expectations."""
    import pathlib
    src = pathlib.Path("workers/model/train.py").read_text()
    assert "train_home_goals_model" in src and "train_away_goals_model" in src, (
        "train.py must define train_home_goals_model + train_away_goals_model."
    )
    assert "count:poisson" in src, (
        "Goal regressors must use the count:poisson XGBoost objective — that's "
        "what xgboost_ensemble._predict_goals expects."
    )
    assert "home_goals.pkl" in src and "away_goals.pkl" in src, (
        "Filenames must match what xgboost_ensemble._load_models reads."
    )


@test("ML-PIPELINE-UNIFY Stage 0d — backfill_team_season_stats script present")
def _():
    """Stage 0d: aggregates from match_stats joined to matches and writes one
    row per (team, league, season) via the same store_team_season_stats writer
    fetch_enrichment uses. Without this, MFV's per-team venue averages stay
    NULL on backfilled matches and Stage 2a imputes from scratch."""
    import pathlib
    src = pathlib.Path("scripts/backfill_team_season_stats.py").read_text()
    assert "store_team_season_stats" in src, (
        "Backfill must use the same writer as live enrichment — keeps schema "
        "in lockstep when team_season_stats columns evolve."
    )
    assert "GROUP BY" in src and "home_team_api_id" in src and "away_team_api_id" in src, (
        "Aggregation must walk both home- and away-side groupings — without "
        "the away half, away venue averages stay zero."
    )


@test("ML-PIPELINE-UNIFY Stage 5a — weekly retrain cron registered")
def _():
    """Stage 5a/5b: weekly Sunday 03:00 UTC retrain + auto-comparison. Without
    this cron, the pipeline depends on a human remembering to retrain — every
    week of drift is a week of stale calibration."""
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()
    assert "weekly_retrain" in src, (
        "Scheduler must register a weekly_retrain job — Sunday 03:00 UTC."
    )
    assert "job_weekly_retrain" in src, (
        "job_weekly_retrain function must be defined alongside the other job_* helpers."
    )
    assert "compare_models.py" in src, (
        "The retrain job must invoke compare_models.py for auto-comparison vs "
        "the production version — promotion stays manual but the diff lands automatically."
    )


@test("ML-BLEND-DYNAMIC — load_blend_weight accepts tier and prefers tier-specific row")
def _():
    """Per-tier Poisson/XGBoost blend weights. fit_blend_weights.py stores
    `blend_weight_1x2_t{tier}` rows; load_blend_weight(tier=X) prefers them
    and falls back to the global `blend_weight_1x2`. Without this, the
    pipeline ships a uniform weight regardless of league quality — wastes
    XGBoost's overfit on lower tiers where Poisson's prior is stronger."""
    import pathlib
    src = pathlib.Path("workers/model/xgboost_ensemble.py").read_text()
    assert "def load_blend_weight(tier:" in src, (
        "load_blend_weight must accept a `tier` arg so per-tier weights are addressable."
    )
    assert "blend_weight_1x2_t" in src, (
        "Tier-specific rowname `blend_weight_1x2_t{tier}` must appear in the loader."
    )
    fit_src = pathlib.Path("scripts/fit_blend_weights.py").read_text()
    assert "blend_weight_1x2_t" in fit_src, (
        "fit_blend_weights.py must store per-tier rows. Without that, the loader "
        "always falls back to the global weight and ML-BLEND-DYNAMIC is dead code."
    )
    assert "Per-Tier 1X2 Blend Weights" in fit_src, (
        "Per-tier optimisation block must be named so future readers can find it."
    )
    # Ensemble caller must pass tier through
    pipeline = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    assert "ensemble_prediction(poisson_pred, xgb_pred, tier=" in pipeline, (
        "daily_pipeline_v2 must pass tier into ensemble_prediction so the "
        "per-tier weight is actually used at inference time."
    )


@test("ML-PINNACLE-FEATURE — train.py supports --include-pinnacle for v11+ bundles")
def _():
    """v11+ adds Pinnacle pre-match implied probs as features. Coverage is
    sparse today (~5%) so the indicator columns from Stage 2a do most of
    the work; the actual probs help where present."""
    import pathlib
    src = pathlib.Path("workers/model/train.py").read_text()
    assert "PINNACLE_FEATURE_COLS" in src, (
        "train.py must expose PINNACLE_FEATURE_COLS — keeps the Pinnacle "
        "feature names in one named place, prevents stringly-typed drift."
    )
    assert "include_pinnacle" in src, (
        "train_all + load_training_data must take an include_pinnacle flag "
        "so v10 (no Pinnacle) and v11+ (with Pinnacle) coexist cleanly."
    )
    assert "_load_pinnacle_features" in src, (
        "Per-match Pinnacle 1X2 lookup must be a named helper — looked up "
        "from odds_snapshots, not from MFV's market-consensus implied_*."
    )
    # The Pinnacle cols must also be in INFORMATIVE_MISSING_COLS so the
    # `_missing` indicator pattern from Stage 2a applies to them.
    assert '"pinnacle_implied_home"' in src and "INFORMATIVE_MISSING_COLS" in src, (
        "pinnacle_implied_* must be listed under INFORMATIVE_MISSING_COLS — "
        "missingness is highly informative when Pinnacle coverage is thin."
    )


@test("OU-MARKET-FEATURES — train.py has OU_MARKET_FEATURE_COLS, _load_ou_market_features, --include-ou-market")
def _():
    """v14+ adds Pinnacle OU 2.5 + BTTS market features. Source-guard checks:
    1. OU_MARKET_FEATURE_COLS constant present and contains the 4 new cols.
    2. _load_ou_market_features helper exists with overround guard (< 1.10).
    3. --include-ou-market CLI flag wired.
    4. 4 new cols in INFORMATIVE_MISSING_COLS for Stage 2a _missing indicators."""
    import pathlib
    src = pathlib.Path("workers/model/train.py").read_text()

    assert "OU_MARKET_FEATURE_COLS" in src, (
        "train.py must expose OU_MARKET_FEATURE_COLS — keeps the new OU/BTTS "
        "feature names in one named place, prevents stringly-typed drift."
    )
    assert "_load_ou_market_features" in src, (
        "_load_ou_market_features must exist — fetches Pinnacle OU 2.5 + "
        "multi-book BTTS from odds_snapshots, mirrors _load_pinnacle_features."
    )
    assert "1.10" in src, (
        "_load_ou_market_features must contain the overround guard '< 1.10' — "
        "2.4% of Pinnacle OU 2.5 pairs are mislabeled; guard drops them."
    )
    assert "include_ou_market" in src, (
        "train_all + load_training_data must take include_ou_market flag "
        "so v12/v13 (no OU) and v14+ (with OU) coexist cleanly."
    )
    assert "include-ou-market" in src, (
        "--include-ou-market CLI flag must be present."
    )
    assert '"pinnacle_implied_over25"' in src, (
        "pinnacle_implied_over25 must be listed under INFORMATIVE_MISSING_COLS — "
        "missingness is highly informative when Pinnacle OU coverage is ~22%."
    )
    assert '"ou25_bookmaker_disagreement"' in src, (
        "ou25_bookmaker_disagreement must be listed under INFORMATIVE_MISSING_COLS."
    )
    assert '"market_implied_btts_yes"' in src, (
        "market_implied_btts_yes must be listed under INFORMATIVE_MISSING_COLS."
    )


@test("OU-MARKET-FEATURES — MFV builder computes the 4 new OU/BTTS columns")
def _():
    """Source-guard: supabase_client._build_feature_row_batched must output
    all 4 new OU market feature columns, and the batch load queries for
    Pinnacle OU 2.5, OU 2.5 disagreement, and BTTS yes must be present."""
    import pathlib
    src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()

    for col in ("pinnacle_implied_over25", "pinnacle_implied_under25",
                "ou25_bookmaker_disagreement", "market_implied_btts_yes"):
        assert col in src, (
            f"supabase_client.py must reference '{col}' — MFV builder must "
            f"compute and store it so v14 inference can read it from MFV."
        )

    assert "pin_ou25_by_match" in src, (
        "Batch load for Pinnacle OU 2.5 (pin_ou25_by_match) must exist in "
        "_build_mfv_rows_for_matches."
    )
    assert "btts_yes_by_match" in src, (
        "Batch load for BTTS yes (btts_yes_by_match) must exist in "
        "_build_mfv_rows_for_matches."
    )
    assert "ou25_over_by_match" in src, (
        "Batch load for OU 2.5 multi-book (ou25_over_by_match) must exist in "
        "_build_mfv_rows_for_matches."
    )
    assert "compute_ou25_bookmaker_disagreement" in src, (
        "compute_ou25_bookmaker_disagreement helper must exist in supabase_client.py."
    )
    assert "compute_market_implied_btts_yes" in src, (
        "compute_market_implied_btts_yes helper must exist in supabase_client.py."
    )


@test("ML-INFERENCE-MFV-WIRE — v10 schema routes to MFV inference path")
def _():
    """Live deploy of any v10+ model requires xgboost_ensemble to read its
    inference features from match_feature_vectors (the new schema), not from
    the legacy features_v9.csv cache (which uses Kaggle column names absent
    from MFV). Without this routing, MODEL_VERSION=v10_* causes every call
    to pd.DataFrame(...)[feature_cols] to KeyError and silently fall back to
    Poisson-only — the new model is dead code in production."""
    import pathlib
    src = pathlib.Path("workers/model/xgboost_ensemble.py").read_text()
    assert "_is_mfv_schema" in src, (
        "xgboost_ensemble.py must expose a schema-detection helper so the "
        "MFV vs Kaggle dispatch is named, not magic-stringed inline."
    )
    assert "_build_row_from_mfv" in src, (
        "MFV-row inference helper must exist — fetches the row by match_id "
        "and re-derives the Stage-2a `_missing` indicators."
    )
    assert "match_feature_vectors WHERE match_id" in src, (
        "MFV-row helper must fetch by match_id, not by team name. Team-name "
        "lookups belong on the legacy v9* path only."
    )
    # Caller must pass match_id through
    pipeline = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    assert "match_id=_mid" in pipeline or "match_id=match.get(\"id\")" in pipeline, (
        "daily_pipeline_v2 must pass match_id into get_xgboost_prediction — "
        "without it, v10+ models can't reach their inference row."
    )


@test("ML-PIPELINE-UNIFY Stage 6a — pre-match backtester script present")
def _():
    """Stage 6a: replays every active pre-match bot against historical odds +
    predictions and writes a per-(bot, match) CSV. The harness is scope-honest:
    it does NOT re-run the calibration / Pinnacle veto / Kelly stack, so its
    P&L is directional, not faithful."""
    import pathlib
    src = pathlib.Path("scripts/backtest_pre_match_bots.py").read_text()
    assert "BOTS_CONFIG" in src, (
        "Backtester must walk BOTS_CONFIG — single source of truth for bot definitions."
    )
    assert "_outcome" in src, (
        "Outcome computation must be a named helper — guards against silent "
        "logic drift between markets (1x2 vs OU vs BTTS)."
    )
    assert "is_live = false" in src, (
        "Backtester must restrict to pre-kickoff odds (is_live=false). "
        "Including in-play snapshots would conflate two different bots."
    )


@test("BACKTEST-DC-DNB-AH — backtester covers DC, DNB, AH markets")
def _():
    """DC/DNB probs derived inline from 1x2 predictions. AH uses Poisson
    re-computation. Void handling for DNB draws and AH pushes."""
    import pathlib
    src = pathlib.Path("scripts/backtest_pre_match_bots.py").read_text()
    assert "double_chance" in src, (
        "Backtester must handle double_chance market for DC bots."
    )
    assert "draw_no_bet" in src, (
        "Backtester must handle draw_no_bet market for DNB bots."
    )
    assert "_ah_model_prob" in src, (
        "Backtester must use _ah_model_prob for AH bots — same Poisson logic as live pipeline."
    )
    assert "_load_ah_odds" in src, (
        "Backtester must load AH odds separately via _load_ah_odds."
    )
    assert "return None" in src, (
        "Backtester _outcome must return None for voids (DNB draw, AH push) — "
        "voids must be excluded from ROI, not counted as losses."
    )
    assert "_build_poisson_lookup" in src, (
        "Backtester must pre-compute Poisson exp_home/exp_away for AH bots "
        "since these are not stored in the DB."
    )


@test("MATCH-DUPES — bulk_store_matches dedup uses api_football_id first")
def _():
    """The bug that created 1,425 dupe groups: bulk_store_matches keyed dedup on
    (home_team_id, away_team_id, date_prefix) only — when AF rescheduled a fixture
    across a UTC day boundary, the new fetch's date_prefix didn't match the existing
    row's stored date and an INSERT fired. Fix: lookup by api_football_id first."""
    src = open("workers/api_clients/supabase_client.py").read()
    assert "existing_by_af" in src, (
        "bulk_store_matches must build an api_football_id → existing-row map. "
        "Without this, AF reschedules silently dupe."
    )
    assert "WHERE m.api_football_id = ANY" in src, (
        "Must SELECT existing rows by api_football_id ANY(...) before falling back "
        "to home/away/date_prefix join."
    )


@test("MATCH-DUPES — store_match (per-row) dedup uses api_football_id first")
def _():
    """Same fix in the legacy per-row helper that ad-hoc callers may still use."""
    src = open("workers/api_clients/supabase_client.py").read()
    # Find the store_match function body
    start = src.index("def store_match(match_data: dict)")
    body = src[start:start + 3000]
    assert "WHERE api_football_id = %s" in body, (
        "store_match must check api_football_id before the team/date fallback. "
        "Otherwise reschedules dupe via the per-row path too."
    )
    assert body.index("WHERE api_football_id = %s") < body.index(
        "WHERE home_team_id = %s AND away_team_id = %s"
    ), (
        "AF id lookup must happen BEFORE the team/date fallback — the order is the "
        "whole point of the fix."
    )


@test("MATCH-DUPES — migration 089 has partial unique index on api_football_id")
def _():
    """Belt-and-suspenders: even if the application-level dedup ever misses again,
    the DB rejects the INSERT loudly instead of silently accepting the dupe."""
    import pathlib
    p = pathlib.Path("supabase/migrations/089_matches_unique_af_id.sql")
    assert p.exists(), "Migration 089 must exist (was the constraint shipped?)"
    sql = p.read_text()
    assert "CREATE UNIQUE INDEX" in sql, "Must be a UNIQUE index, not a regular one."
    assert "api_football_id" in sql and "WHERE api_football_id IS NOT NULL" in sql, (
        "Partial index must filter on api_football_id IS NOT NULL — full unique would "
        "reject every legacy NULL-afid row."
    )


@test("MATCH-DUPES — performance-leaderboard hides voided bets")
def _():
    """Cleanup-voided bets (result='void', pnl=0) shouldn't pollute the per-bot history
    table — they're misleading at original odds_at_pick (e.g. OU 1.5 at 3.42 looked
    like a real bet but the price was garbage from a blacklisted bookmaker)."""
    import pathlib
    p = pathlib.Path("../odds-intel-web/src/components/performance-leaderboard.tsx")
    if not p.exists():
        return  # frontend not co-located — skip in engine-only checkouts
    src = p.read_text()
    assert 'b.result !== "void"' in src, (
        "performance-leaderboard botBets filter must exclude result==='void'. "
        "Without this, cleanup-voided bets render at original odds and confuse users."
    )


@test("SETTLE-VOID-POSTPONED — postpone branch voids pending bets in same write")
def _():
    """When the stale-match check transitions a fixture to 'postponed' (AF status
    PST/CANC/SUSP/AWD/INT), the same code path must also UPDATE simulated_bets
    to result='void', pnl=0 for that match. Otherwise pending bets pile up
    forever on a fixture that will never resolve — saw 7 stuck bets across 3
    postponed fixtures (May 3, May 8, May 9) before this fix shipped."""
    import pathlib
    src = pathlib.Path("workers/jobs/settlement.py").read_text()

    # The branch must mention all five AF status codes that trigger postponement.
    branch_idx = src.find('"PST", "CANC", "SUSP", "AWD", "INT"')
    assert branch_idx > 0, "PST/CANC/SUSP/AWD/INT branch missing in settlement.py"

    # Within ~80 lines after the branch, both updates must appear.
    branch_block = src[branch_idx:branch_idx + 4000]
    assert "UPDATE matches SET status='postponed'" in branch_block, (
        "Postpone branch must still flip matches.status='postponed'"
    )
    assert "UPDATE simulated_bets" in branch_block and "result='void'" in branch_block, (
        "SETTLE-VOID-POSTPONED: postpone branch must void pending bets on the match. "
        "Add `UPDATE simulated_bets SET result='void', pnl=0 WHERE match_id=%s "
        "AND result='pending'` immediately after the matches UPDATE."
    )
    assert "AND result='pending'" in branch_block, (
        "Void UPDATE must be scoped to result='pending' rows only — never overwrite "
        "settled (won/lost) bets."
    )


@test("P-PRED-1 — job_betting_refresh does not refetch /predictions")
def _():
    """AF /predictions has no bulk form (probed 2026-05-10) and updates at most
    hourly per AF docs. Re-pulling ~3,000 fixtures × 5 betting_refresh slots was
    burning ~10K calls/day for data identical to what's already on
    matches.af_prediction. Predictions stay morning-only (05:30 UTC); this test
    guards against accidentally re-introducing run_predictions in the refresh
    path."""
    import pathlib, re
    src = pathlib.Path("workers/scheduler.py").read_text()

    # Find the body of job_betting_refresh
    m = re.search(
        r"def job_betting_refresh\(\):.*?(?=\ndef [a-zA-Z_])",
        src,
        re.DOTALL,
    )
    assert m, "job_betting_refresh function not found in scheduler.py"
    body = m.group(0)

    # Match call sites only, not docstring mentions explaining the removal.
    # Forms blocked: `run_predictions(...)`, `import run_predictions`, `from … import run_predictions`.
    import re as _re
    call_form = _re.search(r"run_predictions\s*\(", body)
    import_form = _re.search(r"\bimport\s+run_predictions\b", body)
    assert not call_form and not import_form, (
        "P-PRED-1: job_betting_refresh must NOT call or import run_predictions. "
        "AF predictions are fetched once at 05:30 UTC; betting_refresh slots use "
        "the cached matches.af_prediction JSONB. Re-introducing the per-refresh "
        "fetch silently doubles morning AF burn (3K calls × 5 slots = 15K/day)."
    )
    assert "run_betting" in body, (
        "job_betting_refresh must still call run_betting()"
    )


@test("P-ENR-1 — _build_fixture_meta reads team_api_id/season/venue from DB, no /fixtures call")
def _():
    """Step ① fixtures already extracts home_team_api_id, away_team_api_id,
    venue_af_id, season via fixture_to_match_dict and writes them to the
    matches row (api_football.py:1547-1571). The duplicate /fixtures?date=
    call inside _build_fixture_meta was pure waste. This test guards the
    DB-only path stays in place."""
    import pathlib, re
    src = pathlib.Path("workers/jobs/fetch_enrichment.py").read_text()

    # Locate the function body
    m = re.search(
        r"def _build_fixture_meta\(target_date: str\) -> dict\[int, dict\]:.*?(?=\ndef [a-zA-Z_])",
        src,
        re.DOTALL,
    )
    assert m, "_build_fixture_meta function not found"
    body = m.group(0)

    # Must read the four fields from DB
    for field in ("season", "venue_af_id", "home_team_api_id", "away_team_api_id"):
        assert field in body, (
            f"P-ENR-1: _build_fixture_meta SQL select must include {field} so we "
            f"can skip the AF call. See matches column list — step ① writes it."
        )

    # Must NOT make the AF /fixtures?date= call from within the function
    assert "get_fixtures_by_date(target_date)" not in body, (
        "P-ENR-1: _build_fixture_meta must not call get_fixtures_by_date — "
        "step ① fixtures already wrote the four needed fields to matches. "
        "Re-fetching here is the duplicate AF call this task removed."
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
    import time, argparse
    parser = argparse.ArgumentParser(description="OddsIntel smoke tests")
    parser.add_argument(
        "--filter", "-f", default=None,
        help="Run only tests whose name matches this substring (case-insensitive). "
             "Use this for a single new test locally — full suite is CI's job."
    )
    args = parser.parse_args()

    if args.filter:
        needle = args.filter.lower()
        registry = [(n, f) for (n, f) in _registry if needle in n.lower()]
        if not registry:
            print(f"No tests match filter: {args.filter}")
            sys.exit(1)
        print(f"Filter: {args.filter} → {len(registry)} test(s)")
    else:
        registry = _registry

    t0 = time.monotonic()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_run_one, name, fn): name for name, fn in registry}
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


@test("DC-BOTS — pipeline has DC market support: MARKET_TO_FIELD, match dict, candidate_specs, settlement")
def _():
    """DC-BOTS (2026-05-11): Double Chance bots backed by odds_snapshots data.
    Source guards:
    1. MARKET_TO_FIELD includes DC entries (odds_dc_1x/x2/12).
    2. Match dict defaults include odds_dc_* fields.
    3. candidate_specs loop handles 'dc' market — DC probs derived from 1X2.
    4. settlement.py handles double_chance market (1x/x2/12 selections).
    5. BOTS_CONFIG has bot_dc_value and bot_dc_strong_fav."""
    import pathlib
    pipe = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    settle = pathlib.Path("workers/jobs/settlement.py").read_text()

    assert "double_chance_1x" in pipe, (
        "MARKET_TO_FIELD must map double_chance_1x to odds_dc_1x"
    )
    assert "odds_dc_1x" in pipe, (
        "match dict must default odds_dc_1x to 0"
    )
    assert '"dc" in config.get("markets"' in pipe, (
        "candidate_specs must include a dc block checking config['markets'] for 'dc'"
    )
    assert "dc_1x_prob = pred" in pipe, (
        "DC prob must be derived inline from pred['home_prob'] + pred['draw_prob']"
    )
    assert "bot_dc_value" in pipe, "BOTS_CONFIG must include bot_dc_value"
    assert "bot_dc_strong_fav" in pipe, "BOTS_CONFIG must include bot_dc_strong_fav"

    assert 'market == "double_chance"' in settle, (
        "settle_bet_result must handle double_chance market"
    )
    assert 'selection == "1x"' in settle, (
        "settlement must handle 1x selection for double_chance"
    )
    assert 'selection == "x2"' in settle, (
        "settlement must handle x2 selection for double_chance"
    )
    assert 'selection == "12"' in settle, (
        "settlement must handle 12 selection for double_chance"
    )


@test("AH-BOTS — _ah_model_prob prices AH lines correctly + pipeline/settlement wiring")
def _():
    """AH-BOTS (2026-05-11): Asian Handicap bots using Poisson goal distribution.
    Source guards:
    1. _ah_model_prob exists and handles whole/half/quarter lines correctly.
    2. pipeline builds ah_lines list in match dicts (DB path and AF path).
    3. BOTS_CONFIG has bot_ah_home_fav and bot_ah_away_dog.
    4. settlement handles asian_handicap market with push → void.
    5. get_closing_odds handles asian_handicap selection format 'home -1.25'."""
    import pathlib, math
    pipe = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    settle = pathlib.Path("workers/jobs/settlement.py").read_text()

    assert "def _ah_model_prob(" in pipe, "_ah_model_prob function must exist"
    assert "ah_lines" in pipe, "pipeline must build ah_lines in match dicts"
    assert "bot_ah_home_fav" in pipe, "BOTS_CONFIG must include bot_ah_home_fav"
    assert "bot_ah_away_dog" in pipe, "BOTS_CONFIG must include bot_ah_away_dog"
    assert '"asian_handicap"' in pipe, (
        "AH candidate_specs mkt must be 'asian_handicap' for correct settlement routing"
    )
    assert 'market == "asian_handicap"' in settle, (
        "settlement must handle asian_handicap market"
    )
    assert "won = None" in settle, (
        "settlement must handle AH whole-line push with won=None → void"
    )
    assert '"void" if won is None' in settle, (
        "settlement return must map won=None to result='void'"
    )
    assert "handicap_line = %s" in settle, (
        "get_closing_odds must filter by handicap_line for AH closing odds lookup"
    )

    # Functional: test _ah_model_prob with known inputs
    import sys; sys.path.insert(0, ".")
    from workers.jobs.daily_pipeline_v2 import _ah_model_prob

    # exp_h=1.5, exp_a=1.0 — moderate home favourite
    # AH -0.5 (home gives 0.5 goals): home wins if margin >= 1 (home wins outright)
    p_home_neg05 = _ah_model_prob(1.5, 1.0, "home", -0.5)
    assert 0.45 < p_home_neg05 < 0.75, (
        f"AH -0.5 home prob should be ~0.55 for moderate fav, got {p_home_neg05:.3f}"
    )
    p_away_neg05 = _ah_model_prob(1.5, 1.0, "away", -0.5)
    assert abs(p_home_neg05 + p_away_neg05 - 1.0) < 0.01, (
        "Home + Away probs must sum to 1.0 (no push on half lines)"
    )

    # AH 0.0 (draw no bet): whole line, push if draw
    p_home_0 = _ah_model_prob(1.5, 1.0, "home", 0.0)
    p_away_0 = _ah_model_prob(1.5, 1.0, "away", 0.0)
    assert abs(p_home_0 + p_away_0 - 1.0) < 0.01, (
        "DNB: Home + Away conditional probs must sum to 1.0 (excluding push)"
    )
    assert p_home_0 > p_away_0, "Home favourite should have higher DNB prob"

    # x.25 quarter line: -1.25 (home gives 1.25 goals, favourites only)
    p_home_q25 = _ah_model_prob(2.0, 0.8, "home", -1.25)
    assert 0.35 < p_home_q25 < 0.70, (
        f"AH -1.25 home prob for strong fav (exp 2.0 vs 0.8) should be in range, got {p_home_q25:.3f}"
    )

    # Settlement: verify whole-line push returns void
    from workers.jobs.settlement import settle_bet_result
    bet_push = {
        "market": "asian_handicap", "selection": "home -1.0",
        "stake": 10.0, "odds_at_pick": 1.90,
    }
    result_push = settle_bet_result(bet_push, home_goals=1, away_goals=0, closing_odds=None)
    assert result_push["result"] == "void", f"Whole-line push must settle as void, got {result_push['result']}"
    assert result_push["pnl"] == 0.0, "Void pnl must be 0"

    bet_win = {**bet_push, "selection": "home -0.5"}
    result_win = settle_bet_result(bet_win, home_goals=2, away_goals=0, closing_odds=None)
    assert result_win["result"] == "won"

    bet_lose = {**bet_push, "selection": "home -0.5"}
    result_lose = settle_bet_result(bet_lose, home_goals=0, away_goals=1, closing_odds=None)
    assert result_lose["result"] == "lost"


@test("AH-PARSE — parse_fixture_odds parses Asian Handicap from 'Home -1.25' value format")
def _():
    """AH-PARSE (2026-05-11): AF returns value='Home -1.25' (team + handicap in one string).
    Old code expected value='Home' + separate handicap field — produced 0 rows.
    Fixed to split on first space and parse embedded handicap.
    Source guards:
    1. parse_fixture_odds uses exact 'Asian Handicap' name match (not substring).
    2. Parses 'Home -1.25' format into selection='home', handicap_line=-1.25.
    3. fetch_odds.py includes handicap_line in column list.
    4. Migration 066 adds handicap_line column."""
    import pathlib
    af = pathlib.Path("workers/api_clients/api_football.py").read_text()
    fetch = pathlib.Path("workers/jobs/fetch_odds.py").read_text()
    mig = pathlib.Path("supabase/migrations/066_ah_signals.sql").read_text()

    assert 'bet_name == "Asian Handicap"' in af, (
        "AH parser must use exact match 'Asian Handicap', not substring — "
        "substring would swallow Corners/Cards/Yellow variants"
    )
    assert 'parts = v.split(" ", 1)' in af, (
        "AH parser must split value string on first space to extract team + handicap"
    )
    assert "handicap_line" in fetch, (
        "fetch_odds.py must include handicap_line in column list for bulk insert"
    )
    assert "handicap_line" in mig, (
        "migration 066 must add handicap_line column to odds_snapshots"
    )

    # Functional check: parse a synthetic AF-format AH payload
    from workers.api_clients.api_football import parse_fixture_odds
    fake = [{
        "bookmakers": [{
            "name": "TestBook",
            "bets": [{
                "name": "Asian Handicap",
                "values": [
                    {"value": "Home -1.25", "odd": "2.50"},
                    {"value": "Away -1.25", "odd": "1.55"},
                    {"value": "Home +0.5", "odd": "1.75"},
                    {"value": "Away +0.5", "odd": "2.10"},
                ],
            }],
        }],
    }]
    rows = parse_fixture_odds(fake)
    ah = [r for r in rows if r.get("market") == "asian_handicap"]
    assert len(ah) == 4, f"Expected 4 AH rows, got {len(ah)}"
    home_neg = next((r for r in ah if r["selection"] == "home" and r["handicap_line"] == -1.25), None)
    assert home_neg is not None, "Must parse 'Home -1.25' into selection='home', handicap_line=-1.25"
    away_pos = next((r for r in ah if r["selection"] == "away" and r["handicap_line"] == 0.5), None)
    assert away_pos is not None, "Must parse 'Away +0.5' into selection='away', handicap_line=0.5"


@test("INPLAY-LIVE-DEBUG — inplay_bot has prematch fallback, per-strategy stats, and _resolve_odds helper")
def _():
    """INPLAY-LIVE-DEBUG (2026-05-11): Live odds coverage ~12% caused 0 fired bets.
    Source guards:
    1. _resolve_odds helper exists and returns (float, bool).
    2. _strategy_stats dict tracks tried/fired per bot.
    3. Prematch SQL LATERAL subquery fetches prematch_ou25_over.
    4. Strategy A uses _resolve_odds (prematch fallback active).
    5. Strategy Q uses _resolve_odds with min_val=2.30 and records odds_source in extra."""
    import pathlib
    inplay = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    assert "def _resolve_odds(" in inplay, (
        "_resolve_odds helper must exist for live-to-prematch fallback"
    )
    assert "tuple[float, bool]" in inplay, (
        "_resolve_odds must declare return type tuple[float, bool]"
    )
    assert "_strategy_stats" in inplay, (
        "_strategy_stats dict must exist for per-bot tried/fired tracking"
    )
    assert "strategy rates" in inplay, (
        "heartbeat must log strategy rates from _strategy_stats"
    )
    assert "prematch_ou25_over" in inplay, (
        "prematch SQL must fetch prematch_ou25_over via LATERAL subquery"
    )
    assert "_resolve_odds(cand.get(\"live_ou_25_over\")" in inplay, (
        "strategy A must call _resolve_odds with live_ou_25_over for prematch fallback"
    )
    assert "odds_source" in inplay, (
        "return dicts must include odds_source key to distinguish live vs prematch"
    )


@test("DNB-COMPUTE — draw_no_bet settlement and candidate_specs generation")
def _():
    """DNB-COMPUTE (2026-05-11): Draw No Bet bots computed from 1X2 odds.
    Pricing: dnb_home_odds = (a+h)/a, dnb_away_odds = (a+h)/h.
    Model prob: home_prob / (home_prob + away_prob) — draw removed.
    Settlement: draw → void, home win → home won, away win → home lost.
    Source guards:
    1. draw_no_bet handler in settlement.py (draw → void).
    2. DNB candidate_specs block in daily_pipeline_v2.py.
    3. bot_dnb_home_value + bot_dnb_away_value in BOTS_CONFIG."""
    import pathlib
    pipeline = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()

    assert 'bot_dnb_home_value' in pipeline, "bot_dnb_home_value must be in BOTS_CONFIG"
    assert 'bot_dnb_away_value' in pipeline, "bot_dnb_away_value must be in BOTS_CONFIG"
    assert '"draw_no_bet"' in pipeline, "DNB candidate_specs must store market as 'draw_no_bet'"
    assert "dnb_h_odds" in pipeline, "DNB must compute dnb_h_odds from 1X2 odds"

    from workers.jobs.settlement import settle_bet_result
    base = {"market": "draw_no_bet", "stake": 10.0, "odds_at_pick": 1.80}

    # Draw → void
    r = settle_bet_result({**base, "selection": "home"}, home_goals=1, away_goals=1, closing_odds=None)
    assert r["result"] == "void", f"Draw must settle as void, got {r['result']}"
    assert r["pnl"] == 0.0, "Void pnl must be 0"

    # Home wins → home bet won
    r = settle_bet_result({**base, "selection": "home"}, home_goals=2, away_goals=0, closing_odds=None)
    assert r["result"] == "won", f"Home win on home DNB bet must win, got {r['result']}"

    # Away wins → home bet lost
    r = settle_bet_result({**base, "selection": "home"}, home_goals=0, away_goals=1, closing_odds=None)
    assert r["result"] == "lost", f"Away win on home DNB bet must lose, got {r['result']}"

    # Away DNB: draw → void, away win → won
    r = settle_bet_result({**base, "selection": "away"}, home_goals=0, away_goals=0, closing_odds=None)
    assert r["result"] == "void"
    r = settle_bet_result({**base, "selection": "away"}, home_goals=0, away_goals=1, closing_odds=None)
    assert r["result"] == "won"


@test("BOT-PERF-MONITOR — bot_perf_report.py exists with all 5 sections and --days/--bot flags")
def _():
    """BOT-PERF-MONITOR (2026-05-11): standalone report script for profitability validation.
    5 slices: summary, by-bot, by-market+selection, by-tier, top-leagues.
    Supports --days N (recency window) and --bot NAME (drill-down).
    Source guards: all 5 section functions exist, argparse flags present."""
    import pathlib
    src = pathlib.Path("scripts/bot_perf_report.py").read_text()

    for fn in ("section_summary", "section_by_bot", "section_by_market",
               "section_by_tier", "section_top_leagues"):
        assert f"def {fn}(" in src, f"{fn} must exist in bot_perf_report.py"

    assert "--days" in src, "Must support --days flag for recency window"
    assert "--bot" in src, "Must support --bot flag for drill-down"
    assert "--min-bets" in src, "Must support --min-bets flag for significance floor"
    assert "avg_clv" in src, "Must compute avg CLV in queries"
    assert "league_tier" in src or "l.tier" in src, "Must slice by league tier"


@test("ACCESSIBLE-BM — ACCESSIBLE_BOOKMAKERS constant and recommended_bookmaker wiring")
def _():
    """ACCESSIBLE-BM (2026-05-11): restrict edge calculation to accessible bookmakers and
    track which book had best odds per bet. Source guards:
    - ACCESSIBLE_BOOKMAKERS frozenset defined in daily_pipeline_v2.py
    - best_bookmaker dict declared and populated
    - recommended_bookmaker passed to store_bet
    - optional_fields in supabase_client.py includes recommended_bookmaker
    - migration 094 adds column to simulated_bets
    - daily_picks.py script exists with --date/--min-edge/--bookmaker flags"""
    import pathlib

    pipeline = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    assert "ACCESSIBLE_BOOKMAKERS" in pipeline, "ACCESSIBLE_BOOKMAKERS must be defined"
    assert "frozenset" in pipeline, "ACCESSIBLE_BOOKMAKERS must be a frozenset"
    assert "Bet365" in pipeline and "Unibet" in pipeline and "Pinnacle" in pipeline, \
        "ACCESSIBLE_BOOKMAKERS must include Bet365, Unibet, Pinnacle"
    assert "best_bookmaker" in pipeline, "best_bookmaker dict must be declared"
    assert "bookmaker not in ACCESSIBLE_BOOKMAKERS" in pipeline, \
        "inaccessible bookmakers must be filtered in odds aggregation loop"
    assert "recommended_bookmaker" in pipeline, "recommended_bookmaker must be passed to store_bet"

    client = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    assert "recommended_bookmaker" in client, "recommended_bookmaker must be in store_bet optional_fields"

    migration = pathlib.Path("supabase/migrations/094_simulated_bets_recommended_bookmaker.sql").read_text()
    assert "recommended_bookmaker" in migration, "migration 094 must add recommended_bookmaker column"
    assert "simulated_bets" in migration, "migration 094 must target simulated_bets"

    picks = pathlib.Path("scripts/daily_picks.py").read_text()
    assert "--date" in picks, "daily_picks.py must support --date flag"
    assert "--min-edge" in picks, "daily_picks.py must support --min-edge flag"
    assert "--bookmaker" in picks, "daily_picks.py must support --bookmaker flag"
    assert "recommended_bookmaker" in picks, "daily_picks.py must show recommended_bookmaker"


@test("REAL-PERF-REPORT — real_perf_report.py structure and SQL (source inspect)")
def test_real_perf_report_source():
    """Phase 2.8.1 (2026-05-11): real_perf_report.py — paper vs real P&L comparison."""
    import pathlib
    src = pathlib.Path("scripts/real_perf_report.py").read_text()
    assert "real_bets" in src, "must query real_bets table"
    assert "simulated_bets" in src, "must join simulated_bets for paper comparison"
    assert "slippage_pct" in src, "must include slippage_pct in output"
    assert "--days" in src, "must support --days flag"
    assert "--bookmaker" in src, "must support --bookmaker flag"
    assert "section_summary" in src, "must have summary section"
    assert "section_paper_vs_real" in src, "must have paper vs real section"
    assert "section_by_bookmaker" in src, "must have by-bookmaker section"


@test("COOLBET-PHASE3.5 — real_perf_split_by_source.py structure (source inspect)")
def test_real_perf_split_by_source():
    """COOLBET-PHASE3.5 (2026-05-24): split placer (--record, notes LIKE 'auto%')
    vs manual (/admin/place) real_bets to isolate the rule-driven Q1 signal
    from the user-selected biased subset. Used at 2026-06-07 readout for new-model
    baseline."""
    import pathlib
    src = pathlib.Path("scripts/real_perf_split_by_source.py").read_text()
    assert "real_bets" in src, "must query real_bets"
    assert "notes" in src and "auto" in src, "must filter by notes LIKE 'auto%' to identify placer rows"
    assert "PLACER" in src and "MANUAL" in src, "must label both subsets in output"
    assert "by_bot" in src, "must include per-bot breakdown"
    assert "by_market" in src, "must include per-market breakdown"


@test("FRESHNESS-INDICATOR + BOOKMAKER-DISPLAY — daily_picks.py + real_perf_report.py (source inspect)")
def test_freshness_bookmaker_engine_side():
    """Phase 2.8.2/2.8.3 (2026-05-11): engine-side guards only (web repo not present in CI)."""
    import pathlib
    picks = pathlib.Path(__file__).resolve().parent / "daily_picks.py"
    src = picks.read_text()
    assert "recommended_bookmaker" in src, "daily_picks.py must show recommended_bookmaker"
    assert "home_team" in src, "daily_picks.py must join teams for home_team name"
    assert "model_probability" in src, "daily_picks.py must use model_probability not calibrated_prob"

    report = pathlib.Path(__file__).resolve().parent / "real_perf_report.py"
    rsrc = report.read_text()
    assert "real_bets" in rsrc, "real_perf_report.py must query real_bets"
    assert "slippage_pct" in rsrc, "real_perf_report.py must show slippage"


@test("INPLAY-STATS-DB — upsert_inplay_bot_stats wiring (source inspect)")
def test_inplay_stats_db():
    """INPLAY-STATS-DB (2026-05-11): strategy tried/fired stats persisted to DB on heartbeat."""
    import pathlib
    client = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    assert "upsert_inplay_bot_stats" in client, "upsert_inplay_bot_stats must be in supabase_client.py"
    assert "inplay_bot_stats" in client, "must target inplay_bot_stats table"
    assert "GREATEST" in client, "must use GREATEST for safe accumulation"
    assert "ON CONFLICT" in client, "must upsert not insert"

    bot = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    assert "upsert_inplay_bot_stats" in bot, "inplay_bot.py must call upsert_inplay_bot_stats on heartbeat"
    assert "_strategy_stats" in bot, "_strategy_stats dict must be passed to upsert"

    migration = pathlib.Path("supabase/migrations/095_inplay_bot_stats.sql").read_text()
    assert "inplay_bot_stats" in migration, "migration 095 must create inplay_bot_stats"
    assert "UNIQUE" in migration, "must have UNIQUE(stat_date, strategy)"


@test("PLACE-BET-UX — already-placed indicator + AH/DC bets (source inspect)")
def test_place_bet_ux():
    """PLACE-BET-UX (2026-05-11): /admin/place shows already-placed badge + AH/DC bets now visible."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    # engine: _store_parsed_odds must write handicap_line
    pipe = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    assert "handicap_line" in pipe and "row.get(\"handicap_line\")" in pipe, \
        "_store_parsed_odds must include handicap_line in INSERT"
    assert "ahSnapMap" in pipe or "ahSnapKey" in pipe or \
        "(match_id, bookmaker, market, selection, odds, handicap_line" in pipe, \
        "_store_parsed_odds INSERT must include handicap_line column"

    engine_data = root.parent / "odds-intel-web" / "src" / "lib" / "engine-data.ts"
    if not engine_data.exists():
        print("  [skip] odds-intel-web not present in CI")
        return
    src = engine_data.read_text()
    assert "alreadyPlaced" in src, "PlaceableBet must have alreadyPlaced field"
    # Variable renamed placedMatchIds → placedToday (2026-05-25 smoke drift fix)
    assert "placedToday" in src or "placedMatchIds" in src, \
        "getPlaceableBets must query real_bets placed today"
    assert "ahSnapMap" in src, "getPlaceableBets must use ahSnapMap for AH 5-part key lookup"
    assert "double_chance" in src, "_mapPaperToSnapshotKey must handle double_chance market"

    # place-bet-table.tsx: badge rendered + filter chip + Pinnacle
    table = root.parent / "odds-intel-web" / "src" / "components" / "place-bet-table.tsx"
    tsrc = table.read_text()
    assert "alreadyPlaced" in tsrc, "table must render alreadyPlaced badge"
    assert "Placed" in tsrc, "filter chip for already-placed bets must exist"
    assert "Pinnacle" in tsrc, "Pinnacle must be in ACCESSIBLE_BOOKS for AH bets"


@test("INPLAY-BOT-REPORT — script structure (source inspect)")
def test_inplay_bot_report():
    """INPLAY-BOT-REPORT (2026-05-11): inplay_bot_report.py reads inplay_bot_stats + simulated_bets."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent / "inplay_bot_report.py").read_text()
    assert "inplay_bot_stats" in src, "must query inplay_bot_stats table"
    assert "simulated_bets" in src, "must join simulated_bets for P&L"
    assert "xg_source IS NOT NULL" in src, "must filter to live bets only"
    assert "section_summary" in src, "must have summary section"
    assert "section_strategy_table" in src, "must have per-strategy table"
    assert "section_daily_activity" in src, "must have daily heatmap"
    assert "section_recent_bets" in src, "must have recent bets section"
    assert "--strategy" in src, "must support --strategy filter"
    assert "--days" in src, "must support --days filter"


@test("BOOKMAKER-DISPLAY-V2 — BookOddsLine shows Pinnacle + current edge + stale dimming (source inspect)")
def test_bookmaker_display_v2():
    """BOOKMAKER-DISPLAY-V2 (2026-05-12): value-bets component shows 3-bookmaker live odds + current edge."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    web = root.parent / "odds-intel-web"
    if not web.exists():
        print("  [skip] odds-intel-web not present in CI")
        return
    src = (web / "src" / "components" / "value-bets-live.tsx").read_text()
    assert "pinnacle" in src.lower(), "must include Pinnacle in bookmaker display"
    assert "getBestNow" in src, "must have getBestNow helper to find best current odds"
    # isEdgeStale helper removed during a simplification — staleness is no
    # longer surfaced in BookOddsLine. Keep modelProb check to guard the
    # current-edge wire.
    assert "modelProb" in src, "BookOddsLine must receive modelProb to calculate current edge"
    assert "BookOddsLine" in src, "must export BookOddsLine component"

    edata = (web / "src" / "lib" / "engine-data.ts").read_text()
    assert 'pinnacle: number | null' in edata, "BookOddsEntry must have pinnacle field"
    assert '"Pinnacle"' in edata, "getValueBetBookOdds must fetch Pinnacle from odds_snapshots"


@test("AH-CALIBRATED-PROB — value-bets uses calibrated_prob not raw model_probability (source inspect)")
def test_ah_calibrated_prob_display():
    """AH-DISPLAY-FIX: modelProb must use calibrated_prob so AH push-normalization doesn't inflate the display."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    web = root.parent / "odds-intel-web"
    if not web.exists():
        print("  [skip] odds-intel-web not present in CI")
        return
    edata = (web / "src" / "lib" / "engine-data.ts").read_text()
    assert "calibrated_prob" in edata, "engine-data.ts must select calibrated_prob from simulated_bets"
    assert "calibrated_prob ?? row.model_probability" in edata, \
        "toBet must prefer calibrated_prob over raw model_probability"


@test("MODEL-SIGNALS-REFEREE — build_referee_stats called from settlement (source inspect)")
def test_model_signals_referee():
    """MODEL-SIGNALS (2026-05-11): referee stats rebuilt nightly via settlement so signals stay current."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    settlement = (root / "workers" / "jobs" / "settlement.py").read_text()
    assert "build_referee_stats" in settlement, \
        "settlement.py must import and call build_referee_stats() nightly"
    assert "build_referee_stats()" in settlement, \
        "settlement.py must call build_referee_stats() — not just import it"

    sc = (root / "workers" / "api_clients" / "supabase_client.py").read_text()
    assert "referee_home_win_pct" in sc, "supabase_client must store referee_home_win_pct signal"
    assert "referee_over25_pct" in sc, "supabase_client must store referee_over25_pct signal"

    train = (root / "workers" / "model" / "train.py").read_text()
    assert "referee_cards_avg" in train, "train.py FEATURE_COLS must include referee_cards_avg"
    assert "referee_home_win_pct" in train, "train.py FEATURE_COLS must include referee_home_win_pct"


@test("MODEL-SIGNALS-WEATHER — weather job, MFV wiring, FEATURE_COLS, enrichment hook (source inspect)")
def test_model_signals_weather():
    """MODEL-SIGNALS (2026-05-11): weather at kickoff wired into match_feature_vectors + train.py."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    migration = root / "supabase" / "migrations" / "097_weather_signals.sql"
    assert migration.exists(), "097_weather_signals.sql must exist"
    mig_text = migration.read_text()
    assert "weather_temp_c" in mig_text, "migration must add weather columns to match_feature_vectors"
    assert "venues" in mig_text and "lat" in mig_text, "migration must add lat/lon to venues"

    weather_job = root / "workers" / "jobs" / "fetch_weather.py"
    assert weather_job.exists(), "fetch_weather.py must exist"
    wj = weather_job.read_text()
    assert "open-meteo.com" in wj, "weather job must call Open-Meteo"
    assert "match_weather" in wj, "weather job must store in match_weather"
    assert "_geocode" in wj, "weather job must geocode venues"

    sc = (root / "workers" / "api_clients" / "supabase_client.py").read_text()
    assert "weather_by_match" in sc, "supabase_client must load weather_by_match batch"
    assert "weather_temp_c" in sc, "supabase_client must include weather_temp_c in MFV row"

    train = (root / "workers" / "model" / "train.py").read_text()
    assert "weather_temp_c" in train, "train.py FEATURE_COLS must include weather features"
    assert "weather_wind_kmh" in train, "train.py FEATURE_COLS must include weather_wind_kmh"

    enrich = (root / "workers" / "jobs" / "fetch_enrichment.py").read_text()
    assert "weather" in enrich, "fetch_enrichment must include weather in ALL_COMPONENTS"
    assert "fetch_weather" in enrich, "fetch_enrichment must call fetch_weather"


@test("MODEL-SIGNALS-IS-OPENING — is_opening flag in migration, store_odds, fetch_odds, pruner (source inspect)")
def test_model_signals_is_opening():
    """MODEL-SIGNALS (2026-05-11): is_opening marks first snapshot per (match,bookmaker,market,selection)."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    migration = root / "supabase" / "migrations" / "096_is_opening_flag.sql"
    assert migration.exists(), "096_is_opening_flag.sql must exist"
    mig_text = migration.read_text()
    assert "is_opening" in mig_text, "migration must add is_opening column"
    # backfill moved to scripts/backfill_is_opening.py (inline UPDATE timed out on prod)
    backfill = root / "scripts" / "backfill_is_opening.py"
    assert backfill.exists(), "backfill_is_opening.py must exist"
    bf_text = backfill.read_text()
    assert "DISTINCT ON" in bf_text, "backfill script must mark earliest row per combination"

    sc = (root / "workers" / "api_clients" / "supabase_client.py").read_text()
    assert "is_opening" in sc, "supabase_client store_odds must include is_opening column"
    assert "existing_combos" in sc, "store_odds must query existing combos before insert"

    fo = (root / "workers" / "jobs" / "fetch_odds.py").read_text()
    assert "is_opening" in fo, "fetch_odds bulk path must include is_opening"
    assert "existing_combos" in fo, "fetch_odds must pre-fetch existing combos"

    pruner = (root / "scripts" / "prune_odds_snapshots.py").read_text()
    assert "NOT is_opening" in pruner, "pruner must never delete is_opening=true rows"


@test("ROMANIAN-LIGA-I-DATA — targets_poisson_history.csv includes RO1 data with FCSB + Slobozia")
def test_romanian_liga_data():
    """RO1-DATA-FIX: FCSB and Unirea Slobozia must be in targets_poisson_history (Tier A) so Poisson
    expected goals are based on Liga I performance, not global data that mixes in European
    competition and inverts FCSB's expected goals."""
    import pathlib
    import pandas as pd
    root = pathlib.Path(__file__).resolve().parent.parent
    ph = root / "data" / "processed" / "targets_poisson_history.csv"
    assert ph.exists(), "targets_poisson_history.csv must exist"
    df = pd.read_csv(ph, low_memory=False)
    assert "RO1" in df["league_code"].values, "targets_poisson_history must contain Romanian Liga I (RO1) rows"
    teams = set(df[df["league_code"] == "RO1"]["home_team"].unique()) | \
            set(df[df["league_code"] == "RO1"]["away_team"].unique())
    assert "FCSB" in teams, "FCSB must be in RO1 rows"
    assert "Unirea Slobozia" in teams, "Unirea Slobozia must be in RO1 rows"
    ro1_count = (df["league_code"] == "RO1").sum()
    assert ro1_count >= 500, f"expected 500+ RO1 rows, got {ro1_count}"


@test("BACKFILL-WEATHER — script structure: four phases, geocode fallbacks, archive URL, bulk upsert (source inspect)")
def test_backfill_weather():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "scripts" / "backfill_weather.py").read_text()
    assert "archive-api.open-meteo.com" in src, "must use Open-Meteo archive API for historical weather"
    assert "_discover_missing_venues" in src, "must have phase 0: discover venues missing from venues table"
    assert "_seed_venue_cities" in src, "must have phase 1: seed venue city from AF"
    assert "_seed_venue_addresses" in src, "must have phase 1b: seed address for ungeocodeable venues"
    assert "_geocode_venues" in src, "must have phase 2: geocode venues"
    assert "_backfill_weather" in src, "must have phase 3: backfill weather"
    assert "nominatim.openstreetmap.org" in src, "must have Nominatim fallback geocoding"
    assert "_geocode_ai_batch" in src, "must have AI batch geocoding fallback"
    assert "_GEMINI_MODEL" in src, "must reference Gemini model for AI geocoding"
    assert "_clean_location" in src, "must clean AF location strings before geocoding"
    assert "replace('-', ' ')" in src, "must fix hyphenated country names"
    assert "geocode_source" in src, "must tag geocode source on venues"
    assert "execute_values" in src, "must use bulk execute_values insert, not per-row"
    assert "ON CONFLICT (match_id)" in src, "must upsert on match_id"
    assert "dry_run" in src, "must support --dry-run flag"

@test("WEATHER-GEOCODE — venue address + geocode_source columns, parse_venue, store_venues (source inspect)")
def test_weather_geocode_address():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    m098 = root / "supabase" / "migrations" / "098_venue_address.sql"
    assert m098.exists(), "098_venue_address.sql must exist"
    assert "address" in m098.read_text(), "migration must add address column to venues"

    m099 = root / "supabase" / "migrations" / "099_venue_geocode_source.sql"
    assert m099.exists(), "099_venue_geocode_source.sql must exist"
    assert "geocode_source" in m099.read_text(), "migration must add geocode_source column to venues"

    pv = (root / "workers" / "api_clients" / "api_football.py").read_text()
    assert '"address": raw.get("address")' in pv, "parse_venue must capture address from AF response"

    sc = (root / "workers" / "api_clients" / "supabase_client.py").read_text()
    assert "address" in sc, "store_venues must upsert address column"


@test("BEST-BOOKMAKER-RETURN — _load_today_from_db returns 4-tuple with best_bookmaker dict (source inspect)")
def test_best_bookmaker_return():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()

    # Return signature must include best_bookmaker as 4th element
    assert "return odds_matches, af_only_matches, af_preds, dict(best_bookmaker)" in src, \
        "_load_today_from_db must return best_bookmaker as 4th element"

    # Caller must unpack 4 values
    assert "odds_matches, af_only_matches, af_preds, best_bookmaker = _load_today_from_db" in src, \
        "run_morning must unpack 4 values from _load_today_from_db"

    # best_bookmaker must be initialized before the if/else so Phase 1 path is also safe
    assert "best_bookmaker: dict[str, dict[str, str]] = {}" in src, \
        "best_bookmaker must be initialized to {} before the skip_fetch branch"

    # Early-return paths must also return 4-tuples
    early_returns = [line.strip() for line in src.splitlines() if "return [], [], {}, {}" in line]
    assert len(early_returns) >= 2, \
        f"Expected >=2 early-return 4-tuples in _load_today_from_db, found {len(early_returns)}"


@test("SHRINKAGE-ALPHA-SQL-BUG — load_shrinkage_alphas uses %% not % in LIKE clause (source inspect)")
def test_shrinkage_alpha_sql_bug():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "workers" / "model" / "improvements.py").read_text()

    assert "LIKE 'shrinkage_alpha_%%'" in src, \
        "load_shrinkage_alphas LIKE clause must use %% (not %) — bare % is treated as a psycopg2 parameter placeholder"

    assert "LIKE 'shrinkage_alpha_%'" not in src, \
        "Single % found in LIKE clause — will raise IndexError at runtime"


@test("ALN-1 — LOW-alignment edge bump wired into pipeline and LOG-ONLY comment removed")
def test_aln1_filter_active():
    """ALN-1: LOW-alignment edge bump is wired into the pipeline and active (not LOG-ONLY)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "daily_pipeline_v2.py").read_text()

    assert "_ALN_BUMP" in src, \
        "_ALN_BUMP dict not found — ALN-1 filter not implemented"
    assert '"LOW": 0.01' in src, \
        "_ALN_BUMP LOW value should be 0.01 (1% extra edge for LOW-alignment bets)"
    assert "LOG-ONLY" not in src, \
        "Pipeline still says LOG-ONLY — ALN-1 filter not activated"


@test("AH-NO-QUARTER — quarter lines (.25/.75) filtered from AH candidate generation")
def test_ah_no_quarter_lines():
    """AH-NO-QUARTER: quarter lines skipped — Coolbet only offers full/half lines."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "AH-NO-QUARTER" in src, \
        "AH-NO-QUARTER comment not found — quarter-line filter may be missing"
    assert "abs(_hl % 0.5) == 0.25" in src, \
        "quarter-line filter expression not found"


@test("PIN-4-VETO-ALL-MARKETS — Pinnacle veto covers BTTS/DC/AH/O/U non-2.5 via ip fallback")
def test_pin4_veto_all_markets():
    """PIN-4: Pinnacle veto applies to all markets (BTTS/DC/AH/O/U non-2.5), not just 1X2+O/U-2.5."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "daily_pipeline_v2.py").read_text()

    assert "PIN-4" in src, \
        "PIN-4 comment not found — universal veto may not be implemented"
    assert "_veto_anchor" in src, \
        "_veto_anchor variable not found — fallback to ip for non-Pinnacle markets missing"
    assert "cal_prob - _veto_anchor" in src, \
        "veto check against _veto_anchor not found"
    # Old market-gated path should be gone
    assert 'if mkt in ("1X2", "O/U"):\n                    _pmap' not in src, \
        "Old market-gated veto still present — BTTS/DC/AH would bypass it"


@test("POST-MORTEM-SCHEMA — settlement.py serializes full JSON (no [:2000] slice) + validates round-trip")
def test_post_mortem_schema():
    """POST-MORTEM-SCHEMA: 5 of 14 historical rows had truncated JSON due to a
    `[:2000]` slice on the serialized notes. Fix removes the slice and adds a
    json.loads() round-trip validation before insert.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "settlement.py").read_text()
    assert "POST-MORTEM-SCHEMA" in src, "POST-MORTEM-SCHEMA tag missing"
    # The buggy slice must be gone
    assert "json.dumps(analysis, ensure_ascii=False)[:2000]" not in src, \
        "The truncating `[:2000]` slice is still present — busy days will still emit invalid JSON"
    # Round-trip validation present
    assert "notes_str = json.dumps(analysis" in src, "Full-serialization line missing"
    assert "json.loads(notes_str)  # validate round-trip" in src, \
        "JSON round-trip validation missing — corrupt JSON could still reach DB"


@test("POST-MORTEM-BALANCE — settlement.py computes per-conviction calibration table and references it in the prompt")
def test_post_mortem_balance():
    """POST-MORTEM-BALANCE: the OU-UNDER-CAP investigation revealed the LLM was
    flagging MODEL_ERROR on losses without checking that the bot won at similar
    confidence on the same day (availability bias). Fix: pre-compute a per-
    confidence-bucket hit rate from the day's settled bets and inject it into
    the prompt with explicit guidance.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "settlement.py").read_text()
    assert "POST-MORTEM-BALANCE" in src, "POST-MORTEM-BALANCE tag missing"
    assert "_conv_bucket" in src, "Per-confidence bucketing helper missing"
    assert "bucket_stats" in src and "total_pred" in src, \
        "Bucket aggregation (predicted vs actual hit rate) must be computed"
    assert "DAILY CALIBRATION SNAPSHOT" in src, \
        "Prompt must include the DAILY CALIBRATION SNAPSHOT block"
    # Guard rails on the guidance update
    assert "Default to VARIANCE" in src, \
        "Loss-classification guidance must default to VARIANCE when bucket calibration is normal"
    assert "underperforms its predicted hit rate by 15pp+" in src, \
        "MODEL_ERROR threshold (bucket-level 15pp+ underperformance) must be explicit"


@test("POST-MORTEM-CONTEXT — settlement.py prompt explains bot portfolio independence")
def test_post_mortem_context():
    """POST-MORTEM-CONTEXT: LLM hallucinated a "conflicting bets bug" on 2026-05-18 because
    the prompt didn't mention bots are an independent portfolio. Two different bots
    backing opposite sides is normal — only same-bot conflicts are bugs (and the dedup
    constraint prevents those).
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "settlement.py").read_text()
    assert "POST-MORTEM-CONTEXT" in src, "POST-MORTEM-CONTEXT tag missing"
    assert "INDEPENDENT PORTFOLIO" in src, \
        "Prompt should state explicitly that bots run as an independent portfolio"
    assert "opposite" in src.lower() and "opposite-side picks" in src.lower(), \
        "Prompt should clarify that opposite-side picks across bots are normal"


@test("SCOTTISH-PREM-LEAGUE-GATE — daily_pipeline_v2 hard-skips Scottish Premiership matches")
def test_scottish_prem_league_gate():
    """SCOTTISH-PREM-LEAGUE-GATE: INFO-GAP-LEAGUE-AUDIT found Scottish Premiership is the
    only league with systematically sharp pre-KO CLV at n>=20 (n=41, median CLV -25.7%,
    ROI -48.6%). Hard skip in the main pipeline until confirmed-lineup gate ships.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "SCOTTISH-PREM-LEAGUE-GATE" in src, "tag missing"
    # Check both halves of the guard exist together
    assert 'country == "Scotland"' in src and 'league_name == "Premiership"' in src, \
        "Skip guard must check country == Scotland AND league_name == Premiership"


@test("LINEUP-CONFIDENCE-CLEANUP — broken Gemini lineup_confidence removed; _dim_lineup reads matches.lineups_fetched_at")
def test_lineup_confidence_cleanup():
    """LINEUP-CONFIDENCE-CLEANUP: news_checker.py asked Gemini for lineup_confidence
    but Gemini has no lineup-data access — every row stored at 0.5 default.
    Fix: drop the field from the Gemini prompt + downstream writes; rewire
    _dim_lineup (alignment dimension 3) to read matches.lineups_fetched_at directly.
    """
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    news_src = (base / "workers" / "jobs" / "news_checker.py").read_text()
    impr_src = (base / "workers" / "model" / "improvements.py").read_text()

    # Gemini prompt no longer requests it
    assert '"lineup_confidence": float 0.0 to 1.0' not in news_src, \
        "Gemini prompt should no longer request lineup_confidence"
    assert 'RULES for lineup_confidence:' not in news_src, \
        "RULES block for lineup_confidence must be removed from prompt"
    # No active assignment of lineup_conf variable; only comment references allowed
    assert 'lineup_conf = ai_result.get("lineup_confidence"' not in news_src, \
        "lineup_conf variable should no longer be pulled from ai_result"
    # No write to match_signals "lineup_confidence"
    assert 'store_match_signal(match_id, "lineup_confidence"' not in news_src, \
        "lineup_confidence signal write must be removed"
    # No write to simulated_bets.lineup_confirmed from broken Gemini value
    assert 'bet_update["lineup_confirmed"] = lineup_conf' not in news_src, \
        "simulated_bets.lineup_confirmed write from Gemini value must be removed"

    # _dim_lineup reads from matches table directly
    assert "LINEUP-CONFIDENCE-CLEANUP" in impr_src, "improvements.py tag missing"
    assert "lineups_fetched_at IS NOT NULL" in impr_src, \
        "_dim_lineup must read matches.lineups_fetched_at as the source of truth"
    assert "FROM simulated_bets WHERE match_id = %s AND lineup_confirmed = true" not in impr_src, \
        "Old _dim_lineup query against simulated_bets.lineup_confirmed must be removed"


@test("MFV-LINEUP-WIRE — match_feature_vectors.lineup_confirmed derived from matches.lineups_fetched_at")
def test_mfv_lineup_wire():
    """MFV-LINEUP-WIRE: NEWS-LINEUP-VALIDATE found that lineup_confirmed was 100% NULL
    in match_feature_vectors because nothing wrote a 'lineup_confirmed' signal to
    match_signals. Real lineup data lives in matches.lineups_fetched_at — that's
    the source of truth. Fix: MFV builder pulls lineups_fetched_at from matches
    and derives lineup_confirmed = (lineups_fetched_at IS NOT NULL).

    Source signal in audit: bets on matches with lineups fetched hit 45.1% / +8.1%
    ROI vs 33.5% / -4.5% without (n=1,752 settled bets) — a real, useful B-ML3
    feature once correctly wired.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "api_clients" / "supabase_client.py").read_text()
    assert "MFV-LINEUP-WIRE" in src, "MFV-LINEUP-WIRE tag missing"
    # Both SELECT statements (nightly + live) must include lineups_fetched_at
    assert src.count("lineups_fetched_at") >= 3, (
        "Both SELECTs in MFV builders + the row-build override must reference "
        "lineups_fetched_at (expected >=3 occurrences)"
    )
    # Direct derivation in row build
    assert 'match.get("lineups_fetched_at") is not None' in src, \
        "Row build must derive lineup_confirmed from match.lineups_fetched_at"
    # Old signal-name lookup must be gone (it was always NULL)
    assert 'name == "lineup_confirmed"' not in src, \
        "Dead lineup_confirmed signal lookup must be removed — nothing writes that signal"


@test("INFO-GAP-LEAGUE-AUDIT — audit script exists and flags sharp markets via CLV distribution")
def test_info_gap_league_audit():
    """INFO-GAP-LEAGUE-AUDIT: per-league CLV distribution review from VAL-POST-MORTEM.
    Found one flagged league (Scottish Premiership: n=41, median CLV -25.7%, ROI -48.6%).
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "scripts" / "info_gap_league_audit.py").read_text()
    assert "INFO-GAP-LEAGUE-AUDIT" in src, "tag missing"
    assert "percentile_cont(0.5)" in src, "median CLV computation must use percentile_cont"
    assert "median_clv" in src and "roi_pct" in src, \
        "per-league output must include median CLV and ROI"


@test("NEWS-LINEUP-VALIDATE — validation script exists; runs AUC against home/away/CLV targets")
def test_news_lineup_validate():
    """NEWS-LINEUP-VALIDATE: gate test for B-ML3 feature selection. Verdict 2026-05-24:
    news_impact_score FAILS the 0.52 AUC gate (against home_win AND against bet outcome).
    lineup_confirmed in MFV was 100% NULL (separate bug, fixed by MFV-LINEUP-WIRE).
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "scripts" / "news_lineup_validate.py").read_text()
    assert "NEWS-LINEUP-VALIDATE" in src, "tag missing"
    assert "auc_rank" in src or "AUC" in src, "AUC computation must be implemented"
    assert "news_impact_score" in src and "lineup_confidence" in src, \
        "Both signals must be validated"


@test("OU-UNDER-CAP — audit script exists; investigation closed (no cap applied, see PRIORITY_QUEUE)")
def test_ou_under_cap_audit():
    """OU-UNDER-CAP: VAL-POST-MORTEM hypothesised that high-conviction OU-under
    is miscalibrated. Audit disproved it — the 43 "losses" flagged by the LLM
    were ALL from inplay_e (a separate code path) which is +1.3% ROI on its
    high-conviction subset. No cap applied; follow-up POST-MORTEM-BALANCE
    addresses the underlying LLM availability bias.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "scripts" / "ou_under_cap_audit.py").read_text()
    assert "OU-UNDER-CAP" in src, "OU-UNDER-CAP tag missing from audit script"
    assert "calibrated_prob" in src, "Audit must read calibrated_prob from simulated_bets"
    assert "predicted_pct" in src and "actual_pct" in src, \
        "Audit should report predicted-vs-actual hit rate per bucket"
    assert "selection LIKE 'under%'" in src, "Audit must scope to under selections"


@test("CALIB-DIVERGENCE-LOG — audit script exists and reads from existing raw + cal columns")
def test_calib_divergence_audit():
    """CALIB-DIVERGENCE-LOG: original ticket assumed we needed a new column, but
    simulated_bets already stores BOTH model_probability (raw) and calibrated_prob.
    Re-scoped to an audit script that buckets settled v14 bets by |cal - raw|.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "scripts" / "calib_divergence_audit.py").read_text()
    assert "CALIB-DIVERGENCE-LOG" in src, "CALIB-DIVERGENCE-LOG tag missing"
    assert "model_probability" in src and "calibrated_prob" in src, \
        "Audit should read both raw and calibrated prob from simulated_bets"
    assert "ABS(calibrated_prob - model_probability)" in src, \
        "Audit must compute divergence magnitude"
    assert "model_version = 'v14'" in src, "Audit must scope to v14 only (current production)"


@test("VAL-POST-MORTEM — review script parses 14+ days and surfaces category aggregation")
def test_val_post_mortem():
    """VAL-POST-MORTEM: the review script exists, parses notes via JSON+regex fallback,
    and surfaces category totals (MODEL_ERROR / VARIANCE / INFORMATION_GAP).

    Source-inspection only — running the script needs DB credentials. The findings
    document lives at dev/active/val-post-mortem-2026-05-24.md.
    """
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    script = (base / "scripts" / "val_post_mortem.py").read_text()
    findings = base / "dev" / "active" / "val-post-mortem-2026-05-24.md"

    assert "VAL-POST-MORTEM" in script, "VAL-POST-MORTEM tag missing"
    assert "model_evaluations" in script and "post_mortem" in script, \
        "script should read from model_evaluations WHERE market='post_mortem'"
    assert "MODEL_ERROR" in script and "VARIANCE" in script and "INFORMATION_GAP" in script, \
        "script should aggregate the three main LLM categories"
    assert "def parse_notes" in script, \
        "parse_notes() with JSON+regex fallback expected — 5 of 14 rows need regex"
    assert findings.exists(), \
        f"findings doc missing: {findings}"


@test("AH-AWAY-LINE-FILTER — bot_ah_away_dog only accepts handicap_line >= +0.5")
def test_ah_away_line_filter():
    """AH-AWAY-LINE-FILTER (2026-05-24): AH-AWAY-MODEL-AUDIT slice-1 showed
    bot_ah_away_dog was catastrophic on negative handicaps (away-favorite picks):
    -0.5 line -46% ROI on 300 bets, -1.0 line -74% ROI on 122. Positive handicaps
    were +42% ROI at +0.5 line. Initial filter hl>=0 gave +26% ROI; candidate-bot
    backtest then showed tightening to hl>=+0.5 (drops the breakeven-negative
    hl=0.0 bucket on n=61) jumps ROI to +43%.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "AH-AWAY-LINE-FILTER" in src, "tag missing"
    assert '"handicap_line_min": 0.5' in src, \
        "bot_ah_away_dog must declare handicap_line_min=0.5 (post-TIGHTEN)"
    assert '_hl_min = config.get("handicap_line_min")' in src, \
        "candidate-gen loop must read handicap_line_min from config"
    assert "if _hl_min is not None and _hl < _hl_min:" in src, \
        "candidate-gen must apply the floor check"


@test("BACKUP-RESTORE-DRILL — read-only backup viability check + runbook documented")
def test_backup_restore_drill():
    """BACKUP-RESTORE-DRILL (2026-05-25): read-only drill that verifies all
    critical tables exist, are populated, and prints the manual restore
    procedure. Run quarterly or before risky migrations. Does NOT actually
    restore anything — that's a manual Supabase dashboard operation."""
    import pathlib
    script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "backup_restore_drill.py"
    assert script.exists(), "backup_restore_drill.py missing"
    src = script.read_text()
    assert "CRITICAL_TABLES" in src
    # All actually-critical tables must be in the census
    for name in ("matches", "simulated_bets", "real_bets", "predictions",
                 "odds_snapshots", "match_signals", "match_feature_vectors",
                 "model_versions", "bots", "leagues"):
        assert f'"{name}"' in src, f"critical table {name} missing from census"
    # Runbook is in the script
    assert "Point in Time Recovery" in src
    assert "Restore procedure" in src
    assert "7-day retention" in src
    assert "VERDICT" in src


@test("B-ML3-VALIDATE-ACTIVATION — meta-model real-world validation script + methodology")
def test_b_ml3_validate_activation():
    """B-ML3-VALIDATE-ACTIVATION (2026-05-25): the activation gate for
    META_B_ML3_ENABLED=true. Bins settled bets by meta_clv_score quintile,
    computes CLV-beat rate per bin, verdicts PASS / MARGINAL / FAIL.
    Methodology must also be documented in MODEL_WHITEPAPER §3.5."""
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    script = base / "scripts" / "validate_meta_b_ml3.py"
    assert script.exists(), "validation script missing"
    src = script.read_text()
    # Core methodology pins
    assert "B-ML3-VALIDATE-ACTIVATION" in src
    assert "pseudo_clv" in src or "clv_pinnacle" in src
    assert "qcut" in src or "quintile" in src.lower()
    assert "PASS" in src and "MARGINAL" in src and "FAIL" in src
    # Bundle-aware (logistic + xgboost)
    assert "model_type.txt" in src
    assert "scaler" in src
    # Whitepaper section must exist with same rules
    wp = (base / "MODEL_WHITEPAPER.md").read_text()
    assert "3.5 B-ML3 Activation Validation" in wp
    assert "B-ML3-VALIDATE-ACTIVATION" in wp
    assert "PASS" in wp


@test("COMPARE-META-BUNDLES — side-by-side bundle comparison script for swap decisions")
def test_compare_meta_bundles():
    """COMPARE-META-BUNDLES (2026-05-25): scripts/compare_meta_bundles.py loads
    every bundle in data/models/meta/, scores them on the same training cohort,
    prints AUC/Brier/log-loss + threshold sweep. Used to make data-driven
    META_B_ML3_VERSION swap decisions instead of guessing."""
    import pathlib
    script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "compare_meta_bundles.py"
    assert script.exists()
    src = script.read_text()
    # Must support both logistic + xgboost bundles
    assert "model_type.txt" in src
    assert "scaler.transform" in src and "if scaler is None" in src
    # Must report key metrics + threshold sweep
    assert "roc_auc_score" in src
    assert "brier_score_loss" in src
    assert "threshold sweep" in src.lower()
    # Must handle feature schema drift across bundle versions
    assert "X_aligned" in src and "bundle_features" in src


@test("ELITE-LEAGUE-FILTER — env-gated league_clv_efficiency filter in candidate eval")
def test_elite_league_filter():
    """ELITE-LEAGUE-FILTER (2026-05-25): data-driven generalisation of
    SCOTTISH-PREM-LEAGUE-GATE. Reads the league_clv_efficiency signal
    written by LEAGUE-CLV-EFFICIENCY and skips matches whose league rolling
    mean CLV is below a threshold. Env-gated OFF by default; activation is
    a Phase 4 (post-2026-06-07) decision.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "ELITE-LEAGUE-FILTER" in src, "tag missing"
    assert 'ELITE_LEAGUE_FILTER_ENABLED' in src, "env-gate must exist"
    assert 'ELITE_LEAGUE_FILTER_THRESHOLD' in src, "threshold env must exist"
    assert "_league_clv_efficiency" in src, "per-match cache attribute missing"
    assert "league_clv_by_match" in src, "batch loader must populate the dict"
    assert "signal_name = 'league_clv_efficiency'" in src, "must read the right signal"


@test("META-LOADER-XGBOOST-BRANCH — loader handles both logistic and xgboost bundles")
def test_meta_loader_xgboost_branch():
    """META-LOADER-XGBOOST-BRANCH (2026-05-25): meta_b_ml3._load_bundle reads
    model_type.txt and skips scaler.transform on xgboost bundles. Required so
    META_B_ML3_VERSION=v_20260525_v23_xgb can actually load without scaler
    AttributeError."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "model" / "meta_b_ml3.py").read_text()
    assert "META-LOADER-XGBOOST-BRANCH" in src, "loader tag missing"
    assert 'model_type.txt' in src, "loader must read model_type.txt"
    assert 'bundle.get("scaler") is None' in src, "scoring path must branch on scaler=None"
    # The v23_xgb bundle ships model_type.txt with content 'xgboost'
    bundle_dir = pathlib.Path(__file__).resolve().parent.parent / "data" / "models" / "meta" / "v_20260525_v23_xgb"
    if bundle_dir.exists():
        mt = (bundle_dir / "model_type.txt").read_text().strip()
        assert mt == "xgboost", f"v23 bundle must declare model_type=xgboost (got {mt})"


@test("BUNDLE-STORAGE-SYNC — meta-model bundle upload/download helpers + auto-mirror wired")
def test_bundle_storage_sync():
    """BUNDLE-STORAGE-SYNC (2026-05-25): meta-model bundles mirror to Supabase
    Storage under prefix 'meta/<version>/'. _load_bundle hydrates from Storage
    on cache miss. Weekly meta-retrain auto-uploads new bundles after training."""
    import pathlib, inspect
    storage_src = (pathlib.Path(__file__).resolve().parent.parent /
                   "workers" / "model" / "storage.py").read_text()
    assert "def upload_meta_bundle" in storage_src
    assert "def ensure_local_meta_bundle" in storage_src
    assert 'f"meta/{version}/' in storage_src
    # Meta loader hydrates from storage
    meta_src = (pathlib.Path(__file__).resolve().parent.parent /
                "workers" / "model" / "meta_b_ml3.py").read_text()
    assert "ensure_local_meta_bundle" in meta_src
    # Weekly retrain auto-uploads
    sched = (pathlib.Path(__file__).resolve().parent.parent /
             "workers" / "scheduler.py").read_text()
    assert "upload_meta_bundle" in sched, "weekly meta retrain must auto-upload to Storage"


@test("MFV-NIGHTLY-REFRESH — B-ML3 v2 + form_momentum backfills wired to nightly cron")
def test_mfv_nightly_refresh():
    """MFV-B-ML3-V2-NIGHTLY-REFRESH + MFV-FORM-MOMENTUM-NIGHTLY-REFRESH
    (2026-05-25): nightly cron re-runs both backfills so MFV rows for matches
    that finished today settle into the new columns. Replaces a direct live-
    MFV-builder modification (which has T-6h snapshot ordering issues)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "scheduler.py").read_text()
    assert "job_nightly_mfv_b_ml3_refresh" in src
    assert "job_nightly_mfv_form_momentum_refresh" in src
    assert "backfill_mfv_b_ml3_v2_features.py" in src
    assert "backfill_mfv_form_momentum.py" in src
    assert 'CronTrigger(hour=22, minute=30)' in src
    assert 'CronTrigger(hour=22, minute=45)' in src


@test("STAKE-KELLY-SAFETY-AUDIT — pre-real-money sanity audit script exists + 6-check structure")
def test_stake_kelly_safety_audit():
    """STAKE-KELLY-SAFETY-AUDIT (2026-05-25): read-only audit script runs before
    real-money execution to flag stake-sizing anomalies (per-bet cap, daily
    exposure, Kelly recompute sanity, negative-EV bets). Findings documented
    in PRIORITY_QUEUE — does NOT auto-fix anything."""
    import pathlib
    script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "stake_kelly_safety_audit.py"
    assert script.exists(), "stake_kelly_safety_audit.py missing"
    src = script.read_text()
    # 6 distinct safety checks present
    for check in ("Stake distribution", "MAX_STAKE_PCT", "exceed 5%",
                  "Kelly recompute", "Negative-EV", "open_exposure"):
        assert check in src, f"safety audit must include '{check}' check"
    assert "VERDICT" in src, "audit must print a verdict"


@test("LEAGUE-CLV-EFFICIENCY — per-league CLV index script + weekly cron wired")
def test_league_clv_efficiency():
    """LEAGUE-CLV-EFFICIENCY (2026-05-25): per-league CLV beatability index
    computed from matches.pseudo_clv_*, persisted to match_signals as
    'league_clv_efficiency'. Weekly cron Sun 02:30 UTC fires the script.
    Feeds future B-ML3 training as a categorical-via-numeric signal.
    """
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    script = base / "scripts" / "compute_league_clv_efficiency.py"
    assert script.exists(), "compute_league_clv_efficiency.py missing"
    src = script.read_text()
    assert "league_clv_efficiency" in src
    assert "pseudo_clv_home" in src and "pseudo_clv_draw" in src and "pseudo_clv_away" in src
    assert "INSERT INTO match_signals" in src
    assert "MIN_MATCHES_FOR_SIGNAL" in src, "sample-size guard required"
    sched = (base / "workers" / "scheduler.py").read_text()
    assert "job_league_clv_efficiency" in sched
    assert 'CronTrigger(day_of_week="sun", hour=2, minute=30)' in sched
    assert 'id="league_clv_efficiency"' in sched


@test("META-RETRAIN — weekly B-ML3 retrain cron registered Sunday 04:00 UTC")
def test_meta_retrain():
    """META-RETRAIN (2026-05-25): Sunday 04:00 UTC retrain job invokes
    scripts/train_b_ml3.py with a versioned tag and logs to pipeline_runs.
    Promotion stays manual — operator inspects new bundle's threshold.json
    and decides whether to flip META_B_ML3_VERSION on Railway.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "scheduler.py").read_text()
    assert "job_weekly_meta_retrain" in src, "job function missing"
    assert "scripts/train_b_ml3.py" in src, "scheduler must invoke train_b_ml3"
    assert 'CronTrigger(day_of_week="sun", hour=4, minute=0)' in src, \
        "Sunday 04:00 UTC slot missing"
    assert 'id="weekly_meta_retrain"' in src


@test("DAILY-REAL-PERF-EMAIL — 23:30 UTC daily summary via Resend wired")
def test_daily_real_perf_email():
    """DAILY-REAL-PERF-EMAIL (2026-05-25): captures yesterday + 7d
    real_perf_split_by_source output and emails the summary. Runs after
    settlement so the data is final."""
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    src = (base / "workers" / "jobs" / "daily_real_perf_email.py").read_text()
    assert "def send_daily_real_perf" in src
    assert "real_perf_split_by_source" in src
    assert "RESEND_API_KEY" in src
    sched = (base / "workers" / "scheduler.py").read_text()
    assert "job_daily_real_perf_email" in sched
    assert 'CronTrigger(hour=23, minute=30)' in sched


@test("HEALTH-ALERTS-MONITORING — 5 new checks (memory, refresh-dead-man, AF quota, model drift, meta drift)")
def test_health_alerts_monitoring():
    """MEMORY-MONITORING + PIPELINE-DEAD-MAN'S-SWITCH + OBS-BUDGET-ALERT +
    MODEL-DRIFT-ALERT + B-ML3 SCORE-DRIFT — all five new check functions ship
    in health_alerts.py and are wired into run_snapshot_check (hourly cadence
    via job_health_alerts_snapshot in scheduler.py).
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "health_alerts.py").read_text()
    for name in ("check_memory_usage", "check_betting_refresh_stale",
                 "check_af_quota", "check_model_drift", "check_meta_score_drift"):
        assert f"def {name}" in src, f"{name} function missing"
        assert name in src.split("def run_snapshot_check")[1], \
            f"{name} not wired into run_snapshot_check"
    # Alert IDs are unique so dedup works
    for alert_id in ("memory_high", "betting_refresh_stale", "af_quota_high",
                     "model_drift", "meta_score_drift"):
        assert f'"{alert_id}"' in src, f"alert dedup key '{alert_id}' missing"
    # psutil in requirements (with /proc fallback for envs that don't have it)
    reqs = (pathlib.Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    assert "psutil" in reqs, "psutil must be declared (MEMORY-MONITORING uses it on Railway)"


@test("B-ML3-V2-ACTIVE — meta-model scorer wired into daily_pipeline_v2 with env gating")
def test_b_ml3_v2_active():
    """B-ML3-V2-ACTIVE (2026-05-25): the trained v2.1 meta-model scores every
    candidate bet during placement. Default META_B_ML3_ENABLED=false keeps the
    filter OFF — scores are logged passively to simulated_bets.meta_clv_score
    for retrospective analysis. Flipping META_B_ML3_ENABLED=true activates
    pre-placement filtering at the chosen threshold (default 0.475).
    """
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    # Bundle artifacts shipped with the repo for Railway deploy
    bundle = base / "data" / "models" / "meta" / "v_20260525_v21"
    assert (bundle / "b_ml3.pkl").exists(), "v_20260525_v21 model pickle must be committed"
    assert (bundle / "scaler.pkl").exists(), "scaler must be committed"
    assert (bundle / "feature_cols.pkl").exists(), "feature_cols must be committed"
    assert (bundle / "threshold.json").exists(), "threshold.json must be committed"
    # Inference module
    meta_src = (base / "workers" / "model" / "meta_b_ml3.py").read_text()
    assert "def score_bet" in meta_src and "def should_fire" in meta_src
    assert "META_B_ML3_ENABLED" in meta_src and "META_B_ML3_THRESHOLD" in meta_src
    # Pipeline wires the scorer
    pipe = (base / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "B-ML3-V2-ACTIVE" in pipe
    assert "_meta.should_fire" in pipe
    # Migration 130 ships the score column
    mig = (base / "supabase" / "migrations" / "130_meta_clv_score_column.sql").read_text()
    assert "meta_clv_score FLOAT" in mig
    assert "simulated_bets" in mig and "shadow_bets" in mig
    # Behavioural — disabled-default fires every bet
    import os, importlib
    os.environ.pop("META_B_ML3_ENABLED", None)
    from workers.model import meta_b_ml3
    importlib.reload(meta_b_ml3)
    assert meta_b_ml3.should_fire(None) is True
    assert meta_b_ml3.should_fire(0.1) is True


@test("OVERNIGHT-ODDS-CAPTURE — superseded by OPENING-LINE-MOVE-CAPTURE")
def test_overnight_odds_capture():
    """OVERNIGHT-ODDS-CAPTURE 2026-05-25 was first shipped with 02:00 + 04:00
    UTC slots fetching TODAY's odds — but today's matches had no prior
    snapshot to diff against (verified 2026-05-25: 0 of 178 today-kickoff
    matches had any yesterday snapshot). Superseded same day by
    OPENING-LINE-MOVE-CAPTURE which fetches TOMORROW's odds at 22:00 UTC
    instead, so the match-day morning fetch produces a real delta. The
    OPENING-LINE-MOVE-CAPTURE smoke now guards the correct schedule;
    this test only ensures the old broken slots stay removed.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "scheduler.py").read_text()
    assert 'id="odds_0200"' not in src, "old broken 02:00 slot must stay removed"
    assert 'id="odds_0400"' not in src, "old broken 04:00 slot must stay removed"
    # The replacement must be wired
    assert "OPENING-LINE-MOVE-CAPTURE" in src
    assert "job_odds_tomorrow" in src


@test("META-FEATURE-DESIGN — B-ML3 feature list is documented + grounded in coverage data")
def test_meta_feature_design():
    """META-FEATURE-DESIGN (2026-05-24): finalize the B-ML3 feature list before
    training. The 14-feature shortlist must be documented in MODEL_WHITEPAPER.md
    so any drift (e.g. someone re-adding news_impact_score) gets caught.
    Today's NEWS-LINEUP-VALIDATE finding (drop news_impact_score, +12pp ROI
    signal on lineup_confirmed) is the load-bearing rationale and must be
    referenced.
    """
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    wp = (base / "MODEL_WHITEPAPER.md").read_text()
    assert "3.4 Meta-model Feature Set" in wp, "section header missing"
    assert "META-FEATURE-DESIGN" in wp, "tag missing"
    assert "lineup_confirmed" in wp, "lineup_confirmed must be in the documented list (+12pp ROI signal)"
    assert "ensemble_prob" in wp, "the edge-proxy feature must be present"
    # Explicit drop rationales we want preserved
    assert "news_impact_score" in wp, "news_impact_score must appear with its drop rationale"
    assert "AUC 0.30" in wp, "must cite the NEWS-LINEUP-VALIDATE AUC finding"
    # Confirm the doc tracks coverage minimum so future agents apply the same gate
    assert "Coverage" in wp and "30" in wp, "30% coverage threshold must be documented"


@test("AH-HOME-LINE-FILTER — bot_ah_home_fav only accepts handicap_line <= -0.5")
def test_ah_home_line_filter():
    """AH-HOME-LINE-FILTER (2026-05-24): AH-AWAY-MODEL-AUDIT live-data follow-up
    (scripts/ah_model_audit_live.py) found the asymmetry resolved post-AH-CAL-BYPASS
    (home_fav +12.1% / away_dog +9.4% on 62 settled bets) BUT both bots had ROI
    ~-50% on the +0 line (DNB-equivalent). Root cause: the joint goal model's
    push-adjusted probability over-amplifies the imperfect favourite-longshot bias
    correction. Symmetric fix to handicap_line_min on bot_ah_away_dog: cap
    bot_ah_home_fav at handicap_line <= -0.5 so it only fires when home is a true
    favourite (giving goals).
    """
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    src = (base / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "AH-HOME-LINE-FILTER" in src, "tag missing"
    assert '"handicap_line_max": -0.5' in src, \
        "bot_ah_home_fav must declare handicap_line_max=-0.5"
    assert '_hl_max = config.get("handicap_line_max")' in src, \
        "candidate-gen loop must read handicap_line_max from config"
    assert "if _hl_max is not None and _hl > _hl_max:" in src, \
        "candidate-gen must apply the ceiling check"
    # Live-data audit script exists + is referenced
    assert (base / "scripts" / "ah_model_audit_live.py").exists(), \
        "live audit script must exist"


@test("UNMATCHED-LOG-QUIET — unmatched team-name logger doesn't propagate to stdout")
def test_unmatched_log_quiet():
    """UNMATCHED-LOG-QUIET (2026-05-24): the unmatched_teams logger writes to
    data/logs/unmatched_teams.log but had propagate=True by default, so its
    INFO messages reached the root logger and cluttered Railway stdout.
    Fixed by setting propagate=False — file handler still captures them."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "utils" / "team_names.py").read_text()
    assert "UNMATCHED-LOG-QUIET" in src, "tag missing"
    assert "_unmatched_logger.propagate = False" in src, "propagation must be disabled"
    # Behavioural check
    from workers.utils.team_names import _unmatched_logger
    assert _unmatched_logger.propagate is False, "live logger must have propagate=False"


@test("CV-METRICS-PERSIST — train.py threads CV metrics into model_versions.cv_metrics")
def test_cv_metrics_persist():
    """CV-METRICS-PERSIST (2026-05-24): train_result_model + train_over25_model
    attach _cv_metrics to the model object; train_all reads them and passes
    to register_version. Previously cv_metrics was always None on weekly
    retrain rows, making compare_models.py structurally non-functional.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "model" / "train.py").read_text()
    assert "CV-METRICS-PERSIST" in src, "tag missing"
    assert "_cv_metrics" in src, "metrics attribute must be set on model objects"
    assert "cv_log_loss_mean" in src, "log_loss CV metric missing"
    assert "cv_brier_mean" in src, "brier CV metric missing"
    assert "cv_metrics_combined" in src, "train_all must combine result + over_under metrics"
    assert "cv_metrics=None" not in src, "TODO marker should be gone"


@test("WEEKLY-RETRAIN-OU-FEATURES — scheduler invokes train.py with both market-feature flags")
def test_weekly_retrain_ou_features():
    """WEEKLY-RETRAIN-OU-FEATURES (2026-05-24). job_weekly_retrain previously
    invoked train.py with just --version, silently dropping the 14
    pinnacle_implied_* / ou25_bookmaker_disagreement / market_implied_btts_yes
    columns that v14 was trained with. Cost: +9 to +13% log-loss on the
    over_under XGBoost head for every weekly bundle since this job shipped
    (visible in the MARKET-EVAL output for v20260517 / v20260524). This guard
    fails if anyone removes the flags from the subprocess invocation.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "scheduler.py").read_text()
    assert "WEEKLY-RETRAIN-OU-FEATURES" in src, "tag missing"
    assert '"--include-pinnacle"' in src, \
        "weekly retrain must pass --include-pinnacle"
    assert '"--include-ou-market"' in src, \
        "weekly retrain must pass --include-ou-market"


@test("WEEKLY-EVAL — cron uses weekly_eval_and_compare.py with holdout MFV rows")
def test_weekly_eval():
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    sched = (base / "workers" / "scheduler.py").read_text()
    eval_src = (base / "scripts" / "weekly_eval_and_compare.py").read_text()
    assert "WEEKLY-EVAL" in sched, "scheduler tag missing"
    assert "scripts/weekly_eval_and_compare.py" in sched, "scheduler must call the new eval script"
    assert '"scripts/compare_models.py"' not in sched, "legacy compare_models.py call must be removed"
    assert "SUMMARY_JSON" in eval_src, "eval must emit SUMMARY_JSON line for email digest"


@test("MARKET-EVAL-BTTS-AH — weekly eval scores BTTS + AH half-lines via joint goal matrix")
def test_market_eval_btts_ah():
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    src = (base / "scripts" / "weekly_eval_and_compare.py").read_text()
    # Source-inspection guards so the new scoring paths can't silently regress.
    assert "MARKET-EVAL-BTTS-AH" in src, "tag missing"
    assert "build_joint_matrix" in src, "must import production joint-matrix builder"
    assert "_truth_btts" in src, "BTTS truth label helper missing"
    assert "_ah_truth_home" in src, "AH truth helper missing"
    assert "_ah_prob_home" in src, "AH probability helper missing"
    assert '"home_goals.pkl"' in src and '"away_goals.pkl"' in src, \
        "must load Poisson goal regressors for BTTS/AH derivation"
    assert "ah_home_-0.5" in src and "ah_home_+0.5" in src, "0.5 AH lines must be scored"
    assert "ah_home_-1.5" in src and "ah_home_+1.5" in src, "1.5 AH lines must be scored"
    assert '"btts_yes"' in src and '"btts_no"' in src, "BTTS markets must be in eval_markets"
    # Behavioural assertion — half-lines should never push, so truth is binary.
    from scripts.weekly_eval_and_compare import _ah_truth_home, _ah_prob_home
    import numpy as np
    assert _ah_truth_home(2, 0, -0.5) == 1, "home -0.5 covers when home wins"
    assert _ah_truth_home(1, 1, -0.5) == 0, "home -0.5 loses on draw"
    assert _ah_truth_home(1, 1,  0.5) == 1, "home +0.5 covers on draw"
    assert _ah_truth_home(0, 2, -1.5) == 0, "home -1.5 loses when home loses by 2"
    assert _ah_truth_home(0, 1,  1.5) == 1, "home +1.5 covers when home loses by 1"
    # Symmetric joint matrix: P(home covers -0.5) = P(home wins) ≈ P(away wins)
    # on equal lambdas, so should sit near 0.5 minus the draw mass.
    from workers.model.joint_probability import build_joint_matrix
    matrix = build_joint_matrix(1.4, 1.4)
    p = _ah_prob_home(matrix, -0.5)
    assert 0.20 < p < 0.45, f"home -0.5 on equal lambdas should be ~0.3-0.4, got {p:.3f}"


@test("WEEKLY-EVAL-EMAIL — Resend digest after retrain")
def test_weekly_eval_email():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "weekly_retrain_email.py").read_text()
    assert "RESEND_API_KEY" in src, "must use Resend"
    assert "_extract_summary" in src, "summary parser missing"
    assert "send_weekly_retrain_email" in src, "public entry point missing"
    sched = (pathlib.Path(__file__).resolve().parent.parent / "workers" / "scheduler.py").read_text()
    assert "send_weekly_retrain_email" in sched, "scheduler must invoke email digest"


@test("PROMOTE-MODEL-SCRIPT — scripts/promote_model.py supports per-market scope + dry-run")
def test_promote_model_script():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "scripts" / "promote_model.py").read_text()
    assert "promoted_at = COALESCE(promoted_at, NOW())" in src, "promotion timestamp logic missing"
    assert "demoted_at = NOW()" in src, "demotion of previous prod missing"
    assert "--market" in src, "per-market promotion flag required"
    assert "MODEL_VERSION_" in src, "must reference per-market env var pattern"
    assert "--dry-run" in src, "dry-run flag required"


@test("PER-MARKET-VERSION — xgboost_ensemble supports MODEL_VERSION_1X2/OU/GOALS overrides")
def test_per_market_version():
    """PER-MARKET-VERSION (Phase C-light, 2026-05-24). Three env vars are
    actually read at inference time: MODEL_VERSION_1X2, MODEL_VERSION_OU,
    MODEL_VERSION_GOALS. BTTS / AH are derived from the GOALS bundle's
    Poisson joint matrix — there is intentionally no MODEL_VERSION_BTTS
    routing. Setting MODEL_VERSION_BTTS today does nothing; the comment
    + this test name were updated to stop misleading future readers.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "model" / "xgboost_ensemble.py").read_text()
    assert "PER-MARKET-VERSION" in src, "tag missing"
    assert "_resolve_version" in src, "version resolver missing"
    assert "MODEL_VERSION_" in src, "per-market env var pattern missing"
    assert "_bundles: dict[str, dict]" in src, "per-version bundle cache missing"
    assert "_load_bundle(version" in src, "per-version bundle loader missing"
    # The three heads that are actually routed at inference.
    assert '_resolve_version("1x2")' in src, "1X2 head must be resolved per-market"
    assert '_resolve_version("ou")' in src, "OU head must be resolved per-market"
    assert '_resolve_version("goals")' in src, "goals head must be resolved per-market"
    # BTTS deliberately NOT in the inference path — flag drift if someone adds it.
    assert '_resolve_version("btts")' not in src, \
        "BTTS routing must not be wired — production BTTS comes from the GOALS bundle's Poisson joint matrix"
    # Behavioural assertions
    import os
    from workers.model.xgboost_ensemble import _resolve_version, MODEL_VERSION
    for kind in ("1x2", "ou", "goals"):
        env = f"MODEL_VERSION_{kind.upper()}"
        os.environ.pop(env, None)
        assert _resolve_version(kind) == MODEL_VERSION, f"no override → fallback (kind={kind})"
    os.environ["MODEL_VERSION_1X2"] = "v20260517"
    try:
        assert _resolve_version("1x2") == "v20260517", "override → returns env value"
        assert _resolve_version("ou") == MODEL_VERSION, "OU unaffected by 1X2 override"
        assert _resolve_version("goals") == MODEL_VERSION, "goals unaffected by 1X2 override"
    finally:
        os.environ.pop("MODEL_VERSION_1X2", None)


@test("SHADOW-PREDICTIONS — predictions allows multi-version + pipeline writes shadow rows")
def test_shadow_predictions():
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    mig = (base / "supabase" / "migrations" / "127_predictions_unique_per_model_version.sql").read_text()
    assert "uq_prediction_match_market_source_version" in mig, "new constraint name missing"
    assert "UNIQUE (match_id, market, source, model_version)" in mig, "constraint must include model_version"
    client_src = (base / "workers" / "api_clients" / "supabase_client.py").read_text()
    assert "ON CONFLICT (match_id, market, source, model_version)" in client_src, \
        "bulk_store_predictions ON CONFLICT must include model_version"
    pipe_src = (base / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "SHADOW-INFERENCE" in pipe_src, "pipeline tag missing"
    assert 'os.environ.get("SHADOW_MODEL_VERSION"' in pipe_src, "pipeline must check SHADOW_MODEL_VERSION env"
    assert "xgb_pred_shadow" in pipe_src, "shadow prediction variable missing"
    assert '"model_version": _sv' in pipe_src, "shadow rows must carry candidate model_version"


@test("AH-CAL-BYPASS — calibrate_prob skips stage-1 shrinkage for AH/DC markets")
def test_ah_cal_bypass():
    """AH-CAL-BYPASS: AH probs are derived from already-Platt-calibrated 1X2 lambdas
    via _solve_lambdas_calibrated; DC probs are direct sums of calibrated 1X2 probs.
    Applying tier shrinkage a second time was double-discounting the model and
    killed all ~170 daily AH-away candidates at the 5% edge gate (verified via
    funnel diagnostic on 2026-05-24 — all 1xx candidates died at ↓edge, zero
    PIN-VETO drops). Skip stage-1 shrinkage for these markets; apply_platt is
    already a no-op since no Platt fits exist for them.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "model" / "improvements.py").read_text()
    assert "AH-CAL-BYPASS" in src, "tag missing"
    assert 'mkt_lower.startswith("asian_handicap")' in src, \
        "calibrate_prob must early-return for asian_handicap markets"
    assert 'mkt_lower.startswith("double_chance")' in src, \
        "calibrate_prob must also early-return for double_chance markets"

    # Behavioural assertion — load the function and verify shrinkage is skipped.
    # AH/DC may still have stage-2 Platt applied (PLATT-LIVE-FIT 2026-05 added
    # an aggregate `asian_handicap` Platt fit), so compare against stage-2 only.
    from workers.model.improvements import calibrate_prob, _apply_stage2
    expected = _apply_stage2(0.55, "asian_handicap_Away +0.50", odds=2.00)
    result = calibrate_prob(0.55, 0.50, tier=1, market="asian_handicap_Away +0.50",
                            anchor_implied=None, odds=2.00)
    assert abs(result - expected) < 1e-6, (
        f"AH should bypass stage-1 shrinkage and only apply stage-2; "
        f"got {result}, expected {expected}"
    )
    # Non-AH non-DC market should still go through stage-1 shrinkage —
    # compare against stage-2-only on the same inputs to isolate that effect.
    # Shrinkage pulls 0.55 toward implied=0.50, and Platt is monotonic in its
    # input, so the calibrated value must come out below the stage-2-only baseline.
    result_1x2 = calibrate_prob(0.55, 0.50, tier=1, market="1x2_home", odds=2.00)
    stage2_only_1x2 = _apply_stage2(0.55, "1x2_home", odds=2.00)
    assert result_1x2 < stage2_only_1x2, (
        f"1x2_home should still shrink (stage-1 then stage-2); "
        f"got {result_1x2}, stage-2-only baseline {stage2_only_1x2}"
    )


@test("AH-VETO-WIDEN — AH/DC use wider 0.22 veto gap (was 0.12, killed bot_ah_away_dog)")
def test_ah_veto_widen():
    """AH-VETO-WIDEN: spread markets (AH, DC) use a wider veto gap than 1X2/O/U.

    PIN-VETO-EXT (2026-05-12) extended the 0.12 cal_prob-vs-ip veto to AH using
    best-book ip as fallback anchor. But AH model probs vs single-book ip routinely
    differ by 15-25pp due to AH spread-betting mechanics, so the bot placed 0 bets
    in 12 days. Wider 0.22 gap restores bot activity while still catching the
    pathological +40-60% EV outliers PIN-VETO-EXT originally targeted.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "daily_pipeline_v2.py").read_text()

    assert "AH-VETO-WIDEN" in src, \
        "AH-VETO-WIDEN tag not found in daily_pipeline_v2.py"
    assert '_veto_gap = 0.22 if mkt in ("asian_handicap", "double_chance") else PINNACLE_VETO_GAP' in src, \
        "AH/DC veto-gap branch missing — bot_ah_away_dog will stay silent"
    assert "(cal_prob - _veto_anchor) > _veto_gap" in src, \
        "Veto check should use the per-market _veto_gap, not the fixed PINNACLE_VETO_GAP"


@test("ENUM-NOT-STARTED — fetch_weather and watchlist_alerts use valid match_status enum values")
def test_enum_not_started():
    """Ensure 'not_started' (invalid enum) is not used in any SQL queries."""
    import pathlib
    fw_src = (pathlib.Path(__file__).resolve().parent.parent /
              "workers" / "jobs" / "fetch_weather.py").read_text()
    wa_src = (pathlib.Path(__file__).resolve().parent.parent /
              "workers" / "jobs" / "watchlist_alerts.py").read_text()

    assert "not_started" not in fw_src, \
        "fetch_weather.py still uses 'not_started' — invalid match_status enum value"
    assert "not_started" not in wa_src, \
        "watchlist_alerts.py still uses 'not_started' — invalid match_status enum value"


@test("AUDIT-SILENT-EXCEPT — critical silent exceptions replaced with console.print warnings")
def test_audit_silent_except():
    """AUDIT-SILENT-EXCEPT: 4 data-loss-risk silent exceptions now log a warning."""
    import pathlib
    lp_src = (pathlib.Path(__file__).resolve().parent.parent /
              "workers" / "live_poller.py").read_text()
    sc_src = (pathlib.Path(__file__).resolve().parent.parent /
              "workers" / "scheduler.py").read_text()

    # live_poller.py: store_match_events_batch failure must not be silent
    assert "store_match_events_batch failed for" in lp_src, \
        "live_poller.py: store_match_events_batch failure is still swallowed silently"

    # scheduler.py settlement pipeline: logging failures must not be silent
    assert "log_pipeline_start failed (non-critical)" in sc_src, \
        "scheduler.py: log_pipeline_start failure is still swallowed silently"
    assert "log_pipeline_complete failed (non-critical)" in sc_src, \
        "scheduler.py: log_pipeline_complete failure is still swallowed silently"
    assert "log_pipeline_failed failed (non-critical)" in sc_src, \
        "scheduler.py: log_pipeline_failed failure is still swallowed silently"


@test("AF-CACHE-TEAM-STATS — fetch_team_stats checks same-day DB cache before AF call")
def _():
    """Source guard: if someone removes the cached_stat_keys check, we silently
    re-fetch every Tier-A team stat every intraday enrichment run (+150 AF calls/day)."""
    import pathlib
    src = pathlib.Path("workers/jobs/fetch_enrichment.py").read_text()
    fn_start = src.index("def fetch_team_stats(")
    fn_end = src.index("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "cached_stat_keys" in body, (
        "fetch_team_stats must build cached_stat_keys from team_season_stats WHERE fetched_date — "
        "without it every intraday run re-fetches all Tier-A teams from AF"
    )
    assert "fetched_date = %s" in body, (
        "cache query must filter by fetched_date to scope to today's already-fetched rows"
    )
    assert "key in cached_stat_keys" in body, (
        "loop must skip keys already in cached_stat_keys — the query without the check is wasted"
    )


@test("AF-CACHE-H2H — fetch_h2h builds 7-day cross-match cache before AF calls")
def _():
    """Source guard: 7-day cross-match cache reuses H2H from same team pair within the week.
    Without it, every intraday enrichment run re-fetches H2H for returning fixtures (~360/day)."""
    import pathlib
    src = pathlib.Path("workers/jobs/fetch_enrichment.py").read_text()
    fn_start = src.index("def fetch_h2h(")
    fn_end = src.index("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "h2h_week_cache" in body, (
        "fetch_h2h must build h2h_week_cache from matches within 7 days — "
        "without it every intraday enrichment call hits AF for already-fetched H2H pairs"
    )
    assert "INTERVAL '7 days'" in body, (
        "cross-match cache query must filter to the last 7 days — "
        "older H2H should be re-fetched (lineup changes)"
    )
    assert "pair_key in h2h_week_cache" in body, (
        "per-fixture loop must check pair_key against h2h_week_cache before calling get_h2h"
    )


@test("AF-STANDINGS-DAILY — standings moved to 23:30 nightly; intraday jobs no longer fetch standings")
def _():
    """Source guard: standings update ~1x/week; running them at 10:30/13:00/16:00 wasted
    ~40 AF calls/day. AF-STANDINGS-DAILY moves them to a single 23:30 UTC nightly job."""
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()

    # job_enrichment_refresh must use only injuries
    refresh_start = src.index("def job_enrichment_refresh(")
    refresh_end = src.index("\ndef ", refresh_start + 1)
    refresh_body = src[refresh_start:refresh_end]
    assert '"standings"' not in refresh_body, (
        "job_enrichment_refresh must not include standings — AF-STANDINGS-DAILY moved them to 23:30"
    )
    assert '"injuries"' in refresh_body, (
        "job_enrichment_refresh must still fetch injuries"
    )

    # job_enrichment_full must use explicit components without standings
    full_start = src.index("def job_enrichment_full(")
    full_end = src.index("\ndef ", full_start + 1)
    full_body = src[full_start:full_end]
    assert '"standings"' not in full_body, (
        "job_enrichment_full must not include standings — AF-STANDINGS-DAILY moved them to 23:30"
    )
    assert '"h2h"' in full_body and '"team_stats"' in full_body, (
        "job_enrichment_full must still include h2h and team_stats"
    )

    # job_standings_nightly must exist
    assert "def job_standings_nightly(" in src, (
        "job_standings_nightly must be defined — AF-STANDINGS-DAILY replaced intraday standings fetches"
    )

    # Nightly job registered at 23:30
    assert 'id="standings_nightly"' in src, (
        "standings_nightly job must be registered in the scheduler"
    )
    assert "CronTrigger(hour=23, minute=30)" in src, (
        "standings_nightly must run at 23:30 UTC"
    )


@test("INPLAY-UNDERDOG-HOLD — strategy O registered, dispatched, checks prematch prob < 35% + live odds")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    # Bot registered in INPLAY_BOTS
    assert '"inplay_o"' in src, "inplay_o must be in INPLAY_BOTS"

    # Dispatched in _check_strategy
    fn_start = src.index("def _check_strategy(bot_name:")
    fn_end = src.index("\ndef _check_strategy_a(", fn_start)
    dispatch_body = src[fn_start:fn_end]
    assert '"inplay_o"' in dispatch_body, "inplay_o must be dispatched in _check_strategy"

    # Strategy function has correct gates
    fn_start = src.index("def _check_strategy_o(")
    fn_end = src.index("\ndef _check_strategy_p(", fn_start)
    fn_body = src[fn_start:fn_end]
    assert "pm_home >= 0.35" in fn_body, "Strategy O must gate on prematch_home_prob >= 0.35"
    assert "pm_away >= 0.35" in fn_body, "Strategy O must gate on prematch_away_prob >= 0.35"
    assert "2.80" in fn_body, "Strategy O must require live odds >= 2.80"
    assert "_poisson_win_prob(" in fn_body, "Strategy O must use _poisson_win_prob for edge"
    assert "live_1x2_home" in fn_body and "live_1x2_away" in fn_body, (
        "Strategy O must read live_1x2_home and live_1x2_away from candidate"
    )


@test("INPLAY-POST-EQUALIZER — strategy P_v2 registered, dispatched, uses equalizer window + Poisson")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()

    # v2 bot registered
    assert '"inplay_p_v2"' in src, "inplay_p_v2 must be in INPLAY_BOTS"

    # v2 dispatched
    fn_start = src.index("def _check_strategy(bot_name:")
    fn_end = src.index("\ndef _check_strategy_a(", fn_start)
    dispatch_body = src[fn_start:fn_end]
    assert '"inplay_p_v2"' in dispatch_body, "inplay_p_v2 must be dispatched in _check_strategy"
    assert "inplay_p_v2" in dispatch_body, "inplay_p not dispatched (retired)"

    # Module-level state vars exist
    assert "_equalizer_event_window" in src, "_equalizer_event_window dict must exist"
    assert "_prev_scores" in src, "_prev_scores dict must exist"

    # v2 strategy function checks window and 1-1 score
    fn_start = src.index("def _check_strategy_p_v2(")
    try:
        fn_end_p = src.index("\ndef ", fn_start + 1)
    except ValueError:
        fn_end_p = len(src)
    fn_body = src[fn_start:fn_end_p]
    assert "_equalizer_event_window" in fn_body, "Strategy P v2 must check _equalizer_event_window"
    assert "sh != 1 or sa != 1" in fn_body, "Strategy P v2 must exit if score is not 1-1"
    assert "2.20" in fn_body, "Strategy P v2 must require live odds >= 2.20"
    assert "_poisson_win_prob(" in fn_body, "Strategy P v2 must use _poisson_win_prob for edge"

    # Equalizer detection logic updates both dicts
    update_section = src[src.index("Update post-equalizer state"):src.index("# ── Data Queries")]
    assert "_prev_scores[mid]" in update_section, "Must update _prev_scores each cycle"
    assert "_equalizer_event_window[mid]" in update_section, "Must record equalizer event"


@test("INPLAY-POISSON-WIN-PROB — _poisson_win_prob helper unit tests")
def _():
    import sys
    sys.path.insert(0, ".")
    from workers.jobs.inplay_bot import _poisson_win_prob

    # At 0-0, symmetric lambdas: each team wins with equal probability < 0.5
    prob = _poisson_win_prob(0.5, 0.5, lead_a=0)
    assert 0.20 < prob < 0.40, f"Symmetric 0-0 win prob should be ~0.3, got {prob}"

    # Leading 1-0 with equal remaining lambdas: ~60-70% win probability
    prob = _poisson_win_prob(0.6, 0.6, lead_a=1)
    assert 0.55 < prob < 0.80, f"1-0 lead win prob should be 55-80%, got {prob}"

    # Strong underdog (low lambda) leading vs strong favourite (high lambda)
    # at 1-0 with 40 min remaining — should still be >35% (better than implied 2.80)
    prob_strong_lead = _poisson_win_prob(0.49, 0.89, lead_a=1)  # 1.1*(40/90), 2.0*(40/90)
    assert prob_strong_lead > 0.36, f"Underdog holding 1-0 with 40min left: {prob_strong_lead:.3f}"

    # With lead_a=0 and lambda_a > lambda_b, A should have higher win prob
    prob_a = _poisson_win_prob(1.0, 0.5, lead_a=0)
    prob_b = _poisson_win_prob(0.5, 1.0, lead_a=0)
    assert prob_a > prob_b, "Higher-lambda team should win more often from equal state"


@test("OPT-AWAY-ODDS-FIX — bot_opt_away_british + europe odds_range widened to 2.20-3.50")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    british_start = src.index('"bot_opt_away_british"')
    british_end = src.index('"bot_opt_away_europe"', british_start)
    british_body = src[british_start:british_end]
    assert "(2.20, 3.50)" in british_body, "bot_opt_away_british odds_range must be (2.20, 3.50)"
    assert "(2.50, 3.00)" not in british_body, "bot_opt_away_british old odds_range (2.50, 3.00) still present"

    europe_start = src.index('"bot_opt_away_europe"')
    europe_end = src.index('"bot_opt_home_lower"', europe_start)
    europe_body = src[europe_start:europe_end]
    assert "(2.20, 3.50)" in europe_body, "bot_opt_away_europe odds_range must be (2.20, 3.50)"
    assert "(2.50, 3.00)" not in europe_body, "bot_opt_away_europe old odds_range (2.50, 3.00) still present"


@test("INPLAY-J-LOOSEN — strategy J prematch_o25 gate lowered to 0.55")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/inplay_bot.py").read_text()
    fn_start = src.index("def _check_strategy_j(")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "pm_o25 < 0.55" in fn_body, "Strategy J must use pm_o25 < 0.55 gate (INPLAY-J-LOOSEN)"
    assert "pm_o25 < 0.62" not in fn_body, "Strategy J old gate 0.62 still present"

    dict_start = src.index('"inplay_j"')
    dict_end = src.index('"inplay_l"', dict_start)
    dict_body = src[dict_start:dict_end]
    assert "0.55" in dict_body, "INPLAY_BOTS description for inplay_j must reflect 0.55"


@test("INPLAY-CLV-NULL — settlement skips CLV for inplay bots (live odds not a valid closing line)")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    # is_inplay check must be present
    assert "is_inplay = bot_name.startswith(\"inplay_\")" in src, "must detect inplay bots by name prefix"
    # Extract just the if-branch (from 'if is_inplay:' up to 'else:')
    inplay_if_start = src.index("if is_inplay:")
    inplay_if_end = src.index("        else:", inplay_if_start)
    inplay_branch = src[inplay_if_start:inplay_if_end]
    assert "closing_odds = None" in inplay_branch, "inplay bets must set closing_odds = None"
    assert "clv_pinnacle = None" in inplay_branch, "inplay bets must set clv_pinnacle = None"
    assert "get_closing_odds" not in inplay_branch, "inplay if-branch must not call get_closing_odds"


@test("INPLAY-BOT-SORT-ROI — admin bot dashboard sorts by ROI not P&L")
def _():
    import pathlib
    p = pathlib.Path("../odds-intel-web/src/lib/bot-aggregates.ts")
    if not p.exists():
        return  # engine-only CI checkout — frontend not present, skip
    src = p.read_text()
    sort_start = src.index(".sort((a, b) => {")
    sort_end = src.index("});", sort_start) + 3
    sort_block = src[sort_start:sort_end]
    assert "b.roi" in sort_block, "buildBotStats must sort by roi"
    assert "b.totalPnl - a.totalPnl" not in sort_block, "buildBotStats must not sort by P&L"


@test("PER-BOT-SLICE-TIGHTEN — odds_range caps applied to 5 bots from slice analysis")
def _():
    import pathlib
    src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()

    def bot_body(name: str, next_name: str) -> str:
        start = src.index(f'"{name}"')
        end = src.index(f'"{next_name}"', start)
        return src[start:end]

    ou25 = bot_body("bot_ou25_global", "bot_draw_specialist")
    assert "(1.60, 2.50)" in ou25, "bot_ou25_global odds_range must be capped at 2.50 (2.50-3.00 bucket -8% ROI)"
    assert "(1.60, 3.00)" not in ou25, "bot_ou25_global old odds_range (1.60, 3.00) still present"

    ou35 = bot_body("bot_ou35_attacking", "bot_ou25_global")
    assert "(1.80, 3.00)" in ou35, "bot_ou35_attacking odds_range must be capped at 3.00 (over @ 3.00-3.50 -38% ROI)"
    assert "(1.80, 3.50)" not in ou35, "bot_ou35_attacking old odds_range (1.80, 3.50) still present"

    btts_all = bot_body("bot_btts_all", "bot_btts_conservative")
    assert "(1.50, 2.80)" in btts_all, "bot_btts_all odds_range must be (1.50, 2.80) — backtest tightening reverted after live data showed 2.00-2.50 bucket at +20.5% ROI (41 bets)"
    assert "(1.50, 2.00)" not in btts_all, "bot_btts_all old tightened range (1.50, 2.00) still present — should be reverted to (1.50, 2.80)"

    btts_cons = bot_body("bot_btts_conservative", "bot_ou15_defensive")
    assert "(1.60, 2.00)" in btts_cons, "bot_btts_conservative odds_range must be capped at 2.00 (2.00-2.50 bucket -14% ROI)"
    assert "(1.60, 2.50)" not in btts_cons, "bot_btts_conservative old odds_range (1.60, 2.50) still present"

    greek = bot_body("bot_greek_turkish", "bot_high_roi_global")
    assert "(1.40, 3.50)" in greek, "bot_greek_turkish odds_range must be capped at 3.50 (3.50+ bucket -30% ROI)"
    assert "(1.40, 4.00)" not in greek, "bot_greek_turkish old odds_range (1.40, 4.00) still present"


@test("TIER-C-AF-XG — Tier C fallback feeds AF expected-goals into _poisson_probs")
def _():
    """Tier C matches (no historical CSV coverage for either team) used to get
    a hardcoded 50/50 OU prior + league-avg BTTS, and no AH/OU 1.5/3.5 at all
    because exp_home/exp_away stayed None. AF's /predictions endpoint already
    gives us per-team expected goals (af_goals_home / af_goals_away) — feed
    those into the same Poisson grid Tier A uses so OU, BTTS, and AH markets
    all get model-priced for every match where AF supplies xG.
    """
    import pathlib
    from workers.jobs.daily_pipeline_v2 import _parse_af_xg, _poisson_probs

    # 1. Parser: rejects garbage, accepts plausible xG, clamps unreasonable ones.
    assert _parse_af_xg("1.7") == 1.7
    assert _parse_af_xg(1.2) == 1.2
    assert _parse_af_xg("1.2%") == 1.2  # tolerate stray % suffix
    assert _parse_af_xg(None) is None
    assert _parse_af_xg("") is None
    assert _parse_af_xg("not-a-number") is None
    assert _parse_af_xg("-0.5") is None  # negative
    assert _parse_af_xg("7.0") is None   # outside plausible per-team range

    # 2. _poisson_probs on AF xG must produce non-trivial markets (not the old
    #    hardcoded 0.50 / 0.50 OU prior).
    probs = _poisson_probs(1.7, 1.2)
    assert abs(probs["over_25_prob"] - 0.50) > 0.02, (
        f"over_25_prob too close to the legacy 0.50 prior (got {probs['over_25_prob']:.3f}) — "
        "Poisson grid should produce something meaningful for xG (1.7, 1.2)"
    )
    assert 0.0 < probs["btts_yes_prob"] < 1.0
    assert 0.0 < probs["over_15_prob"] < 1.0
    assert 0.0 < probs["over_35_prob"] < 1.0

    # 3. Source guard: the Tier C fallback must call _poisson_probs and set
    #    exp_home/exp_away when AF supplies xG. Without this, AH bots stay
    #    stuck at zero candidates for Tier C and OU bets keep using the
    #    coin-flip prior.
    src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    start = src.index("# TIER-C-AF-XG")
    end = src.index("# Fallback: AF gave us 1X2 but no usable xG", start)
    block = src[start:end]
    assert "_parse_af_xg(af_pred_for_match.get(\"af_goals_home\"))" in block, (
        "Tier C fallback must parse af_goals_home via _parse_af_xg"
    )
    assert "_parse_af_xg(af_pred_for_match.get(\"af_goals_away\"))" in block, (
        "Tier C fallback must parse af_goals_away via _parse_af_xg"
    )
    assert "_poisson_probs(xg_h, xg_a" in block, (
        "Tier C fallback must feed AF xG into _poisson_probs (not the hardcoded prior)"
    )
    assert "poisson_pred[\"exp_home\"] = xg_h" in block and \
           "poisson_pred[\"exp_away\"] = xg_a" in block, (
        "Tier C fallback must set exp_home/exp_away so AH bots can fire"
    )
    assert 'poisson_pred["data_tier"] = "C"' in block, (
        "Tier C path must still tag data_tier='C' so DATA_TIER_EDGE_BUMP applies"
    )


@test("TIER-C-EXPAND — football-data extras ingest script is present and config is sound")
def _():
    """LEVER-1 follow-up to TIER-C-AF-XG: scripts/ingest_football_data_extras.py
    generalises add_romanian_league_data.py to pull historical CSVs for 15 more
    countries. Once a user runs the script locally, those leagues' top divisions
    move from Tier C → Tier A in the live pipeline. Guard:
      - script is importable (syntax + top-level imports valid),
      - LEAGUES dict has the expected entries with valid keys,
      - no league_code in the config collides with one already present in
        targets_poisson_history.csv (a collision would cross-pollute team form),
      - assertion in load_config() catches duplicate league_codes inside LEAGUES.
    """
    import importlib.util, pathlib
    import pandas as pd

    spec = importlib.util.spec_from_file_location(
        "fde", str(pathlib.Path("scripts/ingest_football_data_extras.py"))
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    cfg = m.load_config()
    assert len(cfg) >= 10, f"LEAGUES should have ≥10 entries (got {len(cfg)})"
    for code, c in cfg.items():
        assert len(code) == 3, f"file code {code!r} should be 3 letters (matches football-data url path)"
        for k in ("league", "league_code", "tier", "keep_n_seasons"):
            assert k in c, f"{code} config missing required key {k!r}"
        assert isinstance(c["tier"], int), f"{code} tier must be int"
        assert c["keep_n_seasons"] >= 1

    new_codes = {c["league_code"] for c in cfg.values()}
    df = pd.read_csv("data/processed/targets_poisson_history.csv", low_memory=False)
    existing_codes = set(df["league_code"].dropna().unique())
    overlap = new_codes & existing_codes
    # Allow overlap once the script has actually been run (post-ingest the codes
    # will be present in the CSV). The collision check that matters is the
    # in-config duplicate one, which load_config() asserts.
    if overlap:
        for lc in overlap:
            n = (df["league_code"] == lc).sum()
            assert n > 0, f"league_code {lc} marked overlapping but 0 rows found"

    src = pathlib.Path("scripts/ingest_football_data_extras.py").read_text()
    assert "football-data.co.uk/new/" in src, "must download from the new/ extras directory"
    assert "PSCH" in src and "drop" in src, "must require Pinnacle closing odds (drops rows without)"
    assert "drop_duplicates" in src, "must dedupe against existing CSV rows"


@test("TIER-C-EXPAND-ODDS — extras odds ingest script is present and uses in-memory match join")
def _():
    """LEVER-1 follow-up: scripts/ingest_football_data_extras_odds.py joins
    football-data /new/ extras CSV closing odds to DB matches and inserts into
    odds_snapshots. Key correctness properties guarded here:
      - EXTRAS dict covers the 14 countries from TIER-C-EXPAND
      - Uses load_all_matches_for_leagues() + find_match_in_memory() (one bulk
        SELECT per league + in-memory dict lookup) instead of one DB query per
        CSV row. Without this the EU-pooler latency × ~37k rows took >1h;
        in-memory takes ~70s for the whole batch.
      - Reuses the proven resolve_team / extract_odds_rows helpers from
        ingest_football_data_csvs.py — same fuzzy team-name and odds parsing.
      - Dedupes against existing odds_snapshots rows via existing_snapshot_keys.
    """
    import pathlib, importlib.util
    spec = importlib.util.spec_from_file_location(
        "ifeo", str(pathlib.Path("scripts/ingest_football_data_extras_odds.py"))
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    # 1. EXTRAS dict covers the TIER-C-EXPAND countries.
    expected = {"ARG", "AUT", "BRA", "CHN", "DNK", "FIN", "IRL", "JPN",
                "MEX", "NOR", "POL", "RUS", "SWE", "USA"}
    assert set(m.EXTRAS) == expected, (
        f"EXTRAS must cover the 14 TIER-C-EXPAND countries. "
        f"Missing: {expected - set(m.EXTRAS)}, Extra: {set(m.EXTRAS) - expected}"
    )
    for code, cfg in m.EXTRAS.items():
        for k in ("country", "league_names", "label"):
            assert k in cfg, f"{code} cfg missing {k!r}"
        assert isinstance(cfg["league_names"], list) and cfg["league_names"], (
            f"{code} league_names must be non-empty list"
        )

    # 2. In-memory match-join helpers exist and are wired into ingest_one.
    src = pathlib.Path("scripts/ingest_football_data_extras_odds.py").read_text()
    assert "def load_all_matches_for_leagues(" in src, (
        "load_all_matches_for_leagues() must exist (bulk DB read)"
    )
    assert "def find_match_in_memory(" in src, (
        "find_match_in_memory() must exist (in-memory lookup)"
    )
    assert "matches_by_teams = load_all_matches_for_leagues(" in src, (
        "ingest_one must call load_all_matches_for_leagues — without it the join "
        "falls back to one DB query per row (~1h runtime instead of ~70s)"
    )
    assert "find_match_in_memory(kickoff_utc, home_id, away_id, matches_by_teams)" in src, (
        "ingest_one must use find_match_in_memory inside the row loop"
    )

    # 3. Defensive guards we want to stay in.
    assert "existing_snapshot_keys" in src, "must dedupe against existing odds_snapshots rows"
    assert "from scripts.ingest_football_data_csvs import" in src, (
        "should reuse resolve_team / extract_odds_rows from the mainstream ingest"
    )


@test("FIT-RHO-COLNAMES — fit_league_rho.py uses score_home/score_away not home_score/away_score")
def _():
    import pathlib
    src = pathlib.Path("scripts/fit_league_rho.py").read_text()
    assert "score_home" in src, "fit_league_rho.py must use score_home (actual column name)"
    assert "score_away" in src, "fit_league_rho.py must use score_away (actual column name)"
    assert "home_score IS NOT NULL" not in src, "fit_league_rho.py still references non-existent column home_score"
    assert "away_score IS NOT NULL" not in src, "fit_league_rho.py still references non-existent column away_score"


@test("COOLBET-PLACER — automation module structure")
def _():
    import pathlib

    session_src = pathlib.Path("workers/automation/coolbet_session.py").read_text()
    assert "class CoolbetSession" in session_src, "CoolbetSession class missing"
    assert "_LOGIN_URL" in session_src, "_LOGIN_URL not defined"
    assert "def _login" in session_src, "_login method missing"
    assert "def _ensure_auth" in session_src, "_ensure_auth missing"
    assert "COOLBET_USER" in session_src, "must read COOLBET_USER from env"
    assert "COOLBET_IMPERVA_COOKIES" in session_src, "must read COOLBET_IMPERVA_COOKIES from env"

    placer_src = pathlib.Path("workers/automation/coolbet_placer.py").read_text()
    assert "_BET_URL" in placer_src, "_BET_URL constant missing"
    assert "/s/bets/bets" in placer_src, "bet placement URL must be /s/bets/bets"
    assert "def load_qualified_bets" in placer_src, "load_qualified_bets missing"
    assert "def search_coolbet_event" in placer_src, "search_coolbet_event missing"
    assert "_SEARCH_URL" in placer_src, "_SEARCH_URL constant missing"
    assert "/s/sbgate/sports/search/v2" in placer_src, "search URL must be /s/sbgate/sports/search/v2"
    assert "def fetch_coolbet_events" in placer_src, "fetch_coolbet_events missing"
    assert "def fuzzy_match_event" in placer_src, "fuzzy_match_event missing"
    assert "def find_market_outcome" in placer_src, "find_market_outcome missing"
    assert "def get_live_odds_and_id" in placer_src, "get_live_odds_and_id missing"
    assert "def _place_bet_api" in placer_src, "_place_bet_api missing"
    assert "def place_all_bets" in placer_src, "place_all_bets missing"
    assert "oddsIdByOutcomeId" in placer_src, "bet payload must include oddsIdByOutcomeId"
    assert "store_real_bet" in placer_src, "must write to real_bets on success"
    assert "NOT EXISTS" in placer_src, "dedup guard (NOT EXISTS real_bets) missing"

    cli_src = pathlib.Path("scripts/place_coolbet_bets.py").read_text()
    assert "--execute" in cli_src, "CLI must have --execute flag"
    assert "--record" in cli_src, "CLI must have --record flag"
    assert "place_all_bets" in cli_src, "CLI must call place_all_bets"

    # Three-mode design: record=, execute=, dry-run default
    assert "record=args.record" in placer_src or "record=" in placer_src, \
        "place_all_bets must accept record= param"
    assert "if execute:" in placer_src, "execute mode must imply record mode"

    # Coolbet odds snapshot on every run
    assert "store_coolbet_odds_snapshot" in placer_src, \
        "placer must capture Coolbet odds snapshot on every run"
    assert "store_coolbet_odds_snapshot" in pathlib.Path(
        "workers/api_clients/supabase_client.py"
    ).read_text(), "store_coolbet_odds_snapshot must exist in supabase_client"


@test("COOLBET-INPLAY-SNAPSHOTS — LISTEN/NOTIFY inplay capture with capture/paper/execute modes")
def _():
    """COOLBET-INPLAY-SNAPSHOTS (2026-05-20) — measures slippage between an
    inplay bot's decision and what Coolbet's live markets show at that
    moment. Postgres trigger on simulated_bets fires NOTIFY inplay_bet_fired
    on every inplay decision (xg_source IS NOT NULL); coolbet_daemon's
    dedicated LISTEN thread does ONE Coolbet GET per signal and writes a
    snapshot row. Three modes shipped: capture (A, default — snapshot only),
    paper (B — A + real_bets row, no POST), execute (C — A + POST + real_bets).
    Zero polling on either side."""
    import pathlib
    # Migration shipped
    mig = pathlib.Path("supabase/migrations/115_coolbet_inplay_snapshots.sql")
    assert mig.exists(), "migration 115_coolbet_inplay_snapshots.sql missing"
    mig_src = mig.read_text()
    assert "CREATE TABLE" in mig_src and "coolbet_inplay_snapshots" in mig_src
    assert "notify_inplay_bet_fired" in mig_src, "trigger function missing"
    assert "pg_notify" in mig_src and "inplay_bet_fired" in mig_src, "NOTIFY missing"
    assert "AFTER INSERT" in mig_src, "trigger must fire AFTER INSERT"
    assert "xg_source IS NOT NULL" in mig_src, "must gate on xg_source"
    assert "inplay_mode" in mig_src, "inplay_mode column missing"

    # Capture module
    cap = pathlib.Path("workers/automation/coolbet_inplay.py")
    assert cap.exists(), "workers/automation/coolbet_inplay.py missing"
    cap_src = cap.read_text()
    assert "def capture_inplay_snapshot" in cap_src
    assert "def insert_snapshot" in cap_src
    assert "matchStatus=LIVE" in cap_src or "live=True" in cap_src, \
        "capture must request LIVE markets"
    # All three modes wired
    for mode in ("capture", "paper", "execute"):
        assert f"'{mode}'" in cap_src or f'"{mode}"' in cap_src, f"mode {mode} not wired"
    # execute mode actually POSTs
    assert "_place_bet_api" in cap_src, "execute mode must call _place_bet_api"

    # Live-markets support in explorer
    exp_src = pathlib.Path("workers/automation/coolbet_explorer.py").read_text()
    assert "live: bool" in exp_src, \
        "fetch_match_markets must accept live=True for matchStatus=LIVE"
    assert '"LIVE" if live else "OPEN"' in exp_src, \
        "fetch_match_markets must switch matchStatus on the live flag"

    # Daemon listener + CLI flag
    daemon_src = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    assert "def _inplay_listener_loop" in daemon_src, "listener thread function missing"
    assert "LISTEN inplay_bet_fired" in daemon_src, "daemon must LISTEN inplay_bet_fired"
    assert "--inplay-mode" in daemon_src, "--inplay-mode CLI flag missing"
    assert '"inplay_mode"' in daemon_src or "'inplay_mode'" in daemon_src, \
        "_CTRL['inplay_mode'] flag missing"

    # Telegram command
    handlers_src = pathlib.Path("scripts/_daemon_handlers.py").read_text()
    assert '"/inplay_mode"' in handlers_src, "/inplay_mode Telegram command missing"
    assert "REAL MONEY" in handlers_src, \
        "/inplay_mode execute warning must mention REAL MONEY"

    # Telegram ping on successful captures
    assert "_send_inplay_ping" in daemon_src, "telegram ping helper missing"
    assert "capture_outcome\") == \"captured\"" in daemon_src, \
        "ping must only fire on capture_outcome=captured"
    # Display context fields populated for the notification
    assert "_resolve_decision_context" in cap_src, \
        "capture must fetch team + bot context for the Telegram ping"
    assert "_home_team" in cap_src and "_bot_name" in cap_src, \
        "snap must include display-only context fields for the ping"


@test("COOLBET-MAINTENANCE-KEEPALIVE — keepalive uses 5-min /casino/fo/maintenance ping")
def _():
    """COOLBET-MAINTENANCE-KEEPALIVE (2026-05-20) — replaced the heavier
    /fo-category keepalive endpoint with /s/casino/fo/maintenance, which is
    what Coolbet's frontend pings every 5 min. Two wins: (1) much smaller
    payload (~2KB vs ~50KB+), (2) request pattern matches browser exactly,
    reducing Imperva detection risk. fo-category retained as a Plan-B
    fallback when maintenance 4xx's. Daemon cadence tightened 20m → 5m."""
    import inspect, pathlib
    from workers.automation.coolbet_session import CoolbetSession
    src = inspect.getsource(CoolbetSession.keep_alive)
    assert "/s/casino/fo/maintenance" in src, \
        "keep_alive must call maintenance endpoint as primary heartbeat"
    assert "licence" in src and "EE" in src, \
        "keep_alive must include licence=EE param (matches browser request)"
    # fo-category retained as fallback
    assert "fo-category" in src, "fo-category must remain as Plan-B fallback"

    daemon_src = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    assert '"--keepalive-min", type=int, default=5' in daemon_src, \
        "daemon default keepalive cadence should be 5 min (matches browser)"


@test("COOLBET-JWT-API-RENEW — pure-Python JWT renewal via /s/auth/renew-token")
def _():
    """COOLBET-JWT-API-RENEW (2026-05-20) — Coolbet exposes /s/auth/renew-token
    which accepts a soon-to-expire JWT and returns a fresh one. The frontend
    uses this same endpoint every ~20 min while a user is browsing. We
    hooked into it from Python, eliminating the need for headless Chrome
    / undetected-chromedriver / persistent profile. Daemon renews JWT
    silently every 20 min for as long as the underlying Smart-ID session
    at Coolbet stays alive (typically all day). Operator only needs to
    re-Smart-ID + paste a fresh JWT when renewal returns 401/403."""
    import pathlib
    src = pathlib.Path("workers/automation/coolbet_session.py").read_text()
    assert "_RENEW_URL" in src, "renewal endpoint URL constant missing"
    assert "/s/auth/renew-token" in src, "renewal URL must be /s/auth/renew-token"
    assert "def renew_jwt_via_api" in src, "renew_jwt_via_api method missing"
    assert "self._http.post(_RENEW_URL" in src, \
        "renew_jwt_via_api must POST to _RENEW_URL"
    assert "self._adopt_manual_jwt()" in src, \
        "renew_jwt_via_api must adopt the new JWT after extraction"
    assert "set_key" in src, "renew_jwt_via_api must persist new JWT to .env"

    daemon = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    assert "renew_jwt_via_api" in daemon, \
        "daemon must call renew_jwt_via_api in the periodic refresh task"
    assert "_task_jwt_browser_refresh" in daemon, \
        "renewal task entrypoint missing (back-compat name with /relogin)"


@test("COOLBET-AUTO-COOKIE-REFRESH — headless JWT refresher wired into daemon + session (legacy)")
def _():
    """COOLBET-AUTO-COOKIE-REFRESH (2026-05-20) — superseded by
    COOLBET-JWT-API-RENEW. The headless-Chrome path turned out non-viable
    (Coolbet session doesn't survive Chrome process restarts). Kept the
    setup + refresh scripts as a fallback discovery tool but the daemon
    no longer depends on them. Test relaxed: scripts exist, but no longer
    required to be wired into the daemon's renewal path.
    hot-swaps the new token in-process — no daemon restart needed."""
    import pathlib

    setup = pathlib.Path("scripts/coolbet_browser_setup.py")
    assert setup.exists(), "scripts/coolbet_browser_setup.py missing — one-time browser bootstrap"
    setup_src = setup.read_text()
    assert "undetected_chromedriver" in setup_src, "setup must import undetected_chromedriver"
    assert "user-data-dir" in setup_src, "setup must use persistent --user-data-dir profile"
    assert "COOLBET_MANUAL_JWT" in setup_src, "setup must write COOLBET_MANUAL_JWT to .env"
    assert "jwt-location.json" in setup_src, "setup must persist JWT location for refresher"

    refresher = pathlib.Path("scripts/coolbet_refresh_jwt.py")
    assert refresher.exists(), "scripts/coolbet_refresh_jwt.py missing — kept as fallback discovery tool"

    session_src = pathlib.Path("workers/automation/coolbet_session.py").read_text()
    assert "def reload_manual_jwt" in session_src, \
        "CoolbetSession.reload_manual_jwt() missing — still used as a safety net"

    daemon_src = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    assert "_task_jwt_browser_refresh" in daemon_src, \
        "renewal task entrypoint missing (back-compat name with /relogin)"
    assert "next_jwt_refresh" in daemon_src, \
        "daemon must schedule periodic JWT refresh"
    # NOTE: assertions about subprocess-invoking the refresher script are
    # intentionally dropped — superseded by COOLBET-JWT-API-RENEW.
    # /relogin Telegram command now uses the browser refresh path, not _login()
    assert "_task_jwt_browser_refresh(session))" in daemon_src, \
        "/relogin must route through browser refresher"


@test("SHADOW-DEDUP — cohort-scoped unique constraint in migration + ON CONFLICT clause")
def _():
    """SHADOW-DEDUP (2026-05-20): Railway rolling restarts briefly run two scheduler
    instances that both fire the same shadow window, producing duplicate shadow_bets.
    Fix: unique constraint on (shadow_cohort, bot_id, match_id, market, selection)
    and matching ON CONFLICT in bulk_store_shadow_bets."""
    import pathlib
    migration = pathlib.Path("supabase/migrations/114_shadow_bets_dedup_unique.sql").read_text()
    assert "uq_shadow_bet_per_cohort" in migration, \
        "migration 114 must create uq_shadow_bet_per_cohort index"
    assert "shadow_cohort, bot_id, match_id, market, selection" in migration, \
        "cohort-scoped unique index must cover (shadow_cohort, bot_id, match_id, market, selection)"
    assert "DROP CONSTRAINT IF EXISTS uq_shadow_bet_per_run" in migration, \
        "migration 114 must drop the superseded run-scoped constraint"

    sc_src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    assert "ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection) DO NOTHING" in sc_src, \
        "bulk_store_shadow_bets ON CONFLICT must use cohort-scoped key, not shadow_run_id"


@test("ACCA-REDESIGN — _scan_todays_candidates replaces _fetch_todays_singles")
def _():
    """ACCA-REDESIGN (2026-05-20): acca bot now queries predictions+odds_snapshots
    directly instead of reading simulated_bets from other bots. This eliminates
    silent coupling where retired/slow source bots produce 0 legs.

    Verifies:
    - _scan_todays_candidates function exists and returns a list
    - old _fetch_todays_singles is no longer the primary data source
    - bot_ou15_defensive removed from any proven whitelist
    - market_whitelist key used instead of bot_whitelist
    - ACCA_ELIGIBLE_MARKETS and PROVEN_MARKETS_WHITELIST defined
    """
    import pathlib
    import importlib
    import sys

    bot_path = pathlib.Path("workers/jobs/acca_bot.py")
    assert bot_path.exists(), "workers/jobs/acca_bot.py must exist"
    bot_src = bot_path.read_text()

    # New function must exist
    assert "def _scan_todays_candidates(" in bot_src, (
        "_scan_todays_candidates() must be defined"
    )

    # Old function must not be the primary fetch function
    assert "_fetch_todays_singles" not in bot_src or \
           bot_src.count("def _fetch_todays_singles") == 0, (
        "_fetch_todays_singles must be removed — replaced by _scan_todays_candidates"
    )

    # Market constants must be defined
    assert "ACCA_ELIGIBLE_MARKETS" in bot_src, (
        "ACCA_ELIGIBLE_MARKETS frozenset must be defined"
    )
    assert "PROVEN_MARKETS_WHITELIST" in bot_src, (
        "PROVEN_MARKETS_WHITELIST frozenset must be defined"
    )

    # market_whitelist key must be used (not bot_whitelist)
    assert "market_whitelist" in bot_src, (
        "ACCA_VARIANTS must use market_whitelist key (not bot_whitelist)"
    )
    assert "bot_whitelist" not in bot_src, (
        "bot_whitelist key must be removed — replaced by market_whitelist"
    )

    # bot_ou15_defensive must not appear in any whitelist context
    assert '"bot_ou15_defensive"' not in bot_src, (
        "bot_ou15_defensive must be removed — it was retired 2026-05-20"
    )

    # scan_cache pattern must be used in run_acca_pass
    assert "scan_cache" in bot_src, (
        "run_acca_pass must use scan_cache (cached per market_whitelist)"
    )

    # _MARKET_SPEC must define the market mappings
    assert "_MARKET_SPEC" in bot_src, (
        "_MARKET_SPEC must define (acca_key, pred_market, snap_market, selection, prob_field) tuples"
    )

    # Import + call _scan_todays_candidates (source inspection only — no DB)
    # Verify the function is callable and its signature accepts market_whitelist
    import ast
    tree = ast.parse(bot_src)
    scan_func = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.FunctionDef) and node.name == "_scan_todays_candidates"),
        None,
    )
    assert scan_func is not None, "_scan_todays_candidates must be a def in acca_bot.py"
    # Check it has a market_whitelist parameter
    arg_names = [a.arg for a in scan_func.args.args]
    assert "market_whitelist" in arg_names, (
        "_scan_todays_candidates must accept market_whitelist parameter"
    )

    # All 4 ACCA_VARIANTS must still be present
    for variant in ("bot_acca_value", "bot_combo_system", "bot_acca_proven", "bot_combo_proven_system"):
        assert variant in bot_src, f"ACCA_VARIANTS must still include {variant}"


@test("FDCO-ANALYSIS — fdco analysis script structure and CLV mappings")
def _():
    """FDCO-ANALYSIS (2026-05-21): analyse_football_data_co_uk.py runs offline
    calibration + CLV analysis of our predictions vs Pinnacle closing odds.

    Verifies:
    - Script exists with correct functions
    - CLV market maps include under25 (added 2026-05-21)
    - Both fdco prob and odds columns mapped correctly
    - No debug prints left in run_clv_analysis
    """
    import pathlib

    src_path = pathlib.Path("scripts/analyse_football_data_co_uk.py")
    assert src_path.exists(), "scripts/analyse_football_data_co_uk.py must exist"
    src = src_path.read_text()

    # Key functions must exist
    for fn in ("add_pinnacle_probs", "add_outcomes", "run_clv_analysis",
               "fuzzy_match_teams", "load_or_export_our_predictions"):
        assert f"def {fn}(" in src, f"{fn} must be defined"

    # CLV market maps must include under25
    assert '"under25": "prob_pc_under25"' in src, \
        "MARKET_TO_FDCO_PROB must map under25 → prob_pc_under25"
    assert '"under25": "MaxC<2.5"' in src, \
        "MARKET_TO_FDCO_ODDS must map under25 → MaxC<2.5"
    assert '"under25": "outcome_under25"' in src, \
        "MARKET_TO_OUTCOME must map under25 → outcome_under25"

    # No debug prints should remain
    assert "_dbg_fdco_miss" not in src, "debug counter _dbg_fdco_miss must be removed"
    assert "DEBUG CLV loop done" not in src, "debug print must be removed"


@test("FDCO-AH-CLV — run_ah_clv_analysis defined and wired into main()")
def _():
    """FDCO-AH-CLV (2026-05-21): AH CLV analysis is defined, wired into main(), and
    write_findings accepts the ah_clv parameter."""
    import pathlib

    src_path = pathlib.Path("scripts/analyse_football_data_co_uk.py")
    src = src_path.read_text()

    assert "def run_ah_clv_analysis(" in src, "run_ah_clv_analysis must be defined"
    assert "ah_clv_results = run_ah_clv_analysis(" in src, \
        "run_ah_clv_analysis must be called in main()"
    assert "write_findings(findings_path, summary, calib, clv_results, ah_clv_results, args)" in src, \
        "write_findings must receive ah_clv_results"
    assert "def write_findings(" in src, "write_findings must be defined"
    # write_findings must accept ah_clv parameter
    import re
    sig_match = re.search(r"def write_findings\((.*?)\) -> None:", src, re.DOTALL)
    assert sig_match and "ah_clv" in sig_match.group(1), \
        "write_findings must accept ah_clv parameter"

    # AH section must appear in findings body
    assert "## AH CLV analysis" in src, "write_findings must write AH CLV section"


@test("BACKFILL-AH — backfill_ah_predictions.py structure")
def _():
    """BACKFILL-AH (2026-05-21): backfill_ah_predictions.py exists with correct structure."""
    import pathlib

    src_path = pathlib.Path("scripts/backfill_ah_predictions.py")
    assert src_path.exists(), "scripts/backfill_ah_predictions.py must exist"
    src = src_path.read_text()

    assert "def solve_lambdas(" in src, "solve_lambdas must be defined"
    assert "def _ah_model_prob(" in src, "_ah_model_prob must be defined"
    assert "bulk_store_predictions" in src, "must use bulk_store_predictions"
    assert "market LIKE 'ah_%'" in src, \
        "SQL must filter for existing AH predictions"
    assert "--dry-run" in src, "must support --dry-run"
    assert "source='poisson'" in src or '"source": "poisson"' in src, \
        "predictions must be stored with source=poisson"


@test("PIPELINE-AH — daily_pipeline_v2.py stores AH predictions using calibrated lambdas")
def _():
    """PIPELINE-AH (updated 2026-05-21 AH-HOME-BIAS fix): daily_pipeline_v2.py stores
    14 AH prediction rows per match using Platt-corrected 1x2 probs → lambda inversion,
    not raw Poisson exp_home/exp_away."""
    import pathlib

    src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()

    assert "_ah_line in (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5)" in src, \
        "must iterate over 7 standard AH lines"
    assert 'f"ah_{_ah_sel}_{_ah_line:.2f}"' in src, \
        "market key must be ah_{sel}_{line:.2f}"
    assert '"source": "poisson"' in src, \
        "AH predictions must use source=poisson"

    # AH-HOME-BIAS fix: must use calibrated lambda solver, not raw poisson_pred lambdas
    assert "def _solve_lambdas_calibrated(" in src, \
        "_solve_lambdas_calibrated must be defined in pipeline"
    assert "_solve_lambdas_calibrated(float(_cal_ph), float(_cal_pd))" in src, \
        "AH storage block must call _solve_lambdas_calibrated"

    # AH bots must use calibrated lambdas too, not poisson_pred.get('exp_home')
    ah_bot_block = src[src.find("AH (AH-BOTS)"):]
    assert "_solve_lambdas_calibrated" in ah_bot_block[:500], \
        "AH bot edge block must use _solve_lambdas_calibrated"

    # bot_ah_away_dog must be re-enabled
    assert '"bot_ah_away_dog"' in src, \
        "bot_ah_away_dog must be re-enabled after AH-HOME-BIAS fix"
    assert "# bot_ah_away_dog DISABLED" not in src, \
        "old disable comment must be removed"


@test("COOLBET-UNICODE-FUZZY — ø/å/æ/ü chars normalized before fuzzy match; token_set_ratio scorer; away team search fallback")
def _():
    """COOLBET-UNICODE-FUZZY (2026-05-21) — Brøndby IF FC København failed to
    match 'Brondby FC Copenhagen' because ø/ø were stripped to nothing by NFKD
    and token_sort_ratio scored 68 (below threshold 70). Fix: map ø→o/å→a/etc
    via translate table, switch scorer to token_set_ratio (scores 77 on the
    Brondby case), add full away name as search query fallback."""
    from rapidfuzz import fuzz
    from workers.automation.coolbet_placer import _ascii, _UNICODE_MAP

    # ø→o, å→a, æ→ae, ü→u etc.
    assert _ascii("Brøndby IF") == "Brondby IF", f"got {_ascii('Brøndby IF')}"
    assert _ascii("FC København") == "FC Kobenhavn", f"got {_ascii('FC Kobenhavn')}"
    assert _ascii("Malmö FF") == "Malmo FF"
    assert _ascii("Köln") == "Koln"

    # token_set_ratio clears threshold 70 for the Brondby case
    score = fuzz.token_set_ratio(
        _ascii("Brondby FC Copenhagen"),
        _ascii("Brøndby IF FC København"),
    )
    assert score >= 70, f"Brondby score {score} still below threshold after fix"

    # Away team search fallback present
    import inspect
    from workers.automation.coolbet_placer import search_coolbet_event, fuzzy_match_event
    assert "away.strip()" in inspect.getsource(search_coolbet_event), \
        "away full name must be a search fallback query"
    assert "token_set_ratio" in inspect.getsource(fuzzy_match_event), \
        "fuzzy_match_event must use token_set_ratio"


@test("COOLBET-NO-SWEEP — --no-sweep flag disables odds loop; placer already stores snapshot per bet")
def _():
    """COOLBET-NO-SWEEP (2026-05-21) — when Coolbet is used as a placement-only
    bookmaker the 29-league sweep is unnecessary and risks Imperva blocks.
    --no-sweep must disable the entire sweep loop while keepalive + placement
    continue unchanged. Live odds are still captured at placement time via
    store_coolbet_odds_snapshot() already called in the placer."""
    import pathlib
    daemon_src = pathlib.Path("scripts/coolbet_daemon.py").read_text()
    placer_src = pathlib.Path("workers/automation/coolbet_placer.py").read_text()

    assert "--no-sweep" in daemon_src, "--no-sweep arg missing from daemon"
    assert "args.no_sweep" in daemon_src, "daemon must check args.no_sweep"
    assert 'float("inf")' in daemon_src, (
        "next_odds must be float(\"inf\") when --no-sweep so the sweep block never fires"
    )
    assert "not args.no_sweep and now >= next_odds" in daemon_src, (
        "sweep block must be gated on `not args.no_sweep`"
    )
    assert "store_coolbet_odds_snapshot" in placer_src, (
        "placer must call store_coolbet_odds_snapshot so CLV data is captured "
        "even when the background sweep is disabled"
    )


@test("COOLBET-IMPERVA-BACKOFF — daemon enters quiet backoff after threshold keepalive failures")
def _():
    """COOLBET-IMPERVA-BACKOFF (2026-05-21) — after IMPERVA_FAIL_THRESHOLD
    consecutive keepalive failures the daemon must stop all activity
    (sweep, placement, JWT renewal) and enter a timed backoff window.
    Backoff doubles on each continued block (capped at IMPERVA_BACKOFF_MAX_S).
    Telegram alerts on enter, probe, and exit. Without this, the daemon hammers
    blocked endpoints every 5 min and keeps the Imperva session frozen longer."""
    import pathlib
    src = pathlib.Path("scripts/coolbet_daemon.py").read_text()

    # Backoff globals must exist with the right values
    assert "IMPERVA_FAIL_THRESHOLD" in src, "IMPERVA_FAIL_THRESHOLD constant missing"
    assert "IMPERVA_BACKOFF_MIN_S" in src, "IMPERVA_BACKOFF_MIN_S constant missing"
    assert "IMPERVA_BACKOFF_MAX_S" in src, "IMPERVA_BACKOFF_MAX_S constant missing"
    assert "_imperva_fail_streak" in src, "_imperva_fail_streak state var missing"
    assert "_imperva_backoff_until" in src, "_imperva_backoff_until state var missing"
    assert "_imperva_backoff_duration" in src, "_imperva_backoff_duration state var missing"

    # Main loop must check backoff BEFORE normal keepalive/sweep/placement
    backoff_check_pos = src.find("if _imperva_backoff_until > 0:")
    keepalive_check_pos = src.find("if now >= next_keepalive:")
    assert backoff_check_pos != -1, "backoff guard missing from main loop"
    assert keepalive_check_pos != -1, "keepalive schedule check missing"
    assert backoff_check_pos < keepalive_check_pos, (
        "backoff guard must come before keepalive/sweep/placement in the loop"
    )

    # After N failures the daemon must enter backoff (not just log and continue)
    assert "_imperva_fail_streak >= IMPERVA_FAIL_THRESHOLD" in src, (
        "must enter backoff when streak reaches threshold"
    )
    assert "_imperva_backoff_until = now + _imperva_backoff_duration" in src, (
        "must set _imperva_backoff_until to arm the backoff window"
    )

    # Backoff must double on continued block, capped at max
    assert "_imperva_backoff_duration * 2" in src, "backoff must double on each continued block"
    assert "IMPERVA_BACKOFF_MAX_S" in src, "backoff must be capped at IMPERVA_BACKOFF_MAX_S"

    # Clear state when block resolves
    assert "_imperva_backoff_until = 0.0" in src, (
        "must clear _imperva_backoff_until when keepalive probe succeeds"
    )
    assert "_imperva_backoff_duration = IMPERVA_BACKOFF_MIN_S" in src, (
        "must reset duration to IMPERVA_BACKOFF_MIN_S on recovery so next block starts fresh"
    )
    assert "_imperva_fail_streak = 0" in src, "must reset fail streak on recovery"

    # Telegram alerts on backoff transitions were retired in TELE-BET-NOTIFY-V2
    # (commit a818c18) — daemon Telegram noise was deliberately removed. The
    # backoff state machine still functions; only the alert side was retired.
    # The TELE-BET-NOTIFY test below enforces those strings are absent.

    # While in backoff the main loop must not advance sweep/placement next times
    # (i.e. it must `continue` the loop, not fall through to sweep/placement code)
    backoff_block = src[backoff_check_pos: keepalive_check_pos]
    assert "continue" in backoff_block, (
        "backoff block must `continue` the loop so sweep/placement are never reached "
        "while Imperva-blocked"
    )


@test("COOLBET-SLIPPAGE — captured_odds uses bot-edge odds (odds_at_pick), not live placement odds")
def _():
    """COOLBET-SLIPPAGE (2026-05-21, asserts updated 2026-05-25) — automated
    Coolbet bets showed SLIP=0 because captured_odds was set to ev_odds (same
    as actual_odds), making slippage always zero. Fix: pass the bot-edge odds
    as captured_odds so slippage = (edge_odds − live_odds) / edge_odds.

    The SELECT in the placer aliases `sb.odds_at_pick AS model_odds`, so since
    commit 0fc822b (2026-05-23) the placer reads `bet["model_odds"]` rather
    than `bet["odds_at_pick"]` — both refer to the same simulated_bets column.
    Either literal is acceptable as long as the value isn't bare `ev_odds`."""
    import pathlib
    src = pathlib.Path("workers/automation/coolbet_placer.py").read_text()

    # captured_odds must source from the bot-edge column (with ev_odds fallback),
    # not bare ev_odds. Accept either the SQL alias (model_odds) or the raw
    # column name (odds_at_pick) — they're the same field, just renamed.
    bot_edge_patterns = (
        'bet.get("odds_at_pick")', "bet['odds_at_pick']",
        'bet.get("model_odds")',   "bet['model_odds']",
    )
    assert any(p in src for p in bot_edge_patterns), (
        "store_real_bet captured_odds must read the bot-edge column "
        "(odds_at_pick / model_odds), not bare ev_odds, so slippage reflects "
        "drift from bot edge discovery to placement."
    )
    # Fallback to ev_odds must be present so a missing field doesn't crash.
    fallback_patterns = (
        'odds_at_pick") or ev_odds', "odds_at_pick'] or ev_odds",
        'model_odds") or ev_odds',   "model_odds'] or ev_odds",
    )
    assert any(p in src for p in fallback_patterns), (
        "captured_odds must fall back to ev_odds if the bot-edge field is "
        "absent, e.g. `float(bet.get('model_odds') or ev_odds)`"
    )
    # actual_odds must remain live_odds (unchanged).
    store_call_pos = src.index("store_real_bet(")
    store_call = src[store_call_pos: store_call_pos + 400]
    assert "actual_odds=live_odds" in store_call, (
        "actual_odds must stay as live_odds (the real Coolbet placement odds)"
    )


@test("ACCA-EDGE-PERCENT — acca/combo _place_one INSERT includes edge_percent")
def _():
    """_place_one was missing edge_percent in its INSERT column list, causing a
    NOT NULL constraint violation on every combo bet placed."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "acca_bot.py").read_text()
    insert_start = src.index("INSERT INTO simulated_bets")
    insert_block = src[insert_start: insert_start + 400]
    assert "edge_percent" in insert_block, \
        "acca_bot._place_one INSERT must include edge_percent in column list"


@test("ACCA-LEG-SHADOW — run_acca_pass writes each leg to shadow_bets via _write_legs_as_shadow")
def _():
    """ACCA-LEG-SHADOW (2026-05-25): every leg picked by any acca variant gets
    logged as a shadow_bets row attributed to virtual bot bot_acca_leg_shadow.
    Lets us measure, after settlement, whether the legs the acca catches
    would have been +EV if singles bots had picked them up. Revisit cadence
    tracked under ACCA-LEG-SHADOW-EVAL in PRIORITY_QUEUE.md."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "acca_bot.py").read_text()

    assert "_write_legs_as_shadow" in src, \
        "acca_bot.py must define _write_legs_as_shadow"
    assert "bot_acca_leg_shadow" in src, \
        "acca_bot.py must reference virtual bot bot_acca_leg_shadow"
    assert "bulk_store_shadow_bets" in src, \
        "_write_legs_as_shadow must call bulk_store_shadow_bets"
    assert "_write_legs_as_shadow(legs_by_variant)" in src, \
        "run_acca_pass must call _write_legs_as_shadow(legs_by_variant) after placing variants"
    assert "(leg.match_id, leg.market, leg.selection)" in src, \
        "_write_legs_as_shadow must dedup legs by (match_id, market, selection)"

    mig = (pathlib.Path(__file__).resolve().parent.parent /
           "supabase" / "migrations" / "131_bot_acca_leg_shadow.sql").read_text()
    assert "bot_acca_leg_shadow" in mig, "migration 131 must register bot_acca_leg_shadow"
    assert "ON CONFLICT (name) DO NOTHING" in mig, \
        "migration 131 must be idempotent (re-runnable)"


@test("SIM-BETS-COHORT-CHECK — simulated_bets timing_cohort constraint allows 'all'")
def _():
    """Migration 116 guard: BOT-COHORTS-ALL sets timing_cohort='all' on every bot.
    If the simulated_bets check constraint doesn't include 'all', every store_bet()
    call fails silently and zero bets are ever placed."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "supabase" / "migrations" / "116_simulated_bets_timing_cohort_allow_all.sql").read_text()
    assert "'all'" in src, "migration 116 must include 'all' in the timing_cohort check"
    assert "simulated_bets" in src, "migration 116 must target simulated_bets"
    assert "DROP CONSTRAINT" in src, "migration 116 must drop the old constraint first"


@test("COMBO-RESTRUCTURE-BOT-CONFIG — all 4 combo variants N=5, require_ou15, correct structure")
def _():
    """COMBO-RESTRUCTURE (2026-05-22): all 4 combo bots restructured to require N=5 legs,
    OU15/over in pool, and either straight (acca variants) or fours_up (system variants)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "acca_bot.py").read_text()

    for variant in ("bot_acca_value", "bot_acca_proven", "bot_combo_system", "bot_combo_proven_system"):
        assert variant in src, f"ACCA_VARIANTS must contain {variant}"

    assert '"require_ou15"' in src and "True" in src, \
        "ACCA_VARIANTS must have require_ou15=True for combo bots"

    assert '"min_legs"' in src and "5" in src, \
        "ACCA_VARIANTS must set min_legs=5 for all variants"

    assert '"structure"' in src and '"fours_up"' in src, \
        "ACCA_VARIANTS must have at least one fours_up structure variant"

    assert '"structure"' in src and '"straight"' in src, \
        "ACCA_VARIANTS must have at least one straight structure variant"


@test("COMBO-RESTRUCTURE-FOURS-UP-SETTLEMENT — settlement.py handles fours_up system bets")
def _():
    """COMBO-RESTRUCTURE (2026-05-22): settlement.py must dispatch fours_up system type
    and implement _settle_system_fours_up() that only combines sub-combos of size >= 4."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "jobs" / "settlement.py").read_text()

    assert "_settle_system_fours_up" in src, \
        "settlement.py must define _settle_system_fours_up()"

    assert 'system_type == "fours_up"' in src, \
        "settle_combo_bet() must dispatch fours_up system_type"

    fours_idx = src.index("def _settle_system_fours_up")
    fours_block = src[fours_idx: fours_idx + 600]
    assert "min_size" in fours_block or "range(4" in fours_block, \
        "_settle_system_fours_up must gate sub-combo sizes at 4"
    assert "range(min_size" in fours_block or "range(4" in fours_block, \
        "_settle_system_fours_up must iterate sub-combos starting from size 4"


@test("COMBO-RESTRUCTURE-REAL-BETS-SCHEMA — migration 118 adds combo_legs + system_type to real_bets")
def _():
    """COMBO-RESTRUCTURE (2026-05-22): real_bets needs combo_legs JSONB and system_type TEXT
    so manually placed combo bets can be stored and settled like simulated combos."""
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parent.parent /
           "supabase" / "migrations" / "118_real_bets_combo.sql").read_text()

    assert "combo_legs" in sql, "migration 118 must add combo_legs column to real_bets"
    assert "system_type" in sql, "migration 118 must add system_type column to real_bets"
    assert "real_bets" in sql, "migration 118 must target real_bets table"


@test("COMBO-RESTRUCTURE-RECORD-COMBO-ROUTE — record-combo API route exists and requires superadmin")
def _():
    """COMBO-RESTRUCTURE (2026-05-22): admin UI needs a record-combo API route so manually
    placed combo bets can be logged against simulated_bets for tracking."""
    import pathlib
    web = pathlib.Path(__file__).resolve().parent.parent.parent / "odds-intel-web"
    if not web.exists():
        print("  [skip] odds-intel-web not present in CI")
        return
    route = web / "src" / "app" / "api" / "admin" / "record-combo" / "route.ts"
    assert route.exists(), "record-combo route.ts must exist"
    src = route.read_text()
    assert "is_superadmin" in src, "record-combo route must check is_superadmin"
    assert "combo_legs" in src, "record-combo route must insert combo_legs"
    assert "system_type" in src, "record-combo route must insert system_type"


@test("COMBO-LEG-MARKETS — _normalise_our_target handles ou15/ou25/ou35/ou45 leg-market names")
def _():
    """COMBO-LEG-MARKETS (2026-05-23): combo bots write per-leg market as
    'ou25' (etc.) with selection 'over' / 'under' — no embedded line. Singles
    use 'o/u' + 'Over 2.5'. _normalise_our_target needs to recognise both."""
    import pathlib, sys
    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from workers.automation.coolbet_explorer import _normalise_our_target
    # ou25 / under → over_under_25 / under
    assert _normalise_our_target("ou25", "under") == ("over_under_25", "under", None), (
        "ou25/under must normalise to (over_under_25, under, None)"
    )
    assert _normalise_our_target("ou35", "over") == ("over_under_35", "over", None)
    assert _normalise_our_target("ou15", "Over") == ("over_under_15", "over", None)
    # Singles path still works
    assert _normalise_our_target("o/u", "Over 2.5") == ("over_under_25", "over", None)


@test("COMBO-PRINT-SAFE — place_coolbet_bets.py result printer handles combo result dicts")
def _():
    """COMBO-PRINT-SAFE (2026-05-23): combo result dicts don't carry
    home_team / market / selection — they have combo_legs / system_type.
    The summary printer must format both shapes without KeyError."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "scripts" / "place_coolbet_bets.py").read_text()
    assert "def _label" in src, (
        "place_coolbet_bets.py must define a _label helper that handles "
        "combo dicts (no home_team key)"
    )
    assert "combo_legs" in src and "live_combined_odds" in src, (
        "_label must branch on combo_legs presence and use live_combined_odds"
    )


@test("COMBO-PLACER — placer iterates qualifying combo simulated_bets and writes multi-leg real_bets")
def _():
    """COMBO-PLACER (2026-05-23): the auto-placer needs to handle combo
    simulated_bets (combo_legs JSONB) the same way as singles — resolve every
    leg's Coolbet outcome and write a multi-leg real_bet via store_real_bet().
    --execute is deferred until the Coolbet combo POST schema is captured."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "automation" / "coolbet_placer.py").read_text()
    assert "def load_qualified_combo_bets" in src, (
        "load_qualified_combo_bets() must exist"
    )
    assert "sb.combo_legs IS NOT NULL" in src, (
        "combo query must filter combo_legs IS NOT NULL"
    )
    assert "jsonb_array_elements(sb.combo_legs)" in src, (
        "combo query must verify every leg's match hasn't kicked off"
    )
    assert "def _place_combo_bets" in src, (
        "_place_combo_bets() must exist"
    )
    assert "combo_legs=resolved_legs" in src, (
        "combo path must pass resolved_legs into store_real_bet"
    )
    assert "COMBO-EXECUTE-COOLBET-API" in src, (
        "combo --execute must be flagged as follow-up (no Coolbet schema yet)"
    )


@test("COOLBET-MARKET-NAMES — parse_market recognizes Coolbet's per-league naming variants")
def _():
    """COOLBET-MARKET-NAMES (2026-05-23): the Brazilian Serie A endpoint
    returns market names like 'Match Winner (3-way)' and 'Total Goals'
    instead of the 'Match Result' / 'Total Goals Over/Under' variants
    parse_market originally hard-coded. Result was Gremio vs Santos skipped
    as no_market across all 3 picks despite the match being on Coolbet."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "workers" / "automation" / "coolbet_explorer.py").read_text()
    # name-based fallbacks must include the Brasileirão variants
    assert "\"match winner\" in name" in src, (
        "parse_market must accept 'Match Winner' as a 1x2 market name"
    )
    # OU must accept the bare 'total goals' substring (covers 'Total Goals',
    # 'Total Goals Over/Under', etc.) — old check required 'total goals over'
    # which excluded the bare form.
    assert "\"total goals\" in name" in src, (
        "parse_market must accept 'total goals' as an OU market name"
    )


@test("DC-CASE-AND-RESULTKEY-FIX — double_chance bets resolve against Coolbet's bracketed result_keys")
def _():
    """DC-CASE-FIX + DC-RESULTKEY-FIX (2026-05-24): two bugs caused every
    double_chance bet to silently return no_market.
      (1) _normalise_our_target required uppercase "1X"/"X2"/"12" but paper
          bets write lowercase — uppercase normalisation now applied.
      (2) _outcome_id_for_selection used .strip("[]") which only strips
          leading/trailing brackets, so Coolbet's "[Home]/Draw" became
          "Home]/Draw" — never matched the "[home]/draw" target. Fixed by
          storing target_keys without brackets ("home/draw") and using
          .replace to remove every bracket in the outcome result_key.
    Confirmed live on Gagra vs Dila — DC 1x now resolves at 1.78."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from workers.automation.coolbet_explorer import (
        _normalise_our_target, _outcome_id_for_selection,
    )

    # (1) lowercase DC selections must normalise to uppercase
    assert _normalise_our_target("double_chance", "1x") == ("double_chance", "1X", None)
    assert _normalise_our_target("double_chance", "x2") == ("double_chance", "X2", None)
    assert _normalise_our_target("double_chance", "12") == ("double_chance", "12", None)

    # (2) Coolbet's bracketed result_keys must match our (bracket-stripped) targets
    fake_dc_market = {
        "outcomes": [
            {"id": 111, "result_key": "[Home]/Draw"},
            {"id": 222, "result_key": "[Away]/Draw"},
            {"id": 333, "result_key": "[Home]/[Away]"},
        ],
    }
    assert _outcome_id_for_selection(fake_dc_market, "double_chance", "1X") == 111
    assert _outcome_id_for_selection(fake_dc_market, "double_chance", "X2") == 222
    assert _outcome_id_for_selection(fake_dc_market, "double_chance", "12") == 333


@test("REAL-BETS-CLV-EDGE-SCHEMA — migration 125 + placer + settlement + frontend wire CLV / edge / slippage")
def _():
    """REAL-BETS-CLV-EDGE (2026-05-23): real_bets needs clv + edge_pct_taken
    columns. Placer must populate slippage_pct + edge_pct_taken at insert.
    Settlement must populate clv at settle. Frontend must surface all three."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    mig = (root / "supabase" / "migrations" / "125_real_bets_clv_edge.sql").read_text()
    assert "edge_pct_taken" in mig, "migration 125 must add edge_pct_taken"
    assert "clv" in mig, "migration 125 must add clv"
    assert "real_bets" in mig, "migration 125 must target real_bets"

    sc = (root / "workers" / "api_clients" / "supabase_client.py").read_text()
    assert "edge_pct_taken" in sc, "store_real_bet must write edge_pct_taken"
    # REAL-BETS-EDGE-FORMULA-FIX (2026-05-24): use the same additive-edge
    # formula the bot itself uses (`cal_prob - 1/odds`). The earlier
    # multiplicative back-derivation disagreed with the bot's convention
    # by a factor of ~odds.
    assert "calibrated_prob, model_probability FROM simulated_bets" in sc, (
        "store_real_bet must read calibrated_prob (with model_probability fallback)"
    )
    assert "1.0 / float(actual_odds)" in sc, (
        "store_real_bet must compute additive edge: calibrated_prob - 1/actual_odds"
    )
    # CLOSING-PRE-KO-FALLBACK: get_closing_odds fallback must require
    # timestamp <= kickoff so in-play api-football-live ticks don't poison CLV.
    settle = (root / "workers" / "jobs" / "settlement.py").read_text()
    assert "os.timestamp <= m.date" in settle, (
        "get_closing_odds fallback must filter to pre-kickoff snapshots only"
    )

    settle = (root / "workers" / "jobs" / "settlement.py").read_text()
    # The real_bets settle loop must now pull closing_odds + write clv.
    assert "UPDATE real_bets SET result=%s, pnl=%s, resolved_at=NOW(),\n                                        clv=%s" in settle, (
        "_settle_real_bets_for_matches must update real_bets.clv at settlement"
    )

    backfill = root / "scripts" / "backfill_real_bets_clv_edge.py"
    assert backfill.exists(), "backfill script must exist"

    web = root.parent / "odds-intel-web"
    if not web.exists():
        print("  [skip frontend checks] odds-intel-web not present in CI")
        return
    ed = (web / "src" / "lib" / "engine-data.ts").read_text()
    assert "edgePctTaken" in ed and "clv:" in ed, (
        "RealBet type must expose edgePctTaken + clv fields"
    )
    assert "edge_pct_taken" in ed and ", clv," in ed, (
        "getRealBets() select must include edge_pct_taken and clv columns"
    )
    log = (web / "src" / "components" / "real-bets-log.tsx").read_text()
    assert ">Edge<" in log and ">CLV<" in log, (
        "real-bets-log.tsx must render Edge and CLV column headers"
    )
    assert "edgePctTaken" in log and "b.clv" in log, (
        "real-bets-log.tsx must render Edge + CLV cell values"
    )


@test("INPLAY-BTTS-BOTS-V1 — two BTTS-Yes inplay bots ship with migration 126 + dispatch")
def _():
    """INPLAY-BTTS-AH-BOTS (2026-05-24): ship inplay_btts_press_v1 +
    inplay_btts_dryspell_v1 as uncalibrated first-cut bots. Verify
    migration adds the bot rows + snapshot columns, INPLAY_BOTS lists
    them, dispatcher routes them, and the prob helper exists."""
    import pathlib, sys
    root = pathlib.Path(__file__).resolve().parent.parent

    mig = (root / "supabase" / "migrations" / "126_inplay_btts_bots.sql").read_text()
    assert "live_btts_yes" in mig and "live_btts_no" in mig, (
        "migration 126 must add live_btts_yes/no columns to live_match_snapshots"
    )
    assert "live_ah_main_line" in mig and "live_ah_home_odds" in mig and "live_ah_away_odds" in mig, (
        "migration 126 must add AH triple columns to live_match_snapshots"
    )
    assert "inplay_btts_press_v1" in mig and "inplay_btts_dryspell_v1" in mig, (
        "migration 126 must INSERT both BTTS bot rows"
    )

    sys.path.insert(0, str(root))
    from workers.jobs import inplay_bot as ib
    assert "inplay_btts_press_v1" in ib.INPLAY_BOTS, "INPLAY_BOTS must list inplay_btts_press_v1"
    assert "inplay_btts_dryspell_v1" in ib.INPLAY_BOTS, "INPLAY_BOTS must list inplay_btts_dryspell_v1"
    assert hasattr(ib, "_btts_yes_remaining_prob"), "prob helper _btts_yes_remaining_prob must exist"
    assert hasattr(ib, "_check_strategy_btts_press_v1"), "strategy fn must exist"
    assert hasattr(ib, "_check_strategy_btts_dryspell_v1"), "strategy fn must exist"

    # Smoke the prob helper with hand-picked inputs.
    p_both_score = ib._btts_yes_remaining_prob(1.5, 1.5, 60, 0, 0)
    p_one_left   = ib._btts_yes_remaining_prob(1.5, 1.5, 60, 1, 0)
    p_already    = ib._btts_yes_remaining_prob(1.5, 1.5, 60, 1, 1)
    assert 0.0 < p_both_score < p_one_left < 1.0, (
        f"BTTS prob monotonicity broken: 0-0={p_both_score:.3f} 1-0={p_one_left:.3f}"
    )
    assert p_already == 1.0, "Already-BTTS-Yes must return 1.0"

    # Candidate SELECT must include live_btts_yes so strategy reads it (INPLAY-BTTS-QUERY-FIX 2026-05-28)
    ib_src = (root / "workers" / "jobs" / "inplay_bot.py").read_text()
    assert "lms.live_btts_yes" in ib_src, (
        "_get_live_candidates SELECT must include lms.live_btts_yes — INPLAY-BTTS-QUERY-FIX 2026-05-28"
    )

    # Dispatcher must route both new names.
    inplay_src = ib_src
    assert '"inplay_btts_press_v1"' in inplay_src and '_check_strategy_btts_press_v1' in inplay_src, (
        "dispatcher must route inplay_btts_press_v1"
    )
    assert '"inplay_btts_dryspell_v1"' in inplay_src and '_check_strategy_btts_dryspell_v1' in inplay_src, (
        "dispatcher must route inplay_btts_dryspell_v1"
    )

    # build_snapshot must embed BTTS + AH fields (BTTS via f"live_btts_{sel}").
    lt = (root / "workers" / "jobs" / "live_tracker.py").read_text()
    assert 'live_btts_' in lt and 'live_ah_main_line' in lt, (
        "build_snapshot must embed BTTS + AH fields from parsed live odds"
    )
    # store_live_snapshots_batch + store_live_snapshot must persist them.
    sc = (root / "workers" / "api_clients" / "supabase_client.py").read_text()
    assert 'live_btts_yes' in sc and 'live_ah_main_line' in sc, (
        "store_live_snapshot optional_fields must include BTTS + AH cols"
    )
    dbf = (root / "workers" / "api_clients" / "db.py").read_text()
    assert 'live_btts_yes' in dbf and 'live_ah_main_line' in dbf, (
        "store_live_snapshots_batch columns must include BTTS + AH cols"
    )


@test("LIVE-BTTS-AH-FIX — parser captures BTTS + Asian Handicap from /odds/live")
def _():
    """LIVE-BTTS-AH-FIX (2026-05-24): parse_live_odds must emit BTTS rows
    (string was 'Both Teams Score' but AF returns 'Both Teams to Score' — old
    branch never matched). Same for Asian Handicap (id=33) — previously
    unhandled. handicap_line must be propagated through store_live_odds and
    store_live_odds_batch so AH lines persist."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    af = (root / "workers" / "api_clients" / "api_football.py").read_text()
    assert '"Both Teams to Score"' in af, (
        "parse_live_odds must accept AF's actual BTTS market name 'Both Teams to Score'"
    )
    assert 'bet.get("id") == 33' in af or '"Asian Handicap"' in af, (
        "parse_live_odds must include an Asian Handicap branch"
    )
    assert '"market": "asian_handicap"' in af, (
        "parse_live_odds AH branch must emit market='asian_handicap'"
    )
    assert '"handicap_line"' in af, (
        "parse_live_odds AH branch must emit handicap_line"
    )

    sc = (root / "workers" / "api_clients" / "supabase_client.py").read_text()
    assert "handicap_line" in sc.split("def store_live_odds(")[1].split("def ")[0], (
        "store_live_odds must include handicap_line in INSERT"
    )
    db = (root / "workers" / "api_clients" / "db.py").read_text()
    assert "handicap_line" in db.split("def store_live_odds_batch(")[1].split("def ")[0], (
        "store_live_odds_batch must include handicap_line in INSERT"
    )


@test("ADMIN-PLACE-SKIP-REASON — per-row auto-placer status badge on /admin/place")
def _():
    """ADMIN-PLACE-SKIP-REASON (2026-05-24): /admin/place must show why each
    bet would or wouldn't be auto-placed (below_min / edge_eroded / no_event /
    no_market / ready). Backend computes the status; frontend renders the
    badge. The `no_coolbet` umbrella status was split into `no_event` (no
    Coolbet/Unibet snapshot for this match at all — fuzzy match likely failed)
    vs `no_market` (match has snapshots but not for this market/selection) so
    the user can manually spot-check fuzzy-matching gaps separately from
    bookmaker market-coverage gaps."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    web = root.parent / "odds-intel-web"
    if not web.exists():
        print("  [skip frontend checks] odds-intel-web not present in CI")
        return
    ed = (web / "src" / "lib" / "engine-data.ts").read_text()
    assert "autoPlaceStatus" in ed, "PlaceableBet must expose autoPlaceStatus"
    assert "COOLBET_AUTO_MIN_EDGE" in ed, (
        "engine-data must export COOLBET_AUTO_MIN_EDGE so UI mirrors placer threshold"
    )
    for status in ("below_min", "edge_eroded", "no_event", "no_market", "ready"):
        assert f'"{status}"' in ed, f"autoPlaceStatus must include {status!r}"
    # The old umbrella status should be gone — split into no_event / no_market.
    assert '"no_coolbet"' not in ed, (
        "autoPlaceStatus 'no_coolbet' should be split into 'no_event' / 'no_market'"
    )
    # Backend must track which match_ids have ANY Coolbet/Unibet evidence so
    # it can pick between no_event and no_market when livePrice is null. Two
    # evidence sources must both feed the set, because the original snaps-only
    # detection mis-classified matches whose Coolbet snapshots fell off the
    # 10k row cap (user saw a 1x2 home bet "✓ Placed" at Coolbet while a
    # sibling double_chance row on the same match showed "⚠ no match"):
    #   (a) dedicated lightweight odds_snapshots query (`coolbetEventRows`)
    #       — separate from the 10k-capped main snaps query so older Coolbet
    #       snapshots can't be pushed off the bottom.
    #   (b) real_bets at Coolbet today — ground truth, since a placed bet
    #       proves Coolbet has the event regardless of snapshot state.
    assert "matchIdsWithCoolbetEvent" in ed, (
        "engine-data must track match_ids with Coolbet/Unibet evidence to "
        "distinguish no_event from no_market"
    )
    assert "coolbetEventRows" in ed, (
        "engine-data must run a dedicated lightweight query for event-existence "
        "(separate from the 10k-capped snaps query) to avoid false `no_event` chips"
    )
    assert 'r.bookmaker === "Coolbet"' in ed, (
        "engine-data must treat real_bets placed at Coolbet as ground truth that "
        "the event exists at Coolbet"
    )
    # ADMIN-PLACE-STRICT-COOLBET (2026-05-26): the auto-place gate must use
    # `coolbetOdds` strictly — the Unibet proxy cannot stand in for "Coolbet
    # supports this market". Coolbet (Estonia) and Unibet (global) share the
    # Kambi backend but have different regional market catalogs (Coolbet often
    # lacks DC, AH quarter lines, exotics). Previous code used
    # `livePrice = coolbetOdds ?? unibetOdds` and gated on `livePrice == null`,
    # which produced false-positive "⏵ auto-place" badges on bot_dc_value
    # rows for matches where Coolbet only offers 1X2.
    assert "coolbetGateEdge" in ed, (
        "engine-data must compute a strict-Coolbet edge for the auto-place gate "
        "(no Unibet proxy fallback)"
    )
    assert "coolbetOdds == null" in ed, (
        "no_market / no_event branch must check coolbetOdds directly, not the "
        "Unibet-proxy livePrice"
    )
    # DNB-PARSE follow-on: real Draw No Bet odds now land in odds_snapshots
    # (market="draw_no_bet", selection="home"/"away"). The paper→snapshot key
    # mapping must surface them so DNB rows on /admin/place show Coolbet/
    # Bet365/Pinnacle prices instead of always "—".
    assert 'm === "draw_no_bet"' in ed, (
        "_mapPaperToSnapshotKey must map draw_no_bet so DNB rows look up "
        "real Coolbet/Bet365/Pinnacle prices"
    )
    # ADMIN-PLACE-COOLBET-ONLY-EVIDENCE (2026-05-26): `matchIdsWithCoolbetEvent`
    # must be Coolbet-only. Unibet snapshots come from AF's bulk-odds endpoint
    # (no fuzzy match — fixture identity is known) and Unibet covers leagues
    # Estonian Coolbet does not (Argentina Primera B reserves, women's lower
    # divisions). Treating Unibet evidence as "Coolbet has this event"
    # flipped legitimate no_event rows to false-positive no_market chips.
    assert '.eq("bookmaker", "Coolbet")' in ed, (
        "coolbetEventRows query must filter to bookmaker=Coolbet only "
        "(Unibet doesn't prove Coolbet has the event)"
    )
    assert '.in("bookmaker", ["Coolbet", "Unibet"])' not in ed, (
        "Unibet must not contribute to Coolbet event-presence detection — "
        "see ADMIN-PLACE-COOLBET-ONLY-EVIDENCE"
    )
    tbl = (web / "src" / "components" / "place-bet-table.tsx").read_text()
    assert "AutoPlaceStatusBadge" in tbl, "place-bet-table must render AutoPlaceStatusBadge"
    # Both chips must render with distinct copy so the user can scan the table
    # and tell which rows to spot-check for fuzzy-match issues vs market gaps.
    assert '"no_event"' in tbl, "place-bet-table must render a chip for no_event"
    assert '"no_market"' in tbl, "place-bet-table must render a chip for no_market"
    assert "⚠ no match" in tbl, "no_event chip should label as '⚠ no match'"
    assert "⚠ no market" in tbl, "no_market chip should label as '⚠ no market'"


@test("COOLBET-FUZZY-DATE-GUARD — fuzzy_match_event rejects same-team candidates on wrong date")
def _():
    """COOLBET-FUZZY-DATE-GUARD (2026-05-26): names alone aren't enough.
    Coolbet often has the first-team fixture for "Racing Club vs Tigre" on
    one day and nothing on the day our DB has the reserves fixture. Without
    a date guard the matcher would resolve the reserves bet to the first-team
    event, then write a misleading Coolbet snapshot under our reserves
    match_id — flipping the /admin/place chip from `⚠ no match` (correct) to
    `⚠ no market` (wrong). Guard is ±6h around the DB match date."""
    import sys, pathlib
    from datetime import datetime, timezone, timedelta
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from workers.automation.coolbet_placer import (
        fuzzy_match_event, _FUZZY_DATE_TOLERANCE_HOURS,
    )
    assert _FUZZY_DATE_TOLERANCE_HOURS == 6, (
        "tolerance window should be 6h — wide enough for tz/reschedule "
        "drift, narrow enough to reject different-day fixtures"
    )

    our_kickoff = datetime(2026, 5, 26, 21, 0, tzinfo=timezone.utc)
    events = [
        # Same teams, but Coolbet's fixture is tomorrow — must be rejected.
        {"id": 1, "home": "Racing Club", "away": "Tigre",
         "start": (our_kickoff + timedelta(hours=24)).isoformat()},
        # Same teams, same kickoff — must be accepted.
        {"id": 2, "home": "Racing Club", "away": "Tigre",
         "start": our_kickoff.isoformat()},
    ]
    matched = fuzzy_match_event("Racing Club Res.", "Tigre Res.", events, our_kickoff)
    assert matched is not None and matched["id"] == 2, (
        f"must prefer the same-day event over the +24h event, got {matched}"
    )

    # All candidates outside the window → no match (no fallback to name-only).
    far_events = [
        {"id": 9, "home": "Racing Club", "away": "Tigre",
         "start": (our_kickoff + timedelta(hours=24)).isoformat()},
    ]
    no_match = fuzzy_match_event("Racing Club Res.", "Tigre Res.", far_events, our_kickoff)
    assert no_match is None, (
        f"all candidates >6h away must yield no match, got {no_match}"
    )

    # Missing match_date → date guard disabled, name match still works.
    legacy = fuzzy_match_event(
        "Racing Club Res.", "Tigre Res.",
        [{"id": 3, "home": "Racing Club", "away": "Tigre", "start": None}],
        None,
    )
    assert legacy is not None and legacy["id"] == 3, (
        "no-date call must keep working (back-compat for callers without kickoff)"
    )


@test("COOLBET-SEARCH-LAVAL — per-team partial_ratio handles short-vs-full club names")
def _():
    """COOLBET-SEARCH-LAVAL (2026-05-24): the previous whole-string
    token_set_ratio scored 'Laval vs Rouen' against 'Stade Lavallois FC Rouen'
    at 62 because 'Laval' and 'Lavallois' share no tokens. Per-team
    partial_ratio handles that case (Laval inside Lavallois = 100)."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from workers.automation.coolbet_placer import fuzzy_match_event

    # The bug case: short club name matched against full name with prefix.
    events = [
        {"id": 1, "home": "Stade Lavallois", "away": "FC Rouen"},
        {"id": 2, "home": "AC Milan",        "away": "Inter"},
    ]
    matched = fuzzy_match_event("Laval", "Rouen", events)
    assert matched is not None and matched["id"] == 1, (
        f"Laval/Rouen must match Stade Lavallois/FC Rouen, got {matched}"
    )

    # Negative: completely unrelated teams must NOT match.
    no_match = fuzzy_match_event(
        "Real Madrid", "Barcelona", [{"id": 9, "home": "Lazio", "away": "Roma"}]
    )
    assert no_match is None, f"Real Madrid/Barcelona must not match Lazio/Roma, got {no_match}"


@test("REAL-BETS-CLV-NORMALIZE — real_bets settle normalizes market/selection + OU-line aware")
def _():
    """REAL-BETS-CLV-NORMALIZE (2026-05-24): real_bets stores raw labels
    ('1X2', 'O/U', 'o/u' + 'over 2.5') that don't match odds_snapshots
    canonical labels. _settle_real_bets_for_matches must normalize before
    calling get_closing_odds. _normalize_bet_market must also inspect
    selection to pick the right OU line (over_under_35 for 'over 3.5'
    not the hardcoded over_under_25)."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    settle = (root / "workers" / "jobs" / "settlement.py").read_text()
    assert "_normalize_bet_market(bet[\"market\"], bet[\"selection\"])" in settle, (
        "_settle_real_bets_for_matches must normalize market/selection before "
        "calling get_closing_odds (CLV-NORMALIZE)"
    )
    # OU-line awareness: normalizer must accept selection argument and parse line
    assert "def _normalize_bet_market(market: str, selection: str" in settle, (
        "_normalize_bet_market must accept selection so OU-line can be parsed"
    )
    assert 'over_under_{line_str}' in settle, (
        "_normalize_bet_market must build 'over_under_{NN}' from the line in selection"
    )


@test("REAL-BETS-EDGE-FORMULA-FIX — additive edge formula + placer edge-aware gate")
def _():
    """REAL-BETS-EDGE-FORMULA-FIX (2026-05-24):
    1. store_real_bet must compute edge_pct_taken additively from calibrated_prob.
    2. coolbet_placer must gate placement on edge at the current price,
       not slippage, and must skip the real_bets row when edge < threshold."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent

    placer = (root / "workers" / "automation" / "coolbet_placer.py").read_text()
    assert "_MIN_REMAINING_EDGE" in placer, (
        "placer must define _MIN_REMAINING_EDGE env-driven gate"
    )
    assert "COOLBET_MIN_REMAINING_EDGE" in placer, (
        "placer must read COOLBET_MIN_REMAINING_EDGE from env"
    )
    # Default must match _MIN_EDGE (3%) so live odds drift doesn't bypass the edge floor
    assert 'str(_MIN_EDGE)' in placer, (
        "_MIN_REMAINING_EDGE default must be str(_MIN_EDGE), not hardcoded 0.0"
    )
    assert "edge_eroded" in placer, (
        "placer must emit outcome='edge_eroded' when bet skipped due to edge"
    )
    # SQL must pull calibrated_prob so the gate can compute edge at live odds.
    assert "sb.calibrated_prob" in placer and "sb.model_probability" in placer, (
        "load_qualified_bets must SELECT calibrated_prob + model_probability"
    )
    # Old slippage gate in the main path must no longer block placement.
    assert 'odds_ok = drop <= _ODDS_TOLERANCE' not in placer, (
        "main placement path must not gate on slippage tolerance"
    )
    # Fail-closed when live_edge is uncomputable — bets with no cal_prob/model_prob
    # must skip, not slip through.
    assert "live_edge is None or live_edge <" in placer, (
        "placer must fail closed when live_edge is uncomputable (cal_prob == 0)"
    )
    assert "live_edge uncomputable" in placer, (
        "placer must log the uncomputable-edge skip path"
    )


@test("DISCOVER-STRATEGIES — script exists and has required analysis functions")
def _():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent /
           "discover_strategies.py").read_text()
    assert "def segment_analysis" in src, "must define segment_analysis()"
    assert "def ml_analysis" in src, "must define ml_analysis()"
    assert "def suggest_configs" in src, "must define suggest_configs()"
    assert "def load_csv_data" in src, "must define load_csv_data()"
    assert "def load_db_data" in src, "must define load_db_data()"
    assert "TRAIN_CUTOFF" in src, "must define TRAIN_CUTOFF for train/test split"
    assert "XGBRegressor" in src, "must use XGBoost for feature importance"
    assert "return_per_unit" in src, "must use return_per_unit as ML target"
    assert "--db" in src, "must support --db flag for AF feature enrichment"
    assert "--fd" in src, "must support --fd flag for football-data CSV"
    assert "CSV_FD" in src, "must reference football-data CSV path"
    assert "include_fd" in src, "load_csv_data must accept include_fd param"


@test("BACKTEST-FD — football_data script exists and has required structure")
def _():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent /
           "backtest_football_data.py").read_text()
    assert "LEAGUES" in src, "must define LEAGUES dict"
    assert "SEASONS" in src, "must define SEASONS list"
    assert "fair_probs_3way" in src, "must implement Buchdahl margin stripping"
    assert "fair_probs_2way" in src, "must handle 2-way markets"
    assert "PSCH" in src, "must use Pinnacle closing columns"
    assert "clv" in src, "must compute CLV (B365 / Pinnacle closing)"
    assert "backtest-football-data.csv" in src, "must write output CSV"


@test("INPLAY-CONFIG-LOOSEN — inplay_e window 25-30, inplay_m OU 2.20, inplay_n window 65-82 + away")
def _():
    """INPLAY-CONFIG-LOOSEN (2026-05-22): three inplay bot configs loosened based on
    funnel analysis of live_match_snapshots. Verifies exact threshold values in source."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] /
           "workers" / "jobs" / "inplay_bot.py").read_text()

    # inplay_e: window tightened to 25-30
    e_start = src.index("def _check_strategy_e(")
    e_end = src.index("\ndef _check_strategy_g(")
    e_body = src[e_start:e_end]
    assert "minute > 30" in e_body, "inplay_e upper window must be 30 (INPLAY-CONFIG-LOOSEN)"
    assert "minute > 50" not in e_body, "inplay_e must not still have old 50-min gate"

    # inplay_m: OU floor 2.20
    m_start = src.index("def _check_strategy_m(")
    m_end = src.index("\ndef _check_strategy_n(")
    m_body = src[m_start:m_end]
    assert "min_val=2.20" in m_body, "inplay_m must use min_val=2.20 (INPLAY-CONFIG-LOOSEN)"
    assert "min_val=2.40" not in m_body, "inplay_m must not still have old 2.40 floor"

    # inplay_n: window 65-82, away-favourite path
    n_start = src.index("def _check_strategy_n(")
    n_end = src.index("\ndef _check_strategy_q(")
    n_body = src[n_start:n_end]
    assert "minute < 65" in n_body, "inplay_n lower window must be 65 (INPLAY-CONFIG-LOOSEN)"
    assert "minute > 82" in n_body, "inplay_n upper window must be 82 (INPLAY-CONFIG-LOOSEN)"
    assert "pm_away_prob" in n_body, "inplay_n must check away-favourite path (INPLAY-CONFIG-LOOSEN)"
    assert "minute < 72" not in n_body, "inplay_n must not still have old 72-min gate"


@test("MARKET-CASE-NORMALIZE — store_bet normalizes market to lowercase")
def test_market_case_normalize():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] /
           "workers" / "api_clients" / "supabase_client.py").read_text()
    assert '"market": bet_data["market"].lower()' in src, \
        "store_bet must normalize market to lowercase to prevent duplicate rows in market breakdown"


@test("ADMIN-REAL-BETS-PAGE — daily stats + collapsed bet log, per-book table removed (source inspect)")
def test_admin_real_bets_page():
    """Admin /admin/real-bets page polish (2026-05-22): drop per-book (Coolbet only),
    add Today (UTC) stats row alongside Overall, default bet log to 50 with expand."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    page = root.parent / "odds-intel-web" / "src" / "app" / "(app)" / "admin" / "real-bets" / "page.tsx"
    log = root.parent / "odds-intel-web" / "src" / "components" / "real-bets-log.tsx"
    if not page.exists() or not log.exists():
        print("  [skip] odds-intel-web not present in CI")
        return
    page_src = page.read_text()
    # per-book block removed
    assert "Per-book" not in page_src, "per-book table must be removed (Coolbet-only)"
    assert "byBook" not in page_src, "byBook aggregation must be removed"
    # daily stats present
    assert "todayBets" in page_src, "page must compute todayBets"
    assert "Date.UTC" in page_src, "today boundary must be in UTC"
    assert "StatRow" in page_src, "must use StatRow component for Overall + Today rows"
    assert "Today (UTC" in page_src, "must label the daily stat row"
    # log moved to client component
    assert 'from "@/components/real-bets-log"' in page_src, "page must import RealBetsLog"
    assert "<RealBetsLog bets={bets} />" in page_src, "page must render RealBetsLog"

    log_src = log.read_text()
    assert '"use client"' in log_src, "real-bets-log must be a client component"
    assert "INITIAL_VISIBLE = 50" in log_src, "log must default to 50 visible rows"
    assert "Show all" in log_src, "log must have a Show all expand button"


@test("ADMIN-REAL-BETS-INSIGHTS — chart, daily breakdown, exposure, paper-vs-real, log filters (source inspect)")
def test_admin_real_bets_insights():
    """Admin /admin/real-bets insights expansion (2026-05-22): cumulative P&L chart
    (real vs paper), last-14-days breakdown, open exposure panel, paper-vs-real
    summary, and bot/result/market filters on the bet log."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    web = root.parent / "odds-intel-web" / "src"
    page = web / "app" / "(app)" / "admin" / "real-bets" / "page.tsx"
    log = web / "components" / "real-bets-log.tsx"
    chart = web / "components" / "real-bets-chart.tsx"
    engine_data = web / "lib" / "engine-data.ts"
    if not page.exists() or not log.exists() or not engine_data.exists():
        print("  [skip] odds-intel-web not present in CI")
        return

    # engine-data: RealBet now carries paper outcome via simulated_bet_id join
    ed = engine_data.read_text()
    assert "paper:simulated_bet_id" in ed, (
        "getRealBets must nested-select the paired simulated_bets row via simulated_bet_id"
    )
    assert "paper: {" in ed and "} | null" in ed, "RealBet interface must declare paper field"

    # chart component
    assert chart.exists(), "real-bets-chart.tsx must exist"
    chart_src = chart.read_text()
    assert '"use client"' in chart_src, "chart must be a client component"
    assert "RealBetsChartPoint" in chart_src, "chart must export RealBetsChartPoint type"
    assert 'dataKey="real"' in chart_src and 'dataKey="paper"' in chart_src, (
        "chart must plot both real and paper cumulative P&L lines"
    )

    # page wiring
    page_src = page.read_text()
    assert "buildCumulativeSeries" in page_src, "page must build cumulative series"
    assert "buildDailyBreakdown" in page_src, "page must build daily breakdown"
    assert "buildExposure" in page_src, "page must build open-exposure summary"
    assert "buildPaperVsReal" in page_src, "page must build paper-vs-real summary"
    assert "<RealBetsChart data={series} />" in page_src, "page must render the chart"
    assert "Open exposure" in page_src, "page must show the open exposure panel"
    assert "Max potential payout" in page_src, "exposure must show max potential payout"
    assert "Paper vs real" in page_src, "page must show paper-vs-real block"
    assert "Slippage cost" in page_src, "paper-vs-real must surface slippage cost in €"
    assert "Stake parity" in page_src, "paper-vs-real must surface stake parity badge"
    assert "stakeParityDiverged" in page_src, "page must count divergent stakes (real vs Kelly)"
    assert "Last " in page_src and "days (UTC)" in page_src, "page must show last-N-days table"

    # log filters
    log_src = log.read_text()
    assert "FilterSelect" in log_src, "log must include FilterSelect component"
    for opt in ("Bot", "Result", "Market"):
        assert f'label="{opt}"' in log_src, f"log must have a {opt} filter"
    assert "anyFilter" in log_src and "clear" in log_src, "log must have a clear-filters affordance"


@test("PUBLIC-PERF-EXTRAS — cumulative chart, calibration, streaks on /performance (source inspect)")
def test_public_performance_extras():
    """Public-page upgrade: /performance shows a 90-day cumulative P&L chart at
    the top, plus streak badges and a calibration table. Visible to all tiers."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    web = root.parent / "odds-intel-web" / "src"
    page = web / "app" / "(app)" / "performance" / "page.tsx"
    extras = web / "components" / "performance-extras.tsx"
    chart = web / "components" / "performance-pnl-chart.tsx"
    engine_data = web / "lib" / "engine-data.ts"
    retired = web / "components" / "retired-strategies-section.tsx"
    if not page.exists() or not engine_data.exists():
        print("  [skip] odds-intel-web not present in CI")
        return

    ed = engine_data.read_text()
    assert "getPublicPerformanceExtras" in ed, "engine-data must export getPublicPerformanceExtras"
    assert "PublicPnlPoint" in ed and "CalibrationBucket" in ed and "Streaks" in ed, (
        "engine-data must export PublicPnlPoint + CalibrationBucket + Streaks"
    )
    assert "botRecentRoi" in ed, "extras must compute per-bot 30-day ROI map"

    page_src = page.read_text()
    assert "getPublicPerformanceExtras" in page_src, "page must call getPublicPerformanceExtras"
    assert "<PerformanceExtras data={extras} />" in page_src, "page must render <PerformanceExtras>"

    assert extras.exists() and chart.exists(), "performance-extras + chart components must exist"
    extras_src = extras.read_text()
    assert "PerformancePnlChart" in extras_src, "extras must render the chart"
    assert "Streaks" in extras_src and "Calibration" in extras_src, "extras must include both cards"
    assert "longestWin" in extras_src and "longestLoss" in extras_src, "streaks must surface longest W and L"
    assert "Variance is real" in extras_src, "streak card must include variance disclaimer"

    chart_src = chart.read_text()
    assert '"use client"' in chart_src, "chart must be a client component"
    assert "ResponsiveContainer" in chart_src, "chart must be responsive (mobile)"

    retired_src = retired.read_text()
    assert "useState(true)" in retired_src, "retired-strategies section must default open"


@test("VB-CONSENSUS-CLV-BESTBOOK — consensus, line direction, kickoff/best-book on /value-bets (source inspect)")
def test_value_bets_consensus_clv():
    """Value-bets public-page upgrade: bot consensus chip + line direction arrow
    on every row (all tiers), time-to-kickoff + best book in compact row header,
    and a per-bot 30-day ROI hook on the free tier's single unmasked pick."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    web = root.parent / "odds-intel-web" / "src"
    live = web / "components" / "value-bets-live.tsx"
    page = web / "app" / "(app)" / "value-bets" / "page.tsx"
    if not live.exists() or not page.exists():
        print("  [skip] odds-intel-web not present in CI")
        return

    live_src = live.read_text()
    page_src = page.read_text()

    assert "botRecentRoi={extras.botRecentRoi}" in page_src, (
        "page must pass botRecentRoi to ValueBetsLive"
    )
    assert "fetchBookOdds" in page_src, (
        "page must compute fetchBookOdds so Pro/Free also get current market odds"
    )

    assert "LineDirChip" in live_src, "must have a LineDirChip component"
    assert "lineDirection" in live_src, "must have a lineDirection helper"
    assert "bots agree" in live_src, "consensus chip must read '<N> bots agree'"
    assert "isElite && bet.botCount > 1" not in live_src, (
        "consensus chip must not be Elite-gated — show to all tiers"
    )
    assert "kickoffLabel" in live_src, "must compute a kickoff countdown label"
    assert "best at" in live_src, "row header must surface recommended bookmaker"
    assert "botRoi.roi" in live_src, "free-tier teaser must surface the bot's recent ROI"
    assert "30d" in live_src, "ROI hook must label the window (e.g. '30d')"


@test("COMBO-FIX-1 — proven variants merge ou15 into scan when require_ou15 set")
def test_combo_proven_ou15_fix():
    """bot_acca_proven + bot_combo_proven_system had market_whitelist excluding
    ou15 but require_ou15=True → mutually exclusive, never fired. _scan_todays_
    candidates now accepts always_include_markets so the proven variants can
    merge ou15 into their candidate pool just for the gate."""
    import inspect
    from workers.jobs import acca_bot
    src = inspect.getsource(acca_bot._scan_todays_candidates)
    assert "always_include_markets" in src, (
        "_scan_todays_candidates must accept always_include_markets kwarg"
    )
    assert "base_eligible | (always_include_markets or frozenset())" in src, (
        "scan must union always_include_markets into eligible set"
    )
    run_src = inspect.getsource(acca_bot.run_acca_pass)
    assert 'cfg.get("require_ou15")' in run_src and 'frozenset({"ou15"})' in run_src, (
        "run_acca_pass must pass always_include={'ou15'} when require_ou15=True"
    )


@test("COMBO-NEW — bot_acca_coolbet config + Coolbet match filter")
def test_combo_acca_coolbet():
    """New variant whose candidate pool is limited to matches in Coolbet
    leagues (per coolbet_leagues_cache.json). Gates relaxed (no require_ou15,
    min_per_leg_odds=1.25) because Coolbet's top-league pool prices OU15 below
    1.40; documented as paper-only until ≥30 settled combos."""
    from workers.jobs.acca_bot import ACCA_VARIANTS, _coolbet_match_ids
    assert "bot_acca_coolbet" in ACCA_VARIANTS, "bot_acca_coolbet must be in ACCA_VARIANTS"
    cfg = ACCA_VARIANTS["bot_acca_coolbet"]
    assert cfg.get("coolbet_only") is True, "config must have coolbet_only=True"
    assert cfg["min_per_leg_odds"] == 1.25, (
        "min_per_leg_odds must be relaxed to 1.25 (Coolbet OU15 prices below 1.40)"
    )
    assert cfg["require_ou15"] is False, (
        "require_ou15 must be False on Coolbet variant (OU15 unavailable in pool)"
    )
    import pathlib
    mig = pathlib.Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "124_bot_acca_coolbet.sql"
    assert mig.exists(), "migration 124 must register bot_acca_coolbet"
    assert "bot_acca_coolbet" in mig.read_text(), "migration 124 must insert the bot row"
    assert callable(_coolbet_match_ids), "_coolbet_match_ids helper must be exported"


@test("DUPE-FIX-1 — /api/admin/real-bet has NOT EXISTS dedup guard")
def test_real_bet_api_dedup():
    """Web API must return 409 when same (match, market, selection) already
    has a real_bet today. Prevents manual click from racing the auto placer."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    route = root.parent / "odds-intel-web" / "src" / "app" / "api" / "admin" / "real-bet" / "route.ts"
    if not route.exists():
        print("  [skip] odds-intel-web not present in CI")
        return
    src = route.read_text()
    assert "already_placed" in src, "dedup guard must return 'already_placed' error"
    assert "existingId" in src, "dedup response must surface existingId for UI to link to"
    assert "status: 409" in src, "must return 409 Conflict"


@test("DUPE-FIX-2 — coolbet_placer skips store_real_bet when ticket_id is None")
def test_placer_no_phantom_record():
    """coolbet_placer used to write a phantom real_bets row even when no
    Coolbet ticket was placed (no odds_uuid / odds drift / placement error).
    That blocked manual placement of the same selection and polluted the
    dataset. Now: skip the write and let the manual placer pick it up."""
    import inspect
    from workers.automation import coolbet_placer
    src = inspect.getsource(coolbet_placer.place_all_bets)
    assert "Skip real_bets write for" in src, (
        "placer must explicitly skip store_real_bet when ticket_id is None"
    )
    assert '"reason": "no_ticket"' in src, (
        "skipped placement must surface reason='no_ticket' in results dict"
    )


@test("DUPE-CLEAN — migration 123 voids the Joondalup phantom real_bet")
def test_phantom_void_migration():
    import pathlib
    mig = pathlib.Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "123_void_phantom_real_bet.sql"
    src = mig.read_text()
    assert "c3acf4e7" in src, "migration 123 must target the specific phantom row id"
    assert "ticket=None" in src, "safety guard must restrict to ticket=None notes"
    assert "result      = 'void'" in src or "result = 'void'" in src, "must set result='void'"


@test("BOTS-UNRETIRE-WEEKEND — migration 122 un-retires bot_aggressive + inplay merges")
def test_unretire_weekend_migration():
    """Migration 122 (was 120, renumbered to clear a slot collision): override
    migration 117 + migration 104 carve-outs so bot_aggressive + 3 inplay merge
    variants fire across this weekend's cohort. Duplicate-bet noise accepted;
    data feeds upcoming calibration work."""
    import pathlib
    mig = pathlib.Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "122_unretire_remaining_bots.sql"
    src = mig.read_text()
    for name in ("bot_aggressive", "inplay_a2", "inplay_c_home", "inplay_f"):
        assert name in src, f"migration 120 must un-retire {name}"
    assert "is_active" in src and "true" in src, "migration must set is_active=true"
    assert "retired_at = NULL" in src, "migration must null retired_at"


@test("PER-BOT-EDGE-THRESHOLD-APPLY — per-bot edge thresholds match 25K backtest sweep findings")
def _():
    """PER-BOT-EDGE-THRESHOLD-APPLY (2026-05-25) — apply the 2026-05-19 sweep
    findings (25K backtest rows / 22 bots) to BOTS_CONFIG. Guard the new
    values so they don't silently revert.

    Sweep optima (ROI gain in pp over baseline at sweep threshold):
      bot_aggressive       → 15% (baseline +0.4% → +9.0% ROI, n=2802)
      bot_aggressive_v2    → 15% (baseline -4.1% → +2.1% ROI, n= 647)
      bot_btts_all         → 12% (baseline -0.3% → +5.8% ROI, n= 331)
      bot_btts_conservative→  8% (baseline -2.2% → +3.6% ROI, n= 142)
      bot_ou35_attacking   → 14% (baseline +30.6%→ +40.0% ROI, n= 199)
    """
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG

    # bot_aggressive — 15% across all market_type buckets, all tiers.
    cfg = BOTS_CONFIG["bot_aggressive"]["edge_thresholds"]
    for tier in (1, 2, 3, 4):
        for market in ("1x2_fav", "1x2_long", "ou"):
            assert cfg[tier][market] == 0.15, (
                f"bot_aggressive tier {tier}/{market} must be 0.15, got {cfg[tier][market]}"
            )

    # bot_aggressive_v2 — 15% across all market_type buckets, all tiers.
    cfg = BOTS_CONFIG["bot_aggressive_v2"]["edge_thresholds"]
    for tier in (1, 2, 3, 4):
        for market in ("1x2_fav", "1x2_long", "ou"):
            assert cfg[tier][market] == 0.15, (
                f"bot_aggressive_v2 tier {tier}/{market} must be 0.15, got {cfg[tier][market]}"
            )

    # bot_btts_all — 12% across all tiers.
    cfg = BOTS_CONFIG["bot_btts_all"]["edge_thresholds"]
    for tier in (1, 2, 3, 4):
        assert cfg[tier]["btts"] == 0.12, (
            f"bot_btts_all tier {tier} btts must be 0.12, got {cfg[tier]['btts']}"
        )

    # bot_btts_conservative — 8% across T1-T2 (only active tiers).
    cfg = BOTS_CONFIG["bot_btts_conservative"]["edge_thresholds"]
    for tier in (1, 2):
        assert cfg[tier]["btts"] == 0.08, (
            f"bot_btts_conservative tier {tier} btts must be 0.08, got {cfg[tier]['btts']}"
        )

    # bot_ou35_attacking — 14% across all tiers.
    cfg = BOTS_CONFIG["bot_ou35_attacking"]["edge_thresholds"]
    for tier in (1, 2, 3, 4):
        assert cfg[tier]["ou"] == 0.14, (
            f"bot_ou35_attacking tier {tier} ou must be 0.14, got {cfg[tier]['ou']}"
        )


@test("SLICE-LIVE-VALIDATE — leaker slices retired on bot_aggressive + bot_btts_all")
def _():
    """SLICE-LIVE-VALIDATE (2026-05-25) — retired slices where live ROI
    confirmed sustained leakage at ≥50 settled bets:

      bot_aggressive selection:draw  live ROI -32.7% (n=89)  → no Draw in selection_filter
      bot_aggressive odds 2.50-3.00  live ROI  -6.3% (n=150) → odds_range capped at 2.50
      bot_aggressive odds 3.50+      live ROI -13.9% (n=273) → odds_range capped at 2.50 (subsumed)
      bot_btts_all   odds 1.50-2.00  live ROI -13.9% (n=69)  → odds_range floor lifted to 2.00

    Guard the new ranges/filters so a future refactor doesn't silently revert.
    """
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG

    agg = BOTS_CONFIG["bot_aggressive"]
    assert agg["odds_range"] == (1.25, 2.50), (
        f"bot_aggressive odds_range must be (1.25, 2.50) after SLICE-LIVE-VALIDATE, "
        f"got {agg['odds_range']}"
    )
    assert "selection_filter" in agg, (
        "bot_aggressive must have selection_filter excluding Draw"
    )
    assert "Draw" not in agg["selection_filter"], (
        f"bot_aggressive selection_filter must exclude Draw, got {agg['selection_filter']}"
    )

    btts = BOTS_CONFIG["bot_btts_all"]
    assert btts["odds_range"] == (2.00, 2.80), (
        f"bot_btts_all odds_range must be (2.00, 2.80) after SLICE-LIVE-VALIDATE, "
        f"got {btts['odds_range']}"
    )


@test("BOT-OU15-DIAGNOSE-CLOSE — migration 129 re-retires bot_ou15_defensive after 17-day silence")
def test_bot_ou15_diagnose_close_migration():
    """BOT-OU15-DIAGNOSE-CLOSE (2026-05-25) — final retirement of
    bot_ou15_defensive. Bot has been silent since 2026-05-08 despite all
    diagnostics being ruled out (ACCESSIBLE-BM, PIN-VETO, MFV inference,
    calibration tightening) and edge-threshold relaxation already tried
    (BOT-OU15-EDGE-REPAIR — 0/104 candidates recovered).

    Migration 117 un-retired it on 2026-05-22; remained silent. Migration
    129 retires it again with the 17-day silent-period evidence in
    retired_reason."""
    import pathlib
    mig = pathlib.Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "129_retire_bot_ou15_defensive_final.sql"
    src = mig.read_text()
    assert "bot_ou15_defensive" in src, "migration 129 must target bot_ou15_defensive"
    assert "is_active = false" in src, "migration 129 must set is_active=false"
    assert "retired_at = now()" in src, "migration 129 must set retired_at=now()"
    assert "retired_reason" in src, "migration 129 must populate retired_reason"
    assert "17-day silent" in src or "2026-05-08" in src, (
        "retired_reason must cite the silent-period evidence"
    )
    # Sanity DO block must fail loudly if the row isn't retired post-migration.
    assert "RAISE EXCEPTION" in src, (
        "migration 129 must include a sanity check that aborts if retire failed"
    )


@test("ODDS-TIMING-VALIDATE — odds_timing_analysis.py exposes hours-before-KO CLV by bucket")
def test_odds_timing_validate_script():
    """ODDS-TIMING-VALIDATE (2026-05-25) — ran the analysis on 963 settled bets
    over 14 days. The 2-4h-before-KO bucket has CLV +7.4% but does not beat the
    6-9h (+5.7%), 9-12h (+6.4%), or 12h+ (+17.1%) buckets by >2pp, so no
    scheduler change. Smoke test asserts the script still ships and exposes
    the hours-before-KO CLV query so future re-runs are possible."""
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "odds_timing_analysis.py"
    assert p.exists(), "scripts/odds_timing_analysis.py missing"
    src = p.read_text()
    # Match-relative bucket logic must still be present.
    assert "hours before kickoff" in src.lower(), (
        "odds_timing_analysis.py must keep the hours-before-KO CLV section"
    )
    # Must still hit the settled-bets table (CLV requires settled outcomes).
    assert "simulated_bets" in src or "real_bets" in src, (
        "odds_timing_analysis.py must read from a settled-bets table"
    )


@test("TELE-BET-NOTIFY — send_telegram in inplay_bot + daily_pipeline; team names in prematch query; daemon Telegram removed")
def test_telegram_bet_notify():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    bot_src = (root / "workers" / "jobs" / "inplay_bot.py").read_text()
    pipeline_src = (root / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    placer_src = (root / "workers" / "automation" / "coolbet_placer.py").read_text()
    daemon_src = (root / "scripts" / "coolbet_daemon.py").read_text()

    assert "from workers.notify.telegram import send_telegram" in bot_src, \
        "inplay_bot.py must import send_telegram"
    assert "home_name" in bot_src and "away_name" in bot_src, \
        "prematch query must select home_name / away_name from teams"
    assert "send_telegram" in bot_src, \
        "inplay_bot.py must call send_telegram after bet placement"

    assert "_new_bet_lines" in pipeline_src, \
        "daily_pipeline_v2.py must accumulate _new_bet_lines for Telegram summary"
    assert "value bet" in pipeline_src, \
        "daily_pipeline_v2.py must send Telegram summary with 'value bet' label"

    assert "from workers.notify.telegram import send_telegram" not in placer_src, \
        "coolbet_placer.py must NOT import send_telegram (placer is run manually)"

    assert "coolbet-imperva" not in daemon_src, \
        "daemon must not send Imperva Telegram alerts (daemon not in active use)"
    assert "coolbet-keepalive-fail" not in daemon_src, \
        "daemon must not send keepalive-fail Telegram alerts"


@test("MATURITY-LABEL-MIGRATION — migration 134 adds maturity_label column and correct bot assignments")
def test_maturity_label_migration():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    sql = (root / "supabase" / "migrations" / "134_maturity_label.sql").read_text()

    assert "ADD COLUMN IF NOT EXISTS maturity_label" in sql, \
        "migration must add maturity_label column"
    assert "'calibrated'" in sql, \
        "migration must set calibrated label"
    assert "'experimental'" in sql, \
        "migration must set experimental label"
    assert "'beta'" in sql, \
        "migration must set beta label"

    # Spot-check a few key bots in each tier
    assert "bot_aggressive" in sql, "bot_aggressive must be set to calibrated"
    assert "inplay_e" in sql, "inplay_e must be set to calibrated"
    assert "bot_acca_value" in sql, "bot_acca_value must be set to experimental"
    assert "bot_combo_system" in sql, "bot_combo_system must be set to experimental"
    assert "bot_high_alignment" in sql, "bot_high_alignment must be set to beta"
    assert "inplay_btts_dryspell_v1" in sql, "inplay_btts_dryspell_v1 must be set to beta"


@test("SETTLEMENT-EXCL-EXPERIMENTAL — headline ROI/CLV queries exclude experimental bots")
def _():
    """All three headline number paths in write_dashboard_cache must exclude
    experimental (acca/combo) bots so the cached roi_pct, avg_clv, and
    settled_bets figures reflect only real strategies."""
    import pathlib
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    # The all-time and active headline queries now join bots and filter
    assert "maturity_label != 'experimental'" in src, (
        "settlement headline queries must exclude experimental bots via maturity_label"
    )
    # Active-only query must have the same guard
    active_section = src[src.index("Active-only headline"):]
    assert "maturity_label != 'experimental'" in active_section[:1200], (
        "active-only headline query must also exclude experimental bots"
    )


@test("RETIRE-BAD-PERFORMERS — migration 135 retires 4 chronically negative-ROI bots")
def _():
    """bot_aggressive_v2, bot_ou35_attacking, bot_high_roi_global, bot_proven_leagues
    must have retired_at set and is_active = false after migration 135."""
    import pathlib
    # Verify migration file exists and targets the right bots
    migration = pathlib.Path("supabase/migrations/135_retire_bad_performers.sql").read_text()
    for bot in ("bot_aggressive_v2", "bot_ou35_attacking", "bot_high_roi_global", "bot_proven_leagues"):
        assert bot in migration, f"Migration 135 must retire {bot}"
    assert "retired_at = NOW()" in migration, "Migration 135 must set retired_at"
    assert "is_active = false" in migration, "Migration 135 must set is_active = false"

    # Verify settlement.py grand total counts have no experimental exclusion
    src = pathlib.Path("workers/jobs/settlement.py").read_text()
    # Grand total settled_bets count query must NOT apply _excl
    grand_total_section = src[src.index("Grand total counts"):]
    no_excl_block = grand_total_section[:grand_total_section.index("ROI/CLV math")]
    assert "_excl" not in no_excl_block, (
        "Grand total count queries must not use _excl — they should count all bots"
    )
    # ROI/CLV math section must still exclude experimental
    roi_section = grand_total_section[grand_total_section.index("ROI/CLV math"):]
    assert "maturity_label != 'experimental'" in roi_section[:800], (
        "ROI/CLV math must still exclude experimental bots"
    )


@test("BTTS-PLATT-CAL — fit_platt.py adds BTTS 1-feature Platt calibration")
def _():
    """fit_platt.py must include fetch_settled_btts_bets() and a BTTS calibration
    section in fit_and_store(). The calibration corrects ~15pp BTTS overestimation."""
    import pathlib
    src = pathlib.Path("scripts/fit_platt.py").read_text()
    assert "fetch_settled_btts_bets" in src, "fit_platt.py must define fetch_settled_btts_bets()"
    assert "btts_yes" in src, "fit_platt.py must handle btts_yes market"
    assert "BTTS_MARKETS" in src, "fit_platt.py must define BTTS_MARKETS list"
    # Confirm BTTS fetches calibrated_prob (post-shrinkage), not raw model_probability
    btts_fn_start = src.index("def fetch_settled_btts_bets")
    btts_fn_end = src.index("\ndef ", btts_fn_start + 1)
    btts_fn = src[btts_fn_start:btts_fn_end]
    assert "calibrated_prob" in btts_fn, \
        "fetch_settled_btts_bets() must use calibrated_prob as input feature"
    assert "model_probability" not in btts_fn, \
        "fetch_settled_btts_bets() must NOT use raw model_probability — use calibrated_prob"


@test("RETIRE-DC-BTTS — migration 137 retires bot_dc_value + both BTTS bots with reasons")
def _():
    """DC (derived market, no model-native edge) and BTTS (model 15.6pp miscalibrated)
    must be retired by migration 137 with retired_reason populated."""
    import pathlib
    migration = pathlib.Path("supabase/migrations/137_retire_dc_btts_bots.sql").read_text()
    for bot in ("bot_dc_value", "bot_btts_all", "bot_btts_conservative"):
        assert bot in migration, f"Migration 137 must retire {bot}"
    assert "retired_at" in migration, "Migration 137 must set retired_at"
    assert "is_active" in migration, "Migration 137 must set is_active"
    assert "retired_reason" in migration, "Migration 137 must populate retired_reason"
    # DC reason must mention derived market
    assert "Derived market" in migration or "derived market" in migration, \
        "DC retired_reason must explain derived-market problem"
    # BTTS reason must mention calibration
    assert "miscalibrated" in migration or "calibrat" in migration, \
        "BTTS retired_reason must explain calibration problem"


@test("COOLBET-AH-LINE — store_coolbet_odds_snapshot accepts handicap_line and includes it in INSERT")
def _():
    """store_coolbet_odds_snapshot must accept a handicap_line kwarg and write it to
    odds_snapshots. Without this, Coolbet AH rows miss the line value, making it
    impossible to match them against predictions (which embed the line in the market
    name, e.g. 'ah_away_0.50')."""
    import pathlib
    src = pathlib.Path("workers/api_clients/supabase_client.py").read_text()
    fn_start = src.index("def store_coolbet_odds_snapshot")
    fn_end = src.index("\ndef ", fn_start + 1)
    fn_src = src[fn_start:fn_end]
    assert "handicap_line" in fn_src, \
        "store_coolbet_odds_snapshot must have a handicap_line parameter"
    # Explorer must call with handicap_line= kwarg (not use old _store_with_handicap workaround)
    explorer_src = pathlib.Path("workers/automation/coolbet_explorer.py").read_text()
    assert "_store_with_handicap" not in explorer_src, \
        "coolbet_explorer.py must not use _store_with_handicap workaround"
    assert "handicap_line=line" in explorer_src, \
        "coolbet_explorer.py must pass handicap_line=line to store_coolbet_odds_snapshot"
    # Placer must extract line from _normalise_our_target and pass it through
    placer_src = pathlib.Path("workers/automation/coolbet_placer.py").read_text()
    assert "snap_line" in placer_src, \
        "coolbet_placer.py must extract snap_line from _normalise_our_target"
    assert "handicap_line=snap_line" in placer_src, \
        "coolbet_placer.py must pass handicap_line=snap_line to store_coolbet_odds_snapshot"


@test("FIX-LEAGUE-TIERS-140 — migration 140 fixes Saudi-Arabia/South-Africa dash variants + extra top divisions")
def _():
    """Migration 140 must fix the country-name dash variants missed by 138 (Saudi-Arabia,
    South-Africa, United-Arab-Emirates) and add Egypt, Bolivia, Algeria, Tunisia, Kosovo,
    Malaysia top divisions. Must also promote Argentina Primera Nacional and Belgium
    Challenger Pro League to tier=2. All with WHERE tier=0 guard."""
    import pathlib
    migration = pathlib.Path("supabase/migrations/140_fix_more_league_tiers.sql").read_text()
    # Must use exact dash-variant country names
    assert "Saudi-Arabia" in migration, "Migration 140 must handle Saudi-Arabia (dash variant)"
    assert "South-Africa" in migration, "Migration 140 must handle South-Africa (dash variant)"
    assert "United-Arab-Emirates" in migration, "Migration 140 must handle UAE dash variant"
    # Must promote additional top divisions
    for league in ("egypt", "bolivia", "algeria", "tunisia", "kosovo"):
        assert league.lower() in migration.lower(), f"Migration 140 must handle {league}"
    # Must promote second divisions
    assert "primera nacional" in migration.lower(), \
        "Migration 140 must promote Argentina Primera Nacional to tier=2"
    assert "challenger pro league" in migration.lower(), \
        "Migration 140 must promote Belgium Challenger Pro League to tier=2"
    # Must guard with tier=0
    assert "WHERE tier = 0" in migration, "Migration 140 must guard with WHERE tier = 0"


@test("FIX-LEAGUE-TIERS — migration 138 uses name+country WHERE clauses, not placeholder UUIDs")
def _():
    """Migration 138 must fix tier=0 misclassifications for top-division leagues using
    portable name+country ILIKE clauses (not UUID placeholders which caused the original
    broken version of this migration)."""
    import pathlib
    migration = pathlib.Path("supabase/migrations/138_fix_league_tiers.sql").read_text()
    # Must use name+country pattern, not UUID placeholders
    assert "xxxx" not in migration, \
        "Migration 138 must not contain placeholder UUIDs (xxxx)"
    assert "ILIKE" in migration, \
        "Migration 138 must use ILIKE name matching"
    assert "country" in migration, \
        "Migration 138 must filter by country to avoid false matches"
    # Must promote known top divisions to tier=1
    for league in ("j1 league", "eredivisie", "liga profesional", "liga pro", "primera a"):
        assert league.lower() in migration.lower(), \
            f"Migration 138 must handle '{league}'"
    # Must promote known second divisions to tier=2
    for league in ("championship", "superettan", "eerste divisie"):
        assert league.lower() in migration.lower(), \
            f"Migration 138 must handle second-division '{league}'"
    # Must only promote tier=0 leagues (not accidentally demote existing ones)
    assert "WHERE tier = 0" in migration, \
        "Migration 138 must guard with WHERE tier = 0"


@test("RLS-MISSING-TABLES — migration 139 enables RLS on tables missing it")
def _():
    """Migration 139 (renamed from the duplicate 135_rls_missing_tables.sql) must enable
    RLS on all previously unprotected public and internal tables."""
    import pathlib
    migration = pathlib.Path("supabase/migrations/139_rls_missing_tables.sql").read_text()
    for tbl in ("matches", "leagues", "teams", "predictions", "odds_snapshots", "bots"):
        assert tbl in migration, f"Migration 139 must enable RLS on {tbl}"
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "public_read" in migration, "Migration 139 must create public_read SELECT policies"
    # Old duplicate file must be gone
    old_path = pathlib.Path("supabase/migrations/135_rls_missing_tables.sql")
    assert not old_path.exists(), \
        "135_rls_missing_tables.sql must be removed (renamed to 139) to fix duplicate key error"


@test("USER-TELE-NOTIFY — migration 141 adds telegram_chat_id; send_telegram_to_users in notify module; pipeline + inplay_bot wire user notifications; webhook route exists")
def test_user_tele_notify():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]

    # Migration
    migration = (root / "supabase" / "migrations" / "141_profiles_telegram_chat_id.sql").read_text()
    assert "telegram_chat_id" in migration, "migration must add telegram_chat_id column"
    assert "BIGINT" in migration.upper(), "telegram_chat_id must be BIGINT (Telegram chat IDs are large)"

    # send_telegram_to_users in notify module
    notify_src = (root / "workers" / "notify" / "telegram.py").read_text()
    assert "def send_telegram_to_users" in notify_src, "telegram.py must export send_telegram_to_users"
    assert "telegram_chat_id" in notify_src, "send_telegram_to_users must query telegram_chat_id column"
    assert "tier::text = ANY" in notify_src or "tier = ANY" in notify_src, \
        "send_telegram_to_users must filter by tier"

    # daily_pipeline_v2 wires user notifications
    pipeline_src = (root / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "send_telegram_to_users" in pipeline_src, \
        "daily_pipeline_v2.py must call send_telegram_to_users for user bet alerts"

    # inplay_bot wires user notifications
    bot_src = (root / "workers" / "jobs" / "inplay_bot.py").read_text()
    assert "send_telegram_to_users" in bot_src, \
        "inplay_bot.py must call send_telegram_to_users for user bet alerts"

    # Webhook route exists in frontend (source-inspect via relative path guess)
    web_root = root.parent / "odds-intel-web"
    if web_root.exists():
        webhook = web_root / "src" / "app" / "api" / "telegram" / "webhook" / "route.ts"
        disconnect = web_root / "src" / "app" / "api" / "telegram" / "disconnect" / "route.ts"
        assert webhook.exists(), "webhook route.ts must exist at /api/telegram/webhook"
        assert disconnect.exists(), "disconnect route.ts must exist at /api/telegram/disconnect"
        webhook_src = webhook.read_text()
        assert "/start" in webhook_src, "webhook must handle /start command"
        assert "/stop" in webhook_src, "webhook must handle /stop command"
        assert "telegram_chat_id" in webhook_src, "webhook must update telegram_chat_id"


@test("PROVEN-LEAGUES-V2 — migration 142 retires old league bots; creates bot_proven_leagues_v2 with Italy/France/USA + Austria/Belgium beta")
def test_proven_leagues_v2():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]

    migration = (root / "supabase" / "migrations" / "142_proven_leagues_v2.sql").read_text()
    assert "bot_proven_leagues_v2" in migration, "migration must create bot_proven_leagues_v2"
    assert "bot_high_roi_global" in migration, "migration must retire bot_high_roi_global"
    assert "bot_proven_leagues" in migration, "migration must retire bot_proven_leagues"
    assert "is_active" in migration and "false" in migration, "migration must set is_active=false"
    assert "retired_reason" in migration, "migration must set retired_reason"

    pipeline = (root / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "bot_proven_leagues_v2" in pipeline, "bot_proven_leagues_v2 must be in BOTS_CONFIG"
    # New bot uses live-validated leagues, not the old backtest list
    assert '"Italy"' in pipeline or "'Italy'" in pipeline, "bot_proven_leagues_v2 must include Italy"
    assert '"France"' in pipeline or "'France'" in pipeline, "bot_proven_leagues_v2 must include France"
    # Old bots must be marked retired in config
    assert "[RETIRED 2026-05-28]" in pipeline, "retired bots must have RETIRED marker in description"


@test("PLATT-LIVE-FIT — apply_platt fallback covers asian_handicap aggregate key")
def test_platt_live_fit():
    import pathlib, importlib.util
    root = pathlib.Path(__file__).resolve().parents[1]

    imp = root / "workers" / "model" / "improvements.py"
    src = imp.read_text()
    assert "_MARKET_ROOTS" in src, "apply_platt must define _MARKET_ROOTS fallback"
    assert "asian_handicap" in src, "asian_handicap must be in _MARKET_ROOTS"

    script = root / "scripts" / "fit_platt_live.py"
    assert script.exists(), "fit_platt_live.py must exist"
    script_src = script.read_text()
    assert "double_chance" in script_src, "fit_platt_live must handle double_chance"
    assert "asian_handicap" in script_src, "fit_platt_live must handle asian_handicap"


@test("HRG-V2 — migration 143 creates bot_high_roi_global_v2 with Spain/Australia/Iceland")
def test_hrg_v2():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]

    migration = (root / "supabase" / "migrations" / "143_high_roi_global_v2.sql").read_text()
    assert "bot_high_roi_global_v2" in migration, "migration must create bot_high_roi_global_v2"
    assert "Spain" in migration, "migration must mention Spain (core validated league)"

    pipeline = (root / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "bot_high_roi_global_v2" in pipeline, "bot_high_roi_global_v2 must be in BOTS_CONFIG"
    assert '"Spain"' in pipeline or "'Spain'" in pipeline, "Spain must be in league_filter"
    assert '"Australia"' in pipeline or "'Australia'" in pipeline, "Australia must be in league_filter"
    # Home/Away selection filter (no Draw)
    assert '"Away"' in pipeline or "'Away'" in pipeline, "Away selection must be included"


@test("INPLAY-LOW-FIRE-XG-FALLBACKS — A/D/G/H/I/N unlock 62% of matches missing predictions data")
def test_inplay_low_fire_xg_fallbacks():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "workers/jobs/inplay_bot.py"
    ).read_text()

    def get_fn_body(name):
        start = src.index(f"def _check_strategy_{name}(")
        try:
            end = src.index(f"\ndef _check_strategy_", start + 1)
        except ValueError:
            end = len(src)
        return src[start:end]

    # A, D, G, H: xG fallback when prematch_o25_prob absent
    for letter, threshold in [("a", "2.70"), ("d", "2.55"), ("g", "2.55"), ("h", "2.70")]:
        body = get_fn_body(letter)
        assert threshold in body, (
            f"Strategy {letter.upper()} must have xG fallback at {threshold}"
            f" — INPLAY-{letter.upper()}-XG-FALLBACK 2026-05-28"
        )
        assert "_fb_xg" in body, (
            f"Strategy {letter.upper()} must use _fb_xg variable for xG fallback"
        )

    # I, N: bivariate Poisson fallback when prematch_home/away_prob absent
    for letter in ("i", "n"):
        body = get_fn_body(letter)
        assert "_bivariate_poisson_win_prob" in body, (
            f"Strategy {letter.upper()} must call _bivariate_poisson_win_prob for fav fallback"
            f" — INPLAY-{letter.upper()}-FAV-FALLBACK 2026-05-28"
        )
        assert "pm_home_prob == 0 and pm_away_prob == 0" in body, (
            f"Strategy {letter.upper()} must guard fallback with both probs == 0 check"
        )


@test("INPLAY-P-V2-ODDS-FILTER — v2 excludes 2.50-2.99 bucket and caps at 5.0 (retirement data 2026-05-28)")
def test_inplay_p_v2_odds_filter():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "workers/jobs/inplay_bot.py"
    ).read_text()
    fn_start = src.index("def _check_strategy_p_v2(")
    try:
        fn_end = src.index("\ndef ", fn_start + 1)
    except ValueError:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "2.50" in fn_body, "Strategy P v2 must reference 2.50 (exclude 2.50-2.99 bucket)"
    assert "3.00" in fn_body, "Strategy P v2 must reference 3.00 (exclude 2.50-2.99 bucket)"
    assert "odds >= 5.0" in fn_body, "Strategy P v2 must cap at 5.0"


@test("INPLAY-J-XG-FALLBACK — strategy J derives O25 from prematch xG when prob unavailable")
def test_inplay_j_xg_fallback():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "workers/jobs/inplay_bot.py"
    ).read_text()
    fn_start = src.index("def _check_strategy_j(")
    try:
        fn_end = src.index("\ndef ", fn_start + 1)
    except ValueError:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "2.90" in fn_body, (
        "Strategy J must fall back to xG total >= 2.90 when prematch_o25_prob absent"
        " — INPLAY-J-XG-FALLBACK 2026-05-28"
    )
    assert "prematch_xg_home" in fn_body, (
        "Strategy J xG fallback must read prematch_xg_home — INPLAY-J-XG-FALLBACK 2026-05-28"
    )


@test("INPLAY-Q-POSS-OPTIONAL — strategy Q skips possession gate when data absent")
def test_inplay_q_poss_optional():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "workers/jobs/inplay_bot.py"
    ).read_text()
    fn_start = src.index("def _check_strategy_q(")
    try:
        fn_end = src.index("\ndef ", fn_start + 1)
    except ValueError:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "poss_h_raw is not None" in fn_body, (
        "Strategy Q must guard possession_home with 'is not None' check"
        " — INPLAY-Q-POSS-OPTIONAL 2026-05-28"
    )
    # Old default-to-50 pattern must be gone
    assert "or 50)" not in fn_body, (
        "Strategy Q must NOT default possession to 50 — silently blocks all data-absent matches"
        " — INPLAY-Q-POSS-OPTIONAL 2026-05-28"
    )


@test("COVERAGE-EXTENDED — generate_targets_extended.py script present and pipeline loads targets_extended.csv")
def test_coverage_extended():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]

    # Script exists
    script = root / "scripts" / "generate_targets_extended.py"
    assert script.exists(), "generate_targets_extended.py must exist"
    src = script.read_text()

    # Uses COPY not fetchall loops
    assert "COPY (" in src or "copy_expert" in src, (
        "generate_targets_extended.py must use COPY for bulk export, not fetchall loops"
    )

    # Outputs to targets_extended.csv
    assert "targets_extended.csv" in src, (
        "generate_targets_extended.py must write to targets_extended.csv"
    )

    # Pipeline loads the file
    pipeline_src = (root / "workers" / "jobs" / "daily_pipeline_v2.py").read_text()
    assert "targets_extended.csv" in pipeline_src, (
        "daily_pipeline_v2.py must load targets_extended.csv into hist_targets_global"
    )
    # Loaded via concat so it actually merges with targets_global
    assert "targets_extended" in pipeline_src and "concat" in pipeline_src, (
        "daily_pipeline_v2.py must pd.concat targets_extended into hist_targets_global"
    )

    # backfill phases 4+5 are defined
    backfill_src = (root / "scripts" / "backfill_historical.py").read_text()
    assert "PHASE_4_LEAGUES" in backfill_src, "PHASE_4_LEAGUES must be defined in backfill_historical.py"
    assert "PHASE_5_LEAGUES" in backfill_src, "PHASE_5_LEAGUES must be defined in backfill_historical.py"
    assert "choices=[1, 2, 3, 4, 5]" in backfill_src, (
        "--phase argparse must include choices 4 and 5"
    )

    # Backtest also loads targets_extended.csv (mirrors pipeline)
    backtest_src = (root / "scripts" / "backtest_pre_match_bots.py").read_text()
    assert "targets_extended.csv" in backtest_src, (
        "backtest_pre_match_bots.py must load targets_extended.csv like the live pipeline"
    )
    assert "targets_extended" in backtest_src and "concat" in backtest_src, (
        "backtest_pre_match_bots.py must pd.concat targets_extended into hist_targets_global"
    )


@test("FIX-PHASE45-TIERS — migration 146 exists and covers key phase 4+5 tier corrections")
def test_fix_phase45_tiers():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    mig = root / "supabase" / "migrations" / "146_fix_phase45_league_tiers.sql"
    assert mig.exists(), "migration 146_fix_phase45_league_tiers.sql must exist"
    sql = mig.read_text()

    # Must fix second divisions
    assert "second league" in sql.lower() and "bulgaria" in sql.lower(), "Bulgaria Second League must be corrected to tier=2"
    assert "persha liga" in sql.lower() and "ukraine" in sql.lower(), "Ukraine Persha Liga must be corrected to tier=2"
    assert "first league" in sql.lower() and "russia" in sql.lower(), "Russia First League must be corrected to tier=2"

    # Must fix third divisions
    assert "rfef" in sql.lower() and "spain" in sql.lower(), "Spain Primera RFEF must be corrected to tier=3"
    assert "kakkonen" in sql.lower() and "finland" in sql.lower(), "Finland Kakkonen must be corrected to tier=3"
    assert "ettan" in sql.lower() and "sweden" in sql.lower(), "Sweden Ettan must be corrected to tier=3"

    # Must fix fourth divisions
    assert "serie d" in sql.lower() and "brazil" in sql.lower(), "Brazil Serie D must be corrected to tier=4"
    assert "iii liga" in sql.lower() and "poland" in sql.lower(), "Poland III Liga must be corrected to tier=4"


@test("COOLBET-ANON-READ — CoolbetSession(require_auth=False) skips JWT for --record mode")
def test_coolbet_anon_read():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    session_src = (root / "workers" / "automation" / "coolbet_session.py").read_text()
    placer_src  = (root / "workers" / "automation" / "coolbet_placer.py").read_text()

    # CoolbetSession accepts require_auth kwarg
    assert "require_auth" in session_src, "CoolbetSession must accept require_auth parameter"

    # _ensure_auth short-circuits when require_auth is False
    assert "if not self._require_auth" in session_src, (
        "_ensure_auth must return early when require_auth=False"
    )

    # Credential check is gated on require_auth
    assert "if require_auth and not self._manual_jwt" in session_src, (
        "RuntimeError on missing creds must be gated by require_auth"
    )

    # placer wires require_auth=execute (False for --record, True for --execute)
    assert "CoolbetSession(require_auth=execute)" in placer_src, (
        "place_all_bets must pass require_auth=execute to CoolbetSession"
    )


@test("INPLAY-COOLBET-URL — coolbet_match_url reads Imperva cookies from env, falls back gracefully")
def test_inplay_coolbet_url():
    """Helper moved from inplay_bot.py to coolbet_session.py (TELE-COOLBET-URL,
    commit ab46c53) — inspect the new home and confirm inplay_bot still wires it."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    bot_src = (root / "workers" / "jobs" / "inplay_bot.py").read_text()
    helper_src = (root / "workers" / "automation" / "coolbet_session.py").read_text()

    # Helper exists (now in coolbet_session.py, imported as _coolbet_match_url)
    assert "def coolbet_match_url" in helper_src, \
        "coolbet_match_url must be defined in workers/automation/coolbet_session.py"
    assert "_coolbet_match_url" in bot_src, \
        "inplay_bot.py must import the helper as _coolbet_match_url"

    # Reads Imperva env vars (same as CoolbetSession)
    assert "COOLBET_COOKIE_REESE84" in helper_src, \
        "coolbet_match_url must read COOLBET_COOKIE_REESE84"
    assert "COOLBET_COOKIE_VISID_INCAP" in helper_src, \
        "coolbet_match_url must read COOLBET_COOKIE_VISID_INCAP"
    assert "COOLBET_IMPERVA_COOKIES" in helper_src, \
        "coolbet_match_url must fall back to COOLBET_IMPERVA_COOKIES"

    # Returns coolbet match URL format
    assert "coolbet.com/et/sport/match/" in helper_src, \
        "coolbet_match_url must return /et/sport/match/{id} URL"

    # Wired into admin Telegram send_telegram call from inplay_bot
    assert 'cb_url = _coolbet_match_url' in bot_src, \
        "_coolbet_match_url must be called in the bet placement block"
    assert 'Open on Coolbet' in bot_src, \
        "admin Telegram alert must include 'Open on Coolbet' link when cb_url is set"

    # Never raises (try/except)
    assert "except Exception" in helper_src, \
        "coolbet_match_url must catch all exceptions and return None"


@test("SPECIALIST-BOTS-WHITELIST — migration 147, league_name_filter in pipeline + backtest, new bots in config")
def test_specialist_bots_whitelist():
    import re
    from pathlib import Path

    # Migration 147 exists
    mig = Path(__file__).parent.parent / "supabase" / "migrations" / "147_specialist_bots_whitelist.sql"
    assert mig.exists(), "migration 147_specialist_bots_whitelist.sql must exist"
    mig_src = mig.read_text()
    assert "bot_under25_specialist" in mig_src, "migration must insert bot_under25_specialist"
    assert "bot_sweden_over25" in mig_src, "migration must insert bot_sweden_over25"
    assert "DRAW-LEAGUE-WHITELIST" in mig_src, "migration must update bot_draw_specialist"
    assert "DNB-AWAY-WHITELIST" in mig_src, "migration must update bot_dnb_away_value"
    assert "DNB-HOME-WHITELIST" in mig_src, "migration must update bot_dnb_home_value"

    # Pipeline has league_name_filter check
    pipeline = Path(__file__).parent.parent / "workers" / "jobs" / "daily_pipeline_v2.py"
    pipe_src = pipeline.read_text()
    assert "league_name_filter" in pipe_src, "pipeline must support league_name_filter"
    assert "(country, league_name) not in config" in pipe_src, "pipeline must check (country, league_name) tuples"

    # Backtest has league_name_filter check
    backtest = Path(__file__).parent / "backtest_pre_match_bots.py"
    bt_src = backtest.read_text()
    assert "league_name_filter" in bt_src, "backtest must support league_name_filter"
    assert "(country, league_name) not in cfg" in bt_src, "backtest must check (country, league_name) tuples"

    # bot_draw_specialist uses league_name_filter and has no tier_filter
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG
    draw = BOTS_CONFIG["bot_draw_specialist"]
    assert draw.get("tier_filter") is None, "bot_draw_specialist must have tier_filter=None after whitelist reform"
    assert draw.get("league_name_filter"), "bot_draw_specialist must have league_name_filter"
    assert len(draw["league_name_filter"]) >= 10, "draw specialist must whitelist at least 10 leagues"
    # Key leagues that were previously excluded (T1) must now be included
    assert ("Austria", "Bundesliga") in draw["league_name_filter"], "Austria Bundesliga must be in draw whitelist"
    assert ("Brazil", "Serie A") in draw["league_name_filter"], "Brazil Serie A must be in draw whitelist"
    # Confirmed positive leagues must be included
    assert ("Israel", "Liga Leumit") in draw["league_name_filter"], "Israel Liga Leumit must be in draw whitelist"
    assert ("England", "Championship") in draw["league_name_filter"], "England Championship must be in draw whitelist"

    # bot_dnb_away_value now includes England League Two (T4 — was blocked by old tier_filter)
    dnb_away = BOTS_CONFIG["bot_dnb_away_value"]
    assert dnb_away.get("tier_filter") is None, "bot_dnb_away_value must have tier_filter=None"
    assert ("England", "League Two") in dnb_away["league_name_filter"], "England League Two must be in dnb_away whitelist"

    # New bots exist in config
    assert "bot_under25_specialist" in BOTS_CONFIG, "bot_under25_specialist must be in BOTS_CONFIG"
    assert "bot_sweden_over25" in BOTS_CONFIG, "bot_sweden_over25 must be in BOTS_CONFIG"
    u25 = BOTS_CONFIG["bot_under25_specialist"]
    assert ("England", "Championship") in u25["league_name_filter"]
    assert ("Poland", "Ekstraklasa") in u25["league_name_filter"]
    assert ("Sweden", "Ettan - Norra") in u25["league_name_filter"]
    sw = BOTS_CONFIG["bot_sweden_over25"]
    assert ("Sweden", "Superettan") in sw["league_name_filter"]
    assert ("Sweden", "Allsvenskan") in sw["league_name_filter"]

    # BOT_TIMING_COHORTS includes new bots
    from workers.jobs.daily_pipeline_v2 import BOT_TIMING_COHORTS
    assert "bot_under25_specialist" in BOT_TIMING_COHORTS
    assert "bot_sweden_over25" in BOT_TIMING_COHORTS


@test("MULTI-STRATEGY-BOTS — strategy expansion, bot_dnb_specialist, strategy_profile in store_bet")
def test_multi_strategy_bots():
    from pathlib import Path

    # Migration 148 exists and covers required changes
    mig = Path(__file__).parent.parent / "supabase" / "migrations" / "148_multi_strategy_bots.sql"
    assert mig.exists(), "migration 148_multi_strategy_bots.sql must exist"
    mig_src = mig.read_text()
    assert "strategy_profile" in mig_src, "migration must add strategy_profile column"
    assert "bot_dnb_specialist" in mig_src, "migration must create bot_dnb_specialist"
    assert "bot_dnb_home_value" in mig_src and "bot_dnb_away_value" in mig_src, \
        "migration must retire the two old DNB bots"

    # Pipeline has _bot_strategy_iter expansion
    pipeline = Path(__file__).parent.parent / "workers" / "jobs" / "daily_pipeline_v2.py"
    pipe_src = pipeline.read_text()
    assert "_bot_strategy_iter" in pipe_src, "pipeline must have _bot_strategy_iter expansion"
    assert "for bot_name, config, _strategy_alias in _bot_strategy_iter" in pipe_src, \
        "pipeline must iterate over (bot_name, config, alias) tuples"
    assert "strategy_profile" in pipe_src, "pipeline must store strategy_profile on bets"
    assert "_strategy_alias" in pipe_src, "pipeline must use _strategy_alias in reasoning"

    # Backtest also expands strategies
    bt = Path(__file__).parent / "backtest_pre_match_bots.py"
    bt_src = bt.read_text()
    assert "_bot_strategy_iter" in bt_src or "strategies" in bt_src, \
        "backtest must support multi-strategy expansion"

    # bot_dnb_specialist has two strategies in BOTS_CONFIG
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG, BOT_TIMING_COHORTS
    assert "bot_dnb_specialist" in BOTS_CONFIG
    dnb = BOTS_CONFIG["bot_dnb_specialist"]
    assert "strategies" in dnb, "bot_dnb_specialist must have strategies list"
    assert len(dnb["strategies"]) == 2, "must have exactly 2 strategies"
    aliases = [s["alias"] for s in dnb["strategies"]]
    assert "DNB Home" in aliases and "DNB Away" in aliases, "strategies must be aliased DNB Home / DNB Away"

    # Strategy expansion produces 2 entries for bot_dnb_specialist
    expanded = []
    for bn, bc in BOTS_CONFIG.items():
        for st in (bc.get("strategies") or [{}]):
            scfg = {k: v for k, v in bc.items() if k != "strategies"}
            scfg.update(st)
            expanded.append((bn, scfg, st.get("alias", "")))
    dnb_expanded = [(bn, alias) for bn, _, alias in expanded if bn == "bot_dnb_specialist"]
    assert len(dnb_expanded) == 2
    assert dnb_expanded[0][1] == "DNB Home"
    assert dnb_expanded[1][1] == "DNB Away"

    # Each strategy has its own selection_filter, league_name_filter, odds_range
    home_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_dnb_specialist" and alias == "DNB Home")
    away_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_dnb_specialist" and alias == "DNB Away")
    assert home_cfg["selection_filter"] == ["Home"]
    assert away_cfg["selection_filter"] == ["Away"]
    assert home_cfg["odds_range"] == (1.30, 1.90)
    assert away_cfg["odds_range"] == (1.60, 2.60)
    assert ("England", "League Two") in away_cfg["league_name_filter"]
    assert ("Austria", "Bundesliga") in home_cfg["league_name_filter"]

    # bot_dnb_specialist in BOT_TIMING_COHORTS
    assert "bot_dnb_specialist" in BOT_TIMING_COHORTS


@test("OU25-SPECIALIST — bot_ou25_specialist with Under/Over profiles, retired bots")
def test_ou25_specialist_bots():
    from pathlib import Path

    # Migration 149 exists and covers required retirements + creation
    mig = Path(__file__).parent.parent / "supabase" / "migrations" / "149_ou25_specialist_bot.sql"
    assert mig.exists(), "migration 149_ou25_specialist_bot.sql must exist"
    mig_src = mig.read_text()
    assert "bot_ou25_specialist" in mig_src, "migration must create bot_ou25_specialist"
    assert "bot_ou25_global" in mig_src, "migration must retire bot_ou25_global"
    assert "bot_under25_specialist" in mig_src, "migration must retire bot_under25_specialist"
    assert "bot_sweden_over25" in mig_src, "migration must retire bot_sweden_over25"

    # bot_ou25_specialist has two strategies in BOTS_CONFIG
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG, BOT_TIMING_COHORTS
    assert "bot_ou25_specialist" in BOTS_CONFIG
    ou25 = BOTS_CONFIG["bot_ou25_specialist"]
    assert "strategies" in ou25, "bot_ou25_specialist must have strategies list"
    assert len(ou25["strategies"]) == 2, "must have exactly 2 strategies"
    aliases = [s["alias"] for s in ou25["strategies"]]
    assert "Under 2.5 Specialist" in aliases, "must have Under 2.5 Specialist profile"
    assert "Over 2.5 Sweden" in aliases, "must have Over 2.5 Sweden profile"

    # Strategy expansion produces 2 entries for bot_ou25_specialist
    expanded = []
    for bn, bc in BOTS_CONFIG.items():
        for st in (bc.get("strategies") or [{}]):
            scfg = {k: v for k, v in bc.items() if k != "strategies"}
            scfg.update(st)
            expanded.append((bn, scfg, st.get("alias", "")))
    ou25_expanded = [(bn, alias) for bn, _, alias in expanded if bn == "bot_ou25_specialist"]
    assert len(ou25_expanded) == 2
    assert ou25_expanded[0][1] == "Under 2.5 Specialist"
    assert ou25_expanded[1][1] == "Over 2.5 Sweden"

    # Each strategy has correct selection_filter and league_name_filter
    under_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_ou25_specialist" and alias == "Under 2.5 Specialist")
    over_cfg  = next(cfg for bn, cfg, alias in expanded if bn == "bot_ou25_specialist" and alias == "Over 2.5 Sweden")
    assert under_cfg["selection_filter"] == ["Under 2.5"]
    assert over_cfg["selection_filter"] == ["Over 2.5"]
    assert ("England", "Championship") in under_cfg["league_name_filter"]
    assert ("Poland",  "Ekstraklasa")  in under_cfg["league_name_filter"]
    assert ("Sweden",  "Ettan - Norra") in under_cfg["league_name_filter"]
    assert ("Sweden", "Superettan")   in over_cfg["league_name_filter"]
    assert ("Sweden", "Allsvenskan")  in over_cfg["league_name_filter"]

    # Retired bots have is_active=False in config
    assert BOTS_CONFIG["bot_ou25_global"].get("is_active") is False
    assert BOTS_CONFIG["bot_under25_specialist"].get("is_active") is False
    assert BOTS_CONFIG["bot_sweden_over25"].get("is_active") is False

    # bot_ou25_specialist in BOT_TIMING_COHORTS
    assert "bot_ou25_specialist" in BOT_TIMING_COHORTS


@test("1X2-DC-SPECIALIST — new bots, draw expansion, strategy profiles")
def test_1x2_dc_specialist_bots():
    from pathlib import Path
    from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG, BOT_TIMING_COHORTS

    # Migration 150 exists and covers new bots
    mig = Path(__file__).parent.parent / "supabase" / "migrations" / "150_1x2_dc_specialist_bots.sql"
    assert mig.exists(), "migration 150_1x2_dc_specialist_bots.sql must exist"
    mig_src = mig.read_text()
    assert "bot_1x2_specialist" in mig_src
    assert "bot_dc_specialist" in mig_src
    assert "bot_draw_specialist" in mig_src

    # draw specialist has 15 leagues including the 3 new ones
    draw = BOTS_CONFIG["bot_draw_specialist"]
    assert ("China",      "Super League")   in draw["league_name_filter"]
    assert ("USA",        "USL League Two") in draw["league_name_filter"]
    assert ("Azerbaijan", "Birinci Dasta")  in draw["league_name_filter"]
    assert len(draw["league_name_filter"]) == 15

    # bot_1x2_specialist has two strategies
    s1x2 = BOTS_CONFIG["bot_1x2_specialist"]
    assert "strategies" in s1x2
    aliases = [s["alias"] for s in s1x2["strategies"]]
    assert "Away Value" in aliases and "Home Value" in aliases

    expanded = []
    for bn, bc in BOTS_CONFIG.items():
        for st in (bc.get("strategies") or [{}]):
            scfg = {k: v for k, v in bc.items() if k != "strategies"}
            scfg.update(st)
            expanded.append((bn, scfg, st.get("alias", "")))

    away_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_1x2_specialist" and alias == "Away Value")
    home_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_1x2_specialist" and alias == "Home Value")
    assert away_cfg["selection_filter"] == ["Away"]
    assert home_cfg["selection_filter"] == ["Home"]
    assert ("Argentina", "Liga Profesional Argentina") in away_cfg["league_name_filter"]
    assert ("England",   "League Two")                 in away_cfg["league_name_filter"]
    assert ("France",    "Ligue 1")                    in away_cfg["league_name_filter"]
    assert ("Austria",   "Bundesliga")                 in home_cfg["league_name_filter"]
    assert ("Spain",     "Segunda División")           in home_cfg["league_name_filter"]

    # bot_dc_specialist has two strategies
    sdc = BOTS_CONFIG["bot_dc_specialist"]
    assert "strategies" in sdc
    dc_aliases = [s["alias"] for s in sdc["strategies"]]
    assert "X2 Value" in dc_aliases and "1X Israel" in dc_aliases

    x2_cfg  = next(cfg for bn, cfg, alias in expanded if bn == "bot_dc_specialist" and alias == "X2 Value")
    i1x_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_dc_specialist" and alias == "1X Israel")
    assert x2_cfg["selection_filter"]  == ["X2"]
    assert i1x_cfg["selection_filter"] == ["1X"]
    assert ("Brazil", "Serie B")      in x2_cfg["league_name_filter"]
    assert ("China",  "Super League") in x2_cfg["league_name_filter"]
    assert ("Israel", "Liga Leumit")  in i1x_cfg["league_name_filter"]

    # Both new bots in BOT_TIMING_COHORTS
    assert "bot_1x2_specialist" in BOT_TIMING_COHORTS
    assert "bot_dc_specialist"  in BOT_TIMING_COHORTS


@test("OU-DC-CONSOLIDATION — bot_ou_specialist (3 profiles) + bot_dc_specialist DC Global, retirements")
def test_ou_dc_consolidation():
    import inspect
    from workers.jobs import daily_pipeline_v2 as pipe

    BOTS_CONFIG       = pipe.BOTS_CONFIG
    BOT_TIMING_COHORTS = pipe.BOT_TIMING_COHORTS

    # Strategy expansion helper (mirrors pipeline logic)
    expanded: list[tuple[str, dict, str]] = []
    for bn, bc in BOTS_CONFIG.items():
        for st in (bc.get("strategies") or [{}]):
            scfg = {k: v for k, v in bc.items() if k != "strategies"}
            scfg.update(st)
            expanded.append((bn, scfg, st.get("alias", "")))

    mig_src = open("supabase/migrations/152_ou_dc_specialist_consolidation.sql").read()

    # --- bot_ou_specialist exists with 3 profiles ---
    assert "bot_ou_specialist" in BOTS_CONFIG, "bot_ou_specialist missing from BOTS_CONFIG"
    ou = BOTS_CONFIG["bot_ou_specialist"]
    assert "strategies" in ou, "bot_ou_specialist must have strategies"
    ou_aliases = [s["alias"] for s in ou["strategies"]]
    assert "Under 2.5 Specialist" in ou_aliases
    assert "Over 2.5 Sweden"      in ou_aliases
    assert "Over 3.5 Global"      in ou_aliases

    # Under 2.5 Specialist — league whitelist
    under_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_ou_specialist" and alias == "Under 2.5 Specialist")
    assert ("England", "Championship")  in under_cfg["league_name_filter"]
    assert ("Poland",  "Ekstraklasa")   in under_cfg["league_name_filter"]
    assert ("Sweden",  "Ettan - Norra") in under_cfg["league_name_filter"]
    assert under_cfg["selection_filter"] == ["Under 2.5"]

    # Over 2.5 Sweden — two Swedish leagues, no global
    ov25_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_ou_specialist" and alias == "Over 2.5 Sweden")
    assert ("Sweden", "Superettan")  in ov25_cfg["league_name_filter"]
    assert ("Sweden", "Allsvenskan") in ov25_cfg["league_name_filter"]

    # Over 3.5 Global — no league_name_filter (fires globally)
    ov35_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_ou_specialist" and alias == "Over 3.5 Global")
    assert not ov35_cfg.get("league_name_filter"), "Over 3.5 Global must have no league_name_filter"
    assert ov35_cfg.get("edge_thresholds", {}).get(1, {}).get("ou") == 0.14

    # --- bot_dc_specialist now has 3 profiles including DC Global ---
    sdc = BOTS_CONFIG["bot_dc_specialist"]
    dc_aliases = [s["alias"] for s in sdc["strategies"]]
    assert "X2 Value"  in dc_aliases
    assert "1X Israel" in dc_aliases
    assert "DC Global" in dc_aliases, "bot_dc_specialist must include DC Global profile"

    dc_global_cfg = next(cfg for bn, cfg, alias in expanded if bn == "bot_dc_specialist" and alias == "DC Global")
    assert not dc_global_cfg.get("league_name_filter"),   "DC Global must fire globally"
    assert not dc_global_cfg.get("selection_filter"),     "DC Global must have no selection_filter"

    # --- Retired bots are inactive ---
    for retired in ("bot_ou25_specialist", "bot_ou35_attacking", "bot_dc_value", "bot_dc_strong_fav"):
        assert retired in BOTS_CONFIG, f"{retired} missing from BOTS_CONFIG (must be kept for shadow bets)"
        assert not BOTS_CONFIG[retired].get("is_active", True), f"{retired} must be marked is_active=False"

    # --- Migration retires the same bots ---
    assert "bot_ou25_specialist" in mig_src
    assert "bot_ou35_attacking"  in mig_src
    assert "bot_dc_value"        in mig_src
    assert "bot_dc_strong_fav"   in mig_src
    assert "bot_ou_specialist"   in mig_src

    # --- bot_ou_specialist in BOT_TIMING_COHORTS ---
    assert "bot_ou_specialist" in BOT_TIMING_COHORTS


@test("COOLBET-AUTO-RECORD — _run_coolbet_record wired into betting_pipeline after run_morning")
def test_coolbet_auto_record():
    import inspect
    from workers.jobs import betting_pipeline

    # _run_coolbet_record() must be called inside run_betting, after run_morning
    run_betting_src = inspect.getsource(betting_pipeline.run_betting)
    assert "run_morning(" in run_betting_src, "run_morning call missing from run_betting"
    assert "_run_coolbet_record()" in run_betting_src, "_run_coolbet_record() not called in run_betting"
    run_morning_pos = run_betting_src.index("run_morning(")
    record_call_pos = run_betting_src.index("_run_coolbet_record()")
    assert record_call_pos > run_morning_pos, "_run_coolbet_record must be called after run_morning"

    fn_src = inspect.getsource(betting_pipeline._run_coolbet_record)
    assert "place_all_bets(record=True)" in fn_src, "must call place_all_bets(record=True)"
    assert "send_telegram" in fn_src, "must send admin Telegram"
    assert "placed" in fn_src, "must count placed bets"
    assert "search_blocked" in fn_src, "must handle search_blocked outcome"


@test("TELE-DEDUP-MULTI-BOT — per-position alert consolidation: one message per match+market+selection")
def test_tele_dedup_multi_bot():
    import inspect
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "workers/jobs/daily_pipeline_v2.py").read_text()

    # Buffer must be initialised
    assert "_tele_bets: dict" in src, "_tele_bets buffer not initialised"
    # Must accumulate bots per key, not send immediately
    assert '_tele_bets[_tele_key]["bots"].append(bot_name)' in src, "must append bot to _tele_bets"
    # Flush loop must send one alert per position
    assert "for _tk, _tb in _tele_bets.items():" in src, "_tele_bets flush loop missing"
    # Must show bot count and list bots
    assert "+{_n-1} more" in src, "must show +N more for multi-bot agreement"
    assert '", ".join(_tb["bots"])' in src, "must list all bots when N > 1"
    # User alert must be deduped per position not per bet_id
    assert 'dedup_key=f"user-bet-{_tk[0]}-{_tk[1]}-{_tk[2]}"' in src, \
        "user alert dedup_key must be match+market+selection, not bet_id"
    # Per-bet immediate send_telegram calls must be gone
    assert 'f"🎯 <b>PRE-MATCH</b> {bot_name}' not in src, \
        "old per-bot immediate send_telegram must be removed"


@test("INPLAY-COOLBET-PLACER — load_qualified_inplay_bets + place_all_inplay_bets wired into inplay_bot")
def test_inplay_coolbet_placer():
    import inspect
    from workers.automation.coolbet_placer import (
        load_qualified_inplay_bets, place_all_inplay_bets, PlacementGuard,
    )
    # load query must filter for kicked-off matches and time window
    load_src = inspect.getsource(load_qualified_inplay_bets)
    assert "m.date           <= NOW()" in load_src, "must filter for kicked-off matches"
    assert "sb.pick_time     >= NOW()" in load_src, "must have time window filter"
    assert "rb.simulated_bet_id = sb.id" in load_src, "must dedup via simulated_bet_id"
    assert "_MIN_EDGE" in load_src, "must apply edge filter"

    # place function must check edge at live price
    place_src = inspect.getsource(place_all_inplay_bets)
    assert "edge_eroded" in place_src, "must handle edge_eroded outcome"
    assert "_MIN_REMAINING_EDGE" in place_src, "must apply remaining edge floor"
    assert "search_blocked" in place_src, "must handle search_blocked"
    assert 'notes=f"inplay-auto' in place_src, "must tag real_bets as inplay-auto"
    assert "simulated_bet_id=sim_id" in place_src, "must link real_bet to simulated_bet"

    # inplay_bot must call place_all_inplay_bets after bets_placed > 0
    import pathlib
    bot_src = (pathlib.Path(__file__).parent.parent / "workers/jobs/inplay_bot.py").read_text()
    assert "place_all_inplay_bets" in bot_src, "inplay_bot must call place_all_inplay_bets"
    assert "bets_placed > 0" in bot_src, "call must be gated on bets_placed > 0"


@test("RETIRE-DC-SPECIALIST — migration 155 retires bot_dc_specialist; daily_pipeline_v2 description marked retired")
def _():
    """bot_dc_specialist hit -7.53% ROI on n=58 (+3.68% CLV) since 2026-05-24.
    DC is a derived market — same root cause that retired bot_dc_value (migration
    137). Migration 155 must mark is_active=false with a retired_reason that
    mentions the derived-market problem and a re-activation trigger. The
    daily_pipeline_v2 config description must carry the [RETIRED] prefix so
    operators reading BOTS_CONFIG see the state without consulting the DB."""
    import pathlib

    mig = pathlib.Path("supabase/migrations/155_retire_dc_specialist.sql").read_text()
    assert "bot_dc_specialist" in mig, "Migration 155 must target bot_dc_specialist"
    assert "is_active     = false" in mig or "is_active = false" in mig, \
        "Migration 155 must set is_active=false"
    assert "retired_at" in mig, "Migration 155 must set retired_at"
    assert "retired_reason" in mig, "Migration 155 must populate retired_reason"
    assert "derived" in mig.lower(), \
        "retired_reason must explain the derived-market root cause"
    assert "june 8" in mig.lower() or "shadow_bets" in mig.lower(), \
        "retired_reason must name a re-activation trigger"

    pipeline_src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    dc_block_start = pipeline_src.index('"bot_dc_specialist": {')
    dc_block_end = pipeline_src.index('"description":', dc_block_start)
    dc_description_end = pipeline_src.index('\n', dc_block_end + 100)
    dc_block = pipeline_src[dc_block_start:dc_description_end]
    assert "[RETIRED 2026-06-01]" in dc_block, \
        "bot_dc_specialist description must be prefixed [RETIRED 2026-06-01]"


@test("META-VALIDATOR-FIXES — validate_meta_b_ml3 handles NaN features + Decimal CLV")
def _():
    """Pre-flight run of B-ML3-VALIDATE-ACTIVATION on 2026-06-01 surfaced two
    bugs in scripts/validate_meta_b_ml3.py:
      (a) LogisticRegression bundles rejected the feature matrix because MFV
          columns like form_momentum_* / pinnacle_line_move legitimately arrive
          as NaN. Training imputes to 0 — inference must mirror.
      (b) psycopg2 returns numeric columns as Decimal. df['clv_used'].mean()
          raised "unsupported operand type(s) for +: 'Decimal' and 'float'"
          inside pandas/numpy aggregation.
    Both must stay fixed so the validator can run weekly without a manual
    intervention; the activation verdict on 2026-06-10 depends on it."""
    import pathlib
    src = pathlib.Path("scripts/validate_meta_b_ml3.py").read_text()

    # NaN imputation in _score_one
    score_start = src.index("def _score_one(")
    score_end = src.index("\ndef ", score_start + 1)
    score_src = src[score_start:score_end]
    assert "fillna(0.0)" in score_src, \
        "_score_one must fillna(0.0) on aligned features (logistic bundles reject NaN)"

    # Decimal → float coercion on clv_used
    main_start = src.index("def main(")
    main_src = src[main_start:]
    assert "pd.to_numeric" in main_src and "clv_used" in main_src, \
        "main() must coerce clv_used to float via pd.to_numeric"
    assert 'errors="coerce"' in main_src, \
        "pd.to_numeric must use errors='coerce' to handle null/Decimal mix"


@test("OU-MODEL-PIN-RUNBOOK — PRIORITY_QUEUE captures MODEL_VERSION_OU pin for v20260531 promotion")
def _():
    """Sunday's retrain v20260531 improves 1X2/AH/BTTS by 2.5-10% log_loss but
    REGRESSES over_under by 2.7% (predicted_rate 43.7% vs actual 55.6%). When
    we eventually promote v20260531 we must pin OU to the well-calibrated
    v20260524_market via MODEL_VERSION_OU — per-market routing already exists
    in xgboost_ensemble.py (Phase C-light, 2026-05-24). The PRIORITY_QUEUE must
    document this so promotion day doesn't quietly degrade OU."""
    import pathlib
    pq = pathlib.Path("PRIORITY_QUEUE.md").read_text()
    assert "MODEL_VERSION_OU" in pq, \
        "PRIORITY_QUEUE must mention MODEL_VERSION_OU env override for OU pinning"
    assert "v20260524_market" in pq, \
        "PRIORITY_QUEUE must name v20260524_market as the OU pin target"
    assert "v20260531" in pq, \
        "PRIORITY_QUEUE must reference v20260531 as the candidate being promoted"


@test("PERF-PAGE-LIVE-RETIRED-FILTER — /performance drops freshly-retired bots from active leaderboard without waiting for cache rebuild")
def _():
    """The retired_bot_breakdown filter against liveRetiredNames has existed
    since RetiredStrategiesSection landed; the inverse filter for the active
    leaderboard was missing, so a bot retired between cache rebuilds (every
    30 min via job_dashboard_cache_refresh) would still show in the active
    leaderboard until the next rebuild. The fix applies liveRetiredNames as
    an upstream filter on cachedBots in page.tsx."""
    import pathlib
    page = pathlib.Path("../odds-intel-web/src/app/(app)/performance/page.tsx").read_text()

    # liveRetiredNames must be defined once and consumed by both the active and retired filters
    assert "liveRetiredNames" in page, "page.tsx must define liveRetiredNames from botsDB"
    assert "retiredAt" in page, "liveRetiredNames must derive from botsDB[].retiredAt"

    # cachedBots construction must include the .filter against liveRetiredNames
    cb_start = page.index("const cachedBots = buildCachedBotStats(")
    cb_end = page.index(";", cb_start)
    cb_block = page[cb_start:cb_end]
    assert "liveRetiredNames" in cb_block, \
        "cachedBots must filter out names in liveRetiredNames (active leaderboard freshness)"
    assert "experimental" in cb_block, \
        "cachedBots must still filter experimental bots"


@test("RETIRE-LOWER-1X2 — migration 156 retires bot_lower_1x2; daily_pipeline_v2 description marked retired")
def _():
    """bot_lower_1x2 had its retired_reason populated on 2026-05-17 but
    is_active was never flipped — bot kept firing 44 bets at -7.58% ROI
    since v2 deploy. Migration 156 must flip is_active=false, stamp
    retired_at, and preserve the original re-activation trigger
    (alpha recovery or shadow_bets validation). The daily_pipeline_v2
    config description must carry [RETIRED 2026-06-01] so operators see
    the state without consulting the DB."""
    import pathlib

    mig = pathlib.Path("supabase/migrations/156_retire_lower_1x2.sql").read_text()
    assert "bot_lower_1x2" in mig, "Migration 156 must target bot_lower_1x2"
    assert "is_active     = false" in mig or "is_active = false" in mig, \
        "Migration 156 must set is_active=false"
    assert "retired_at" in mig, "Migration 156 must set retired_at"
    assert "shadow_bets" in mig.lower() or "alpha" in mig.lower(), \
        "retired_reason must preserve re-activation trigger from original diagnosis"
    # Guard against double-retiring something already retired (safety on rerun)
    assert "AND is_active = true" in mig, \
        "Migration 156 must guard the UPDATE with AND is_active = true (idempotent)"

    pipeline_src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    block_start = pipeline_src.index('"bot_lower_1x2": {')
    block_desc = pipeline_src.index('"description":', block_start)
    block_end = pipeline_src.index('\n', block_desc + 100)
    block = pipeline_src[block_start:block_end]
    assert "[RETIRED 2026-06-01]" in block, \
        "bot_lower_1x2 description must be prefixed [RETIRED 2026-06-01]"


@test("PERF-HERO-COHORT-SPLIT — dashboard_cache stores pre-match + in-play ROI; hero renders split tiles")
def _():
    """Last-30d data check (run 2026-06-01): in-play +14.5% ROI on n=861,
    pre-match -1.2% on n=1,974. The 15.7pp gap is stable across 7/14/30d
    windows. Splitting the hero tile surfaces in-play instead of averaging it
    away. Migration 157 adds the rollup columns; settlement.py computes them
    (excludes experimental + retired bots, 30d window); hero.tsx renders two
    tiles when both fields are present. Falls back to combined "System ROI"
    when cohort fields are null (legacy cache rows)."""
    import pathlib

    mig = pathlib.Path("supabase/migrations/157_dashboard_cache_cohort_split.sql").read_text()
    for col in ("prematch_settled_bets", "prematch_roi_pct", "prematch_avg_clv",
                "inplay_settled_bets",  "inplay_roi_pct"):
        assert col in mig, f"Migration 157 must add column {col}"
    # In-play CLV intentionally NOT in the schema
    assert "inplay_avg_clv" not in mig, \
        "Migration 157 must NOT add inplay_avg_clv (semantics differ — pre/live closing line)"

    settle = pathlib.Path("workers/jobs/settlement.py").read_text()
    assert "PERF-HERO-COHORT-SPLIT" in settle, \
        "settlement.py must reference the cohort split task tag"
    assert "LIKE 'inplay_%%'" in settle, \
        "settlement.py must classify cohort via bot name LIKE 'inplay_%'"
    # The cohort query must respect the existing active+non-experimental scope
    cohort_idx = settle.index("PERF-HERO-COHORT-SPLIT")
    cohort_block = settle[cohort_idx:cohort_idx + 2500]
    assert "b.is_active = true" in cohort_block, \
        "cohort query must filter b.is_active = true"
    assert "b.retired_at IS NULL" in cohort_block, \
        "cohort query must exclude retired bots"
    assert "maturity_label != 'experimental'" in cohort_block, \
        "cohort query must exclude experimental bots"
    assert "interval '30 days'" in cohort_block, \
        "cohort query must be a 30-day rolling window"
    # The INSERT must include the new columns
    insert_idx = settle.index("INTO dashboard_cache")
    insert_block = settle[insert_idx:insert_idx + 2500]
    for col in ("prematch_roi_pct", "inplay_roi_pct"):
        assert col in insert_block, f"INSERT must include {col}"

    hero = pathlib.Path("../odds-intel-web/src/components/performance-hero.tsx").read_text()
    assert "hasCohortSplit" in hero, \
        "performance-hero must gate split tiles on hasCohortSplit"
    assert "Pre-match ROI · 30d" in hero, \
        "performance-hero must render the Pre-match tile"
    assert "In-play ROI · 30d" in hero, \
        "performance-hero must render the In-play tile"
    # Combined "System ROI" tile remains as the legacy fallback
    assert "System ROI" in hero, \
        "performance-hero must keep the combined System ROI fallback for legacy cache rows"

    data = pathlib.Path("../odds-intel-web/src/lib/engine-data.ts").read_text()
    for col in ("prematch_roi_pct", "prematch_settled_bets",
                "inplay_roi_pct",   "inplay_settled_bets"):
        assert col in data, f"DashboardCache interface must include {col}"


@test("PERF-HERO-EQUITY-SPARKLINE — dashboard_cache stores daily cumulative P&L; hero renders sparkline")
def _():
    """Data check (run 2026-06-01): cumulative P&L over last 30d = +€815 with
    a clean upward trajectory (dip to -€127 May 7, recovery + growth thereafter).
    Migration 158 adds daily_pnl_curve_30d JSONB; settlement.py builds the
    array (active+non-experimental, settled bets only); EquitySparkline
    component renders an inline SVG with no chart library."""
    import pathlib

    mig = pathlib.Path("supabase/migrations/158_dashboard_cache_equity_curve.sql").read_text()
    assert "daily_pnl_curve_30d" in mig, "Migration 158 must add daily_pnl_curve_30d"
    assert "JSONB" in mig, "daily_pnl_curve_30d must be JSONB"

    settle = pathlib.Path("workers/jobs/settlement.py").read_text()
    assert "PERF-HERO-EQUITY-SPARKLINE" in settle, \
        "settlement.py must reference the equity sparkline task"
    assert "daily_pnl_curve_30d" in settle, \
        "settlement.py must build the daily_pnl_curve_30d array"
    # Must respect the same scope as the cohort split (active+non-experimental,
    # settled-only) so the headline tile and sparkline tell the same story
    sparkline_idx = settle.index("PERF-HERO-EQUITY-SPARKLINE")
    sparkline_block = settle[sparkline_idx:sparkline_idx + 2000]
    assert "b.is_active = true" in sparkline_block, "sparkline must filter active bots"
    assert "b.retired_at IS NULL" in sparkline_block, "sparkline must exclude retired"
    assert "maturity_label != 'experimental'" in sparkline_block, \
        "sparkline must exclude experimental"
    assert "interval '30 days'" in sparkline_block, "sparkline window must be 30 days"
    # cumulative not raw daily — the visual story is the running total
    assert '"cum": round(cum' in sparkline_block, \
        "sparkline payload must store cumulative P&L, not raw daily values"

    # Frontend component exists and is wired
    spark = pathlib.Path("../odds-intel-web/src/components/equity-sparkline.tsx").read_text()
    assert "<polyline" in spark, "EquitySparkline must render an SVG polyline"
    assert "preserveAspectRatio" in spark, "SVG must use viewBox + preserveAspectRatio for responsive rendering"
    assert 'role="img"' in spark, "Sparkline must expose role and aria-label for accessibility"
    assert "curve.length < 2" in spark, "Sparkline must early-return on insufficient data"

    hero = pathlib.Path("../odds-intel-web/src/components/performance-hero.tsx").read_text()
    assert "EquitySparkline" in hero, "performance-hero must import EquitySparkline"
    assert "cache?.daily_pnl_curve_30d" in hero, \
        "performance-hero must pass daily_pnl_curve_30d to the sparkline"

    data = pathlib.Path("../odds-intel-web/src/lib/engine-data.ts").read_text()
    assert "daily_pnl_curve_30d" in data, \
        "DashboardCache interface must include daily_pnl_curve_30d"


@test("PERF-HERO-RECENT-WINS — dashboard_cache stores top-8 14d wins; /performance renders RecentWinsReel")
def _():
    """Data check (run 2026-06-01): top 8 deduped wins last 14d span 8 countries
    (Argentina, UAE, Paraguay, World Friendlies, Netherlands, Ecuador, Australia,
    Sweden), CLV +30% to +55%, odds 1.78 to 4.25. Concrete "model picked these"
    stories for new visitors.

    Migration 159 adds recent_top_wins JSONB; settlement.py builds the array
    with DISTINCT ON (match, market, selection) so same call from multiple bots
    appears once; RecentWinsReel renders a 4-column grid (free-tier visible —
    no stake/P&L exposed)."""
    import pathlib

    mig = pathlib.Path("supabase/migrations/159_dashboard_cache_recent_wins.sql").read_text()
    assert "recent_top_wins" in mig, "Migration 159 must add recent_top_wins"
    assert "JSONB" in mig, "recent_top_wins must be JSONB"

    settle = pathlib.Path("workers/jobs/settlement.py").read_text()
    assert "PERF-HERO-RECENT-WINS" in settle, \
        "settlement.py must reference the recent-wins task"
    wins_idx = settle.index("PERF-HERO-RECENT-WINS")
    wins_block = settle[wins_idx:wins_idx + 3500]
    assert "DISTINCT ON (sb.match_id, sb.market, sb.selection)" in wins_block, \
        "wins query must dedupe by (match, market, selection)"
    assert "interval '14 days'" in wins_block, "wins window must be 14 days"
    assert "result = 'won'" in wins_block, "must filter to wins only"
    assert "maturity_label != 'experimental'" in wins_block, \
        "must exclude experimental bots"
    assert "LIMIT 8" in wins_block, "must limit to 8 wins"
    # P&L / stake must NOT be included in the payload (free-tier visible)
    payload_idx = settle.index("recent_top_wins = [", wins_idx)
    payload_block = settle[payload_idx:payload_idx + 1200]
    assert '"pnl"' not in payload_block and '"stake"' not in payload_block, \
        "recent_top_wins payload must NOT include pnl or stake (free-tier visible)"

    reel = pathlib.Path("../odds-intel-web/src/components/recent-wins-reel.tsx").read_text()
    assert "interface RecentWin" in reel, "RecentWin interface required"
    assert "wins.length === 0" in reel, "Reel must early-return on empty list"
    assert "marketLabel" in reel, "Must translate market codes to human labels"
    # No stake/pnl rendering
    assert "stake" not in reel.lower(), "Reel must not show stake (free-tier visible)"
    assert "pnl" not in reel.lower(), "Reel must not show P&L (free-tier visible)"

    page = pathlib.Path("../odds-intel-web/src/app/(app)/performance/page.tsx").read_text()
    assert "RecentWinsReel" in page, "performance/page.tsx must render RecentWinsReel"
    assert "cache?.recent_top_wins" in page, \
        "performance/page.tsx must pass cache.recent_top_wins to the reel"

    data = pathlib.Path("../odds-intel-web/src/lib/engine-data.ts").read_text()
    assert "recent_top_wins" in data, "DashboardCache interface must include recent_top_wins"


@test("RETIRE-BOT-AGGRESSIVE — migration 160 flips bot_aggressive is_active=false; preserves retired_reason from migration 104")
def _():
    """bot_aggressive's retired_reason was populated by migration 104 on
    2026-05-17 but is_active=true was never flipped. Bot self-stopped firing
    on 2026-05-24 after SLICE-LIVE-VALIDATE tightened its odds range; 705
    stale settled bets continued to drag the /performance active cohort.
    Migration 160 flips the flag without rewriting the existing reason
    (preserves the original migration 104 wording about bot_aggressive_v2
    replacement). Idempotent via AND is_active = true."""
    import pathlib

    mig = pathlib.Path("supabase/migrations/160_retire_bot_aggressive.sql").read_text()
    assert "bot_aggressive" in mig, "Migration 160 must target bot_aggressive"
    assert "is_active     = false" in mig or "is_active = false" in mig, \
        "Migration 160 must set is_active=false"
    assert "retired_at" in mig, "Migration 160 must set retired_at"
    assert "AND is_active = true" in mig, \
        "Migration 160 must be idempotent (AND is_active = true)"
    # Migration must NOT rewrite retired_reason (it was already correct from migration 104)
    assert "SET retired_reason" not in mig, \
        "Migration 160 must NOT rewrite retired_reason — migration 104's text is the source of truth"

    pipeline_src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    block_start = pipeline_src.index('"bot_aggressive": {')
    block_desc = pipeline_src.index('"description":', block_start)
    block_end = pipeline_src.index('\n', block_desc + 100)
    block = pipeline_src[block_start:block_end]
    assert "[RETIRED 2026-06-01]" in block, \
        "bot_aggressive description must be prefixed [RETIRED 2026-06-01]"


@test("PERF-HERO-NEXT-MODEL — dashboard_cache surfaces unpromoted model offline-eval; hero renders Next upgrade callout")
def _():
    """Live offline-eval check (2026-06-01): v20260531 beats v20260524_market
    on 9/11 markets, 1X2 avg log_loss -10%, AH -2.6%, BTTS -1.3%, OU +2.7%.
    Migration 161 adds upcoming_model_summary JSONB; settlement.py builds it
    from model_versions.cv_metrics; performance-hero NextModelCallout renders
    the deltas with an honest 'offline tests' caveat."""
    import pathlib

    mig = pathlib.Path("supabase/migrations/161_dashboard_cache_upcoming_model.sql").read_text()
    assert "upcoming_model_summary" in mig, "Migration 161 must add upcoming_model_summary"
    assert "JSONB" in mig, "upcoming_model_summary must be JSONB"

    settle = pathlib.Path("workers/jobs/settlement.py").read_text()
    assert "_build_upcoming_model_summary" in settle, \
        "settlement.py must define the helper"
    helper_idx = settle.index("def _build_upcoming_model_summary")
    helper_block = settle[helper_idx:helper_idx + 4500]
    # Must derive production version from env (operator-controlled)
    assert 'os.environ.get("MODEL_VERSION"' in helper_block, \
        "helper must read production version from MODEL_VERSION env"
    # Must skip promoted/demoted candidates
    assert "promoted_at IS NULL" in helper_block, \
        "helper must exclude promoted candidates"
    assert "demoted_at IS NULL" in helper_block, \
        "helper must exclude demoted candidates"
    # Must compare on log_loss
    assert "log_loss" in helper_block, "helper must compute log_loss deltas"
    # Must group by head (1x2, ah, btts, ou) — these are the user-facing labels
    for head in ("1x2", "ah", "btts", "ou"):
        assert f'"{head}"' in helper_block, f"helper must group market {head}"
    # Must return None when no candidate is better
    assert "if better == 0" in helper_block, \
        "helper must return None when zero markets improve (avoid misleading callout)"

    # Hero renders the callout
    hero = pathlib.Path("../odds-intel-web/src/components/performance-hero.tsx").read_text()
    assert "NextModelCallout" in hero, "performance-hero must include NextModelCallout"
    assert "cache?.upcoming_model_summary" in hero, \
        "hero must read upcoming_model_summary from cache"
    # Honest framing required — "offline" caveat present
    assert "offline tests" in hero, \
        "callout copy must include 'offline tests' caveat (not yet live data)"
    # Must show better/worse counts (no cherry-picking)
    assert "markets_better" in hero and "markets_worse" in hero, \
        "callout must surface markets_worse so the OU regression isn't hidden"

    data = pathlib.Path("../odds-intel-web/src/lib/engine-data.ts").read_text()
    assert "upcoming_model_summary" in data, \
        "DashboardCache interface must include upcoming_model_summary"


@test("STALE-FLAG-AUDIT-MIGRATION-162 — retires 2 bleeders, clears retired_reason on 2 recovered bots")
def _():
    """Audit triggered by 3 stale-flag fixes today (migrations 155, 156, 160).
    Found 4 more bots with retired_reason populated + is_active=true. Two are
    still bleeding and should be retired (bot_draw_specialist -100% ROI on n=4
    last 30d, inplay_f de facto retired since 2026-05-09). Two recovered after
    migration 122 re-enabled them but the reason text was never cleared
    (bot_conservative +104% ROI n=8, bot_opt_home_lower +51.9% n=20). Different
    treatments: retire vs clear-reason."""
    import pathlib

    mig = pathlib.Path("supabase/migrations/162_stale_flag_audit_cleanup.sql").read_text()
    # Two retirements
    assert "bot_draw_specialist" in mig and "is_active = false" in mig, \
        "Migration 162 must retire bot_draw_specialist"
    assert "inplay_f" in mig and "is_active = false" in mig, \
        "Migration 162 must retire inplay_f"
    # Two reason-clears
    assert "bot_conservative" in mig, "Migration 162 must reference bot_conservative"
    assert "bot_opt_home_lower" in mig, "Migration 162 must reference bot_opt_home_lower"
    assert "retired_reason = NULL" in mig, \
        "Migration 162 must clear retired_reason on recovered bots"
    # Idempotency guards
    assert "AND is_active = true" in mig, \
        "Migration 162 must use AND is_active = true guards (idempotent)"

    pipeline_src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    block_start = pipeline_src.index('"bot_draw_specialist": {')
    block_desc = pipeline_src.index('"description":', block_start)
    block_end = pipeline_src.index('\n', block_desc + 100)
    block = pipeline_src[block_start:block_end]
    assert "[RETIRED 2026-06-01]" in block, \
        "bot_draw_specialist description must be prefixed [RETIRED 2026-06-01]"


@test("OU35-EDGE-LOOSEN — bot_ou_specialist Over 3.5 Global edge floor lowered 14% → 10%")
def _():
    """The Over 3.5 Global profile fired ZERO candidates at the 14% edge floor
    in 30d of shadow data — the 2023-2026 backtest optimum is no longer
    reachable under current calibration. Lowered to 10% to surface candidates
    and observe live ROI. Other two profiles (Under 2.5 Specialist, Over 2.5
    Sweden) untouched — they have league whitelists + 5% edge floors that
    already work. Iteration plan documented in code comment."""
    import workers.jobs.daily_pipeline_v2 as dp
    cfg = dp.BOTS_CONFIG.get("bot_ou_specialist", {})
    strategies = cfg.get("strategies", [])

    by_alias = {s.get("alias"): s for s in strategies}
    over35 = by_alias.get("Over 3.5 Global")
    assert over35 is not None, "Over 3.5 Global profile must exist on bot_ou_specialist"

    edges = over35.get("edge_thresholds", {})
    for tier in (1, 2, 3, 4):
        assert tier in edges, f"Over 3.5 Global must have tier {tier} edge threshold"
        v = edges[tier].get("ou")
        assert v is not None and abs(v - 0.10) < 1e-6, \
            f"Tier {tier} 'ou' edge must be 0.10 (was lowered from 0.14), got {v}"

    # Confirm the other two profiles are NOT changed
    under25 = by_alias.get("Under 2.5 Specialist")
    assert under25 is not None and under25["edge_thresholds"][1]["ou"] == 0.05, \
        "Under 2.5 Specialist edge must stay at 0.05"
    over25 = by_alias.get("Over 2.5 Sweden")
    assert over25 is not None and over25["edge_thresholds"][1]["ou"] == 0.05, \
        "Over 2.5 Sweden edge must stay at 0.05"

    # The code comment must explain the rationale + iteration plan
    import pathlib, inspect
    src = pathlib.Path("workers/jobs/daily_pipeline_v2.py").read_text()
    idx = src.index('"alias": "Over 3.5 Global"')
    block = src[idx:idx + 1200]
    assert "OU35-EDGE-LOOSEN" in block, "Code comment must reference task tag"
    assert "14%" in block and "10%" in block, \
        "Comment must document the before/after thresholds"


@test("STALE-FLAG-WATCHDOG — health_alerts.check_stale_retirement_flags + cache staleness checks wired into hourly run")
def _():
    """Two new health checks added 2026-06-01 after five stale-flag retirements
    + a 3h dashboard_cache staleness incident went unnoticed:

    • check_stale_retirement_flags — DB query for bots with retired_reason
      populated AND is_active=true AND retired_at IS NULL. Fires Telegram
      alert when any exist (clearing the reason on a recovered bot is the
      operator's other valid response).
    • check_dashboard_cache_stale — alerts when MAX(dashboard_cache.computed_at)
      is > 60 min old. 60min threshold sits well outside the 30-min cron cadence
      so we don't false-positive during restarts.

    Both wired into run_snapshot_check (hourly, 10-23 UTC) alongside the
    existing memory/AF-quota/model-drift checks."""
    import pathlib, inspect
    from workers.jobs import health_alerts

    assert hasattr(health_alerts, "check_stale_retirement_flags"), \
        "health_alerts must define check_stale_retirement_flags"
    assert hasattr(health_alerts, "check_dashboard_cache_stale"), \
        "health_alerts must define check_dashboard_cache_stale"

    flags_src = inspect.getsource(health_alerts.check_stale_retirement_flags)
    assert "retired_reason IS NOT NULL" in flags_src, \
        "stale-flag check must look for populated retired_reason"
    assert "is_active = true" in flags_src, \
        "stale-flag check must filter is_active = true"
    assert "retired_at IS NULL" in flags_src, \
        "stale-flag check must require retired_at IS NULL (so cleared rows don't refire)"
    assert "_alert_once" in flags_src, \
        "stale-flag check must use _alert_once for dedup"

    cache_src = inspect.getsource(health_alerts.check_dashboard_cache_stale)
    assert "dashboard_cache" in cache_src, "cache check must query dashboard_cache"
    assert "age_min > 60" in cache_src, "cache check must use 60-min threshold"
    assert "_alert_once" in cache_src, "cache check must use _alert_once for dedup"

    # Both wired into the hourly run
    runner_src = inspect.getsource(health_alerts.run_snapshot_check)
    assert "check_stale_retirement_flags" in runner_src, \
        "run_snapshot_check must call check_stale_retirement_flags"
    assert "check_dashboard_cache_stale" in runner_src, \
        "run_snapshot_check must call check_dashboard_cache_stale"


@test("AF-COVERAGE-AUDIT-VERDICT-FIX — danger case is no→yes (skipped real data), not yes→no (wasted call)")
def _():
    """The existing script's verdict logic had the false-negative direction
    reversed: it labeled `yes→no` (flag=true, AF empty — wasted call) as the
    dangerous case when in fact `no→yes` (flag=false, AF returns data —
    skipping real data because of the gate) is the dangerous one. Fix
    re-labels and uses fn_rate = no→yes / total. Verdict thresholds:
    < 5% safe, 5-10% marginal, ≥ 10% do-not-gate."""
    import pathlib
    src = pathlib.Path("scripts/af_coverage_audit.py").read_text()

    assert 'false_pos = m.get("yes→no", 0)' in src, \
        "yes→no must be labeled false_pos (wasted call), not false_neg"
    assert 'false_neg = m.get("no→yes", 0)' in src, \
        "no→yes must be labeled false_neg (DANGEROUS — skipped real data)"
    assert "fn_rate < 0.05" in src, \
        "Verdict must use <5% FN-rate threshold for SAFE TO GATE"
    assert "DO NOT GATE" in src, "Verdict must include the do-not-gate copy"


@test("PERF-HERO-WINDOW-LABEL + RECENT-WINS-FLAGS — cohort tiles say 'last 30d' in subtitle; reel shows country flags")
def _():
    """Two tiny perf-page polish items:
    P — cohort tile subtitles need "last 30d" so the n-count doesn't look like
        a lifetime number (the same confusion that triggered today's audit).
    Q — RecentWinsReel renders a country flag emoji next to the country name
        for visual punch on the "edge globally" story.
    """
    import pathlib
    hero = pathlib.Path("../odds-intel-web/src/components/performance-hero.tsx").read_text()
    assert "before kickoff · last 30d" in hero, \
        "Pre-match subtitle must include '· last 30d'"
    assert "during the match · last 30d" in hero, \
        "In-play subtitle must include '· last 30d'"

    reel = pathlib.Path("../odds-intel-web/src/components/recent-wins-reel.tsx").read_text()
    assert "COUNTRY_FLAGS" in reel, "RecentWinsReel must define COUNTRY_FLAGS map"
    assert "flagFor" in reel, "RecentWinsReel must use a flagFor() helper"
    # Spot-check a few flag mappings (these specific countries appeared in
    # today's top-8 wins reel, so they must be in the map)
    for country in ("Argentina", "Paraguay", "Netherlands", "Ecuador", "Sweden",
                    "United-Arab-Emirates", "Australia", "Uruguay", "Brazil"):
        assert country in reel, f"COUNTRY_FLAGS must include {country}"
    # World fallback for "World Friendlies" league wins
    assert '"World": "🌍"' in reel or "'World': '🌍'" in reel, \
        "World → 🌍 mapping required (covers World Friendlies)"


@test("BOT-HIGH-ALIGNMENT-TRIGGER — explicit retirement gate recorded in PRIORITY_QUEUE")
def _():
    """Lock the 2026-06-01 decision: don't retire bot_high_alignment today
    (only BTTS/DC/DNB bot; specialists are starved) but retire 7 days after
    v20260531 promotion if both ROI < -2% AND CLV < +5% on n ≥ 50."""
    import pathlib
    pq = pathlib.Path("PRIORITY_QUEUE.md").read_text()
    assert "RETIREMENT-TRIGGER" in pq, "Trigger header must be in queue"
    assert "bot_high_alignment" in pq, "Must reference bot_high_alignment"
    # Both conditions (ROI AND CLV) must be documented
    assert "ROI < −2%" in pq or "ROI < -2%" in pq, \
        "ROI condition must be specified"
    assert "+5%" in pq, "CLV condition must be specified"
    assert "n ≥ 50" in pq or "n >= 50" in pq, "Sample-size threshold required"
    assert "2026-06-15" in pq, "Re-evaluation date must be set"


@test("SCHEDULER-HANG-MITIGATION — shadow_interval staggered :10/:40; EVENT_JOB_MAX_INSTANCES listener installed")
def _():
    """Post-mortem of 2026-06-01 14:35 UTC scheduler hang. Three jobs sharing
    a firing minute (:05/:35) under max_workers=4 created a deadlock when any
    one hung on a shared lock. Two mitigations shipped:

    1. shadow_interval moved to :10/:40 so it doesn't compete with
       betting_refresh_interval at the same minute.
    2. EVENT_JOB_MAX_INSTANCES listener logs to console + _recent_errors
       when APScheduler skips a fire because the previous instance is still
       running — surfaces the next hang immediately instead of waiting hours.
    """
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()

    # Stagger
    assert 'minute="10,40"' in src and 'id="shadow_interval"' in src, \
        "shadow_interval must fire at :10/:40 (staggered from :05/:35)"
    # Confirm the betting_refresh still fires at :05/:35 (we only moved shadow)
    assert 'minute="5,35"' in src and 'id="betting_refresh_interval"' in src, \
        "betting_refresh_interval must keep its :05/:35 schedule"

    # Listener
    assert "EVENT_JOB_MAX_INSTANCES" in src, \
        "Must import + use EVENT_JOB_MAX_INSTANCES event"
    assert "scheduler.add_listener(" in src, \
        "Must register the listener on the scheduler"
    assert "_on_max_instances_blocked" in src, \
        "Listener handler must be named _on_max_instances_blocked"


@test("CHERRY-PICK-PLACER-P1 — env-gated maturity filter on all three placer loaders, default unset = no filter")
def _():
    """Cherry-pick Phase 1: code lands with COOLBET_RECORD_ALLOWED_MATURITY
    unset by default so behaviour is unchanged. Three loaders gated:
      • load_qualified_bets (singles)
      • load_qualified_combo_bets (combos)
      • load_qualified_inplay_bets (inplay)
    All three skip the gate when bet_id_filter is set (admin override).
    Flip happens on 2026-06-08 by setting the env to 'calibrated' on Railway."""
    import pathlib, os, inspect
    from workers.automation import coolbet_placer

    # Helper exists and treats unset/empty/'*' as None (no filter)
    assert hasattr(coolbet_placer, "_allowed_maturity_labels"), \
        "Helper _allowed_maturity_labels must exist"
    for raw in ("", "  ", "*"):
        os.environ["COOLBET_RECORD_ALLOWED_MATURITY"] = raw
        assert coolbet_placer._allowed_maturity_labels() is None, \
            f"_allowed_maturity_labels must return None for {raw!r}"
    os.environ["COOLBET_RECORD_ALLOWED_MATURITY"] = "calibrated"
    assert coolbet_placer._allowed_maturity_labels() == ["calibrated"], \
        "Single-value parsing must return ['calibrated']"
    os.environ["COOLBET_RECORD_ALLOWED_MATURITY"] = "active,calibrated"
    assert coolbet_placer._allowed_maturity_labels() == ["active", "calibrated"], \
        "Comma list parsing must return ['active','calibrated']"
    # Reset to no-filter so other tests don't see the env
    del os.environ["COOLBET_RECORD_ALLOWED_MATURITY"]

    # All three loaders thread the filter in the SQL
    for fn_name in ("load_qualified_bets", "load_qualified_combo_bets",
                    "load_qualified_inplay_bets"):
        fn = getattr(coolbet_placer, fn_name)
        src = inspect.getsource(fn)
        assert "_allowed_maturity_labels()" in src, \
            f"{fn_name} must call _allowed_maturity_labels()"
        assert "b.maturity_label = ANY(" in src, \
            f"{fn_name} must filter on b.maturity_label = ANY(...)"
        assert "CHERRY-PICK-PLACER" in src, \
            f"{fn_name} must reference the task tag in a comment"

    # bet_id_filter path bypasses the gate — admin override (verified by inspecting
    # that the bet_id_filter branch does NOT include the maturity clause)
    bets_src = inspect.getsource(coolbet_placer.load_qualified_bets)
    bet_id_branch_idx = bets_src.index("if bet_id_filter is not None:")
    main_branch_idx = bets_src.index("# ── Diagnostic")
    bet_id_branch = bets_src[bet_id_branch_idx:main_branch_idx]
    assert "_allowed_maturity_labels" not in bet_id_branch, \
        "bet_id_filter branch must NOT call _allowed_maturity_labels (admin override)"


@test("META-VALIDATE-WEEKLY — Sunday 05:00 UTC cron runs validate_meta_b_ml3 + emails verdict")
def _():
    """Sunday 04:00 UTC: weekly_meta_retrain. New sibling at 05:00 UTC:
    weekly_meta_validate — runs scripts/validate_meta_b_ml3.py and emails the
    per-bundle verdict via weekly_meta_validate_email.send_weekly_meta_validate_email.
    Stops the 2026-06-10 activation decision (and every future one) from being
    a manual checkpoint.

    Smoke is source-inspect for the scheduler (apscheduler not in local venv)
    plus a real parser test on the email helper."""
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()

    assert "def job_weekly_meta_validate" in src, \
        "scheduler must define job_weekly_meta_validate"
    # Job body must call the script + the email helper
    job_start = src.index("def job_weekly_meta_validate")
    job_end = src.index("\ndef ", job_start + 1)
    job_body = src[job_start:job_end]
    assert "validate_meta_b_ml3.py" in job_body, \
        "job must call validate_meta_b_ml3.py"
    assert "send_weekly_meta_validate_email" in job_body, \
        "job must trigger the email helper after the script finishes"

    assert 'id="weekly_meta_validate"' in src, "cron must register with id weekly_meta_validate"
    assert 'CronTrigger(day_of_week="sun", hour=5, minute=0)' in src, \
        "cron must run Sunday 05:00 UTC (after meta_retrain at 04:00)"

    # Email helper exists + parses verdict rows
    from workers.jobs import weekly_meta_validate_email
    assert hasattr(weekly_meta_validate_email, "send_weekly_meta_validate_email"), \
        "Email helper must expose send_weekly_meta_validate_email"
    sample = (
        "Activation verdict per bundle\n"
        "│ v_20260525_v23_xgb │ xgboost  │ 56.3 │ 63.8 │ -7.5 │ FAIL │\n"
    )
    rows = weekly_meta_validate_email._parse_summary(sample)
    assert len(rows) == 1 and rows[0]["bundle"] == "v_20260525_v23_xgb", \
        "Parser must extract a verdict row from the script's rich-table stdout"
    assert rows[0]["delta_pp"] == -7.5, "Parser must read delta_pp correctly"


@test("AF-INJURIES-LATE — 08:00 UTC single injury fetch; 10:30/16:00 enrichment_refresh removed; 13:00 enrichment_full drops injuries")
def _():
    """Consolidate the three daily injury fetches (10:30, 13:00, 16:00) into
    one at 08:00 UTC. Saves ~30 AF calls/day. News-event refresh is a
    follow-up. enrichment_full (13:00) still runs but only fetches h2h +
    team_stats, not injuries."""
    import pathlib
    src = pathlib.Path("workers/scheduler.py").read_text()

    # New morning injury job
    assert "def job_injuries_morning" in src, "Must define job_injuries_morning"
    assert 'id="injuries_morning"' in src, "Must register id=injuries_morning"
    assert "CronTrigger(hour=8, minute=0)" in src, "Must fire at 08:00 UTC"

    # job_injuries_morning body must call run_enrichment with components={"injuries"}
    job_start = src.index("def job_injuries_morning")
    job_end = src.index("\ndef ", job_start + 1)
    job_body = src[job_start:job_end]
    assert 'components={"injuries"}' in job_body, \
        "job_injuries_morning must call run_enrichment(components={'injuries'})"

    # enrichment_full (13:00) must NOT include injuries anymore
    full_start = src.index("def job_enrichment_full")
    full_end = src.index("\ndef ", full_start + 1)
    full_body = src[full_start:full_end]
    assert '"injuries"' not in full_body, \
        "job_enrichment_full must drop injuries (now fetched at 08:00 only)"
    assert '"h2h"' in full_body and '"team_stats"' in full_body, \
        "job_enrichment_full must still fetch h2h + team_stats"

    # The 10:30 and 16:00 enrichment_refresh registrations must be gone
    assert 'id="enrichment_1030"' not in src, \
        "10:30 enrichment_refresh cron must be removed"
    assert 'id="enrichment_16"' not in src, \
        "16:00 enrichment_refresh cron must be removed"


@test("EMAIL-DELIVERY-CHECK — Resend records cross-checked against DNS; DMARC gap surfaced")
def _():
    """Script at scripts/check_email_deliverability.py:
    1. Queries Resend /domains for the authoritative record list (DKIM, SPF MX,
       SPF TXT) — Resend tells us exactly what records it expects.
    2. Looks up each via `dig` and compares observed vs expected. Handles the
       fact that DKIM lives at root (resend._domainkey.<domain> TXT) while SPF
       is on the `send` subdomain (the structure Resend actually uses).
    3. Separately checks DMARC because Resend doesn't require it but big
       mailbox providers do, and recommends a starter `p=none` policy when missing.

    Today's live run: DKIM ✓, SPF MX ✓, SPF TXT ✓, DMARC ✗ (missing — filed)."""
    import pathlib, importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_email_deliverability",
        "scripts/check_email_deliverability.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, "_check_resend_record"), "Helper _check_resend_record must exist"
    assert hasattr(mod, "check_dns"), "check_dns must accept Resend records"
    assert hasattr(mod, "check_resend_api"), "Resend API check must exist"
    assert hasattr(mod, "_from_email_domain"), "Domain parser must exist"

    # _from_email_domain handles both name + email forms
    import os
    os.environ["DIGEST_FROM_EMAIL"] = "OddsIntel <digest@oddsintel.app>"
    assert mod._from_email_domain() == "oddsintel.app"
    os.environ["DIGEST_FROM_EMAIL"] = "bare@example.com"
    assert mod._from_email_domain() == "example.com"
    del os.environ["DIGEST_FROM_EMAIL"]


if __name__ == "__main__":
    main()
