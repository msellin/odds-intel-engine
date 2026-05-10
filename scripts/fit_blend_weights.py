"""
OddsIntel — Blend Weight & Shrinkage Alpha Optimizer (MOD-2)

Computes optimal model parameters from settled prediction outcomes:

  1. Poisson/XGBoost blend weight
     Compares source='poisson' vs source='xgboost' predictions per market,
     finds the blend weight (0–1) that minimizes log-loss on settled matches.

  2. Shrinkage alpha per tier (CALIBRATION_ALPHA)
     From source='ensemble' predictions, finds the per-tier alpha that minimizes
     log-loss when blending model_probability with implied_probability:
         calibrated = alpha * model_prob + (1 - alpha) * implied_prob
     Replaces hardcoded T1=0.20 / T2=0.30 / T3=0.50 / T4=0.65.

Results are stored in model_calibration as rows with market names like:
  - 'blend_weight_1x2'          → platt_a = optimal poisson weight (0.0–1.0)
  - 'shrinkage_alpha_t1_1x2'    → platt_a = optimal alpha for tier 1, 1x2
  - 'shrinkage_alpha_t1_goalline' → platt_a = optimal alpha for tier 1, O/U/BTTS
  (platt_b = 0 for all of these — field repurposed as param value)

The pipeline (improvements.py) loads these on startup and falls back to
hardcoded values if the table is empty or has insufficient samples.

Usage:
    python scripts/fit_blend_weights.py
    python scripts/fit_blend_weights.py --min-samples 200
    python scripts/fit_blend_weights.py --dry-run      # analysis only, no writes
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize_scalar
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write

MIN_SAMPLES_DEFAULT = 100
TIERS = [1, 2, 3, 4]

_GOALLINE_PREFIXES = ("over", "under", "btts")


def _is_goalline(market: str) -> bool:
    return market.lower().startswith(_GOALLINE_PREFIXES)


def _log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Binary cross-entropy (log-loss)."""
    p = np.clip(probs, 1e-7, 1 - 1e-7)
    return float(-np.mean(outcomes * np.log(p) + (1 - outcomes) * np.log(1 - p)))


def _brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    return float(np.mean((probs - outcomes) ** 2))


def _outcome_bit(market: str, match_result: str) -> float | None:
    """Return 1.0 if the market selection won, 0.0 if lost, None if unknown."""
    mkt = market.lower()
    res = (match_result or "").lower()
    if not res:
        return None
    if mkt == "1x2_home":
        return 1.0 if res == "home" else 0.0
    if mkt == "1x2_draw":
        return 1.0 if res == "draw" else 0.0
    if mkt == "1x2_away":
        return 1.0 if res == "away" else 0.0
    # Over/under and BTTS: match result alone is insufficient — need score
    # We store score_home/score_away on matches; use them if present
    return None


def _outcome_bit_with_score(market: str, score_home: int | None, score_away: int | None,
                             match_result: str) -> float | None:
    """Resolve market outcome including score-based markets."""
    basic = _outcome_bit(market, match_result)
    if basic is not None:
        return basic

    if score_home is None or score_away is None:
        return None

    mkt = market.lower()
    total = score_home + score_away

    if mkt in ("over25", "over_25"):
        return 1.0 if total > 2.5 else 0.0
    if mkt in ("under25", "under_25"):
        return 1.0 if total < 2.5 else 0.0
    if mkt in ("over15", "over_15"):
        return 1.0 if total > 1.5 else 0.0
    if mkt in ("under15", "under_15"):
        return 1.0 if total < 1.5 else 0.0
    if mkt in ("over35", "over_35"):
        return 1.0 if total > 3.5 else 0.0
    if mkt in ("under35", "under_35"):
        return 1.0 if total < 3.5 else 0.0
    if mkt in ("btts_yes",):
        return 1.0 if (score_home > 0 and score_away > 0) else 0.0
    if mkt in ("btts_no",):
        return 1.0 if not (score_home > 0 and score_away > 0) else 0.0

    return None


# ─── Data fetching ────────────────────────────────────────────────────────────

