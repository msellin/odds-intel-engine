#!/usr/bin/env python3
"""
Operator-supplied manual settlement for stuck CS2 bets.

For matches that fall outside all three auto-settle strategies
(bo3.gg --days 3, HLTV id-window, HLTV-live, PandaScore ±6h), the
operator looks up the result on the web (HLTV team page, Liquipedia,
event organizer site, news) and supplies it here. We INSERT into
cs2_results (or ON CONFLICT UPDATE), then cs2_bot --settle closes
the bet rows.

Idempotent: re-running for the same bo3gg_id overwrites cs2_results
with the supplied values; cs2_bot.--settle skips already-resolved
rows so PnL only adjusts when winner/score changes.

Usage:
    python3 scripts/esports/cs2_manual_settle.py \\
        --bo3gg-id 121929 --winner team1 --score1 2 --score2 1 \\
        --note "hltv 06-12 manual"
    python3 scripts/esports/cs2_manual_settle.py \\
        --bo3gg-id 121929 --winner team1 --score1 2 --score2 1 --apply

By default this runs a DRY-RUN — shows what would change without
writing. Use --apply to actually update the DB.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write


def _load_bet_context(bo3gg_id: int) -> dict | None:
    """Find the bet's team1/team2/kickoff_time/best_of. Falls back to the
    cs2_upcoming_matches row when no open bet exists (lets the operator
    settle a match before any bot fired on it)."""
    rows = execute_query("""
        SELECT DISTINCT ON (bo3gg_id)
               bo3gg_id, team1, team2, kickoff_time
        FROM cs2_simulated_bets
        WHERE bo3gg_id = %s
        ORDER BY bo3gg_id, placed_at DESC
        LIMIT 1
    """, (bo3gg_id,))
    if rows:
        bet = dict(rows[0])
        bo_rows = execute_query(
            "SELECT best_of FROM cs2_upcoming_matches WHERE bo3gg_id = %s LIMIT 1",
            (bo3gg_id,),
        )
        bet["best_of"] = bo_rows[0]["best_of"] if bo_rows else None
        return bet
    rows = execute_query("""
        SELECT bo3gg_id, team1, team2, kickoff_time, best_of
        FROM cs2_upcoming_matches WHERE bo3gg_id = %s LIMIT 1
    """, (bo3gg_id,))
    return dict(rows[0]) if rows else None


def _existing_result(bo3gg_id: int) -> dict | None:
    rows = execute_query(
        "SELECT winner, score1, score2, raw_status FROM cs2_results WHERE bo3gg_id = %s",
        (bo3gg_id,),
    )
    return dict(rows[0]) if rows else None


def _upsert_result(bet: dict, winner: str, score1: int, score2: int, note: str) -> bool:
    raw_status = f"manual:{note}" if note else "manual"
    res = execute_write("""
        INSERT INTO cs2_results
            (bo3gg_id, team1, team2, kickoff_time, best_of, winner, score1, score2, raw_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bo3gg_id) DO UPDATE SET
            winner = EXCLUDED.winner,
            score1 = EXCLUDED.score1,
            score2 = EXCLUDED.score2,
            raw_status = EXCLUDED.raw_status,
            finished_at = NOW()
    """, (bet["bo3gg_id"], bet["team1"], bet["team2"], bet["kickoff_time"],
          bet.get("best_of"), winner, score1, score2, raw_status))
    return bool(res)


def _run_bot_settle() -> None:
    """Chain cs2_bot --settle so newly-resolved cs2_results rows close the
    open cs2_simulated_bets rows + update bot bankrolls in one shot."""
    print()
    print("  → running cs2_bot --settle to close newly-resolvable bets...")
    cmd = [sys.executable, "scripts/esports/cs2_bot.py", "--settle"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    print("    " + (res.stdout or "").strip().replace("\n", "\n    "))
    if res.returncode != 0:
        print(f"    [!] cs2_bot --settle exited {res.returncode}: "
              f"{(res.stderr or '').strip()[:300]}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--bo3gg-id", type=int, required=True,
                   help="cs2_results.bo3gg_id — the match identifier")
    p.add_argument("--winner", choices=["team1", "team2"], required=True,
                   help="Which side won (team1/team2 in the bet's orientation)")
    p.add_argument("--score1", type=int, required=True, help="Maps won by team1")
    p.add_argument("--score2", type=int, required=True, help="Maps won by team2")
    p.add_argument("--note", default="", help="Free-form note stamped into raw_status")
    p.add_argument("--apply", action="store_true",
                   help="Actually write (default: dry-run only)")
    args = p.parse_args()

    bet = _load_bet_context(args.bo3gg_id)
    if not bet:
        print(f"[!] no bet or upcoming row found for bo3gg_id={args.bo3gg_id}",
              file=sys.stderr)
        return 2

    existing = _existing_result(args.bo3gg_id)

    # Sanity: winner side must match the score direction.
    if (args.winner == "team1") != (args.score1 > args.score2):
        print(f"[!] winner={args.winner} contradicts score {args.score1}-{args.score2}",
              file=sys.stderr)
        return 2

    print(f"\n  bet:     bo3gg_id={bet['bo3gg_id']}  {bet['team1']} vs {bet['team2']}  "
          f"KO {bet['kickoff_time']}  BO={bet.get('best_of') or '?'}")
    if existing:
        print(f"  existing cs2_results: winner={existing['winner']}  "
              f"score={existing['score1']}-{existing['score2']}  "
              f"raw_status={existing['raw_status']}")
    else:
        print("  existing cs2_results: (none — will INSERT)")

    winner_name = bet["team1"] if args.winner == "team1" else bet["team2"]
    print(f"  proposed: winner={args.winner} ({winner_name})  "
          f"score={args.score1}-{args.score2}  note={args.note or '(none)'}")
    print(f"  mode: {'APPLY' if args.apply else 'dry-run'}\n")

    if args.apply:
        ok = _upsert_result(bet, args.winner, args.score1, args.score2, args.note)
        print(f"  cs2_results upsert: {'✓ written' if ok else '⚠ no-op (race?)'}")
        _run_bot_settle()
    else:
        print("  (dry-run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
