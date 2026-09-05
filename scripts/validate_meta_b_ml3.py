"""B-ML3-VALIDATE-ACTIVATION — meta-model real-world validation.

Original 2026-05-25. Rewritten 2026-09-06 (META-VALIDATE-DISABLED-2026-08-31 /
SCHEDULER-META-VALIDATE-SEGFAULT / META-EVAL-PIPELINE-BROKEN).

The question this script answers:

  "Does a meta bundle's score actually rank bets by the CLV we bet on —
   the real, settled, de-vigged Pinnacle closing-line value?"

--------------------------------------------------------------------------
WHAT CHANGED 2026-09-06 AND WHY
--------------------------------------------------------------------------

1. THE SEGFAULT WAS NEVER XGBOOST OR SKLEARN.

   `weekly_meta_validate` died with `exit -11` every Sunday from ~2026-07-04.
   Three separate workarounds were added on the theory that
   `predict_proba` was crashing (a bundle skip-list, row-at-a-time
   inference, an avoid-`.loc` rebuild). All three were misdiagnoses: the
   last line flushed before the crash was "scoring with <bundle>...", and
   the crash was read off that line. It actually happened AFTER scoring.

   Reproduced on the VPS 2026-09-06 under `python -X faulthandler`:

       File ".../pandas/core/internals/managers.py", line 879 in reindex_indexer
       File ".../pandas/core/internals/managers.py", line 1090 in take
       File ".../pandas/core/generic.py", line 4089 in take
       File ".../pandas/core/groupby/ops.py", line 1260 in _sorted_data
       File ".../pandas/core/groupby/ops.py", line 627 in get_iterator
       File ".../scripts/validate_meta_b_ml3.py", line 362 in main   <-- df.groupby("bin")

   i.e. a native crash inside pandas' BlockManager.take, reached from the
   quintile `df.groupby("bin")` loop. It reproduces with sklearn and
   xgboost never imported at all. Column bisection isolated the trigger to
   the `pick_time` column (`datetime64[us, UTC]`, built by
   `pd.DataFrame(list-of-psycopg2-dicts)`): a frame of columns 0..10 groups
   fine, adding `pick_time` segfaults, casting it to `str` fixes it.
   pandas 3.0.4 / numpy 2.4.6 on the VPS; `requirements.txt` pins only
   `pandas>=2.2.0`, so the box drifted onto the pandas 3.0 line.

   THE FIX: never build a pandas object out of raw psycopg2 rows. The SQL
   below casts every column it selects to `float8` or `text`, and the only
   frames this script constructs are all-float / all-str. No Decimal, no
   datetime, no object blocks -> no BlockManager.take on an object block.
   All 12 bundles on the VPS load and `predict_proba` (single AND batch)
   cleanly, so the skip-list is gone too.

2. IT WAS JUDGING THE WRONG QUANTITY.

   The old gate binned on `clv_pinnacle`, falling back to `clv`, and scored
   the BINARY "CLV > 0" rate, passing a bundle at a 5pp top-vs-bottom
   quintile spread. Three problems:

   (a) `clv_pinnacle` is the raw, vigged comparison. The number the gate
       exists to protect is `clv_pinnacle_devig`.
   (b) A binary beat-rate throws away magnitude. Per gotcha 8, de-vigged
       CLV has per-bet SD 0.090 (n=78 for +/-2% precision); the beat
       indicator has SD ~0.5. At ~180 bets/bin the standard error on a
       beat-rate difference is ~5.3pp, so the old 5pp gate passed on pure
       noise roughly one run in six. It is not a test.
   (c) It included in-play bots. `inplay_*` bots are 364 of the 1,042 1X2
       bets in the current window — 35% of the cohort — and gotcha 14 says
       CLV is meaningless for in-play, because the price was taken
       mid-match and compared against a pre-match close.

   THE FIX: cohort is pre-match only, metric is the real
   `clv_pinnacle_devig`, and the gate is a signed significance test on the
   score/CLV relationship, not an unsigned spread. A bundle whose score is
   ANTI-correlated with real CLV now gets its own INVERTED verdict — which
   is what the whole `pseudo_clv_home` family of bundles does
   (META-MFV-TARGET-INVERTED-2026-09-06: r=-0.638 since 2026-08-01), and
   what a validator judging on the training proxy could never see.

3. IT WAS GRADING BUNDLES ON THEIR OWN TRAINING ROWS.

   META-EVAL-PIPELINE-BROKEN item (c). Every bundle was scored on the whole
   `--since` cohort. For a bets-mode bundle — fitted on settled bets — most
   of that cohort IS its training set. On the first fixed run
   `v_20260706_bets_xgb` scored r=+0.519, t=+15.19 that way; restricted to
   bets placed after its training cutoff the number is the honest one.

   THE FIX: each bundle is graded only on bets settled AFTER its training
   cutoff (parsed from the `v_YYYYMMDD_*` version name, else the pickle
   mtime). Too few out-of-sample bets is its own verdict,
   INSUFFICIENT-OOS — never a PASS. `--no-oos-guard` restores the old
   in-sample behaviour for diagnostics only.

--------------------------------------------------------------------------
KNOWN CAVEAT (gotcha 44)
--------------------------------------------------------------------------
`clv_pinnacle_devig` is written by a one-off backfill, never by settlement,
and was computed from `odds_at_pick` — a MAX() high-water mark. Its LEVELS
are optimistic. This script uses it for RANKING (does a higher meta score
mean higher CLV?), which the multiplicative price error largely preserves —
but because that error scales with odds, a quintile spread can be faked by
an odds-mix difference between bins. The per-bin `odds_avg` column and the
odds-mix warning exist precisely so that artefact is visible rather than
silently believed.

Run:
    python3 scripts/validate_meta_b_ml3.py --since 2026-05-25
    python3 scripts/validate_meta_b_ml3.py --since 2026-05-25 --bundles v_20260831_meta
"""
from __future__ import annotations
import argparse
import faulthandler
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