def fetch_predictions(source: str) -> list[dict]:
    """Fetch settled predictions with outcomes, league tier, and scores."""
    rows = execute_query(
        """
        SELECT
            p.model_probability,
            p.implied_probability,
            p.market,
            l.tier  AS league_tier,
            m.result,
            m.score_home,
            m.score_away
        FROM predictions p
        INNER JOIN matches m ON m.id = p.match_id
        INNER JOIN leagues l ON l.id = m.league_id
        WHERE p.source = %s
          AND m.status = 'finished'
          AND m.result IS NOT NULL
          AND p.model_probability IS NOT NULL
          AND p.implied_probability IS NOT NULL
        """,
        [source],
    )

    labeled = []
    for row in rows:
        market = row.get("market", "")
        outcome = _outcome_bit_with_score(
            market,
            row.get("score_home"),
            row.get("score_away"),
            row.get("result", ""),
        )
        if outcome is None:
            continue
        labeled.append({
            "model_prob":    float(row["model_probability"]),
            "implied_prob":  float(row["implied_probability"]),
            "market":        market,
            "tier":          int(row.get("league_tier") or 1),
            "outcome":       outcome,
        })

    return labeled


# ─── Blend weight optimization ────────────────────────────────────────────────

def optimize_blend_weight(poisson_rows: list[dict], xgb_rows: list[dict],
                          market_filter: str | None = None) -> dict:
    """
    Find the optimal Poisson weight w in [0,1] such that:
        blended = w * poisson_prob + (1-w) * xgb_prob
    minimizes log-loss on settled matches.

    Returns dict with weight, sample count, and metrics.
    """
    # Index xgb rows by (match implied_prob, market) — we match on implied_prob
    # as a proxy for match identity since we don't have match_id here.
    # Better: key on (implied_prob, market) pair. This is approximate but sufficient
    # for optimizing blend weights across thousands of matches.
    xgb_by_key: dict[tuple, float] = {}
    for row in xgb_rows:
        if market_filter and row["market"] != market_filter:
            continue
        key = (round(row["implied_prob"], 4), row["market"], row["tier"])
        xgb_by_key[key] = row["model_prob"]

    paired = []
    for row in poisson_rows:
        if market_filter and row["market"] != market_filter:
            continue
        key = (round(row["implied_prob"], 4), row["market"], row["tier"])
        xgb_prob = xgb_by_key.get(key)
        if xgb_prob is not None:
            paired.append({
                "poisson_prob": row["model_prob"],
                "xgb_prob":     xgb_prob,
                "outcome":      row["outcome"],
            })

    if len(paired) < MIN_SAMPLES_DEFAULT:
        return {"weight": 0.5, "n": len(paired), "skipped": True}

    poisson_arr = np.array([r["poisson_prob"] for r in paired])
    xgb_arr     = np.array([r["xgb_prob"]     for r in paired])
    outcomes    = np.array([r["outcome"]       for r in paired])

    def loss(w):
        blended = w * poisson_arr + (1 - w) * xgb_arr
        return _log_loss(blended, outcomes)

    result = minimize_scalar(loss, bounds=(0.0, 1.0), method="bounded")
    w_opt = float(result.x)

    # Metrics
    ll_50_50    = loss(0.5)
    ll_poisson  = loss(1.0)
    ll_xgb      = loss(0.0)
    ll_optimal  = loss(w_opt)

    return {
        "weight":       round(w_opt, 4),
        "n":            len(paired),
        "ll_optimal":   round(ll_optimal, 6),
        "ll_50_50":     round(ll_50_50, 6),
        "ll_poisson":   round(ll_poisson, 6),
        "ll_xgb":       round(ll_xgb, 6),
        "skipped":      False,
    }


# ─── Shrinkage alpha optimization ─────────────────────────────────────────────

