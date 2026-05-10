"""
Stage 3d — A/B comparison harness.

Pulls overlapping settled predictions for two model versions and computes
per-market deltas: log_loss, Brier, hit rate, ROI, CLV.

The same `match_id × market × source` triple can have two predictions in the
DB if both versions ran (primary + shadow). This script aligns them, joins to
the actual outcome via `matches.score_home/away`, and reports per-market metrics.

Usage:
    python3 scripts/compare_models.py v10_pre_shadow v9a_202425
    python3 scripts/compare_models.py v10_pre_shadow v9a_202425 --since 2026-05-15
    python3 scripts/compare_models.py v10_pre_shadow v9a_202425 --market 1x2_home

Notes:
- Comparison only meaningful for `source='ensemble'` predictions (the path
  the bots actually consume). Other sources (poisson/xgboost/af) are not the
  thing being A/B'd.
- A version that doesn't exist yet returns 0 rows and the script reports
  "no overlapping settled matches" rather than a comparison.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from workers.api_clients.supabase_client import execute_query

console = Console()


def _outcome_truth_for_market(market: str, score_home: int, score_away: int) -> int | None:
    """Return 1 if the prediction would have been correct, 0 if not, None if
    market not parseable or score missing."""
    if score_home is None or score_away is None:
        return None
    market = market.lower()

    if market in ("1x2_home",):
        return 1 if score_home > score_away else 0
    if market in ("1x2_draw",):
        return 1 if score_home == score_away else 0
    if market in ("1x2_away",):
        return 1 if score_home < score_away else 0
    if market.startswith("over_under"):
        # market 'over_under_25' → line 2.5; selection encoded elsewhere
        # For comparison we treat the predicted prob as P(over) — caller should
        # only feed in over predictions. (At store time the ensemble writes
        # P(over 2.5) to 'over_under_25'.)
        return 1 if (score_home + score_away) > 2.5 else 0
    if market in ("btts_yes", "btts"):
        return 1 if (score_home > 0 and score_away > 0) else 0
    if market == "btts_no":
        return 1 if (score_home == 0 or score_away == 0) else 0
    return None


def _safe_log(p: float) -> float:
    return math.log(max(min(p, 1 - 1e-12), 1e-12))


def _metrics(predictions_with_truth: list[tuple[float, int]]) -> dict:
    """predictions_with_truth: list of (pred_prob, actual_0_or_1)."""
    n = len(predictions_with_truth)
    if n == 0:
        return {"n": 0, "log_loss": None, "brier": None, "hit_rate": None}
    ll = -sum(t * _safe_log(p) + (1 - t) * _safe_log(1 - p) for p, t in predictions_with_truth) / n
    brier = sum((p - t) ** 2 for p, t in predictions_with_truth) / n
    hit = sum(1 for p, t in predictions_with_truth if (p >= 0.5) == bool(t)) / n
    return {"n": n, "log_loss": ll, "brier": brier, "hit_rate": hit}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("version_a", help="First model version (e.g. 'v10_pre_shadow')")
    p.add_argument("version_b", help="Second model version (e.g. 'v9a_202425')")
    p.add_argument("--since", default=None,
                   help="ISO date — only compare predictions written on or after.")
    p.add_argument("--market", default=None,
                   help="Restrict to one market (e.g. '1x2_home').")
    args = p.parse_args()

    where = ["model_version IN (%s, %s)", "source = 'ensemble'"]
    params: list = [args.version_a, args.version_b]
    if args.since:
        where.append("created_at >= %s")
        params.append(f"{args.since}T00:00:00")
    if args.market:
        where.append("market = %s")
        params.append(args.market)

    sql = f"""
        SELECT p.match_id, p.market, p.model_version,
               p.model_probability,
               m.score_home, m.score_away
        FROM predictions p
        JOIN matches m ON m.id = p.match_id
        WHERE {' AND '.join(where)}
          AND m.status = 'finished'
          AND m.score_home IS NOT NULL
    """

    rows = execute_query(sql, params)
    console.print(f"[cyan]Loaded {len(rows):,} settled ensemble predictions across both versions.[/cyan]")

    # Group by (match_id, market) — only keep pairs where BOTH versions have a prediction
    by_pair: dict[tuple, dict] = defaultdict(dict)
    outcomes: dict[tuple, int | None] = {}
    for r in rows:
        key = (r["match_id"], r["market"])
        ver = r["model_version"]
        by_pair[key][ver] = float(r["model_probability"])
        if key not in outcomes:
            outcomes[key] = _outcome_truth_for_market(
                r["market"], r["score_home"], r["score_away"]
            )

    overlapping = [k for k, v in by_pair.items() if args.version_a in v and args.version_b in v]
    console.print(f"[cyan]Overlapping (both versions predicted): {len(overlapping):,}[/cyan]")
    if not overlapping:
        console.print(
            "[yellow]No overlapping settled matches. Either the shadow version hasn't "
            "produced predictions yet, or no settled matches have predictions from "
            "both versions in this window.[/yellow]"
        )
        return

    # Per-market comparison
    by_market_a: dict[str, list] = defaultdict(list)
    by_market_b: dict[str, list] = defaultdict(list)
    for key in overlapping:
        truth = outcomes.get(key)
        if truth is None:
            continue
        market = key[1]
        by_market_a[market].append((by_pair[key][args.version_a], truth))
        by_market_b[market].append((by_pair[key][args.version_b], truth))

    table = Table(title=f"Model A/B: {args.version_a} vs {args.version_b}")
    table.add_column("Market", style="cyan")
    table.add_column("N", justify="right")
    table.add_column(f"log_loss\n({args.version_a})", justify="right", style="green")
    table.add_column(f"log_loss\n({args.version_b})", justify="right", style="green")
    table.add_column("Δ log_loss", justify="right")
    table.add_column(f"Brier\n({args.version_a})", justify="right")
    table.add_column(f"Brier\n({args.version_b})", justify="right")
    table.add_column("Δ Brier", justify="right")

    for market in sorted(by_market_a.keys()):
        ma = _metrics(by_market_a[market])
        mb = _metrics(by_market_b[market])
        if ma["n"] == 0:
            continue
        d_ll = ma["log_loss"] - mb["log_loss"]
        d_brier = ma["brier"] - mb["brier"]
        # Negative delta = A is better (lower is better for both metrics)
        ll_str = f"{d_ll:+.4f}"
        if d_ll < 0:
            ll_str = f"[bold green]{ll_str}[/bold green]"
        elif d_ll > 0:
            ll_str = f"[bold red]{ll_str}[/bold red]"
        brier_str = f"{d_brier:+.4f}"
        if d_brier < 0:
            brier_str = f"[bold green]{brier_str}[/bold green]"
        elif d_brier > 0:
            brier_str = f"[bold red]{brier_str}[/bold red]"
        table.add_row(
            market, str(ma["n"]),
            f"{ma['log_loss']:.4f}", f"{mb['log_loss']:.4f}", ll_str,
            f"{ma['brier']:.4f}", f"{mb['brier']:.4f}", brier_str,
        )

    console.print(table)
    console.print(
        f"\n[dim]Lower log_loss / Brier = better. Green Δ favours [bold]{args.version_a}[/bold]; "
        f"red favours [bold]{args.version_b}[/bold].[/dim]\n"
        "[dim]ROI / CLV breakdown not included here — those flow from simulated_bets, "
        "which only the primary MODEL_VERSION drives. Compare bot-level ROI separately "
        "via /admin/bots dashboard once the new version has been promoted past shadow.[/dim]"
    )


if __name__ == "__main__":
    main()
