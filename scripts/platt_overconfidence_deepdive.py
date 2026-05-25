"""GLOBAL-PLATT-OVERCONFIDENCE deep-dive — why are 30-50% bins overconfident?

LONGSHOT-GEO-AUDIT (2026-05-25) found that 30-50% calibrated_prob bins are
systematically -12 to -16pp overconfident globally. Per-country breakdown
rejected geographic concentration. This script tests 3 follow-up hypotheses
by binning the SAME 30-50% bin cohort by 3 different dimensions and
measuring the calibration gap per sub-bin.

H1 — ODDS BUCKET: is the gap concentrated in longshot odds (>3.0)?
     Stake on a 35% predicted match at 2.50 vs 4.00 — same predicted
     prob, but the longshot one may be miscalibrated more.

H2 — LEAGUE TIER: is it concentrated in Tier 2-4 (lower leagues)?
     Tier 1 has tight markets; tier 4 has thinner books = more model bias.

H3 — TIME OF SEASON: is it worse early-season (high uncertainty)?
     Use season_progress signal we built earlier. Quartiles 0-25/25-50/50-75/75-100.

Output: per-hypothesis breakdown table + verdict (which dimension explains
most of the gap). Findings shape the 2026-06-08 deploy: should we add
per-(tier × odds-bucket) alpha shrinkage on top of isotonic?

Run: python3 scripts/platt_overconfidence_deepdive.py [--days 90]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()


def _bin_label(value: float, edges: list[float]) -> str:
    """Return 'edges[i]–edges[i+1]' label for value, or last bucket."""
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return f"{edges[i]:.2f}-{edges[i+1]:.2f}"
    return f"≥{edges[-1]:.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="Window in days (longer than LONGSHOT-GEO-AUDIT's 60 to get more focus-bin sample)")
    args = ap.parse_args()

    console.print(f"[bold]GLOBAL-PLATT-OVERCONFIDENCE deep-dive — 3 hypothesis tests (last {args.days}d)[/bold]")

    # Load focus-bin bets: 30-50% calibrated_prob, 1x2 market, settled
    # Join: bets → matches → leagues for tier; pull season_progress signal too.
    rows = execute_query("""
        SELECT
            sb.calibrated_prob,
            sb.odds_at_pick,
            sb.result,
            sb.pnl,
            l.tier AS league_tier,
            (
                SELECT signal_value FROM match_signals ms
                WHERE ms.match_id = sb.match_id
                  AND ms.signal_name = 'season_progress'
                ORDER BY ms.captured_at DESC LIMIT 1
            ) AS season_progress
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        JOIN leagues l ON l.id = m.league_id
        WHERE sb.market = '1x2'
          AND sb.result IN ('won', 'lost', 'void')
          AND sb.calibrated_prob >= 0.30
          AND sb.calibrated_prob < 0.50
          AND sb.calibrated_prob IS NOT NULL
          AND sb.odds_at_pick IS NOT NULL
          AND sb.pick_time >= NOW() - (%s || ' days')::interval
    """, (args.days,))
    if not rows:
        console.print("[yellow]No bets in 30-50% bin.[/yellow]")
        return
    console.print(f"  Cohort: {len(rows):,} bets in 30-50% calibrated_prob (last {args.days}d)")

    # Global gap baseline
    total_won = sum(1 for r in rows if r["pnl"] is not None and float(r["pnl"]) > 0)
    total_predicted = sum(float(r["calibrated_prob"]) for r in rows) / len(rows)
    total_actual = total_won / len(rows)
    global_gap_pp = (total_actual - total_predicted) * 100
    console.print(f"  Global: avg predicted={total_predicted*100:.1f}%  actual={total_actual*100:.1f}%  gap={global_gap_pp:+.1f}pp\n")

    # ─── H1: ODDS BUCKET ────────────────────────────────────────────────
    odds_edges = [1.30, 2.00, 2.50, 3.00, 3.50, 4.00, 10.00]
    bucket_stats: dict = {}
    for r in rows:
        o = float(r["odds_at_pick"])
        b = _bin_label(o, odds_edges)
        s = bucket_stats.setdefault(b, {"n": 0, "won": 0, "p_sum": 0.0})
        s["n"] += 1
        s["p_sum"] += float(r["calibrated_prob"])
        if r["pnl"] is not None and float(r["pnl"]) > 0:
            s["won"] += 1

    t = Table(title="H1 — calibration gap by ODDS bucket")
    for c in ("odds bin", "n", "avg pred", "actual hit", "gap (act-pred)"):
        t.add_column(c)
    for b in sorted(bucket_stats.keys(), key=lambda x: float(x.split("-")[0].replace("≥", ""))):
        s = bucket_stats[b]
        if s["n"] < 20:
            continue
        avg_pred = s["p_sum"] / s["n"]
        actual = s["won"] / s["n"]
        gap = (actual - avg_pred) * 100
        t.add_row(b, str(s["n"]), f"{avg_pred*100:.1f}%", f"{actual*100:.1f}%", f"{gap:+.1f}pp")
    console.print(t)

    # ─── H2: LEAGUE TIER ────────────────────────────────────────────────
    tier_stats: dict = {}
    for r in rows:
        t_lvl = r["league_tier"] or 0
        s = tier_stats.setdefault(t_lvl, {"n": 0, "won": 0, "p_sum": 0.0})
        s["n"] += 1
        s["p_sum"] += float(r["calibrated_prob"])
        if r["pnl"] is not None and float(r["pnl"]) > 0:
            s["won"] += 1

    t = Table(title="H2 — calibration gap by LEAGUE TIER")
    for c in ("tier", "n", "avg pred", "actual hit", "gap (act-pred)"):
        t.add_column(c)
    for tier in sorted(tier_stats.keys()):
        s = tier_stats[tier]
        if s["n"] < 20:
            continue
        avg_pred = s["p_sum"] / s["n"]
        actual = s["won"] / s["n"]
        gap = (actual - avg_pred) * 100
        t.add_row(f"T{tier}", str(s["n"]), f"{avg_pred*100:.1f}%", f"{actual*100:.1f}%", f"{gap:+.1f}pp")
    console.print(t)

    # ─── H3: SEASON PROGRESS QUARTILE ───────────────────────────────────
    sp_rows = [r for r in rows if r["season_progress"] is not None]
    if not sp_rows:
        console.print("[yellow]H3: no bets with season_progress signal — skip[/yellow]")
    else:
        sp_edges = [0.0, 0.25, 0.50, 0.75, 1.01]
        sp_stats: dict = {}
        for r in sp_rows:
            sp = float(r["season_progress"])
            b = _bin_label(sp, sp_edges)
            s = sp_stats.setdefault(b, {"n": 0, "won": 0, "p_sum": 0.0})
            s["n"] += 1
            s["p_sum"] += float(r["calibrated_prob"])
            if r["pnl"] is not None and float(r["pnl"]) > 0:
                s["won"] += 1
        t = Table(title=f"H3 — calibration gap by SEASON_PROGRESS (n={len(sp_rows):,} of {len(rows):,} have signal)")
        for c in ("phase", "n", "avg pred", "actual hit", "gap (act-pred)"):
            t.add_column(c)
        for b in sorted(sp_stats.keys()):
            s = sp_stats[b]
            if s["n"] < 20:
                continue
            avg_pred = s["p_sum"] / s["n"]
            actual = s["won"] / s["n"]
            gap = (actual - avg_pred) * 100
            t.add_row(b, str(s["n"]), f"{avg_pred*100:.1f}%", f"{actual*100:.1f}%", f"{gap:+.1f}pp")
        console.print(t)

    # ─── Verdict ────────────────────────────────────────────────────────
    def _spread(stats: dict, min_n: int = 20) -> tuple[float, float, float]:
        """Returns (worst_gap, best_gap, spread_pp)."""
        gaps = []
        for s in stats.values():
            if s["n"] < min_n:
                continue
            avg_pred = s["p_sum"] / s["n"]
            actual = s["won"] / s["n"]
            gaps.append((actual - avg_pred) * 100)
        if not gaps:
            return 0, 0, 0
        return min(gaps), max(gaps), max(gaps) - min(gaps)

    h1_worst, h1_best, h1_spread = _spread(bucket_stats)
    h2_worst, h2_best, h2_spread = _spread(tier_stats)
    h3_worst = h3_best = h3_spread = 0
    if sp_rows:
        h3_worst, h3_best, h3_spread = _spread(sp_stats)

    console.print("\n[bold]Hypothesis verdict — which dimension explains most of the gap?[/bold]")
    console.print(f"  H1 (odds bucket):     spread {h1_spread:.1f}pp  (worst {h1_worst:+.1f}, best {h1_best:+.1f})")
    console.print(f"  H2 (league tier):     spread {h2_spread:.1f}pp  (worst {h2_worst:+.1f}, best {h2_best:+.1f})")
    console.print(f"  H3 (season progress): spread {h3_spread:.1f}pp  (worst {h3_worst:+.1f}, best {h3_best:+.1f})")

    winner = max([("H1 odds", h1_spread), ("H2 tier", h2_spread), ("H3 season", h3_spread)], key=lambda x: x[1])
    console.print(f"\n[bold green]→ Strongest concentration: {winner[0]} ({winner[1]:.1f}pp spread)[/bold green]")
    if winner[1] >= 8:
        console.print("[yellow]This dimension explains material variance — consider sub-dimension calibration on 06-08.[/yellow]")
    elif winner[1] >= 4:
        console.print("[yellow]Moderate concentration — isotonic alone may be enough; revisit after 2 weeks of post-deploy data.[/yellow]")
    else:
        console.print("[dim]Low concentration — bias is uniform across all 3 dimensions. Isotonic is sufficient.[/dim]")


if __name__ == "__main__":
    main()