def optimize_shrinkage_alpha(rows: list[dict]) -> dict:
    """
    Find alpha in [0,1] that minimizes log-loss of:
        calibrated = alpha * model_prob + (1 - alpha) * implied_prob
    Returns dict with alpha, sample count, and metrics.
    """
    if len(rows) < MIN_SAMPLES_DEFAULT:
        return {"alpha": None, "n": len(rows), "skipped": True}

    model_arr   = np.array([r["model_prob"]   for r in rows])
    implied_arr = np.array([r["implied_prob"] for r in rows])
    outcomes    = np.array([r["outcome"]      for r in rows])

    def loss(alpha):
        blended = alpha * model_arr + (1 - alpha) * implied_arr
        return _log_loss(blended, outcomes)

    result      = minimize_scalar(loss, bounds=(0.0, 1.0), method="bounded")
    alpha_opt   = float(result.x)

    # Baselines
    ll_model    = loss(1.0)
    ll_market   = loss(0.0)
    ll_opt      = loss(alpha_opt)
    brier_opt   = _brier_score(alpha_opt * model_arr + (1 - alpha_opt) * implied_arr, outcomes)

    return {
        "alpha":        round(alpha_opt, 4),
        "n":            len(rows),
        "ll_optimal":   round(ll_opt, 6),
        "ll_model":     round(ll_model, 6),
        "ll_market":    round(ll_market, 6),
        "brier":        round(brier_opt, 6),
        "skipped":      False,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(min_samples: int = MIN_SAMPLES_DEFAULT, dry_run: bool = False):
    print(f"\n{'─'*70}")
    print("  MOD-2: Blend Weight & Shrinkage Alpha Optimizer")
    print(f"  dry_run={dry_run}  min_samples={min_samples}")
    print(f"{'─'*70}")

    # Fetch data
    print("\n  Fetching settled predictions...")
    ensemble_rows = fetch_predictions("ensemble")
    poisson_rows  = fetch_predictions("poisson")
    xgb_rows      = fetch_predictions("xgboost")

    print(f"    ensemble : {len(ensemble_rows)}")
    print(f"    poisson  : {len(poisson_rows)}")
    print(f"    xgboost  : {len(xgb_rows)}")

    if not ensemble_rows:
        print("\n  No settled ensemble predictions. Run after settlement.")
        return

    # ── 1. Poisson/XGBoost blend weight ──────────────────────────────────────
    print(f"\n  {'─'*65}")
    print("  § 1. Poisson / XGBoost Blend Weight")
    print(f"  {'─'*65}")
    print("  Current hardcoded: 50/50 (poisson_weight=0.5)")

    blend_results = {}
    for market in ("1x2_home", "1x2_draw", "1x2_away"):
        res = optimize_blend_weight(poisson_rows, xgb_rows, market_filter=market)
        blend_results[market] = res
        if res["skipped"]:
            print(f"\n  {market}: {res['n']} matched pairs (need {min_samples}) — SKIPPED")
        else:
            print(f"\n  {market} (n={res['n']}):")
            print(f"    Optimal Poisson weight: {res['weight']:.4f}  "
                  f"(XGBoost: {1-res['weight']:.4f})")
            print(f"    Log-loss @ optimal:     {res['ll_optimal']:.6f}")
            print(f"    Log-loss @ 50/50:       {res['ll_50_50']:.6f}")
            print(f"    Log-loss @ Poisson only:{res['ll_poisson']:.6f}")
            print(f"    Log-loss @ XGBoost only:{res['ll_xgb']:.6f}")
            gain = res["ll_50_50"] - res["ll_optimal"]
            print(f"    Improvement vs 50/50:   {gain:.6f} ({gain/res['ll_50_50']*100:.2f}%)")

    # Overall blend weight (all 1x2 markets combined)
    res_overall = optimize_blend_weight(poisson_rows, xgb_rows, market_filter=None)
    print(f"\n  Overall 1x2 blend (n={res_overall['n']}): "
          f"Poisson={res_overall['weight']:.4f}, XGBoost={1-res_overall['weight']:.4f}")

    if not res_overall["skipped"] and not dry_run:
        execute_write(
            """
            INSERT INTO model_calibration
                (market, platt_a, platt_b, sample_count, ece_before, ece_after, fitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "blend_weight_1x2",
                round(res_overall["weight"], 6),
                0.0,
                res_overall["n"],
                round(res_overall["ll_50_50"], 6),
                round(res_overall["ll_optimal"], 6),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        print(f"    → Stored blend_weight_1x2 = {res_overall['weight']:.4f} in model_calibration")

    # ── 1b. Per-tier 1x2 blend weights (ML-BLEND-DYNAMIC) ─────────────────
    # Lower-tier leagues have noisier rolling stats, so XGBoost overfits on
    # them. Constantinou 2019 (and our own Scotland League Two pseudo-CLV
    # data) suggest the optimal blend shifts toward Poisson at tier 3-4.
    # We fit per-tier and store as `blend_weight_1x2_t{tier}` so the live
    # ensemble can pick the right weight by match tier.
    print(f"\n  {'─'*65}")
    print("  § 1b. Per-Tier 1X2 Blend Weights (ML-BLEND-DYNAMIC)")
    print(f"  {'─'*65}")
    for tier in TIERS:
        p_rows = [r for r in poisson_rows if r["tier"] == tier]
        x_rows = [r for r in xgb_rows if r["tier"] == tier]
        if not p_rows or not x_rows:
            print(f"\n  Tier {tier}: no data")
            continue
        res_t = optimize_blend_weight(p_rows, x_rows, market_filter=None)
        if res_t["skipped"]:
            print(f"\n  Tier {tier}: n={res_t['n']} (need {min_samples}) — SKIPPED")
            continue
        print(f"\n  Tier {tier} (n={res_t['n']}): "
              f"Poisson={res_t['weight']:.4f}, XGBoost={1-res_t['weight']:.4f}, "
              f"ll_opt={res_t['ll_optimal']:.4f} (vs 50/50 {res_t['ll_50_50']:.4f})")
        if not dry_run:
            execute_write(
                """
                INSERT INTO model_calibration
                    (market, platt_a, platt_b, sample_count, ece_before, ece_after, fitted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    f"blend_weight_1x2_t{tier}",
                    round(res_t["weight"], 6),
                    0.0,
                    res_t["n"],
                    round(res_t["ll_50_50"], 6),
                    round(res_t["ll_optimal"], 6),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            print(f"    → Stored blend_weight_1x2_t{tier} = {res_t['weight']:.4f}")

    # ── 2. Shrinkage alpha per tier ───────────────────────────────────────────
    print(f"\n  {'─'*65}")
    print("  § 2. Shrinkage Alpha Per Tier (CALIBRATION_ALPHA)")
    print(f"  {'─'*65}")
    HARDCODED = {1: 0.20, 2: 0.30, 3: 0.50, 4: 0.65}
    HARDCODED_GL = {1: 0.35, 2: 0.45, 3: 0.65, 4: 0.80}
    print(f"  Current hardcoded (1x2):     {HARDCODED}")
    print(f"  Current hardcoded (goalline):{HARDCODED_GL}")

    for tier in TIERS:
        tier_rows_1x2 = [r for r in ensemble_rows
                         if r["tier"] == tier and r["market"].startswith("1x2")]
        tier_rows_gl  = [r for r in ensemble_rows
                         if r["tier"] == tier and _is_goalline(r["market"])]

        print(f"\n  Tier {tier}:")

        for label, rows, old_alpha, market_key in [
            ("1x2",      tier_rows_1x2, HARDCODED.get(tier, 0.35),    f"shrinkage_alpha_t{tier}_1x2"),
            ("goalline", tier_rows_gl,  HARDCODED_GL.get(tier, 0.50), f"shrinkage_alpha_t{tier}_goalline"),
        ]:
            res = optimize_shrinkage_alpha(rows)
            if res["skipped"]:
                print(f"    {label}: {res['n']} samples (need {min_samples}) — SKIPPED")
                continue

            alpha_opt = res["alpha"]
            delta = alpha_opt - old_alpha
            direction = "▲" if delta > 0 else "▼"
            print(f"    {label} (n={res['n']}):")
            print(f"      Old alpha={old_alpha:.2f}  Optimal={alpha_opt:.4f}  "
                  f"{direction} {abs(delta):.4f}")
            print(f"      Log-loss: optimal={res['ll_optimal']:.6f}  "
                  f"model-only={res['ll_model']:.6f}  market-only={res['ll_market']:.6f}")
            print(f"      Brier score: {res['brier']:.6f}")

            if not dry_run:
                execute_write(
                    """
                    INSERT INTO model_calibration
                        (market, platt_a, platt_b, sample_count, ece_before, ece_after, fitted_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        market_key,
                        round(alpha_opt, 6),
                        0.0,
                        res["n"],
                        round(old_alpha, 6),
                        round(res["ll_optimal"], 6),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                print(f"      → Stored {market_key} = {alpha_opt:.4f} in model_calibration")

    print(f"\n{'─'*70}")
    if dry_run:
        print("  DRY RUN — no writes performed.")
    else:
        print("  ✅ Done. Run improvements.py to pick up new values on next pipeline start.")
    print(f"{'─'*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit Poisson/XGBoost blend weights and shrinkage alphas")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT,
                        help=f"Minimum samples per market (default: {MIN_SAMPLES_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analysis only — do not write to model_calibration")
    args = parser.parse_args()

    run(min_samples=args.min_samples, dry_run=args.dry_run)
