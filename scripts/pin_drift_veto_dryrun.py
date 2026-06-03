"""Dry-run validator: re-applies pin_cross_drift_veto.check_pin_cross_drift_veto
against the last 60 days of settled bets and confirms the savings numbers
match the analysis (scripts/pin_drift_veto_analysis.py).

This is the smoke check that the helper implements the policy we picked
empirically. If the dry-run output diverges from the analysis, the helper
has a bug.
"""
from __future__ import annotations

import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.pin_cross_drift_veto import check_pin_cross_drift_veto, get_thresholds  # noqa: E402


SQL = """
SELECT
    sb.id::text            AS bet_id,
    sb.market,
    sb.selection,
    sb.stake,
    sb.pnl,
    sb.result::text        AS result,
    sb.timing_cohort,
    sb.news_impact_score   AS bet_news,
    mfv.news_impact_score  AS mfv_news,
    mfv.pinnacle_line_move_home_at_t6h AS pm_h,
    mfv.pinnacle_line_move_draw_at_t6h AS pm_d,
    mfv.pinnacle_line_move_away_at_t6h AS pm_a
FROM simulated_bets sb
JOIN matches m ON m.id = sb.match_id
JOIN match_feature_vectors mfv ON mfv.match_id = sb.match_id
WHERE sb.result::text IN ('won', 'lost')
  AND m.date >= NOW() - INTERVAL '60 days'
  AND mfv.opening_implied_home IS NOT NULL
"""


def main() -> int:
    rows = execute_query(SQL)
    by_market: dict[str, dict] = {}

    for r in rows:
        # Use stronger-signal news (bet or match-level) — same as analysis.
        bet_news = float(r["bet_news"]) if r["bet_news"] is not None else 0.0
        mfv_news = float(r["mfv_news"]) if r["mfv_news"] is not None else 0.0
        news_to_pass = bet_news if abs(bet_news) >= abs(mfv_news) else mfv_news

        decision = check_pin_cross_drift_veto(
            market=r["market"],
            pin_line_move_home_at_t6h=float(r["pm_h"]) if r["pm_h"] is not None else None,
            pin_line_move_draw_at_t6h=float(r["pm_d"]) if r["pm_d"] is not None else None,
            pin_line_move_away_at_t6h=float(r["pm_a"]) if r["pm_a"] is not None else None,
            news_impact_score=news_to_pass,
        )

        bucket = by_market.setdefault(r["market"], {
            "total_n": 0, "total_staked": 0.0, "total_pnl": 0.0,
            "veto_n": 0, "veto_staked": 0.0, "veto_pnl": 0.0,
            "keep_n": 0, "keep_staked": 0.0, "keep_pnl": 0.0,
        })
        stake = float(r["stake"] or 0)
        pnl = float(r["pnl"] or 0)
        bucket["total_n"] += 1
        bucket["total_staked"] += stake
        bucket["total_pnl"] += pnl
        # Only count refresh bets in the veto cohort (morning has no drift data → no_drift_data).
        # Bets with timing_cohort='morning' won't be vetoed even if they happen to have data, by design.
        is_morning = (r.get("timing_cohort") or "").lower() == "morning"
        if decision["should_veto"] and not is_morning:
            bucket["veto_n"] += 1
            bucket["veto_staked"] += stake
            bucket["veto_pnl"] += pnl
        else:
            bucket["keep_n"] += 1
            bucket["keep_staked"] += stake
            bucket["keep_pnl"] += pnl

    print("Thresholds:")
    for m, t in sorted(get_thresholds().items()):
        print(f"  {m:<16} >= {t:.3f}")
    print()

    print(f"{'Market':<16} {'N':>5} {'Staked':>10} {'PnL':>10} {'ROI':>8} | {'vetoN':>6} {'vetoStk':>9} {'vetoPnL':>9} {'vetoROI':>9} | {'keepN':>6} {'keepROI':>8}")
    print("-" * 130)

    totals = {"total_n": 0, "total_staked": 0.0, "total_pnl": 0.0,
              "veto_n": 0, "veto_staked": 0.0, "veto_pnl": 0.0,
              "keep_n": 0, "keep_staked": 0.0, "keep_pnl": 0.0}
    for market in sorted(by_market.keys()):
        b = by_market[market]
        for k in totals:
            totals[k] += b[k]
        total_roi = (b["total_pnl"] / b["total_staked"] * 100) if b["total_staked"] else 0
        veto_roi = (b["veto_pnl"] / b["veto_staked"] * 100) if b["veto_staked"] else 0
        keep_roi = (b["keep_pnl"] / b["keep_staked"] * 100) if b["keep_staked"] else 0
        print(f"{market:<16} {b['total_n']:>5} {b['total_staked']:>10.2f} {b['total_pnl']:>+10.2f} {total_roi:>+7.2f}% | "
              f"{b['veto_n']:>6} {b['veto_staked']:>9.2f} {b['veto_pnl']:>+9.2f} {veto_roi:>+8.2f}% | "
              f"{b['keep_n']:>6} {keep_roi:>+7.2f}%")

    print("-" * 130)
    tot_roi = (totals["total_pnl"] / totals["total_staked"] * 100) if totals["total_staked"] else 0
    veto_roi = (totals["veto_pnl"] / totals["veto_staked"] * 100) if totals["veto_staked"] else 0
    keep_roi = (totals["keep_pnl"] / totals["keep_staked"] * 100) if totals["keep_staked"] else 0
    print(f"{'TOTAL':<16} {totals['total_n']:>5} {totals['total_staked']:>10.2f} {totals['total_pnl']:>+10.2f} {tot_roi:>+7.2f}% | "
          f"{totals['veto_n']:>6} {totals['veto_staked']:>9.2f} {totals['veto_pnl']:>+9.2f} {veto_roi:>+8.2f}% | "
          f"{totals['keep_n']:>6} {keep_roi:>+7.2f}%")

    saved = -totals["veto_pnl"]
    print()
    print(f"  Vetoed-cohort PnL: {totals['veto_pnl']:+.2f}  →  if we'd skipped these bets, we'd have saved {saved:+.2f} over 60d")
    print(f"  Annualised:        {saved * 365 / 60:+.2f}")
    print(f"  Retained ROI:      {keep_roi:+.2f}%  (vs baseline all-bets ROI of {tot_roi:+.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
