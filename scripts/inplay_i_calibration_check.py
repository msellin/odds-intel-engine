#!/usr/bin/env python3
"""
Diagnostic: dump settled inplay_i bets with per-bet model features.

Splits the cohort into pre-fix (before INPLAY-I-INVESTIGATE 2026-06-06, which
added Bayesian xG update + market-gate) and post-fix to measure calibration
improvement. ECE on the pre-fix n=11 cohort was 24.61%.

Note: Platt calibration requires ≥50 samples (fit_platt_live.py gate). This
script is diagnostic-only until post-fix bets accumulate.

Usage:
    python3 scripts/inplay_i_calibration_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import lazily so the file is importable even without DB creds (smoke tests).
_QUERY: str | None = None

FIX_DATE = "2026-06-06"  # Date INPLAY-I-INVESTIGATE fixes shipped
PLATT_MIN_SAMPLES = 50   # fit_platt_live.py gate


def _get_rows() -> list[dict]:
    from workers.supabase_client import execute_query  # type: ignore

    return execute_query("""
        SELECT
            sb.id,
            sb.created_at::date            AS date,
            sb.market,
            sb.selection,
            sb.odds_at_pick,
            sb.model_probability,
            sb.edge_percent,
            sb.result,
            sb.pnl,
            sb.reasoning,
            sb.match_minute_at_pick,
            m.home_team,
            m.away_team,
            m.league
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        JOIN bots b ON b.id = sb.bot_id
        WHERE b.name = 'inplay_i'
          AND sb.result IN ('won', 'lost')
        ORDER BY sb.created_at
    """) or []


def _cohort_stats(rows: list[dict]) -> dict:
    if not rows:
        return {}
    wins = sum(1 for r in rows if r["result"] == "won")
    hit_rate = wins / len(rows)
    avg_prob = sum(float(r["model_probability"]) for r in rows) / len(rows)
    mae = sum(
        abs(float(r["model_probability"]) - (1.0 if r["result"] == "won" else 0.0))
        for r in rows
    ) / len(rows)
    total_pnl = sum(float(r["pnl"]) for r in rows)
    return {
        "n": len(rows), "wins": wins, "hit_rate": hit_rate,
        "avg_model_prob": avg_prob, "mae_ece": mae, "total_pnl": total_pnl,
    }


def main() -> None:
    rows = _get_rows()

    pre = [r for r in rows if str(r["date"]) <= FIX_DATE]
    post = [r for r in rows if str(r["date"]) > FIX_DATE]

    print(f"inplay_i settled bets: {len(rows)} total")
    print(f"  Pre-fix  (≤{FIX_DATE}): {len(pre)}")
    print(f"  Post-fix (>{FIX_DATE}):  {len(post)}")
    print()

    for label, cohort in [("Pre-fix", pre), ("Post-fix", post)]:
        s = _cohort_stats(cohort)
        if not s:
            print(f"=== {label}: no data ===\n")
            continue
        platt_note = (
            f"  ✗ Platt fit not yet possible (n={s['n']} < {PLATT_MIN_SAMPLES})"
            if s["n"] < PLATT_MIN_SAMPLES
            else f"  ✓ Platt fit possible (n={s['n']} ≥ {PLATT_MIN_SAMPLES})"
        )
        print(
            f"=== {label} cohort (n={s['n']}) ===\n"
            f"  Hit rate:         {s['hit_rate']:.1%} (won={s['wins']})\n"
            f"  Avg model_prob:   {s['avg_model_prob']:.3f}\n"
            f"  Actual hit rate:  {s['hit_rate']:.3f}\n"
            f"  MAE (ECE proxy):  {s['mae_ece']:.3f} ({s['mae_ece']*100:.1f}%)\n"
            f"  Total PnL:        ${s['total_pnl']:.2f}\n"
            f"{platt_note}\n"
        )

    print("=== Per-bet breakdown ===")
    col_w = 90
    print(f"{'':4} {'date':<11} {'match':<32} {'league':<25} {'m':>3} {'sel':<5} "
          f"{'odds':>6} {'mp':>6} {'hit':>4} {'pnl':>7}  extra")
    print("-" * col_w)
    for r in rows:
        match_str = f"{r['home_team']} vs {r['away_team']}"[:31]
        league_str = (r["league"] or "")[:24]
        extra: dict = {}
        if r["reasoning"]:
            try:
                extra = json.loads(r["reasoning"]).get("extra", {})
            except Exception:
                pass

        pm_fav = extra.get("pm_fav_prob")
        pm_imp = extra.get("pm_implied_fav")
        gap_str = ""
        if pm_fav is not None and pm_imp is not None:
            gap = float(pm_fav) - float(pm_imp)
            flag = " ⚠ market-gate" if gap > 0.15 else ""
            gap_str = f"gap={gap:.2f}{flag}"

        marker = "POST" if str(r["date"]) > FIX_DATE else "pre "
        hit = "W" if r["result"] == "won" else "L"
        print(
            f"[{marker}] {str(r['date']):<10} {match_str:<32} {league_str:<25} "
            f"{r['match_minute_at_pick'] or 0:>3} {r['selection']:<5} "
            f"{float(r['odds_at_pick']):>6.2f} {float(r['model_probability']):>6.3f} "
            f"{hit:>4} {float(r['pnl']):>7.2f}  {gap_str}"
        )

    print()
    if len(post) < PLATT_MIN_SAMPLES:
        remaining = PLATT_MIN_SAMPLES - len(post)
        print(
            f"ACTION: wait for {remaining} more post-fix settled bets before Platt fit.\n"
            f"        Run `python3 scripts/fit_platt_live.py` once post-fix n≥{PLATT_MIN_SAMPLES}.\n"
            f"        (Estimate: ~2026-07-15 at current fire rate)"
        )


if __name__ == "__main__":
    main()
