#!/usr/bin/env python3
"""
Snapshot closing odds for pending CS2 bets — CLV measurement.

For each pending row in cs2_simulated_bets whose match kicks off within
CLOSING_WINDOW_MIN minutes, read the current odds for the same bookie from
cs2_upcoming_matches and store as closing_odds_at_kickoff. Computes CLV
when settlement also has odds_at_pick.

Runs frequently (every 15 min) so we catch each bet's "last possible odds
snapshot" close to kickoff.

Usage:
    python3 scripts/esports/cs2_clv_snapshot.py
"""
import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write


CLOSING_WINDOW_MIN = 45      # snapshot when match starts within next 45 min
BOOKIE_COL = {
    "bo3gg":    ("bookie_odds1",   "bookie_odds2"),
    "coolbet":  ("coolbet_odds1",  "coolbet_odds2"),
    "pinnacle": ("pinnacle_odds1", "pinnacle_odds2"),
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true", help="Print only, do not write")
    args = p.parse_args()

    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(minutes=CLOSING_WINDOW_MIN)).isoformat()

    rows = execute_query("""
        SELECT b.id, b.bo3gg_id, b.team1, b.team2, b.market, b.pick, b.bookie, b.odds_at_pick,
               u.bookie_odds1, u.bookie_odds2, u.coolbet_odds1, u.coolbet_odds2,
               u.pinnacle_odds1, u.pinnacle_odds2
        FROM cs2_simulated_bets b
        JOIN cs2_upcoming_matches u ON u.bo3gg_id = b.bo3gg_id
        WHERE b.closing_odds_at_kickoff IS NULL
          AND b.result IS NULL
          AND b.kickoff_time BETWEEN %s AND %s
    """, (now.isoformat(), horizon))

    print(f"=== CS2 CLV snapshot  {now.strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  pending bets in next {CLOSING_WINDOW_MIN}m: {len(rows)}")

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from scraper_state import scraper_run  # type: ignore
    except ImportError:
        scraper_run = None  # type: ignore

    ctx = scraper_run("clv_snapshot", "Closing-odds snapshot for pending bets (every 15min)") if (scraper_run and not args.dry) else None
    st = ctx.__enter__() if ctx else None
    if st:
        st.set_total(len(rows))
        st.set_pending(len(rows))
    written = 0
    for r in rows:
        cols = BOOKIE_COL.get(r["bookie"])
        if not cols:
            continue
        col1, col2 = cols
        # Which side did we pick? Match by team name.
        side_odds = r[col1] if r["pick"] == r["team1"] else r[col2]
        if side_odds is None:
            continue
        closing = float(side_odds)
        op = float(r["odds_at_pick"])
        clv = round(op / closing - 1.0, 4) if closing > 1 else None
        action = "  dry" if args.dry else "  set"
        print(f"  {action}  {r['team1'][:18]:18} vs {r['team2'][:18]:18}  pick={r['pick'][:18]:18}  "
              f"book={r['bookie']:8}  op={op:.2f}  closing={closing:.2f}  CLV={clv:+.4f}")
        if not args.dry:
            execute_write("""
                UPDATE cs2_simulated_bets
                SET closing_odds_at_kickoff = %s,
                    closing_odds_snapshot_at = NOW(),
                    clv = %s
                WHERE id = %s
            """, (closing, clv, r["id"]))
            written += 1
            if st: st.tick_done()

    print(f"\n  updated {written} bets\n")
    if ctx: ctx.__exit__(None, None, None)


if __name__ == "__main__":
    main()
