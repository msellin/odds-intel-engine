"""
OU-PINNACLE-CAP cleanup — void historical OU bets where odds_at_pick > 2x
Pinnacle for the same (match, market, selection). These were placed via the
MAX-across-books aggregator promoting a mislabelled non-Pinnacle row that
the new gate would now drop. The blacklist + implied-sum gate from
ODDS-QUALITY-CLEANUP didn't catch single-side label errors when the under
side was legitimate.

Mirrors scripts/cleanup_ou_bets_after_quality_fix.py:
  1. Mark affected settled bets as result='void', pnl=0 (idempotent)
  2. Hard-delete affected pending bets (no result yet)
  3. Recompute simulated_bets.bankroll_after running totals per bot
  4. Recompute bots.current_bankroll = starting + sum(pnl)

Run: python scripts/cleanup_ou_pinnacle_cap.py            (dry-run)
     python scripts/cleanup_ou_pinnacle_cap.py --apply    (execute)
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402

VOID_REASON_PREFIX = "[OU-PINNACLE-CAP void] "

# (match, market, selection) -> Pinnacle min odds, vs simulated_bets odds_at_pick
SCAN_SQL = """
WITH bets AS (
  SELECT b.id, b.bot_id, b.match_id, b.odds_at_pick, b.selection,
         b.result, b.pnl, b.stake, b.created_at, b.reasoning,
         CASE
           WHEN b.selection LIKE 'over 0.5%%' OR b.selection LIKE 'under 0.5%%' THEN 'over_under_05'
           WHEN b.selection LIKE 'over 1.5%%' OR b.selection LIKE 'under 1.5%%' THEN 'over_under_15'
           WHEN b.selection LIKE 'over 2.5%%' OR b.selection LIKE 'under 2.5%%' THEN 'over_under_25'
           WHEN b.selection LIKE 'over 3.5%%' OR b.selection LIKE 'under 3.5%%' THEN 'over_under_35'
           WHEN b.selection LIKE 'over 4.5%%' OR b.selection LIKE 'under 4.5%%' THEN 'over_under_45'
         END AS pin_market,
         CASE
           WHEN b.selection LIKE 'over %%'  THEN 'over'
           WHEN b.selection LIKE 'under %%' THEN 'under'
         END AS pin_sel
  FROM simulated_bets b
  WHERE b.market = 'O/U'
),
pin AS (
  SELECT match_id, market, selection, MIN(odds) AS pin_odds
  FROM odds_snapshots
  WHERE bookmaker = 'Pinnacle' AND market LIKE 'over_under_%%'
  GROUP BY match_id, market, selection
)
SELECT b.id, b.bot_id, b.match_id, b.odds_at_pick, b.selection, b.result, b.pnl, b.stake,
       p.pin_odds, b.reasoning
FROM bets b
JOIN pin p ON p.match_id = b.match_id AND p.market = b.pin_market AND p.selection = b.pin_sel
WHERE b.odds_at_pick > 2.0 * p.pin_odds
  AND b.pin_market IS NOT NULL
"""


def scan() -> list[dict]:
    rows = execute_query(SCAN_SQL, [])
    return rows or []


def summarize(rows):
    by_bot = {}
    for r in rows:
        bot = r["bot_id"]
        b = by_bot.setdefault(bot, {"total": 0, "pending": 0, "won": 0, "lost": 0, "void": 0, "pnl": 0.0, "staked": 0.0})
        b["total"] += 1
        b["staked"] += float(r["stake"] or 0)
        b["pnl"] += float(r["pnl"] or 0)
        res = r["result"] or "pending"
        if res in b:
            b[res] += 1
    return by_bot


def apply_cleanup(rows):
    pending_ids = [r["id"] for r in rows if (r["result"] or "pending") == "pending"]
    settled_ids = [r["id"] for r in rows if (r["result"] or "pending") != "pending" and (r["result"] or "") != "void"]

    # 1. Hard-delete pending — no settled state to roll back
    if pending_ids:
        execute_write("DELETE FROM simulated_bets WHERE id = ANY(%s::uuid[])", [pending_ids])
        print(f"  Deleted {len(pending_ids)} pending bets")

    # 2. Void settled bets (idempotent — won't re-touch already-voided rows)
    if settled_ids:
        execute_write(
            """UPDATE simulated_bets
               SET result = 'void',
                   pnl = 0,
                   reasoning = %s || COALESCE(reasoning, '')
               WHERE id = ANY(%s::uuid[]) AND result <> 'void'""",
            [VOID_REASON_PREFIX, settled_ids],
        )
        print(f"  Voided {len(settled_ids)} settled bets")

    # 3. Recompute bankroll_after running totals per bot
    bot_ids_affected = list({r["bot_id"] for r in rows})
    for bot_id in bot_ids_affected:
        execute_write(
            """
            WITH ranked AS (
              SELECT id,
                     SUM(COALESCE(pnl, 0)) OVER (ORDER BY created_at, id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_pnl
              FROM simulated_bets
              WHERE bot_id = %s::uuid
            )
            UPDATE simulated_bets sb
            SET bankroll_after = (
              SELECT 1000.0 + ranked.running_pnl FROM ranked WHERE ranked.id = sb.id
            )
            WHERE sb.bot_id = %s::uuid
            """,
            [bot_id, bot_id],
        )

    # 4. Recompute bots.current_bankroll
    for bot_id in bot_ids_affected:
        execute_write(
            """UPDATE bots
               SET current_bankroll = starting_bankroll + COALESCE((
                 SELECT SUM(pnl) FROM simulated_bets WHERE bot_id = %s::uuid AND pnl IS NOT NULL
               ), 0)
               WHERE id = %s::uuid""",
            [bot_id, bot_id],
        )
    print(f"  Recomputed bankroll_after + current_bankroll for {len(bot_ids_affected)} bots")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Execute (default: dry-run)")
    args = p.parse_args()

    print(f"{'[APPLY]' if args.apply else '[DRY RUN]'} OU-PINNACLE-CAP cleanup\n")

    rows = scan()
    if not rows:
        print("No affected bets found — nothing to do.")
        return

    summary = summarize(rows)
    print(f"{'bot_id':40s}  total  pend  won  lost  void  staked    pnl")
    print("-" * 90)
    tot = {"total": 0, "pending": 0, "won": 0, "lost": 0, "void": 0, "pnl": 0.0, "staked": 0.0}
    for bot_id, s in sorted(summary.items(), key=lambda x: -x[1]["pnl"]):
        print(f"{bot_id}  {s['total']:5d}  {s['pending']:4d}  {s['won']:3d}  {s['lost']:4d}  {s['void']:4d}  {s['staked']:7.2f}  {s['pnl']:7.2f}")
        for k in tot:
            tot[k] += s[k]
    print("-" * 90)
    print(f"{'TOTAL':40s}  {tot['total']:5d}  {tot['pending']:4d}  {tot['won']:3d}  {tot['lost']:4d}  {tot['void']:4d}  {tot['staked']:7.2f}  {tot['pnl']:7.2f}\n")

    if not args.apply:
        print("Dry run only — re-run with --apply to execute.")
        return

    apply_cleanup(rows)
    print("\nDone.")


if __name__ == "__main__":
    main()