# Any future native crash names its own Python line instead of costing
# another two weeks of guessing which C extension did it.
faulthandler.enable()

import joblib
import json
import math
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "meta"

# Gotcha 9 / gotcha 44: the production odds guard for 1X2 is
# soft <= Pinnacle x 1.35. Since clv_pinnacle_devig = odds x devig(close) - 1,
# a de-vigged CLV above +0.35 IS a price more than 35% above the de-vigged
# Pinnacle close — the mislabelled-line / stale-quote signature, not an edge.
DEFAULT_CLV_CAP = 0.35


def _load_bundle(bundle_dir: Path) -> dict | None:
    """Load a meta bundle. Returns None if unloadable."""
    try:
        mt_path = bundle_dir / "model_type.txt"
        model_type = mt_path.read_text().strip() if mt_path.exists() else "logistic"
        scaler = joblib.load(bundle_dir / "scaler.pkl") if model_type == "logistic" else None
        model = joblib.load(bundle_dir / "b_ml3.pkl")
        # v_20260607_bets ships model_type.txt == "logistic" but pickles an
        # XGBClassifier and a null scaler.pkl. Trust the pickle, not the label.
        if scaler is None and model_type == "logistic":
            model_type = f"{type(model).__name__.lower()} (mislabelled bundle)"
        return {
            "name": bundle_dir.name,
            "model": model,
            "scaler": scaler,
            "feature_cols": joblib.load(bundle_dir / "feature_cols.pkl"),
            "model_type": model_type,
            "threshold": json.loads((bundle_dir / "threshold.json").read_text()).get("chosen_threshold", 0.5)
                         if (bundle_dir / "threshold.json").exists() else 0.5,
        }
    except Exception as e:
        console.print(f"[yellow]Could not load {bundle_dir.name}: {e}[/yellow]")
        return None


