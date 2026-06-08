#!/usr/bin/env python3
"""
Single CS2 value bot — bot_cs2_value_v1.

Scans cs2_upcoming_matches for value opportunities and writes one
cs2_simulated_bets row per (match, market, bookie). One bet per bookie per
side per match (UNIQUE constraint prevents re-fires on the same opportunity).

Value rule:
  - We have model coverage (has_elo_history = TRUE).
  - Bookie offers >= our threshold odds for the side.
  - Implied edge = (bookie_odds - threshold_odds) / threshold_odds >= 0.05 (5%)
    (the threshold itself already bakes in a 3% target edge, so we want at
    least an extra 5% above it before firing).

Markets supported: match_winner (1x2), atleast1map (BO3/BO5 only).

Settlement happens in cs2_settlement.py — that job populates cs2_results,
which we join to here to mark won/lost/pnl.

Usage:
    python3 scripts/esports/cs2_bot.py              # dry run, print only
    python3 scripts/esports/cs2_bot.py --record     # write simulated_bets
    python3 scripts/esports/cs2_bot.py --settle     # only run settlement step
"""
import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write

BOT_NAME = "bot_cs2_value_v1"
MIN_EXTRA_EDGE = 0.05   # 5% above the threshold (which already has 3% baked in)
STAKE = 1.0             # uniform stake — sizing comes later from Kelly


def _load_open_matches() -> list[dict]:
    """Pre-kickoff matches with model coverage."""
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=24)).isoformat()
    return execute_query("""
        SELECT id, bo3gg_id, team1, team2, kickoff_time, best_of,
               fair_odds1, fair_odds2, threshold_odds1, threshold_odds2,
               fair_odds_map1, fair_odds_map2, threshold_map1, threshold_map2,
               bookie_odds1, bookie_odds2,
               coolbet_odds1, coolbet_odds2,
               pinnacle_odds1, pinnacle_odds2
        FROM cs2_upcoming_matches
        WHERE has_elo_history = TRUE
          AND kickoff_time >= %s
          AND kickoff_time <= %s
          AND threshold_odds1 IS NOT NULL
    """, (now.isoformat(), horizon))


def _scan_one(row: dict) -> list[dict]:
    """Return list of (match, side, market, bookie, odds, fair, thr) value picks."""
    picks: list[dict] = []
    best_of = row["best_of"] or 3
    bookmakers = (
        ("bo3gg", row["bookie_odds1"], row["bookie_odds2"]),
        ("coolbet", row["coolbet_odds1"], row["coolbet_odds2"]),
        ("pinnacle", row["pinnacle_odds1"], row["pinnacle_odds2"]),
    )

    for bookie, b_odds1, b_odds2 in bookmakers:
        # match_winner
        for side, team_name, odds, fair, thr in [
            ("team1", row["team1"], b_odds1, row["fair_odds1"], row["threshold_odds1"]),
            ("team2", row["team2"], b_odds2, row["fair_odds2"], row["threshold_odds2"]),
        ]:
            if odds is None or thr is None or fair is None:
                continue
            if odds < thr:
                continue
            extra = (odds - thr) / thr
            if extra < MIN_EXTRA_EDGE:
                continue
            picks.append({
                "side": side, "team": team_name, "market": "match_winner",
                "bookie": bookie, "odds": float(odds), "fair": float(fair),
                "thr": float(thr), "edge": extra,
            })

        # atleast1map (BO3/5 only)
        if best_of >= 3:
            for side, team_name, odds, fair, thr in [
                ("team1", row["team1"], b_odds1, row["fair_odds_map1"], row["threshold_map1"]),
                ("team2", row["team2"], b_odds2, row["fair_odds_map2"], row["threshold_map2"]),
            ]:
                if odds is None or thr is None or fair is None:
                    continue
                if odds < thr:
                    continue
                extra = (odds - thr) / thr
                if extra < MIN_EXTRA_EDGE:
                    continue
                picks.append({
                    "side": side, "team": team_name, "market": "atleast1map",
                    "bookie": bookie, "odds": float(odds), "fair": float(fair),
                    "thr": float(thr), "edge": extra,
                })

    return picks


def _write_bet(row: dict, pick: dict) -> bool:
    res = execute_write("""
        INSERT INTO cs2_simulated_bets
            (bot_name, bo3gg_id, placed_at, kickoff_time,
             team1, team2, market, pick, bookie,
             odds_at_pick, fair_odds, threshold_odds, edge, stake)
        VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bot_name, bo3gg_id, market, bookie) DO NOTHING
    """, (
        BOT_NAME, row["bo3gg_id"], row["kickoff_time"],
        row["team1"], row["team2"],
        pick["market"], pick["team"], pick["bookie"],
        pick["odds"], pick["fair"], pick["thr"], pick["edge"], STAKE,
    ))
    return bool(res)


def _settle() -> int:
    """Settle open cs2_simulated_bets against cs2_results."""
    open_bets = execute_query("""
        SELECT b.id, b.team1, b.team2, b.market, b.pick, b.odds_at_pick, b.stake,
               r.winner, r.score1, r.score2
        FROM cs2_simulated_bets b
        JOIN cs2_results r ON b.bo3gg_id = r.bo3gg_id
        WHERE b.result IS NULL
    """, ())

    settled = 0
    for row in open_bets:
        won = _bet_won(row)
        if won is None:
            continue
        odds = float(row["odds_at_pick"]); stake = float(row["stake"])
        result = "won" if won else "lost"
        pnl = round(stake * (odds - 1), 4) if won else round(-stake, 4)
        execute_write(
            "UPDATE cs2_simulated_bets SET result = %s, pnl = %s, settled_at = NOW() WHERE id = %s",
            (result, pnl, row["id"]),
        )
        settled += 1
    return settled


def _bet_won(row: dict) -> bool | None:
    winner_team = row["team1"] if row["winner"] == "team1" else row["team2"]
    if row["market"] == "match_winner":
        return row["pick"] == winner_team
    if row["market"] == "atleast1map":
        s1, s2 = row.get("score1"), row.get("score2")
        if s1 is None or s2 is None:
            return None
        team_score = s1 if row["pick"] == row["team1"] else s2
        return team_score >= 1
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--record", action="store_true", help="Write simulated_bets to DB")
    p.add_argument("--settle", action="store_true", help="Only run settlement, no scan")
    args = p.parse_args()

    print(f"\n=== CS2 BOT  {BOT_NAME}  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    if args.settle:
        n = _settle()
        print(f"  settled: {n} bets\n")
        return

    matches = _load_open_matches()
    print(f"  {len(matches)} open model-covered matches")

    total_picks, total_written = 0, 0
    for row in matches:
        picks = _scan_one(row)
        if not picks:
            continue
        for p in picks:
            total_picks += 1
            tag = "  fired" if args.record else "  dry"
            print(f"    {tag}  {row['team1']:25} vs {row['team2']:25}  "
                  f"{p['market']:12} → {p['team']:20} @ {p['bookie']:8} {p['odds']:>5.2f}  "
                  f"(thr {p['thr']:.2f}, edge +{p['edge']*100:.1f}%)")
            if args.record:
                if _write_bet(row, p):
                    total_written += 1

    print(f"\n  picks: {total_picks}  written: {total_written}")

    if args.record:
        n = _settle()
        print(f"  settled: {n} prior open bets")
    print()


if __name__ == "__main__":
    main()
