"""
CS2 Coolbet placer — paper-first.

Reads recent unplaced cs2_simulated_bets, looks up Coolbet odds from
cs2_upcoming_matches.coolbet_odds{1,2} (scraped by cs2_coolbet_scanner),
applies a slippage gate, and records to cs2_real_bets.

v1 ships with paper-only flow. Real-money execution (--execute) is gated
behind explicit operator authorization — memory note
`feedback_coolbet_execute_safety` is binding: never run --execute without
the user typing `EXECUTE AUTHORIZED` first.

Run:
    python3 scripts/esports/cs2_coolbet_placer.py --record       # paper
    python3 scripts/esports/cs2_coolbet_placer.py --execute      # REAL — gated
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402


# Max odds drift Coolbet can have vs bot's pick before we skip.
# Negative slippage = Coolbet odds dropped (bet got worse). Positive = improved.
MAX_NEGATIVE_SLIPPAGE_PCT = -0.05  # -5%


def load_unplaced_picks() -> list[dict]:
    """Pending bot picks with kickoff in the next 24h, not already placed."""
    return execute_query("""
        SELECT
            sb.id              AS sim_id,
            sb.bot_name,
            sb.bo3gg_id,
            sb.team1, sb.team2,
            sb.market,
            sb.pick,
            sb.odds_at_pick    AS bot_odds_at_pick,
            sb.fair_odds,
            sb.edge,
            sb.stake_eur,
            sb.kickoff_time,
            um.coolbet_odds1, um.coolbet_odds2,
            um.pinnacle_odds1, um.pinnacle_odds2
        FROM cs2_simulated_bets sb
        LEFT JOIN cs2_upcoming_matches um ON um.bo3gg_id = sb.bo3gg_id
        WHERE sb.result IS NULL
          AND sb.kickoff_time > NOW()
          AND sb.kickoff_time < NOW() + INTERVAL '24 hours'
          AND sb.bot_name LIKE 'bot_cs2_%%'
          AND NOT EXISTS (
            SELECT 1 FROM cs2_real_bets rb WHERE rb.cs2_simulated_bet_id = sb.id
          )
        ORDER BY sb.kickoff_time
    """, None)


def coolbet_odds_for_pick(row: dict) -> tuple[float | None, str]:
    """Map (market, pick) → Coolbet odds. Returns (odds, selection) or (None, reason).
    CS2 only has the match-winner market today (no AH / OU). cs2_simulated_bets
    labels it 'match_winner'."""
    if row["market"] != "match_winner":
        return None, f"unsupported_market:{row['market']}"
    pick = (row["pick"] or "").strip()
    # pick is the team NAME, not 'team1'/'team2'. Match by team name.
    if pick == row["team1"]:
        odds, sel = row["coolbet_odds1"], "team1"
    elif pick == row["team2"]:
        odds, sel = row["coolbet_odds2"], "team2"
    else:
        return None, f"pick_mismatch:{pick}_vs_{row['team1']}_or_{row['team2']}"
    if odds is None:
        return None, "no_coolbet_odds"
    return float(odds), sel


def insert_real_bet(row: dict, coolbet_odds: float, selection: str,
                    paper: bool, ticket_id: str | None = None) -> int | None:
    bot_odds = float(row["bot_odds_at_pick"]) if row["bot_odds_at_pick"] is not None else None
    slippage = (coolbet_odds - bot_odds) / bot_odds if bot_odds else None
    # Modeled fair_odds gives us edge at the captured price:
    # edge = (captured / fair) - 1 since odds<fair means -EV.
    fair = float(row["fair_odds"]) if row["fair_odds"] is not None else None
    edge_taken = (coolbet_odds / fair) - 1 if fair else None
    stake = float(row["stake_eur"]) if row["stake_eur"] is not None else None

    rows = execute_query("""
        INSERT INTO cs2_real_bets
            (cs2_simulated_bet_id, bot_name, bo3gg_id, team1, team2,
             market, selection, bookmaker,
             bot_odds_at_pick, captured_odds, slippage_pct, edge_pct_taken,
             paper, stake_eur, ticket_id, placed_at, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'coolbet',
                %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        ON CONFLICT (cs2_simulated_bet_id) DO NOTHING
        RETURNING id
    """, (row["sim_id"], row["bot_name"], row["bo3gg_id"], row["team1"], row["team2"],
          row["market"], selection,
          bot_odds, coolbet_odds, slippage, edge_taken,
          paper, stake, ticket_id, "v1 paper" if paper else "v1 real"))
    return rows[0]["id"] if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true",
                    help="Paper mode: write to cs2_real_bets with paper=true. No Coolbet API call.")
    ap.add_argument("--execute", action="store_true",
                    help="REAL MONEY: place on Coolbet. Requires explicit operator authorization.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap placements per run (0 = no cap)")
    args = ap.parse_args()

    if args.execute:
        # Hard gate per memory feedback_coolbet_execute_safety.
        print("[!] --execute flag detected. This places REAL MONEY bets on Coolbet.",
              file=sys.stderr)
        print("[!] Refusing to run from CLI without explicit operator authorization.",
              file=sys.stderr)
        print("[!] To proceed, edit this script's gate or set EXECUTE_AUTHORIZED=1 env.",
              file=sys.stderr)
        if os.getenv("EXECUTE_AUTHORIZED") != "1":
            sys.exit(2)

    print(f"=== CS2 Coolbet Placer  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  mode: {'EXECUTE (REAL MONEY)' if args.execute else 'RECORD (paper)'}")

    picks = load_unplaced_picks()
    print(f"  {len(picks)} unplaced picks in next 24h")

    if args.limit:
        picks = picks[:args.limit]

    placed = skipped = 0
    for row in picks:
        coolbet, sel_or_reason = coolbet_odds_for_pick(row)
        if coolbet is None:
            print(f"  [-] {row['bot_name']:20} {row['team1'][:15]:15} vs {row['team2'][:15]:15}  "
                  f"skip: {sel_or_reason}")
            skipped += 1
            continue

        bot_odds = float(row["bot_odds_at_pick"])
        slip = (coolbet - bot_odds) / bot_odds
        if slip < MAX_NEGATIVE_SLIPPAGE_PCT:
            print(f"  [-] {row['bot_name']:20} {row['pick'][:15]:15}  "
                  f"slippage {slip*100:+.1f}% < {MAX_NEGATIVE_SLIPPAGE_PCT*100}% — skip (line moved against us)")
            skipped += 1
            continue

        # Paper mode: just record. Real mode would POST to Coolbet here.
        rid = insert_real_bet(row, coolbet, sel_or_reason,
                              paper=not args.execute, ticket_id=None)
        if rid is None:
            print(f"  [=] {row['bot_name']:20} {row['pick'][:15]:15}  already placed (dedup)")
            skipped += 1
            continue
        print(f"  [✓] {row['bot_name']:20} {row['pick'][:15]:15} @ {coolbet:.2f}  "
              f"(bot saw {bot_odds:.2f}, slip {slip*100:+.1f}%, paper={not args.execute})")
        placed += 1

    print(f"\n  placed: {placed}  skipped: {skipped}")


if __name__ == "__main__":
    main()
