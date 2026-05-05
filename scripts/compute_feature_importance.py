"""
OddsIntel — Feature Importance Per League (P3.5)

Computes Pearson correlation between match signals and binary match outcomes
for each league × market combination. Results stored in `feature_importance` table.

This answers: "Which signals actually matter in which leagues?"
E.g. ELO difference might be highly predictive in Bundesliga (T1) but weak
in Scottish League Two (T4) where other factors dominate.

Run weekly (or manually when you want a fresh view):
    python scripts/compute_feature_importance.py
    python scripts/compute_feature_importance.py --min-samples 50
    python scripts/compute_feature_importance.py --league-id <uuid>
    python scripts/compute_feature_importance.py --top 10    # show top N signals per league

Requires 30+ matched (signal, outcome) pairs per (league, signal, market).
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write, bulk_upsert

MIN_SAMPLES_DEFAULT = 30
TOP_SIGNALS_DEFAULT = 20  # max signals to store per (league, market)


# ─── Data fetching ────────────────────────────────────────────────────────────

def fetch_signal_outcome_pairs(league_id: str | None = None) -> list[dict]:
    """
    Fetch (signal_name, signal_value, match outcome) per match per league.
    Joins match_signals with finished matches and their outcomes.
    """
    where = "AND l.id = %s" if league_id else ""
    params = [league_id] if league_id else []

    rows = execute_query(
        f"""
        SELECT
            ms.match_id,
            ms.signal_name,
            ms.signal_value,
            m.result,
            m.score_home,
            m.score_away,
            m.league_id,
            l.name   AS league_name,
            l.tier   AS league_tier
        FROM match_signals ms
        INNER JOIN matches m  ON m.id  = ms.match_id
        INNER JOIN leagues l  ON l.id  = m.league_id
        WHERE m.status = 'finished'
          AND m.result IS NOT NULL
          AND ms.signal_value IS NOT NULL
          {where}
        ORDER BY m.league_id, ms.signal_name
        """,
        params,
    )
    return rows


# ─── Outcome resolution ───────────────────────────────────────────────────────

def _resolve_outcomes(score_home: int | None, score_away: int | None,
                      result: str) -> dict[str, float | None]:
    """Return binary outcomes for all markets we care about."""
    r = result.lower()
    outcomes: dict[str, float | None] = {
        "1x2_home": 1.0 if r == "home" else 0.0,
        "1x2_draw": 1.0 if r == "draw" else 0.0,
        "1x2_away": 1.0 if r == "away" else 0.0,
    }

    if score_home is not None and score_away is not None:
        total = score_home + score_away
        outcomes["over25"]   = 1.0 if total > 2.5 else 0.0
        outcomes["under25"]  = 1.0 if total < 2.5 else 0.0
        outcomes["over15"]   = 1.0 if total > 1.5 else 0.0
        outcomes["btts_yes"] = 1.0 if (score_home > 0 and score_away > 0) else 0.0

    return outcomes


# ─── Correlation computation ──────────────────────────────────────────────────

def pearson_r(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson r between x and y; returns 0 on edge cases."""
    if len(x) < 3:
        return 0.0
    # Remove NaN pairs
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 3:
        return 0.0
    std_x = x.std()
    std_y = y.std()
    if std_x == 0 or std_y == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(min_samples: int = MIN_SAMPLES_DEFAULT,
        top_signals: int = TOP_SIGNALS_DEFAULT,
        league_id: str | None = None,
        dry_run: bool = False):

    print(f"\n{'─'*70}")
    print(f"  P3.5: Feature Importance Per League")
    print(f"  min_samples={min_samples}  top_signals={top_signals}  dry_run={dry_run}")
    print(f"{'─'*70}")

    print("\n  Fetching signal-outcome data...")
    rows = fetch_signal_outcome_pairs(league_id)
    print(f"  → {len(rows)} (signal, match) rows loaded")

    if not rows:
        print("  No data found. Run after settlement populates match results.")
        return

    # Group: league_id → signal_name → list of (signal_value, match outcome dict)
    # We also capture league_name + tier for display
    league_meta: dict[str, dict] = {}  # league_id → {name, tier}
    league_signal_data: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        lid = str(row["league_id"])
        league_meta[lid] = {
            "name": row.get("league_name", ""),
            "tier": row.get("league_tier", 1),
        }
        outcomes = _resolve_outcomes(
            row.get("score_home"),
            row.get("score_away"),
            row.get("result", ""),
        )
        signal_val = float(row["signal_value"])
        league_signal_data[lid][row["signal_name"]].append((signal_val, outcomes))

    markets = ["1x2_home", "1x2_draw", "1x2_away", "over25", "btts_yes"]

    total_rows_written = 0
    total_leagues = 0
    upsert_rows = []

    for lid, signals in league_signal_data.items():
        meta = league_meta[lid]
        league_results = []  # (abs_r, signal_name, market, r, n)

        for signal_name, pairs in signals.items():
            signal_vals = np.array([p[0] for p in pairs])
            for market in markets:
                outcome_vals = np.array([
                    p[1].get(market)
                    for p in pairs
                    if p[1].get(market) is not None
                ])
                # Match signal values to same indices where outcome is available
                valid_idx = [
                    i for i, p in enumerate(pairs)
                    if p[1].get(market) is not None
                ]
                if len(valid_idx) < min_samples:
                    continue

                sig_subset = signal_vals[valid_idx]
                r = pearson_r(sig_subset, outcome_vals)
                league_results.append((abs(r), signal_name, market, r, len(valid_idx)))

        if not league_results:
            continue

        total_leagues += 1
        # Sort by abs_r descending, take top N per market
        league_results.sort(key=lambda x: -x[0])

        # Collect top signals per market
        market_counts: dict[str, int] = defaultdict(int)
        for abs_r, signal_name, market, r, n in league_results:
            if market_counts[market] >= top_signals:
                continue
            market_counts[market] += 1
            upsert_rows.append({
                "league_id":       lid,
                "league_name":     meta["name"],
                "signal_name":     signal_name,
                "market":          market,
                "correlation":     round(r, 6),
                "abs_correlation": round(abs_r, 6),
                "sample_count":    n,
                "fitted_at":       datetime.now(timezone.utc).isoformat(),
            })
            total_rows_written += 1

    print(f"\n  Computed importance for {total_leagues} leagues, {total_rows_written} rows total")

    if dry_run:
        # Print a sample — top 10 signals across all leagues for 1x2_home
        print("\n  Sample (top 10 by |r| for 1x2_home across all leagues):")
        home_rows = [r for r in upsert_rows if r["market"] == "1x2_home"]
        home_rows.sort(key=lambda x: -x["abs_correlation"])
        for row in home_rows[:10]:
            print(f"    {row['league_name']:30s}  {row['signal_name']:35s}  "
                  f"r={row['correlation']:+.4f}  n={row['sample_count']}")
        print(f"\n  DRY RUN — no writes performed.")
        return

    if not upsert_rows:
        print("  No qualifying (signal, market, league) pairs. More data needed.")
        return

    # Bulk upsert into feature_importance
    # No unique key to upsert on — just INSERT (new rows each run, ordered by fitted_at)
    batch = 500
    for i in range(0, len(upsert_rows), batch):
        chunk = upsert_rows[i:i + batch]
        cols = list(chunk[0].keys())
        values = [tuple(r[c] for c in cols) for r in chunk]
        placeholders = ", ".join(["(%s)" % ", ".join(["%s"] * len(cols))] * len(values))

        # Flatten for execute
        flat_vals = [v for row in values for v in row]
        execute_write(
            f"INSERT INTO feature_importance ({', '.join(cols)}) VALUES "
            + ", ".join(["(" + ", ".join(["%s"] * len(cols)) + ")"] * len(values)),
            flat_vals,
        )

    print(f"  ✅ Inserted {total_rows_written} rows into feature_importance")

    # Print top 10 by |r| for 1x2_home
    home_rows = [r for r in upsert_rows if r["market"] == "1x2_home"]
    home_rows.sort(key=lambda x: -x["abs_correlation"])
    print(f"\n  Top 10 signals by |r| for 1x2_home:")
    for row in home_rows[:10]:
        print(f"    {row['league_name']:30s}  {row['signal_name']:35s}  "
              f"r={row['correlation']:+.4f}  n={row['sample_count']}")

    print(f"\n{'─'*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute signal-outcome correlations per league")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT,
                        help=f"Min (signal, outcome) pairs per (league, signal, market) (default: {MIN_SAMPLES_DEFAULT})")
    parser.add_argument("--top", type=int, default=TOP_SIGNALS_DEFAULT,
                        help=f"Max signals to store per (league, market) (default: {TOP_SIGNALS_DEFAULT})")
    parser.add_argument("--league-id", type=str, default=None,
                        help="Only compute for one league (UUID)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analysis only — do not write to feature_importance table")
    args = parser.parse_args()

    run(
        min_samples=args.min_samples,
        top_signals=args.top,
        league_id=args.league_id,
        dry_run=args.dry_run,
    )
