"""WEEKLY-EVAL — called by job_weekly_retrain after the new bundle is trained.

Runs offline held-out evaluation of CANDIDATE vs PRODUCTION on the last 14
days of settled MFV rows, writes results to model_versions.cv_metrics for
both versions, and prints a comparison summary. Replaces the legacy
compare_models.py call which structurally returned 0-overlap (candidate
bundles never produce predictions, so the predictions-table-overlap path
returned nothing).

Usage:
    python3 scripts/weekly_eval_and_compare.py <candidate> <production>
    python3 scripts/weekly_eval_and_compare.py v20260524 v14
"""
from __future__ import annotations
import os, sys, math, json, argparse
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
import joblib
import numpy as np
from datetime import date, timedelta
from rich.console import Console

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "soccer"

# WEEKLY-EVAL-HOLDOUT-NOT-HELD-OUT-2026-09-03. Enforcing an honest window is
# only half the job: a candidate trained through yesterday leaves a *technically*
# honest window of one day. The first run of that guard scored v20260903 on
# EIGHT matches and printed a full verdict table with swings up to +32.5% and
# six markets flagged NO SKILL — noise wearing the costume of a result. Refusing
# a rigged verdict and then emitting a meaningless one is the same failure.
MIN_HOLDOUT_ROWS = 200


def _ensure_local(version: str) -> bool:
    bp = MODELS_DIR / version
    if (bp / "feature_cols.pkl").exists():
        return True
    from workers.model.storage import ensure_local_bundle
    return ensure_local_bundle(version, MODELS_DIR)


def _load_bundle(version: str) -> dict | None:
    bp = MODELS_DIR / version
    if not (bp / "feature_cols.pkl").exists():
        return None
    bundle = {
        "feature_cols": joblib.load(bp / "feature_cols.pkl"),
        "result_1x2":   joblib.load(bp / "result_1x2.pkl"),
        "over_under":   joblib.load(bp / "over_under.pkl"),
    }
    # MARKET-EVAL-BTTS-AH (2026-05-24): also load the Poisson goal regressors
    # so we can derive BTTS / AH probabilities from the joint goal distribution —
    # same path production uses for those markets. Missing files are OK on
    # legacy v9* bundles; the BTTS/AH eval branches skip them in that case.
    for fname, key in [("home_goals.pkl", "home_goals"), ("away_goals.pkl", "away_goals")]:
        fp = bp / fname
        bundle[key] = joblib.load(fp) if fp.exists() else None
    return bundle


def _truth_1x2(sh, sa):
    if sh > sa: return [1, 0, 0]
    if sh == sa: return [0, 1, 0]
    return [0, 0, 1]


def _truth_ou25(sh, sa):
    return [1, 0] if (sh + sa) > 2.5 else [0, 1]


def _truth_btts(sh, sa):
    """[btts_yes, btts_no] one-hot truth."""
    return [1, 0] if (sh > 0 and sa > 0) else [0, 1]


# AH lines we score. Half-lines only — they never push, so the binary truth
# label is well-defined and log-loss is interpretable. Integer lines (0/1/2)
# are skipped here to avoid the half-win/push complication.
_AH_LINES = [
    ("ah_home_-0.5", -0.5),  # home -0.5: covers iff home wins
    ("ah_home_+0.5",  0.5),  # home +0.5: covers iff home wins or draws
    ("ah_home_-1.5", -1.5),  # home -1.5: covers iff home wins by 2+
    ("ah_home_+1.5",  1.5),  # home +1.5: covers iff home loses by ≤1 (or wins/draws)
]


def _ah_truth_home(sh: int, sa: int, line: float) -> int:
    """1 if home covers the AH line, 0 otherwise. Half-lines never push."""
    margin = sh - sa  # positive = home wins by N
    return 1 if (margin + line) > 0 else 0


