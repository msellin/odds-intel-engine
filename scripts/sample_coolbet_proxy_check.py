"""
SELF-USE-VALIDATION — Phase 0.1 sampling script

Pulls today's still-pending bets from active bots, joins odds_snapshots for
Unibet (Kambi-platform proxy for Coolbet) + Bet365, prints a worksheet you
can fill in by hand from coolbet.ee.

Why forward-looking, not backward-looking: Coolbet does not publish historical
odds. Once a match kicks off, the displayed price is gone. So we sample
matches that haven't kicked off yet — open coolbet.ee in another tab, write
down the actual displayed price for each row, decide if Unibet ≈ Coolbet.

Run:
  python scripts/sample_coolbet_proxy_check.py            # default 30 rows
  python scripts/sample_coolbet_proxy_check.py --n 50
  python scripts/sample_coolbet_proxy_check.py --csv worksheet.csv

Workflow:
  1. Run this. It prints a table + saves CSV.
  2. Open coolbet.ee, look up each match, find the same market+selection.
  3. Write the actual Coolbet price next to the bot's row.
  4. Compute % gap (Unibet vs Coolbet, Bet365 vs Coolbet).
  5. After 20-30 samples decide if Unibet is a good enough proxy.
"""

import argparse
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

# Map our internal market/selection labels to the odds_snapshots
# (market, selection) tuple. Mirrors the Phase 2 bookmaker price lookup.
def _bot_to_snapshot_keys(market: str, selection: str) -> tuple[str, str] | None:
    """('1X2','Home') -> ('1x2','home') etc."""
    m = (market or "").lower()
    s = (selection or "").lower().strip()
    if m == "1x2":
        if s in ("home", "draw", "away"):
            return ("1x2", s)
    if m == "btts":
        if s in ("yes", "no"):
            return ("btts", s)
    if m == "o/u":
        for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
            if s.startswith(f"over {line}"):
                return (f"over_under_{line.replace('.', '')}", "over")
            if s.startswith(f"under {line}"):
                return (f"over_under_{line.replace('.', '')}", "under")
    return None


SAMPLE_SQL = """
SELECT b.id AS bet_id, b.bot_id, bots.name AS bot_name, b.match_id,
       b.market, b.selection, b.odds_at_pick, b.calibrated_prob,
       b.edge_percent, b.created_at AS pick_time, b.stake,
       m.date AS kickoff, m.home_team_id, m.away_team_id,
       ht.name AS home_team, at.name AS away_team,
       l.name AS league, l.country
FROM simulated_bets b
JOIN bots ON bots.id = b.bot_id
JOIN matches m ON m.id = b.match_id
LEFT JOIN teams ht ON ht.id = m.home_team_id
LEFT JOIN teams at ON at.id = m.away_team_id
LEFT JOIN leagues l ON l.id = m.league_id
WHERE b.result = 'pending'
  AND m.date > NOW()         -- only matches that have NOT kicked off yet
ORDER BY m.date ASC, ABS(b.edge_percent) DESC NULLS LAST
"""

PIN_REF_SQL = """
SELECT odds FROM odds_snapshots
WHERE match_id = %s AND market = %s AND selection = %s
  AND bookmaker = %s
ORDER BY ABS(EXTRACT(EPOCH FROM (timestamp - %s::timestamptz)))
LIMIT 1
"""


def fetch_book_price(match_id, market, selection, bookmaker, pick_time):
    """Closest-in-time row for one (book, market, selection)."""
    r = execute_query(PIN_REF_SQL, [match_id, market, selection, bookmaker, pick_time])
    return float(r[0]["odds"]) if r else None


def gap_pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b * 100.0