def _score_one(bundle: dict, X: pd.DataFrame) -> np.ndarray:
    """Score a feature matrix with a bundle. Handles logistic + xgboost.

    Batch `predict_proba` was blamed for the 2026-07 segfault and replaced
    with a row-at-a-time loop. That was wrong (see module docstring): every
    bundle on the VPS batch-predicts fine. Batch is back; the row-at-a-time
    path is kept only as a fallback for a genuinely broken bundle.
    """
    # Align to the bundle's expected feature schema (schemas drift across versions)
    aligned = pd.DataFrame(0.0, index=X.index, columns=bundle["feature_cols"])
    for c in bundle["feature_cols"]:
        if c in X.columns:
            aligned[c] = X[c].values
    # Logistic bundles (StandardScaler + LogisticRegression) reject NaN. MFV-derived
    # features like form_momentum / pinnacle_line_move can legitimately be NaN when
    # upstream data is thin; training imputes to 0, so we mirror that here.
    aligned = aligned.fillna(0.0)
    aligned = aligned.astype(np.float64)
    # Pass the DataFrame through, not `.values` — the scalers were fitted with
    # feature names and sklearn warns (loudly, once per call) if you strip them.
    X_eval = aligned if bundle["scaler"] is None else bundle["scaler"].transform(aligned)
    model = bundle["model"]
    try:
        return np.asarray(model.predict_proba(X_eval)[:, 1], dtype=np.float64)
    except Exception as e:
        console.print(f"[yellow]  batch predict failed ({e}) — falling back to row-at-a-time[/yellow]")
        arr = X_eval.values if hasattr(X_eval, "values") else X_eval
        scores = np.zeros(len(arr))
        for i in range(len(arr)):
            scores[i] = float(model.predict_proba(arr[i:i + 1])[0, 1])
        return scores


def _load_settled_bets_with_features(since: str, include_inplay: bool = False) -> list[dict]:
    """Pull settled PRE-MATCH bets joined to their MFV row.

    Returns a list of plain dicts, deliberately NOT a DataFrame: building a
    pandas frame straight out of psycopg2 rows is what segfaulted the old
    version (see module docstring). Every numeric column is cast to float8
    and every timestamp to text in SQL so nothing object-dtyped ever reaches
    pandas.

    Gotcha 14: `inplay_*` bots are excluded by default. Their price was taken
    mid-match, so comparing it to a PRE-match Pinnacle close is not CLV.
    """
    inplay_clause = "" if include_inplay else "AND COALESCE(b.name, '') NOT ILIKE 'inplay%%'"
    rows = execute_query(f"""
        SELECT
          sb.id::text                       AS bet_id,
          COALESCE(b.name, sb.bot_id::text) AS bot_name,
          sb.market                         AS market,
          sb.selection                      AS selection,
          sb.result                         AS result,
          sb.pick_time::text                AS pick_time,
          sb.stake::float8                  AS stake,
          sb.pnl::float8                    AS pnl,
          sb.odds_at_pick::float8           AS odds_at_pick,
          sb.meta_clv_score::float8         AS stored_score,
          sb.clv_pinnacle_devig::float8     AS clv_devig,
          sb.clv_pinnacle::float8           AS clv_pinnacle_raw,
          sb.clv::float8                    AS clv_raw,
          m.id::text                        AS match_id,
          COALESCE(l.tier, 4)::float8       AS league_tier,
          mfv.*
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN bots b ON b.id = sb.bot_id
        LEFT JOIN leagues l ON l.id = m.league_id
        JOIN match_feature_vectors mfv ON mfv.match_id = sb.match_id
        WHERE sb.pick_time >= %s
          AND sb.result IN ('won', 'lost')
          AND mfv.opening_implied_home IS NOT NULL
          {inplay_clause}
        ORDER BY sb.pick_time
    """, (since,))
    return rows or []


# META-EVAL-PIPELINE-FIX 2026-07-18: filter non-1X2 bets like training does.
# The meta model is architecturally 1X2-only — `scripts/train_b_ml3.py`
# explicitly purges non-1X2 rows (see B-ML3-BETS-MODE-1X2-FILTER, 2026-06-21).
_SEL_MAP = {
    "1x2_home": "home", "1x2_draw": "draw", "1x2_away": "away",
    "home": "home", "draw": "draw", "away": "away",
}