def _ah_prob_home(matrix, line: float) -> float:
    """P(home covers AH `line`) from the joint goal matrix."""
    n = matrix.shape[0]
    h_grid, a_grid = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    margin = h_grid - a_grid
    mask = (margin + line) > 0
    return float(matrix[mask].sum())


def _safe_log(p, eps=1e-7):
    return math.log(max(eps, min(1 - eps, p)))


def _build_row(raw: dict, feature_cols, tier: int) -> dict:
    row = {}
    for col in feature_cols:
        if col == "tier":
            row[col] = tier
            continue
        if col.endswith("_missing"):
            base = col[: -len("_missing")]
            row[col] = 1 if (raw.get(base) is None) else 0
            continue
        v = raw.get(col)
        try:
            row[col] = 0.0 if v is None else float(v)
        except (TypeError, ValueError):
            row[col] = 0.0
    return row


def evaluate(version: str, rows: list) -> dict | None:
    """Return per-market metrics for one version against the held-out rows."""
    if not _ensure_local(version):
        console.print(f"[red]Bundle {version} not in local or Storage — skip[/red]")
        return None
    bundle = _load_bundle(version)
    if not bundle:
        return None
    fc = bundle["feature_cols"]

    # MARKET-EVAL-BTTS-AH: import the same joint-matrix builder production uses
    # for BTTS / AH derivation, so the offline eval matches inference path.
    from workers.model.joint_probability import build_joint_matrix

    can_score_goals = bundle.get("home_goals") is not None and bundle.get("away_goals") is not None

    metrics = defaultdict(lambda: {"ll": 0.0, "brier": 0.0, "hits": 0, "n": 0, "pred_sum": 0.0})
    for r in rows:
        sh, sa = int(r["score_home"]), int(r["score_away"])
        truth_3 = _truth_1x2(sh, sa)
        truth_2 = _truth_ou25(sh, sa)
        truth_btts = _truth_btts(sh, sa)
        tier = r.get("tier") or 1
        row = _build_row(dict(r), fc, tier)
        X = np.array([[row[c] for c in fc]], dtype=float)
        try:
            probs_1x2 = bundle["result_1x2"].predict_proba(X)[0]
            probs_ou = bundle["over_under"].predict_proba(X)[0]
        except Exception:
            continue
        # DRAW-CALIBRATION-EVAL-PARITY-2026-08-16: apply the same shrink the
        # live inference path applies (workers/model/xgboost_ensemble.py).
        # Without this, rigorous_eval bypasses the shrink and reports
        # uncalibrated numbers while the bots run on calibrated ones — the
        # eval would show "no change" and we'd never know if the shrink
        # was actually helping. classes_ order is [A, D, H] typically.
        from workers.model.draw_calibration import apply_draw_calibration
        classes = list(bundle["result_1x2"].classes_)
        if "H" in classes:
            idx_h, idx_d, idx_a = classes.index("H"), classes.index("D"), classes.index("A")
        else:
            # train.py outcome_map: home=0, draw=1, away=2. classes_ = [0,1,2].
            idx_h, idx_d, idx_a = 0, 1, 2
        _hp, _dp, _ap = apply_draw_calibration(
            float(probs_1x2[idx_h]), float(probs_1x2[idx_d]), float(probs_1x2[idx_a])
        )
        # Splat back into the same slots; downstream metrics loop uses
        # index 0=home, 1=draw, 2=away — same order as _truth_1x2, so we
        # rebuild probs_1x2 in that fixed order regardless of bundle
        # classes_ ordering.
        probs_1x2 = (_hp, _dp, _ap)
        for i, mkt in enumerate(["home", "draw", "away"]):
            p = float(probs_1x2[i])
            m = metrics[f"1x2_{mkt}"]
            m["n"] += 1
            m["pred_sum"] += p
            m["ll"] += -_safe_log(p) if truth_3[i] else -_safe_log(1 - p)
            m["brier"] += (p - truth_3[i]) ** 2
            m["hits"] += truth_3[i]
        # WEEKLY-EVAL-OU-INVERTED-2026-09-03: this used to zip ["over25",
        # "under25"] against predict_proba's column order. The OU head is a
        # BINARY classifier with classes_ = [0, 1] where the target y is the
        # "over 2.5" indicator, so predict_proba returns
        # [P(under), P(over)] — column 0 is UNDER. Pairing by position
        # therefore scored P(under) as the over-2.5 probability and vice versa.
        #
        # It is invisible in the output: over25 and under25 are complementary,
        # so their log-losses are identical either way and the table looks
        # self-consistent. The tell is the predicted rate. On the 2026-08-20..
        # 09-03 holdout the actual over rate was 0.5907; the swapped reading
        # implied 0.4377 (off by 0.153) where the correct one implies 0.5623
        # (off by 0.028).
        #
        # Impact: every weekly OU verdict since this script replaced
        # compare_models.py was computed on inverted probabilities, which
        # inflates OU log-loss enough to make a working head look like it has
        # no skill. Re-check any OU promotion decision taken on this output.
        for mkt, p in (("over25", float(probs_ou[1])),
                       ("under25", float(probs_ou[0]))):
            i = 0 if mkt == "over25" else 1
            m = metrics[mkt]
            m["n"] += 1
            m["pred_sum"] += p
            m["ll"] += -_safe_log(p) if truth_2[i] else -_safe_log(1 - p)
            m["brier"] += (p - truth_2[i]) ** 2
            m["hits"] += truth_2[i]

        # MARKET-EVAL-BTTS-AH — score the Poisson-derived markets.
        # Production builds the DC-corrected joint matrix from
        # (home_goals, away_goals) regressors → derives BTTS + every AH line
        # from that matrix. We mirror that here exactly so a bundle's
        # BTTS/AH log-loss in eval reflects what it would produce in prod.
        if not can_score_goals:
            continue
        try:
            exp_h = max(0.05, float(bundle["home_goals"].predict(X)[0]))
            exp_a = max(0.05, float(bundle["away_goals"].predict(X)[0]))
            matrix = build_joint_matrix(exp_h, exp_a)
        except Exception:
            continue

        # BTTS: P(both teams score). Truth = score_home > 0 AND score_away > 0.
        n = matrix.shape[0]
        h_grid, a_grid = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        p_btts_yes = float(matrix[(h_grid >= 1) & (a_grid >= 1)].sum())
        p_btts_no = 1.0 - p_btts_yes
        for i, (mkt, p) in enumerate([("btts_yes", p_btts_yes), ("btts_no", p_btts_no)]):
            m = metrics[mkt]
            m["n"] += 1
            m["pred_sum"] += p
            m["ll"] += -_safe_log(p) if truth_btts[i] else -_safe_log(1 - p)
            m["brier"] += (p - truth_btts[i]) ** 2
            m["hits"] += truth_btts[i]

        # AH half-lines: no pushes, clean binary outcome. Only score the home
        # side of each line — the away-side probability is 1 - P(home covers)
        # and would just duplicate the log-loss signal.
        for mkt, line in _AH_LINES:
            p_home_covers = _ah_prob_home(matrix, line)
            truth = _ah_truth_home(sh, sa, line)
            m = metrics[mkt]
            m["n"] += 1
            m["pred_sum"] += p_home_covers
            m["ll"] += -_safe_log(p_home_covers) if truth else -_safe_log(1 - p_home_covers)
            m["brier"] += (p_home_covers - truth) ** 2
            m["hits"] += truth
    # Aggregate
    out = {}
    for mkt, m in metrics.items():
        if m["n"] == 0:
            continue
        out[mkt] = {
            "n": m["n"],
            "log_loss": round(m["ll"] / m["n"], 4),
            "brier": round(m["brier"] / m["n"], 4),
            "hit_rate": round(m["hits"] / m["n"], 4),
            "pred_rate": round(m["pred_sum"] / m["n"], 4),
        }
    return out