def main():
    p = argparse.ArgumentParser(description="Phase 0 Coolbet-proxy sampling worksheet")
    p.add_argument("--limit", type=int, default=None, help="Optional cap on rows (default: all)")
    p.add_argument("--csv", type=str, default="dev/active/self-use-validation-phase0-worksheet.csv",
                   help="Output CSV path (default writes into dev/active/)")
    args = p.parse_args()

    print("Pulling all pending bets on matches that have NOT kicked off yet...\n")
    rows = execute_query(SAMPLE_SQL, [])
    if not rows:
        print("No pending bets on upcoming matches. Wait for the morning betting run, then re-run.")
        return
    if args.limit:
        rows = rows[:args.limit]

    enriched = []
    for r in rows:
        mkt_keys = _bot_to_snapshot_keys(r["market"], r["selection"])
        if mkt_keys is None:
            continue
        snap_market, snap_sel = mkt_keys
        pick_time = r["pick_time"]
        unibet = fetch_book_price(r["match_id"], snap_market, snap_sel, "Unibet", pick_time)
        bet365 = fetch_book_price(r["match_id"], snap_market, snap_sel, "Bet365", pick_time)
        pinnacle = fetch_book_price(r["match_id"], snap_market, snap_sel, "Pinnacle", pick_time)
        bot_odds = float(r["odds_at_pick"]) if r["odds_at_pick"] else None
        enriched.append({
            "kickoff_utc": str(r["kickoff"])[:16],
            "league": f"{r['country']} / {r['league']}" if r["country"] else r["league"],
            "match": f"{r['home_team']} vs {r['away_team']}",
            "bot": r["bot_name"],
            "market": r["market"],
            "sel": r["selection"],
            "bot_odds": bot_odds,
            "unibet": unibet,
            "bet365": bet365,
            "pinnacle": pinnacle,
            "coolbet_actual": "",  # blank — fill in by hand from coolbet.ee
            "unibet_vs_bot_%": round(gap_pct(unibet, bot_odds), 2) if unibet else None,
            "bet365_vs_bot_%": round(gap_pct(bet365, bot_odds), 2) if bet365 else None,
            "stake_eur_2-3": "",  # placeholder for the stake you'd use
            "match_id": r["match_id"],
            "bet_id": r["bet_id"],
        })

    # Print compact table
    print(f"{'Kickoff':<16}  {'Bot':<25}  {'Sel':<14}  {'Bot':>6}  {'Unibet':>6}  {'Bet365':>6}  {'Pin':>6}  Match")
    print("-" * 130)
    for e in enriched:
        bo = f"{e['bot_odds']:.2f}" if e['bot_odds'] else "—"
        un = f"{e['unibet']:.2f}" if e['unibet'] else "—"
        b3 = f"{e['bet365']:.2f}" if e['bet365'] else "—"
        pi = f"{e['pinnacle']:.2f}" if e['pinnacle'] else "—"
        bot = (e['bot'] or '')[:24]
        sel = (e['sel'] or '')[:13]
        match = e['match'][:60]
        print(f"{e['kickoff_utc']:<16}  {bot:<25}  {sel:<14}  {bo:>6}  {un:>6}  {b3:>6}  {pi:>6}  {match}")

    # Save CSV — open in Excel/Numbers, fill coolbet_actual column from coolbet.ee
    out_path = Path(args.csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(enriched[0].keys()))
        w.writeheader()
        w.writerows(enriched)
    print(f"\nWorksheet saved: {out_path}")
    print(f"Rows usable: {len(enriched)} of {len(rows)} sampled")
    print()
    print("Next steps:")
    print("  1. Open the CSV in Numbers/Excel.")
    print("  2. Open coolbet.ee in a browser. For each row, find the match + market and write")
    print("     the displayed price into the 'coolbet_actual' column.")
    print("  3. After 20-30 rows filled, compute mean/median gap between Coolbet and Unibet.")
    print("  4. If gap is consistently <3%, Unibet is a good enough proxy — proceed to Phase 2.")
    print("  5. If gap >5% or Coolbet often doesn't offer the market, consider Phase 1")
    print("     (The Odds API direct Coolbet feed, free tier 500 reqs/mo).")


if __name__ == "__main__":
    main()
