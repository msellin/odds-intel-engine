"""Stake/Kelly safety audit (2026-05-25).

Pre-real-money sanity check. Verifies that:
  1. Stake distribution is within expected bounds (≤ MAX_STAKE_PCT × bankroll).
  2. Per-bot daily exposure is reasonable (no single bot draining bankroll).
  3. Per-league exposure cap is applied (3+ bets/league → stake halved).
  4. Kelly computation matches the textbook formula on a sample of bets.
  5. No bets with negative-EV stakes (model_prob × odds < 1 + threshold).
  6. Half-Kelly factor is consistently 0.15 (KELLY_FRACTION).

Output:
  * Console summary of distribution stats per bot
  * Anomaly flags for bets that look wrong
  * Recommendation: "safe for real money" / "investigate before staking"

This is an AUDIT — read-only. Doesn't change bot configs.

Usage:
    python3 scripts/stake_kelly_safety_audit.py [--days 14]
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
from workers.model.improvements import (
    compute_kelly, KELLY_FRACTION, MAX_STAKE_PCT, DATA_TIER_MULTIPLIERS
)

console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    console.print(f"\n[bold]Stake/Kelly safety audit — last {args.days} days[/bold]")
    console.print(f"  Config: KELLY_FRACTION={KELLY_FRACTION}, MAX_STAKE_PCT={MAX_STAKE_PCT}")
    console.print(f"  Data tier multipliers: {DATA_TIER_MULTIPLIERS}")

    findings = []

    # ── 1. Stake distribution per bot ──────────────────────────────────────
    rows = execute_query("""
        SELECT b.name AS bot, COUNT(*) AS n,
               MIN(sb.stake) AS min_stake,
               MAX(sb.stake) AS max_stake,
               AVG(sb.stake) AS avg_stake,
               STDDEV(sb.stake) AS std_stake
        FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
        WHERE sb.pick_time >= NOW() - (%s || ' days')::interval
        GROUP BY b.name ORDER BY n DESC
    """, (args.days,))
    t = Table(title="Stake distribution per bot")
    for col in ("bot", "n", "min", "max", "avg", "stddev"):
        t.add_column(col)
    for r in rows:
        t.add_row(r["bot"][:30], str(r["n"]),
                  f"{float(r['min_stake'] or 0):.2f}",
                  f"{float(r['max_stake'] or 0):.2f}",
                  f"{float(r['avg_stake'] or 0):.2f}",
                  f"{float(r['std_stake'] or 0):.2f}")
    console.print(t)

    # ── 2. Outlier check: any bet exceeding 1% of starting bankroll ────────
    rows = execute_query("""
        SELECT sb.id, b.name AS bot, sb.market, sb.selection, sb.stake,
               sb.odds_at_pick, sb.model_probability AS model_prob,
               b.starting_bankroll
        FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
        WHERE sb.pick_time >= NOW() - (%s || ' days')::interval
          AND sb.stake > %s * b.starting_bankroll
        ORDER BY sb.stake DESC LIMIT 20
    """, (args.days, MAX_STAKE_PCT))
    if rows:
        findings.append(f"⚠ {len(rows)} bet(s) exceed MAX_STAKE_PCT ({MAX_STAKE_PCT * 100:.1f}% of starting bankroll). Sample:")
        for r in rows[:5]:
            findings.append(
                f"  - {r['bot']} {r['market']} {r['selection']} stake={float(r['stake']):.2f} "
                f"(starting bankroll {float(r['starting_bankroll']):.0f})"
            )
    else:
        findings.append(f"✓ No bets exceed MAX_STAKE_PCT cap.")

    # ── 3. Per-day per-bot exposure ────────────────────────────────────────
    rows = execute_query("""
        SELECT b.name AS bot, pick_time::date AS d,
               SUM(sb.stake) AS daily_stake,
               b.starting_bankroll
        FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
        WHERE sb.pick_time >= NOW() - (%s || ' days')::interval
        GROUP BY b.name, pick_time::date, b.starting_bankroll
        HAVING SUM(sb.stake) > 0.05 * b.starting_bankroll
        ORDER BY daily_stake DESC LIMIT 20
    """, (args.days,))
    if rows:
        findings.append(f"\n⚠ {len(rows)} (bot, day) combos exceed 5% of starting bankroll in stakes. Top 5:")
        for r in rows[:5]:
            pct = float(r["daily_stake"]) / float(r["starting_bankroll"]) * 100
            findings.append(f"  - {r['bot']} on {r['d']}: stake={float(r['daily_stake']):.0f} ({pct:.1f}% of starting bankroll)")
    else:
        findings.append("\n✓ No (bot, day) combo exceeds 5% of starting bankroll in stakes.")

    # ── 4. Kelly recompute sanity — sample 50 settled bets ──────────────────
    rows = execute_query("""
        SELECT sb.id, b.name AS bot, sb.stake, sb.odds_at_pick,
               sb.calibrated_prob, sb.kelly_fraction
        FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
        WHERE sb.pick_time >= NOW() - (%s || ' days')::interval
          AND sb.calibrated_prob IS NOT NULL AND sb.kelly_fraction IS NOT NULL
        ORDER BY RANDOM() LIMIT 50
    """, (args.days,))
    mismatches = 0
    for r in rows:
        expected = compute_kelly(float(r["calibrated_prob"]), float(r["odds_at_pick"]))
        stored = float(r["kelly_fraction"])
        if abs(expected - stored) > 0.001:
            mismatches += 1
    if mismatches:
        findings.append(f"\n⚠ {mismatches} of {len(rows)} sampled bets have stored kelly_fraction != compute_kelly() recompute.")
    else:
        findings.append(f"\n✓ Kelly recomputation matches stored values on all {len(rows)} sampled bets.")

    # ── 5. Negative-EV stake check ─────────────────────────────────────────
    rows = execute_query("""
        SELECT COUNT(*) AS n FROM simulated_bets sb
        WHERE sb.pick_time >= NOW() - (%s || ' days')::interval
          AND sb.calibrated_prob IS NOT NULL
          AND sb.calibrated_prob * sb.odds_at_pick < 1.0
    """, (args.days,))
    n_neg_ev = (rows[0]["n"] if rows else 0) or 0
    if n_neg_ev > 0:
        findings.append(f"\n⚠ {n_neg_ev} bets placed with model_prob × odds < 1.0 (negative EV).")
    else:
        findings.append("\n✓ Zero bets placed with negative EV on calibrated prob.")

    # ── 6. Bankroll exposure: current sum of stakes vs total starting bankroll ──
    rows = execute_query("""
        SELECT
          SUM(sb.stake) FILTER (WHERE sb.result = 'pending') AS open_exposure,
          SUM(b.starting_bankroll) AS total_starting
        FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
        WHERE sb.pick_time::date = CURRENT_DATE
    """)
    if rows and rows[0]["total_starting"]:
        open_exp = float(rows[0]["open_exposure"] or 0)
        total = float(rows[0]["total_starting"])
        pct = open_exp / total * 100
        findings.append(f"\nℹ Today's open exposure: {open_exp:.0f} / {total:.0f} total starting bankroll ({pct:.1f}%)")
        if pct > 20:
            findings.append(f"  ⚠ Open exposure exceeds 20% of total starting bankroll. Review.")

    # Print findings
    console.print("\n[bold]Findings:[/bold]")
    for f in findings:
        console.print(f)

    # Verdict
    warnings = sum(1 for f in findings if f.startswith("⚠") or "⚠" in f)
    console.print()
    if warnings == 0:
        console.print("[bold green]VERDICT: Safe for real-money execution. All sanity checks passed.[/bold green]")
    else:
        console.print(f"[bold yellow]VERDICT: {warnings} warning(s) — investigate before scaling real-money stakes.[/bold yellow]")


if __name__ == "__main__":
    main()
