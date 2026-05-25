"""ALN-1-TUNE — recommend new _ALN_BUMP values from settled-bet ROI per class.

Reads simulated_bets joined to alignment_class + result, computes per-class
ROI on settled cohort, and recommends a re-tuning of `_ALN_BUMP` in
workers/jobs/daily_pipeline_v2.py.

Decision rule:
  - If a class has positive ROI ≥ +5% over baseline (NONE class), LOWER its
    bump (accept those bets at less edge — they're profitable).
  - If a class has negative ROI ≤ -5%, RAISE its bump (require more edge).
  - If within ±5% of baseline, keep current bump.

Output:
  - Per-class table: n bets, hit rate, mean ROI, current bump, recommended bump
  - Diff summary + a `dev/active/aln1_tune_recommendation_YYYYMMDD.md`

This is ANALYSIS ONLY — does not change BOTS_CONFIG. Apply post-2026-06-07.

Run: python3 scripts/aln1_tune_analysis.py
"""
from __future__ import annotations
import argparse
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()

# Current production bumps (must mirror daily_pipeline_v2.py:2704)
CURRENT_BUMPS = {"LOW": 0.01, "MEDIUM": 0.0, "HIGH": 0.0, "NONE": 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="Window in days")
    args = ap.parse_args()

    console.print(f"\n[bold]ALN-1-TUNE — alignment-class ROI analysis (last {args.days} days)[/bold]")
    rows = execute_query("""
        SELECT alignment_class,
               COUNT(*) AS settled,
               COUNT(*) FILTER (WHERE pnl > 0) AS wins,
               SUM(stake) AS total_stake,
               SUM(pnl) AS total_pnl,
               AVG(edge_percent) AS avg_edge
        FROM simulated_bets
        WHERE alignment_class IS NOT NULL
          AND result IN ('won','lost','void')
          AND pick_time >= NOW() - (%s || ' days')::interval
        GROUP BY alignment_class
        ORDER BY alignment_class
    """, (args.days,))

    if not rows:
        console.print("[yellow]No settled bets with alignment_class — nothing to analyze.[/yellow]")
        return

    t = Table(title=f"Per-alignment-class settlement ({args.days}d)")
    for col in ("class", "n_settled", "hit%", "stake", "pnl", "ROI%", "avg_edge%", "current_bump", "recommendation"):
        t.add_column(col)

    recommendations = {}
    baseline_roi = None
    by_class = {r["alignment_class"]: r for r in rows}
    none_row = by_class.get("NONE")
    if none_row and none_row["total_stake"]:
        baseline_roi = float(none_row["total_pnl"] or 0) / float(none_row["total_stake"])

    for r in rows:
        cls = r["alignment_class"]
        n = r["settled"] or 0
        wins = r["wins"] or 0
        stake = float(r["total_stake"] or 0)
        pnl = float(r["total_pnl"] or 0)
        roi = pnl / stake if stake else 0
        hit = wins / n if n else 0
        cur_bump = CURRENT_BUMPS.get(cls, 0.0)

        # Decision rule, compared to NONE baseline (or absolute if no baseline)
        ref = baseline_roi if baseline_roi is not None else 0.0
        delta = roi - ref
        if n < 100:
            new_bump = cur_bump
            advice = f"keep (n={n}<100, too small to act on)"
        elif delta >= 0.05:
            new_bump = max(cur_bump - 0.005, -0.01)
            advice = f"+EV (+{delta * 100:.1f}pp vs NONE) → lower bump (accept at less edge)"
        elif delta <= -0.05:
            new_bump = cur_bump + 0.005
            advice = f"-EV ({delta * 100:.1f}pp vs NONE) → raise bump (require more edge)"
        else:
            new_bump = cur_bump
            advice = f"flat (±5%): keep bump"
        recommendations[cls] = new_bump

        t.add_row(
            cls, str(n), f"{hit * 100:.1f}", f"{stake:.0f}",
            f"{pnl:+.0f}", f"{roi * 100:+.2f}",
            f"{float(r['avg_edge'] or 0):+.2f}",
            f"{cur_bump:+.4f}",
            f"{new_bump:+.4f}  ({advice})",
        )
    console.print(t)

    # Diff summary
    console.print("\n[bold]Recommendation summary:[/bold]")
    any_change = False
    for cls in ("NONE", "LOW", "MEDIUM", "HIGH"):
        cur = CURRENT_BUMPS.get(cls, 0.0)
        new = recommendations.get(cls, cur)
        if abs(new - cur) > 1e-6:
            any_change = True
            arrow = "↓" if new < cur else "↑"
            console.print(f"  {cls}: {cur:+.4f}  {arrow}  {new:+.4f}")
        else:
            console.print(f"  {cls}: {cur:+.4f} (unchanged)")

    # Write recommendation doc
    out_path = Path(__file__).resolve().parent.parent / "dev" / "active" / f"aln1_tune_recommendation_{date.today().strftime('%Y%m%d')}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(f"# ALN-1-TUNE — Recommendation (generated {date.today()})\n\n")
        f.write(f"Window: last {args.days} days · Baseline NONE ROI: {baseline_roi * 100 if baseline_roi else 0:+.2f}%\n\n")
        f.write("| Class | n | hit% | ROI% | current bump | recommended bump | rationale |\n")
        f.write("|-------|---|------|------|--------------|------------------|----------|\n")
        for r in rows:
            cls = r["alignment_class"]
            n = r["settled"] or 0
            wins = r["wins"] or 0
            stake = float(r["total_stake"] or 0)
            pnl = float(r["total_pnl"] or 0)
            roi = pnl / stake if stake else 0
            hit = wins / n if n else 0
            cur_bump = CURRENT_BUMPS.get(cls, 0.0)
            new_bump = recommendations.get(cls, cur_bump)
            f.write(f"| {cls} | {n} | {hit * 100:.1f} | {roi * 100:+.2f} | {cur_bump:+.4f} | {new_bump:+.4f} | (auto) |\n")
        f.write("\n**To apply** (post-2026-06-07 only — Phase 3.5 lock):\n")
        f.write("Edit `workers/jobs/daily_pipeline_v2.py:2704`:\n```python\n")
        f.write("_ALN_BUMP = {\n")
        for cls in ("LOW", "MEDIUM", "HIGH", "NONE"):
            f.write(f"    \"{cls}\": {recommendations.get(cls, CURRENT_BUMPS.get(cls, 0.0)):.4f},\n")
        f.write("}\n```\n")
    console.print(f"\n[green]Wrote recommendation: {out_path}[/green]")
    if not any_change:
        console.print("[yellow]No changes recommended — current bumps look right for this window.[/yellow]")


if __name__ == "__main__":
    main()
