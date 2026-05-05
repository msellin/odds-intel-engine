"""
OddsIntel — Dynamic Dixon-Coles Rho per League Tier

Estimates the Dixon-Coles correlation parameter (rho) from historical match
scorelines, grouped by league tier. The global default is rho = -0.13, but
different tier environments have different draw dynamics — lower-tier leagues
often have wider variation in scoreline distributions.

Method (0-0 cell MLE estimate):
  The tau correction for a 0-0 draw is:
      tau(0,0) = 1 - lambda_h * lambda_a * rho

  Rearranging using the empirical 0-0 rate:
      rho = (1 - actual_00 / expected_00_independent) / (mean_lambda_h * mean_lambda_a)

  Where:
    actual_00    = fraction of matches ending 0-0
    expected_00  = Poisson.pmf(0, mean_lambda_h) * Poisson.pmf(0, mean_lambda_a)
    mean_lambda  = mean goals scored per team across all matches

  This is a fast closed-form estimate. Full MLE per-match lambda fitting is
  more accurate but requires match-level expected-goal estimates.

Results stored in model_calibration:
  market = 'dc_rho_tier_{n}', platt_a = rho value (platt_b = 0, unused)
  The betting pipeline loads these at startup via _load_dc_rho_cache().

Schedule: Add to Sunday settlement refit (scheduler.py step 6/6),
          alongside Platt scaling and blend weight refitting.

Usage:
    python scripts/fit_league_rho.py
    python scripts/fit_league_rho.py --min-matches 100 --dry-run
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from scipy.stats import poisson as scipy_poisson
from workers.api_clients.db import execute_query, execute_write

GLOBAL_RHO = -0.13
MIN_MATCHES_DEFAULT = 200
# Physically sensible range for football: draws are slightly over-represented
# vs independent Poisson, so rho is almost always negative in football.
RHO_MIN = -0.30
RHO_MAX = 0.05


def _estimate_rho(home_scores: list, away_scores: list) -> float | None:
    """
    Estimate rho from a group of match scores using the 0-0 cell equation.

    Returns rho clipped to [RHO_MIN, RHO_MAX], or None if insufficient data.
    """
    n = len(home_scores)
    if n == 0:
        return None

    mean_lh = sum(home_scores) / n
    mean_la = sum(away_scores) / n

    if mean_lh <= 0 or mean_la <= 0:
        return None

    # Independent Poisson expected 0-0 rate
    expected_00 = scipy_poisson.pmf(0, mean_lh) * scipy_poisson.pmf(0, mean_la)
    if expected_00 <= 0:
        return None

    # Empirical 0-0 rate
    actual_00 = sum(1 for h, a in zip(home_scores, away_scores) if h == 0 and a == 0) / n

    # Solve for rho from tau(0,0) = actual/expected = 1 - lh*la*rho
    tau_00 = actual_00 / expected_00
    rho = (1.0 - tau_00) / (mean_lh * mean_la)

    return max(RHO_MIN, min(RHO_MAX, rho))


def run(min_matches: int = MIN_MATCHES_DEFAULT, dry_run: bool = False) -> dict:
    """
    Fetch finished matches from DB, estimate rho per tier, store in model_calibration.

    Returns dict of {tier: rho} for all tiers with sufficient data.
    Falls back to GLOBAL_RHO for tiers below the minimum match threshold.
    """
    print(f"\n{'─'*65}")
    print(f"  Dixon-Coles Rho Estimation (min {min_matches} matches/tier)")
    print(f"{'─'*65}")

    rows = execute_query(
        """
        SELECT m.home_score, m.away_score, l.tier
        FROM matches m
        LEFT JOIN leagues l ON m.league_id = l.id
        WHERE m.status = 'finished'
          AND m.home_score IS NOT NULL
          AND m.away_score IS NOT NULL
          AND l.tier IS NOT NULL
        """,
        [],
    )

    if not rows:
        print("  No finished matches found in DB. Run after settlement.")
        return {}

    print(f"  Total finished matches with scores: {len(rows)}")

    # Group by tier
    by_tier: dict[int, tuple[list, list]] = {}
    for row in rows:
        tier = int(row.get("tier") or 1)
        if tier not in by_tier:
            by_tier[tier] = ([], [])
        by_tier[tier][0].append(int(row["home_score"] or 0))
        by_tier[tier][1].append(int(row["away_score"] or 0))

    results: dict[int, float] = {}
    now = datetime.now(timezone.utc)

    for tier in sorted(by_tier.keys()):
        home_scores, away_scores = by_tier[tier]
        n = len(home_scores)

        if n < min_matches:
            print(f"\n  Tier {tier}: {n} matches — below minimum ({min_matches}), "
                  f"keeping global rho={GLOBAL_RHO}")
            continue

        rho = _estimate_rho(home_scores, away_scores)
        if rho is None:
            print(f"\n  Tier {tier}: {n} matches — estimation failed, keeping global")
            continue

        mean_h = sum(home_scores) / n
        mean_a = sum(away_scores) / n
        actual_00_pct = 100 * sum(1 for h, a in zip(home_scores, away_scores)
                                   if h == 0 and a == 0) / n
        expected_00_pct = 100 * scipy_poisson.pmf(0, mean_h) * scipy_poisson.pmf(0, mean_a)

        change = rho - GLOBAL_RHO
        direction = "↑" if change > 0 else "↓"

        print(f"\n  Tier {tier}: {n} matches")
        print(f"    λ_home={mean_h:.3f}  λ_away={mean_a:.3f}")
        print(f"    0-0 actual={actual_00_pct:.1f}%  expected_indep={expected_00_pct:.1f}%")
        print(f"    rho={rho:.4f}  (global={GLOBAL_RHO}, diff={change:+.4f} {direction})")

        results[tier] = rho

        if not dry_run:
            market_key = f"dc_rho_tier_{tier}"
            execute_write(
                """
                INSERT INTO model_calibration
                    (market, platt_a, platt_b, sample_count, fitted_at)
                VALUES (%s, %s, 0.0, %s, %s)
                """,
                (market_key, round(rho, 6), n, now.isoformat()),
            )
            print(f"    → Stored as '{market_key}'")

    if dry_run and results:
        print(f"\n  Dry run — not written to DB.")

    if not results:
        print("\n  No tiers had sufficient data. Global rho=-0.13 remains in effect.")

    print(f"\n{'─'*65}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit Dixon-Coles rho per league tier")
    parser.add_argument("--min-matches", type=int, default=MIN_MATCHES_DEFAULT,
                        help=f"Minimum matches per tier for estimation (default: {MIN_MATCHES_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute estimates but do not write to DB")
    args = parser.parse_args()

    run(min_matches=args.min_matches, dry_run=args.dry_run)