def _build_feature_row_for_bet(row: dict) -> dict | None:
    """Build the feature dict for ONE 1X2 bet. Mirrors both training
    (`scripts/train_b_ml3.py::_build_feature_matrix`) and inference
    (`workers/model/meta_b_ml3.py::_build_feature_row`).

    Returns None for non-1X2 bets (OU/BTTS/AH/DC) — those markets aren't in
    the meta model's training corpus so scoring them is data poisoning.

    NOTE (META-MFV-TARGET-INVERTED-2026-09-06): `ensemble_prob_draw` and
    `ensemble_prob_away` are 100% NULL in `match_feature_vectors`, so in
    practice only `home` selections survive the guard below. That is the
    home-only reality of the meta model, not a bug in this function — the
    skip counters below make it visible on every run rather than silent.
    """
    raw_sel = (row.get("selection") or "").strip().lower()
    market = (row.get("market") or "").strip().lower()

    if not (market.startswith("1x2") or market == ""):
        return None
    sel_norm = _SEL_MAP.get(raw_sel)
    if sel_norm is None:
        return None

    ens = row.get(f"ensemble_prob_{sel_norm}")
    opening = row.get(f"opening_implied_{sel_norm}")
    if ens is None or opening is None:
        return None
    ens, opening = float(ens), float(opening)

    feat = {
        # Selection-aware (pivoted per-side)
        "edge_proxy": ens - opening,
        "ensemble_prob": ens,
        "opening_implied": opening,
        "pinnacle_line_move": row.get(f"pinnacle_line_move_{sel_norm}_at_t6h"),
        "sharp_consensus": row.get(f"sharp_consensus_{sel_norm}_at_t6h"),
        "odds_volatility": row.get(f"odds_volatility_{sel_norm}_at_t6h"),
        # Match-level v2.1
        "bookmaker_disagreement": row.get("bookmaker_disagreement"),
        "elo_diff": row.get("elo_diff"),
        "form_ppg_home": row.get("form_ppg_home"),
        "form_ppg_away": row.get("form_ppg_away"),
        "lineup_confirmed": row.get("lineup_confirmed"),
        "rest_days_home": row.get("rest_days_home"),
        "rest_days_away": row.get("rest_days_away"),
        "fixture_importance": row.get("fixture_importance"),
        "league_position_home": row.get("league_position_home"),
        "odds_drift_home_at_t6h": row.get("odds_drift_home_at_t6h"),
        "steam_move_at_t6h": row.get("steam_move_at_t6h"),
        "form_momentum_home": row.get("form_momentum_home"),
        "form_momentum_away": row.get("form_momentum_away"),
        # Extended v3 signals (bets-mode bundle v_20260607+)
        "pinnacle_ah_line_at_t6h": row.get("pinnacle_ah_line_at_t6h"),
        "pinnacle_ah_line_move": row.get("pinnacle_ah_line_move"),
        "league_draw_rate_ytd": row.get("league_draw_rate_ytd"),
        "season_progress": row.get("season_progress"),
        "line_velocity": row.get("line_velocity"),
        "xg_overperf_home": row.get("xg_overperf_home"),
        "xg_overperf_away": row.get("xg_overperf_away"),
        "league_clv_efficiency": row.get("league_clv_efficiency"),
        "injury_severity_score_home": row.get("injury_severity_score_home"),
        "injury_severity_score_away": row.get("injury_severity_score_away"),
        "team_avg_player_rating_home": row.get("team_avg_player_rating_home"),
        "team_avg_player_rating_away": row.get("team_avg_player_rating_away"),
        # Meta / context
        "time_to_kickoff_h": 24.0,  # approximation — not stored per-bet
        "league_tier": int(row.get("league_tier") or 4),
        # Selection one-hot (drop_first = "home" → only draw + away)
        "selection_draw": 1 if sel_norm == "draw" else 0,
        "selection_away": 1 if sel_norm == "away" else 0,
    }
    # Missing indicators (mirror training THIN_FEATURES_FOR_INDICATORS)
    thin = ["bookmaker_disagreement", "fixture_importance", "league_position_home",
            "rest_days_home", "rest_days_away", "pinnacle_line_move",
            "sharp_consensus", "odds_volatility", "odds_drift_home_at_t6h",
            "pinnacle_ah_line_at_t6h", "pinnacle_ah_line_move"]
    for c in thin:
        feat[f"{c}_missing"] = 1 if feat.get(c) is None else 0
    # Cast + numeric coerce, NaN → 0
    for k, v in list(feat.items()):
        if isinstance(v, bool):
            feat[k] = int(v)
        elif v is None:
            feat[k] = 0.0
        else:
            try:
                feat[k] = float(v)
            except (TypeError, ValueError):
                feat[k] = 0.0
    return feat


_BUNDLE_DATE_RE = re.compile(r"v_(\d{4})(\d{2})(\d{2})")


