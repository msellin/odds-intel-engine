"""
Audit OddsIntel vs WinnerOdds in a matched window. Writes the standard
ledger/comparison_winnerodds.json that the landing page reads at runtime.

Mirrors the shape of scripts/audit_vs_{signalodds,deepbetting,forebet,
tipstrr,betaminic}.py — same `their_stats`, `our_stats_same_window`,
`scope_notes` keys so the consolidated landing fetch works
uniformly.

Reuses the proven wo_pull_window + wo_summary helpers from
scripts/production_audit_vs_winnerodds.py (which is the heavy
multi-pass audit that produces the per-country breakdown). This
script just snapshots the headline numbers to a JSON that the web
landing can fetch.

Usage:
    python3 scripts/audit_vs_winnerodds.py
    python3 scripts/audit_vs_winnerodds.py --start 2026-05-04 --end 2026-06-25
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(str(Path(__file__).resolve().parent.parent))
load_dotenv()

from scripts.production_audit_vs_winnerodds import wo_pull_window, wo_summary  # noqa: E402
from workers.api_clients.db import execute_query  # noqa: E402


WINDOW_START_DEFAULT = "2026-05-04"
WINDOW_END_DEFAULT = "2026-06-25"
STAKE = 10.0
MIN_SAMPLE = 50


def our_stats(start: str, end: str) -> dict:
    """Our matched cohort: production strategies, 1X2 + OU 2.5 only,
    settled, in the same window. BTTS excluded to stay apples-to-apples
    (WinnerOdds publishes a mixed-market feed; the per-bet table doesn't
    cleanly separate BTTS for us to match it)."""
    rows = execute_query(
        """
        SELECT
          COUNT(*) AS n,
          SUM(sb.pnl)::numeric AS pnl,
          SUM(sb.stake)::numeric AS stake,
          COUNT(*) FILTER (WHERE sb.result = 'won') AS won
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.result IN ('won', 'lost')
          AND b.maturity_label IN ('calibrated', 'beta', 'active')
          AND sb.market IN ('1x2', 'o/u', 'over_under_25')
          AND sb.created_at >= %s::date
          AND sb.created_at <  %s::date
        """,
        (start, end),
    )
    r = rows[0]
    n = int(r["n"] or 0)
    pnl = float(r["pnl"] or 0)
    stake = float(r["stake"] or 0)
    won = int(r["won"] or 0)
    return {
        "n": n,
        "stake_total": round(stake, 2),
        "pnl_total": round(pnl, 2),
        "roi_pct": round(100 * pnl / stake, 2) if stake else 0,
        "hit_rate_pct": round(100 * won / n, 2) if n else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=WINDOW_START_DEFAULT)
    ap.add_argument("--end", default=WINDOW_END_DEFAULT)
    args = ap.parse_args()

    print(f"Auditing OddsIntel vs WinnerOdds  {args.start} → {args.end}")
    print()
    print("Pulling WinnerOdds public picks...")
    rows = wo_pull_window(args.start, args.end)
    their = wo_summary(rows)
    print(f"  WO: n={their['n']}  ROI {their['roi']:+.2f}%  hit {their['hit_rate']:.1f}%")
    print()

    ours = our_stats(args.start, args.end)
    print(f"  OddsIntel matched: n={ours['n']}  ROI {ours['roi_pct']:+.2f}%")

    status = "ok" if (their["n"] >= MIN_SAMPLE and ours["n"] >= MIN_SAMPLE) else "insufficient_sample"

    payload = {
        "source": "WinnerOdds",
        "source_url": "https://winnerodds.com",
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": args.start, "end": args.end},
        "status": status,
        "min_sample_each_side": MIN_SAMPLE,
        "scope_notes": (
            "WinnerOdds picks pulled from their public GraphQL endpoint "
            "(period=12, FOOTBALL); their published unit-stake bets settled "
            "WIN/LOSE/VOID. OddsIntel cohort: production strategies "
            "(calibrated+beta+active maturity), 1X2 + OU 2.5 markets only, "
            "settled (won/lost). Both sides settled at €10 flat stake for "
            "apples-to-apples ROI."
        ),
        "reproducible_via": "scripts/audit_vs_winnerodds.py",
        "their_stats": {
            "n": their["n"],
            "stake_total": round(their["stake"], 2),
            "pnl_total": round(their["pnl"], 2),
            "roi_pct": round(their["roi"], 2),
            "hit_rate_pct": round(their["hit_rate"], 2),
            "avg_clv_pct": round(their["avg_clv"], 4),
        },
        "our_stats_same_window": ours,
    }

    out = Path("ledger") / "comparison_winnerodds.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
