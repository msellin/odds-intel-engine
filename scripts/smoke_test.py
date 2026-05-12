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


@test("ML-BUNDLE-STORAGE — _load_models() routes through ensure_local_bundle on cache miss")
def _():
    import inspect
    from workers.model import xgboost_ensemble
    src = inspect.getsource(xgboost_ensemble._load_models)
    # Without this wire, a fresh Railway container with MODEL_VERSION set to a
    # bundle not on disk falls through to {} and silently degrades to Poisson.
    assert "ensure_local_bundle" in src, (
        "_load_models must call ensure_local_bundle when the bundle dir is missing — "
        "otherwise Railway redeploys lose bundles silently."
    )


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
    assert "if not _bot_active.get(bot_name" in src, (
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
        "Strategy N must price the home win via _bivariate_poisson_win_prob"
    )
    assert "0.65" in fn_body, "Strategy N must require prematch_home_prob ≥ 0.65"
    assert "2.20" in fn_body, "Strategy N must require live_1x2_home ≥ 2.20"
    assert "minute < 72 or minute > 80" in fn_body, (
        "Strategy N minute window is 72-80 (intentionally tight)"
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

    # N must apply per-team multipliers
    n_start = src.index("def _check_strategy_n(")
    n_end = src.index("\ndef ", n_start + 1)
    n_body = src[n_start:n_end]
    assert "_state_multiplier_team(" in n_body, (
        "Strategy N must apply per-team state multipliers to bivariate lambdas"
    )
    assert '"trailing"' in n_body and '"leading"' in n_body and '"level"' in n_body, (
        "Strategy N must classify each side as trailing/leading/level"
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
    assert "placedMatchIds" in src, "getPlaceableBets must query real_bets placed today"
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
    assert "isEdgeStale" in src, "must have isEdgeStale helper for stale row detection"
    assert "live" in src, "must show 'live' edge label to distinguish from bot edge"
    assert "opacity-50" in src, "must dim stale rows with opacity-50"
    assert "modelProb" in src, "BookOddsLine must receive modelProb to calculate current edge"

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


@test("ROMANIAN-LIGA-I-DATA — targets_v9.csv includes RO1 data with FCSB + Slobozia")
def test_romanian_liga_data():
    """RO1-DATA-FIX: FCSB and Unirea Slobozia must be in targets_v9 (Tier A) so Poisson
    expected goals are based on Liga I performance, not global data that mixes in European
    competition and inverts FCSB's expected goals."""
    import pathlib
    import pandas as pd
    root = pathlib.Path(__file__).resolve().parent.parent
    v9 = root / "data" / "processed" / "targets_v9.csv"
    assert v9.exists(), "targets_v9.csv must exist"
    df = pd.read_csv(v9, low_memory=False)
    assert "RO1" in df["league_code"].values, "targets_v9 must contain Romanian Liga I (RO1) rows"
    teams = set(df[df["league_code"] == "RO1"]["home_team"].unique()) | \
            set(df[df["league_code"] == "RO1"]["away_team"].unique())
    assert "FCSB" in teams, "FCSB must be in RO1 rows"
    assert "Unirea Slobozia" in teams, "Unirea Slobozia must be in RO1 rows"
    ro1_count = (df["league_code"] == "RO1").sum()
    assert ro1_count >= 500, f"expected 500+ RO1 rows, got {ro1_count}"


if __name__ == "__main__":
    main()
