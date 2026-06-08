#!/usr/bin/env python3
"""
CS2 settlement — fetch finished bo3.gg matches and write results.

Populates `cs2_results` for calibration/retraining joins,
and settles open rows in `cs2_bets` whose match has now finished.

Usage:
    python3 scripts/esports/cs2_settlement.py            # last 24h
    python3 scripts/esports/cs2_settlement.py --days 3   # wider lookback
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Reuse bo3.gg helpers + winner inference from the scanner
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.esports import cs2_elo_scanner as scanner
from workers.api_clients.db import execute_write, execute_query


async def _fetch_finished_window(days: int) -> list[dict]:
    """Fetch finished bo3.gg matches in the last `days` days."""
    try:
        from cs2api import CS2APIClient
    except ImportError:
        print("[!] cs2api not installed: pip3 install cs2api", file=sys.stderr)
        return []

    api = CS2APIClient()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        data = await scanner._bo3gg_request(api, "/matches", {
            "scope": "widget-matches",
            "page[offset]": 0,
            "page[limit]": 100,
            "sort": "-start_date",
            "filter[matches.status][in]": "finished,defwin",
            "filter[matches.discipline_id][eq]": 1,
            "filter[matches.start_date][gt]": since,
            "with": "teams,tournament,games",
        })
        return data.get("results", [])
    finally:
        await api.close()


def _result_row(r: dict) -> dict | None:
    """Convert raw bo3.gg match into a cs2_results row, or None if unusable."""
    bo3gg_id = r.get("id")
    if not bo3gg_id:
        return None

    t1 = (r.get("team1") or {}).get("name") or ""
    t2 = (r.get("team2") or {}).get("name") or ""
    if not t1 or not t2 or t1 == "TBD" or t2 == "TBD":
        return None

    winner_int = scanner._determine_series_winner(r)
    if winner_int is None:
        return None

    score1 = score2 = None
    try:
        score1 = int((r.get("team1") or {}).get("score", ""))
        score2 = int((r.get("team2") or {}).get("score", ""))
    except (ValueError, TypeError):
        pass

    try:
        kickoff = datetime.fromisoformat(r["start_date"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        kickoff = None

    return {
        "bo3gg_id": int(bo3gg_id),
        "team1": t1,
        "team2": t2,
        "kickoff_time": kickoff.isoformat() if kickoff else None,
        "best_of": r.get("bo_type") or 3,
        "winner": "team1" if winner_int == 1 else "team2",
        "score1": score1,
        "score2": score2,
        "raw_status": r.get("status"),
    }


def _write_results(rows: list[dict]) -> int:
    written = 0
    for row in rows:
        execute_write("""
            INSERT INTO cs2_results
                (bo3gg_id, team1, team2, kickoff_time, best_of, winner, score1, score2, raw_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (bo3gg_id) DO UPDATE SET
                winner = EXCLUDED.winner,
                score1 = EXCLUDED.score1,
                score2 = EXCLUDED.score2,
                raw_status = EXCLUDED.raw_status,
                finished_at = NOW()
        """, (
            row["bo3gg_id"], row["team1"], row["team2"], row["kickoff_time"],
            row["best_of"], row["winner"], row["score1"], row["score2"], row["raw_status"],
        ))
        written += 1
    return written


def _settle_bets() -> int:
    """Settle any open cs2_bets whose match has a result row.

    cs2_bets has match_id → cs2_upcoming_matches.id.
    cs2_upcoming_matches has bo3gg_id → cs2_results.bo3gg_id.
    """
    open_bets = execute_query("""
        SELECT b.id, b.team_name, b.market, b.odds, b.stake,
               m.team1, m.team2, m.bo3gg_id, m.best_of,
               r.winner, r.score1, r.score2
        FROM cs2_bets b
        JOIN cs2_upcoming_matches m ON b.match_id = m.id
        JOIN cs2_results r ON m.bo3gg_id = r.bo3gg_id
        WHERE b.result IS NULL
    """, ())

    settled = 0
    for row in open_bets:
        b_id = row["id"]
        team_name = row["team_name"]
        market = row["market"]
        odds = float(row["odds"])
        stake = float(row["stake"])

        bet_won = _bet_won(market, team_name, row)
        if bet_won is None:
            continue  # unknown market — skip

        result = "won" if bet_won else "lost"
        pnl = round(stake * (odds - 1), 4) if bet_won else round(-stake, 4)
        execute_write(
            "UPDATE cs2_bets SET result = %s, pnl = %s WHERE id = %s",
            (result, pnl, b_id),
        )
        settled += 1

    return settled


def _bet_won(market: str, team_name: str, row: dict) -> bool | None:
    """Return whether bet won, or None if market not supported."""
    winner_team = row["team1"] if row["winner"] == "team1" else row["team2"]
    s1, s2 = row.get("score1"), row.get("score2")

    if market == "match_winner":
        return team_name == winner_team

    if market == "atleast1map":
        # Team won at least 1 map if their score >= 1
        if s1 is None or s2 is None:
            return None
        team_score = s1 if team_name == row["team1"] else s2
        return team_score >= 1

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="CS2 settlement — bo3.gg results → cs2_results + settle cs2_bets")
    parser.add_argument("--days", type=int, default=1, help="Lookback window (default 1)")
    args = parser.parse_args()

    print(f"\n=== CS2 SETTLEMENT  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  Fetching finished matches in last {args.days}d from bo3.gg...")

    raw = asyncio.run(_fetch_finished_window(args.days))
    print(f"  {len(raw)} finished matches returned")

    rows = [r for r in (_result_row(x) for x in raw) if r]
    print(f"  {len(rows)} usable result rows")

    written = _write_results(rows)
    print(f"  {written} written to cs2_results (insert or update)")

    settled = _settle_bets()
    print(f"  {settled} cs2_bets settled\n")


if __name__ == "__main__":
    main()