def _bundle_trained_through(bundle_dir: Path) -> str | None:
    """Best-effort training cutoff for a bundle, as an ISO date string.

    META-EVAL-PIPELINE-BROKEN item (c): the old validator (and
    `compare_meta_bundles.py`) scored every bundle on the FULL cohort, which
    for a bets-mode bundle trained on settled bets is largely IN SAMPLE. On
    the 2026-09-06 run that made `v_20260706_bets_xgb` look like r=+0.519,
    t=+15.19 — a number produced mostly by bets it had memorised.

    No bundle stores a trained-through timestamp (`threshold.json` carries
    only CV metrics and `n_training_rows`), so infer it from the version
    name `v_YYYYMMDD_*`, falling back to the pickle's mtime. Conservative by
    construction: an over-late cutoff shrinks the OOS cohort, it never
    smuggles training rows back in.
    """
    m = _BUNDLE_DATE_RE.match(bundle_dir.name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        import datetime as _dt
        ts = (bundle_dir / "b_ml3.pkl").stat().st_mtime
        return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).date().isoformat()
    except Exception:
        return None


def _pearson_t(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Pearson r and its t-statistic. Returns (0, 0) when undefined."""
    n = len(x)
    if n < 5:
        return 0.0, 0.0
    sx, sy = float(np.std(x)), float(np.std(y))
    if sx == 0 or sy == 0:
        return 0.0, 0.0
    r = float(np.corrcoef(x, y)[0, 1])
    if not math.isfinite(r) or abs(r) >= 1.0:
        return (r if math.isfinite(r) else 0.0), 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-25",
                    help="Pull bets settled on/after this date")
    ap.add_argument("--n-bins", type=int, default=5, help="Quintile (5) or quartile (4) bins")
    ap.add_argument("--bundles", default="",
                    help="Comma-separated bundle names to score. Empty = every bundle on disk.")
    ap.add_argument("--clv-cap", type=float, default=DEFAULT_CLV_CAP,
                    help=f"Drop bets with |clv_pinnacle_devig| above this (gotcha 9 odds guard). "
                         f"Default {DEFAULT_CLV_CAP}.")
    ap.add_argument("--include-inplay", action="store_true",
                    help="Include inplay_* bots. Off by default — gotcha 14: CLV is "
                         "meaningless for in-play bets and they are 35%% of the raw cohort.")
    ap.add_argument("--no-oos-guard", action="store_true",
                    help="Score every bundle on the FULL cohort, including bets it was trained on. "
                         "Diagnostic only — the numbers it prints are in-sample and not a verdict.")
    ap.add_argument("--min-n", type=int, default=200,
                    help="Bets required before a PASS is allowed (gotcha 8: n≈78 buys ±2%% on de-vigged CLV)")
    args = ap.parse_args()

    console.print(f"\n[bold]B-ML3 validation — settled bets since {args.since}[/bold]")
    console.print("[dim]metric: real clv_pinnacle_devig on settled pre-match bets "
                  "(NOT the pseudo_clv_home training proxy — see META-MFV-TARGET-INVERTED-2026-09-06)[/dim]")
    rows = _load_settled_bets_with_features(args.since, include_inplay=args.include_inplay)
    console.print(f"  {len(rows):,} settled bets joined to MFV"
                  f"{'' if args.include_inplay else ' (pre-match only, inplay_* excluded)'}")
    if not rows:
        console.print("[red]No rows. Aborting.[/red]")
        return

    # Build features + the analysis record for each usable bet. Both are
    # strictly float/str — nothing object-dtyped reaches pandas (segfault fix).
    feat_rows: list[dict] = []
    res_rows: list[dict] = []
    skips = {"non_1x2_market": 0, "unknown_selection": 0, "missing_ensemble_or_opening": 0,
             "no_real_devig_clv": 0, "clv_outlier": 0, "kept": 0}
    sel_kept = {"home": 0, "draw": 0, "away": 0}
    for row in rows:
        market = (row.get("market") or "").strip().lower()
        raw_sel = (row.get("selection") or "").strip().lower()
        if not (market.startswith("1x2") or market == ""):
            skips["non_1x2_market"] += 1
            continue
        sel_norm = _SEL_MAP.get(raw_sel)
        if sel_norm is None:
            skips["unknown_selection"] += 1
            continue
        if row.get(f"ensemble_prob_{sel_norm}") is None or row.get(f"opening_implied_{sel_norm}") is None:
            skips["missing_ensemble_or_opening"] += 1
            continue
        clv_devig = row.get("clv_devig")
        if clv_devig is None:
            skips["no_real_devig_clv"] += 1
            continue
        clv_devig = float(clv_devig)
        if abs(clv_devig) > args.clv_cap:
            skips["clv_outlier"] += 1
            continue
        f = _build_feature_row_for_bet(row)
        if f is None:
            skips["missing_ensemble_or_opening"] += 1
            continue
        stake = float(row.get("stake") or 0.0)
        pnl = float(row.get("pnl") or 0.0)
        res_rows.append({
            # str, deliberately — a datetime64 column here is what segfaulted
            # the old validator (see module docstring).
            "pick_date": str(row.get("pick_time") or "")[:10],
            "clv_used": clv_devig,
            "won": 1.0 if row.get("result") == "won" else 0.0,
            "roi_per_bet": (pnl / stake) if stake else 0.0,
            "odds": float(row.get("odds_at_pick") or 0.0),
        })
        feat_rows.append(f)
        sel_kept[sel_norm] += 1
        skips["kept"] += 1

    console.print(f"  cohort build: {skips}")
    console.print(f"  selections kept: {sel_kept}  "
                  f"[dim](draw/away are 0 because MFV never stores their ensemble probs — "
                  f"META-MFV-TARGET-INVERTED-2026-09-06)[/dim]")
    sys.stdout.flush()
    if not feat_rows:
        console.print("[red]No bets had usable features + a real de-vigged CLV. Aborting.[/red]")
        return

    X = pd.DataFrame(feat_rows).reset_index(drop=True).astype(np.float64)
    df = pd.DataFrame(res_rows).reset_index(drop=True)
    # Decimal/None never reach here (SQL casts to float8), but keep the coercion
    # as a belt-and-braces guard — this is the line META-VALIDATOR-FIXES pins.
    for c in ("clv_used", "won", "roi_per_bet", "odds"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["clv_beat"] = (df["clv_used"] > 0).astype(int)
    n = len(df)
    console.print(f"  [bold]{n:,} bets in the scored cohort[/bold] — "
                  f"mean real de-vigged CLV {df['clv_used'].mean() * 100:+.2f}%\n")

    # Load bundles
    explicit = {b.strip() for b in args.bundles.split(",") if b.strip()}
    bundles = []
    for d in sorted([d for d in MODELS_DIR.iterdir() if d.is_dir() and (d / "b_ml3.pkl").exists()]):
        if explicit and d.name not in explicit:
            continue
        b = _load_bundle(d)
        if b:
            b["trained_through"] = _bundle_trained_through(d)
            bundles.append(b)
    console.print(f"[bold]Loaded {len(bundles)} bundles[/bold]: {[b['name'] for b in bundles]}\n")

    summary_rows = []
    for b in bundles:
        console.print(f"[dim]scoring with {b['name']}...[/dim]")
        sys.stdout.flush()
        try:
            scores = _score_one(b, X)
        except Exception as e:
            console.print(f"[yellow]Could not score with {b['name']}: {e}[/yellow]")
            continue
        df["score"] = scores

        # OUT-OF-SAMPLE RESTRICTION (META-EVAL-PIPELINE-BROKEN item c).
        # A bets-mode bundle was fitted on settled bets, so scoring it over
        # a cohort that starts before its training cutoff grades it on its
        # own training rows. Keep only bets placed AFTER the cutoff.
        cutoff = None if args.no_oos_guard else b.get("trained_through")
        if cutoff:
            work = df[df["pick_date"] > cutoff].reset_index(drop=True)
            in_sample_n = len(df) - len(work)
        else:
            work, in_sample_n = df.reset_index(drop=True), 0
        n_eval = len(work)
        if cutoff:
            console.print(f"  [dim]trained through {cutoff} — dropping {in_sample_n} in-sample bets, "
                          f"{n_eval} out-of-sample remain[/dim]")
        if n_eval < 20:
            console.print(f"  [yellow]INSUFFICIENT-OOS: only {n_eval} bets settled after this bundle's "
                          f"training cutoff ({cutoff}). No honest verdict is possible — the bundle is "
                          f"newer than the evidence.[/yellow]\n")
            summary_rows.append({
                "bundle": b["name"], "model_type": b["model_type"], "n": n_eval,
                "r": 0.0, "t": 0.0, "spread_pp": 0.0, "spread_se": float("nan"),
                "top_clv": float("nan"), "bot_clv": float("nan"), "odds_ratio": float("nan"),
                "verdict": "INSUFFICIENT-OOS",
                "advice": f"only {n_eval} out-of-sample bets after {cutoff}",
            })
            continue
        try:
            work["bin"] = pd.qcut(work["score"], q=args.n_bins, labels=False, duplicates="drop")
        except ValueError:
            work["bin"] = pd.cut(work["score"], bins=args.n_bins, labels=False)
        work["bin"] = pd.to_numeric(work["bin"], errors="coerce").fillna(-1).astype(int)

        r, t = _pearson_t(work["score"].to_numpy(dtype=float), work["clv_used"].to_numpy(dtype=float))

        tbl = Table(title=f"{b['name']} ({b['model_type']}) — quintiles by meta score, "
                          f"judged on real de-vigged Pinnacle CLV "
                          f"({'FULL COHORT, in-sample contaminated' if not cutoff else f'out-of-sample, after {cutoff}'})")
        for col in ("bin", "n", "score_avg", "CLV%", "CLV_se", "beat%", "hit%", "ROI%", "odds_avg"):
            tbl.add_column(col)
        bin_stats = []
        for bin_id in sorted(work["bin"].unique()):
            sub = work[work["bin"] == bin_id]
            k = len(sub)
            clv_pct = sub["clv_used"].mean() * 100
            clv_se = (sub["clv_used"].std(ddof=1) / math.sqrt(k) * 100) if k > 1 else float("nan")
            bin_stats.append({
                "bin": int(bin_id), "n": k, "clv_pct": clv_pct, "clv_se": clv_se,
                "beat": sub["clv_beat"].mean() * 100, "hit": sub["won"].mean() * 100,
                "roi": sub["roi_per_bet"].mean() * 100, "odds": sub["odds"].mean(),
                "score": sub["score"].mean(),
            })
            tbl.add_row(str(int(bin_id)), str(k), f"{sub['score'].mean():.3f}",
                        f"{clv_pct:+.2f}", f"{clv_se:.2f}",
                        f"{sub['clv_beat'].mean() * 100:.1f}",
                        f"{sub['won'].mean() * 100:.1f}",
                        f"{sub['roi_per_bet'].mean() * 100:+.1f}",
                        f"{sub['odds'].mean():.2f}")
        console.print(tbl)

        if len(bin_stats) < 2:
            console.print("[yellow]  Score has no spread — bundle emits a near-constant score. Skipping verdict.[/yellow]\n")
            continue
        top, bot = bin_stats[-1], bin_stats[0]
        spread_pp = top["clv_pct"] - bot["clv_pct"]
        spread_se = math.sqrt((top["clv_se"] ** 2) + (bot["clv_se"] ** 2)) if top["n"] > 1 and bot["n"] > 1 else float("nan")

        # Gotcha 44: a multiplicative price error scales with odds, so an
        # odds-mix difference between Q1 and Q5 can manufacture a CLV spread.
        odds_ratio = (top["odds"] / bot["odds"]) if bot["odds"] else float("nan")
        odds_warn = math.isfinite(odds_ratio) and (odds_ratio > 1.25 or odds_ratio < 0.80)
        if odds_warn:
            console.print(f"  [yellow]odds-mix warning: Q{top['bin']} mean odds {top['odds']:.2f} vs "
                          f"Q{bot['bin']} {bot['odds']:.2f} (ratio {odds_ratio:.2f}). gotcha 44 — the CLV "
                          f"spread may be an odds artefact of the stale odds_at_pick basis, not skill.[/yellow]")

        # --------------------------------------------------------------
        # THE GATE. Signed, significance-based, on the real metric.
        #
        # Replaces the old "top-vs-bottom CLV-beat rate >= 5pp". That gate
        # was unsigned in effect and far too loose: a beat-rate difference
        # at ~180 bets/bin has SE ~5.3pp, so 5pp was noise. De-vigged CLV
        # has per-bet SD 0.090 (gotcha 8), which is what makes a t-test on
        # the mean worth running at these sample sizes.
        # --------------------------------------------------------------
        if t <= -2.0:
            verdict = "INVERTED"
            advice = (f"Score is ANTI-correlated with real de-vigged CLV (r={r:+.3f}, t={t:+.2f}, n={n_eval}). "
                      f"Gating on this bundle would systematically pick the WORSE bets. "
                      f"Do not promote; this is the pseudo_clv-label signature "
                      f"(META-MFV-TARGET-INVERTED-2026-09-06).")
        elif t >= 2.0 and spread_pp >= 2.0 and n_eval >= args.min_n and not odds_warn:
            verdict = "PASS"
            advice = (f"r={r:+.3f} t={t:+.2f} on n={n_eval}, Q5-Q1 spread {spread_pp:+.2f}pp "
                      f"(±{spread_se:.2f}) of real de-vigged CLV — promotion candidate.")
        elif t >= 1.0:
            verdict = "MARGINAL"
            reason = []
            if t < 2.0:
                reason.append(f"t={t:+.2f} below +2.0")
            if spread_pp < 2.0:
                reason.append(f"Q5-Q1 spread {spread_pp:+.2f}pp below +2.00pp")
            if n_eval < args.min_n:
                reason.append(f"n={n_eval} below --min-n {args.min_n}")
            if odds_warn:
                reason.append("odds-mix warning unresolved")
            advice = f"Right sign but not decisive: {', '.join(reason)}. Wait for more settled bets."
        else:
            verdict = "FAIL"
            advice = (f"No usable link to real de-vigged CLV (r={r:+.3f}, t={t:+.2f}, "
                      f"Q5-Q1 {spread_pp:+.2f}pp). Meta score is noise here.")

        summary_rows.append({
            "bundle": b["name"], "model_type": b["model_type"], "n": n_eval,
            "r": r, "t": t, "spread_pp": spread_pp, "spread_se": spread_se,
            "top_clv": top["clv_pct"], "bot_clv": bot["clv_pct"],
            "odds_ratio": odds_ratio, "verdict": verdict, "advice": advice,
        })
        colour = {"PASS": "green", "MARGINAL": "yellow", "FAIL": "red",
                  "INVERTED": "red", "INSUFFICIENT-OOS": "yellow"}[verdict]
        console.print(f"  [bold {colour}]{verdict}[/bold {colour}]: {advice}\n")

    if not summary_rows:
        console.print("[red]No bundle produced a verdict.[/red]")
        return

    tbl = Table(title="Verdict per bundle — real de-vigged Pinnacle CLV on settled pre-match bets")
    for col in ("bundle", "type", "n(OOS)", "r", "t", "Q1 CLV%", "Q5 CLV%", "Δpp", "verdict"):
        tbl.add_column(col)
    for s in sorted(summary_rows, key=lambda s: -s["t"]):
        dash = s["verdict"] == "INSUFFICIENT-OOS"
        tbl.add_row(s["bundle"], s["model_type"], str(s["n"]),
                    "—" if dash else f"{s['r']:+.3f}", "—" if dash else f"{s['t']:+.2f}",
                    "—" if dash else f"{s['bot_clv']:+.2f}",
                    "—" if dash else f"{s['top_clv']:+.2f}",
                    "—" if dash else f"{s['spread_pp']:+.2f}", s["verdict"])
    console.print(tbl)

    passers = [s for s in summary_rows if s["verdict"] == "PASS"]
    inverted = [s for s in summary_rows if s["verdict"] == "INVERTED"]
    if passers:
        best = max(passers, key=lambda s: s["t"])
        console.print(f"\n[bold green]Recommend: META_B_ML3_VERSION={best['bundle']} "
                      f"(r={best['r']:+.3f}, t={best['t']:+.2f}, n={best['n']})[/bold green]")
        console.print("[dim]Promotion is still a manual env flip — this script never promotes.[/dim]")
    elif all(s["verdict"] == "INSUFFICIENT-OOS" for s in summary_rows):
        console.print("\n[yellow]No bundle has enough out-of-sample settled bets to judge yet. "
                      "Keep META_B_ML3_ENABLED=false and re-run once this week's bets settle.[/yellow]")
    elif any(s["verdict"] == "MARGINAL" for s in summary_rows):
        console.print("\n[yellow]No bundle clears the gate. Keep META_B_ML3_ENABLED=false.[/yellow]")
    else:
        console.print("\n[red]No bundle beats the gate against real de-vigged CLV. "
                      "Keep META_B_ML3_ENABLED=false.[/red]")
    if inverted:
        console.print(f"[bold red]{len(inverted)} bundle(s) are ANTI-correlated with real CLV: "
                      f"{[s['bundle'] for s in inverted]}. Gating on any of these is worse than "
                      f"no gate at all.[/bold red]")


if __name__ == "__main__":
    main()