def _persist_metrics(version: str, holdout_window: tuple[str, str], n_matches: int, metrics: dict, note: str):
    """Write metrics back to model_versions.cv_metrics for audit + history."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    payload = {
        "holdout_window": f"{holdout_window[0]}..{holdout_window[1]}",
        "holdout_n_matches": n_matches,
        "metrics": metrics,
        "source": "scripts/weekly_eval_and_compare.py",
        "note": note,
    }
    cur.execute("UPDATE model_versions SET cv_metrics = %s::jsonb WHERE version = %s",
                (json.dumps(payload), version))
    conn.commit()
    cur.close(); conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("candidate", help="New candidate model version (e.g. v20260524)")
    p.add_argument("production", help="Currently-promoted version (e.g. v14)")
    # WEEKLY-EVAL-BASELINE-2026-08-26: the caller passes ONE production version,
    # but production is routed per market (MODEL_VERSION_OU / _OU_T{tier} /
    # _1X2 / _BTTS, then the global). When those disagree, a single-baseline
    # table silently compares some markets against a model that is not live —
    # which is what happened to OU from 2026-07-19 onward.
    # --warn-split-baseline (2026-08-26) removed 2026-08-31: it only ever
    # printed a warning that the single baseline was wrong, and the per-market
    # resolution below makes the condition it warned about impossible. Leaving
    # the flag declared-but-unread would be a no-op the caller could set and
    # believe was doing something.
    # WEEKLY-EVAL-PERMARKET-BASELINE-2026-08-31: warning about the split was
    # never enough — the table still scored every market against the ONE
    # version passed in. Since 2026-07-19 the global MODEL_VERSION has served
    # no market head at all (1X2 -> v20260823, OU -> v20260719, global ->
    # v20260712), so every weekly verdict was measured against a model that
    # was not live anywhere. Now each market is scored against the version
    # that actually serves it, resolved the way inference resolves it.
    p.add_argument("--single-baseline", action="store_true", default=False,
                   help="Legacy: score every market against the one `production` "
                        "arg instead of its live per-market baseline")
    p.add_argument("--days", type=int, default=14, help="Held-out window in days (default 14)")
    args = p.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    console.print(f"[bold]Held-out window: {start} → {end}[/bold]")

    # Load held-out matches
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.cursor().execute("SET statement_timeout='180s'")
    dc = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    dc.execute("""
        SELECT mfv.*, m.score_home, m.score_away, l.tier
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE mfv.match_date >= %s AND mfv.match_date <= %s
          AND m.status='finished' AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
    """, (start.isoformat(), end.isoformat()))
    rows = dc.fetchall()
    conn.close()
    console.print(f"  {len(rows):,} settled MFV rows")
    if not rows:
        console.print("[red]No held-out data — exiting[/red]"); sys.exit(0)

    # Which live model version actually serves each evaluated market head.
    # BTTS and AH are derived from the Poisson goal regressors, so they follow
    # the 'goals' head; over/under follows 'ou'; the 1x2 outcomes follow '1x2'.
    MARKET_HEAD = {
        "1x2_home": "1x2", "1x2_draw": "1x2", "1x2_away": "1x2",
        "over25": "ou", "under25": "ou",
        "btts_yes": "goals", "btts_no": "goals",
        "ah_home_-0.5": "goals", "ah_home_+0.5": "goals",
        "ah_home_-1.5": "goals", "ah_home_+1.5": "goals",
    }

    def _live_baselines() -> dict:
        """market -> version actually serving it right now."""
        if args.single_baseline:
            return {m: args.production for m in MARKET_HEAD}
        try:
            from workers.model.xgboost_ensemble import _resolve_version as _rv
            head_version = {h: _rv(h) for h in ("1x2", "ou", "goals")}
        except Exception as e:
            console.print(f"[yellow]Could not resolve per-market versions ({e}) — "
                          f"falling back to the single baseline {args.production}[/yellow]")
            return {m: args.production for m in MARKET_HEAD}
        return {m: head_version[h] for m, h in MARKET_HEAD.items()}

    baseline_for = _live_baselines()

    # ---- WEEKLY-EVAL-HOLDOUT-NOT-HELD-OUT-2026-09-03 --------------------
    # This script called its window "held out" while holding nothing out.
    # `train.py` trains on everything up to the run date unless --cutoff is
    # passed, and the weekly cron did not pass it. Measured 2026-09-03:
    # v20260903 trained through 2026-09-03 and was scored on 2026-08-20..09-03
    # — the ENTIRE window was in its training set. v20260830 had 10 of the 14
    # days. Only the older baseline (v20260719, trained through 07-19) was
    # honestly scored.
    #
    # So every weekly verdict compared a model that had memorised the test
    # window against one that had not, and the newer model won by construction.
    # That is the single mechanism most likely to promote a worse model, and it
    # is invisible: the table looks normal and the winner looks decisive.
    #
    # The guard lives HERE rather than in the caller on purpose. Requiring the
    # cron to pass --cutoff would work right up until someone forgets, and the
    # failure is silent. Reading each version's real training window and
    # refusing to score on data it has seen cannot be forgotten.
    compared = [args.candidate] + sorted(set(baseline_for.values()))
    _c = psycopg2.connect(os.environ["DATABASE_URL"])
    _dc = _c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    _dc.execute("""SELECT version, training_window_end FROM model_versions
                    WHERE version = ANY(%s)""", (compared,))
    trained_through = {r["version"]: r["training_window_end"] for r in _dc.fetchall()}
    _c.close()

    unknown = [v for v in compared if trained_through.get(v) is None]
    if unknown:
        # Refuse rather than assume. A version with no recorded training window
        # might have been trained through today; guessing produces exactly the
        # rigged comparison this guard exists to stop.
        console.print(f"[red]No training_window_end recorded for: "
                      f"{', '.join(unknown)}. Cannot prove the holdout is held "
                      f"out for these versions, so refusing to score them. "
                      f"Populate model_versions.training_window_end.[/red]")
        sys.exit(2)

    # PER-BASELINE windows, not one global window. Each market is scored
    # against the version that serves it, so the honest window for a market is
    # set by that pair alone — candidate and its own baseline.
    #
    # A global window is safe but wasteful, and wastefulness here is its own
    # correctness problem: on 2026-09-04 a global rule cut 6,941 rows to 949
    # because the 1X2 baseline v20260830 was contaminated, even though the O/U
    # pair was honest across the whole span. An underpowered verdict is not a
    # trustworthy verdict — it just fails to detect real differences instead of
    # inventing fake ones.
    def _honest_start(*versions):
        return max(trained_through[v] for v in versions) + timedelta(days=1)

    for v in compared:
        console.print(f"[dim]  {v}: trained through {trained_through[v]}[/dim]")

    def _load(win_start):
        c = psycopg2.connect(os.environ["DATABASE_URL"])
        c.cursor().execute("SET statement_timeout='180s'")
        d = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        d.execute("""
            SELECT mfv.*, m.score_home, m.score_away, l.tier
            FROM match_feature_vectors mfv
            JOIN matches m ON m.id = mfv.match_id
            LEFT JOIN leagues l ON l.id = m.league_id
            WHERE mfv.match_date >= %s AND mfv.match_date <= %s
              AND m.status='finished' AND m.score_home IS NOT NULL
              AND m.score_away IS NOT NULL
        """, (win_start.isoformat(), end.isoformat()))
        r = d.fetchall()
        c.close()
        return r

    # Group markets by the baseline serving them; one honest window per group.
    by_baseline: dict[str, list[str]] = defaultdict(list)
    for mkt, ver in baseline_for.items():
        by_baseline[ver].append(mkt)

    windows = {}
    for ver in by_baseline:
        ws = max(_honest_start(args.candidate, ver), start)
        if ws > end:
            console.print(
                f"[red]{ver}: no honest holdout — the newer of "
                f"{args.candidate} ({trained_through[args.candidate]}) and {ver} "
                f"({trained_through[ver]}) trained past the window end ({end}). "
                f"Markets served by {ver} cannot be scored. Retrain the candidate "
                f"with --cutoff so a genuine holdout exists.[/red]")
        windows[ver] = ws
    if all(w > end for w in windows.values()):
        sys.exit(2)

    # Evaluate per baseline group, each on ITS OWN honest window. The
    # candidate is re-scored per group because the row set differs; that costs
    # a few seconds and buys a comparison neither side has memorised.
    baseline_versions = sorted(by_baseline)
    cand_metrics, prod_metrics = {}, {}
    market_window, market_n = {}, {}
    by_version = {}
    rows = []                      # widest set actually scored, for reporting
    for ver in baseline_versions:
        ws = windows[ver]
        if ws > end:
            continue               # already reported as unscoreable above
        grp_rows = _load(ws)
        if len(grp_rows) < MIN_HOLDOUT_ROWS:
            console.print(
                f"[red]{ver}: only {len(grp_rows)} settled rows in the honest "
                f"window {ws}..{end} (need {MIN_HOLDOUT_ROWS}). Markets served "
                f"by it are NOT scored — a verdict on this little data is noise, "
                f"and printing it anyway is how a rigged comparison becomes a "
                f"meaningless one. Retrain the candidate with an earlier "
                f"--cutoff, or wait for fixtures to settle.[/red]")
            continue
        if len(grp_rows) > len(rows):
            rows = grp_rows
        console.print(f"\n[bold]Evaluating {args.candidate} vs {ver}[/bold] "
                      f"on {ws} -> {end} ({len(grp_rows):,} rows)")
        cm = evaluate(args.candidate, grp_rows)
        bm = evaluate(ver, grp_rows)
        if not cm or not bm:
            console.print(f"[red]Could not evaluate {args.candidate} or {ver}[/red]")
            sys.exit(1)
        by_version[ver] = bm
        for mkt in by_baseline[ver]:
            if mkt in cm and mkt in bm:
                cand_metrics[mkt] = cm[mkt]
                prod_metrics[mkt] = bm[mkt]
                market_window[mkt] = f"{ws}..{end}"
                market_n[mkt] = len(grp_rows)

    if not cand_metrics:
        console.print("[red]No market could be scored on an honest window.[/red]")
        sys.exit(2)
    if len(set(market_window.values())) > 1:
        console.print("\n[cyan]Markets were scored on different honest windows "
                      "because their baselines were trained to different dates:"
                      "[/cyan]")
        for w in sorted(set(market_window.values())):
            mk = sorted(m for m in market_window if market_window[m] == w)
            console.print(f"[cyan]  {w}  n={market_n[mk[0]]:,}  "
                          f"{', '.join(mk)}[/cyan]")

    # Persist — the candidate, plus every distinct live baseline scored here.
    # Each version is persisted against the window it was ACTUALLY scored on.
    # Recording one global window here would re-introduce the very claim this
    # fix removes — that everything was measured on held-out data when it was
    # not.
    win = (start.isoformat(), end.isoformat())
    cand_note = (f"OFFLINE eval vs live per-market baselines "
                 f"{ {m: baseline_for[m] for m in sorted(set(baseline_for))} }; "
                 f"honest windows {sorted(set(market_window.values()))}"
                 if not args.single_baseline else
                 f"OFFLINE eval vs production={args.production} (n={len(rows)})")
    _persist_metrics(args.candidate, win, len(rows), cand_metrics, cand_note)
    for v, m in by_version.items():
        vw = windows[v]
        _persist_metrics(v, (vw.isoformat(), end.isoformat()), len(rows), m,
                         f"OFFLINE eval baseline vs candidate={args.candidate} "
                         f"on honest window {vw}..{end}")
    console.print(f"\n[green]Persisted cv_metrics for {args.candidate} "
                  f"and {len(by_version)} baseline version(s)[/green]")

    if args.single_baseline:
        console.print(f"[yellow]--single-baseline: every market scored against "
                      f"{args.production}; markets it does not serve are NOT a live "
                      f"comparison.[/yellow]")
    elif len(baseline_versions) > 1:
        console.print(f"[cyan]Production is split across {len(baseline_versions)} versions; "
                      f"each market below is scored against the one that serves it.[/cyan]")

    console.print(f"\n[bold]CANDIDATE {args.candidate} vs LIVE PRODUCTION[/bold]\n")
    print(f"  {'market':<16}{'baseline':>12}{'ll_cand':>10}{'ll_prod':>10}"
          f"{'Δll%':>8}{'no_skill':>10}{'verdict':>12}")
    print("  " + "-" * 78)
    market_verdicts = {}
    eval_markets = [
        "1x2_home", "1x2_draw", "1x2_away",
        "over25", "under25",
        "btts_yes", "btts_no",
        "ah_home_-0.5", "ah_home_+0.5", "ah_home_-1.5", "ah_home_+1.5",
    ]
    # A model that cannot beat a constant fixed at the observed base rate has
    # NO SKILL, whatever its absolute log loss looks like. Reporting this next
    # to the head-to-head is what surfaced that the OU 2.5 and BTTS heads had
    # been worse than guessing the average for months while looking merely
    # "slightly behind" the incumbent. `hit_rate` here is the realised outcome
    # rate for the market, so it IS the base rate.
    def _no_skill(m):
        pr = m.get("hit_rate")
        if pr is None or not (0 < pr < 1):
            return None
        return -(pr * math.log(pr) + (1 - pr) * math.log(1 - pr))

    no_skill_fails = []
    for mkt in eval_markets:
        c = cand_metrics.get(mkt); p = prod_metrics.get(mkt)
        if not c or not p:
            continue
        ll_delta = 100 * (c["log_loss"] - p["log_loss"]) / p["log_loss"]
        br_delta = 100 * (c["brier"] - p["brier"]) / p["brier"]
        verdict = "BETTER" if ll_delta < -1 else ("WORSE" if ll_delta > 1 else "TIE")
        market_verdicts[mkt] = verdict
        ns = _no_skill(c)
        ns_txt = "n/a"
        if ns is not None:
            beats = c["log_loss"] < ns
            ns_txt = f"{ns:.4f}" + ("" if beats else " !")
            if not beats:
                no_skill_fails.append(mkt)
        base = baseline_for.get(mkt, args.production)
        print(f"  {mkt:<16}{base:>12}{c['log_loss']:>10.4f}{p['log_loss']:>10.4f}"
              f"{ll_delta:>+7.1f}%{ns_txt:>10}{verdict:>12}")

    if no_skill_fails:
        console.print(
            f"\n[red]NO SKILL: {', '.join(no_skill_fails)} — the candidate loses to a "
            f"constant fixed at the base rate. Beating the incumbent on these markets "
            f"is not evidence the head works.[/red]")

    # Headline summary for cron output
    better = sum(1 for v in market_verdicts.values() if v == "BETTER")
    worse = sum(1 for v in market_verdicts.values() if v == "WORSE")
    ties = sum(1 for v in market_verdicts.values() if v == "TIE")
    console.print(f"\n[bold]Headline:[/bold] candidate is BETTER on {better} / WORSE on {worse} / TIED on {ties} markets")
    # Return JSON summary for downstream consumers (email digest)
    print()
    print("SUMMARY_JSON:", json.dumps({
        "candidate": args.candidate, "production": args.production,
        "baseline_per_market": baseline_for,
        "baseline_versions": baseline_versions,
        "no_skill_markets": no_skill_fails,
        "holdout_window": f"{start}..{end}", "n_matches": len(rows),
        "market_verdicts": market_verdicts,
        "candidate_metrics": cand_metrics, "production_metrics": prod_metrics,
    }))


if __name__ == "__main__":
    main()
