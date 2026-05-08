#!/usr/bin/env python3
"""
Daily Twitter post draft generator.
Finds today's most interesting match signal and outputs a ready-to-post tweet.
Run: python scripts/daily_twitter_draft.py
"""

import os
import sys
from datetime import date
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv()

DB_URL = os.environ["DATABASE_URL"]

def get_conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def find_best_match():
    """Find today's most interesting match based on injuries + tight Pinnacle O/U line."""
    today = date.today().isoformat()
    conn = get_conn()
    cur = conn.cursor()

    # Get today's scheduled matches with Pinnacle O/U 2.5 odds
    # League priority weights: top competitions score higher regardless of injury count
    cur.execute("""
        WITH pinnacle_ou AS (
            SELECT DISTINCT ON (os.match_id, os.selection)
                os.match_id,
                os.selection,
                os.odds
            FROM odds_snapshots os
            JOIN matches m ON os.match_id = m.id
            WHERE m.status = 'scheduled'
              AND DATE(m.date) = %s
              AND os.market = 'over_under_25'
              AND os.bookmaker = 'Pinnacle'
            ORDER BY os.match_id, os.selection, os.timestamp DESC
        ),
        injury_counts AS (
            SELECT match_id,
                   COUNT(*) FILTER (WHERE team_side = 'home') AS home_out,
                   COUNT(*) FILTER (WHERE team_side = 'away') AS away_out,
                   COUNT(*) AS total_out
            FROM match_injuries
            GROUP BY match_id
        ),
        league_priority AS (
            SELECT l.id AS league_id,
                   CASE l.name
                     WHEN 'UEFA Champions League'     THEN 100
                     WHEN 'UEFA Europa League'         THEN 80
                     WHEN 'UEFA Conference League'     THEN 70
                     WHEN 'Premier League'             THEN 60
                     WHEN 'La Liga'                    THEN 60
                     WHEN 'Bundesliga'                 THEN 60
                     WHEN 'Serie A'                    THEN 60
                     WHEN 'Ligue 1'                    THEN 55
                     WHEN 'Eredivisie'                 THEN 40
                     WHEN 'Primeira Liga'              THEN 40
                     WHEN 'Championship'               THEN 35
                     WHEN 'Scottish Premiership'       THEN 30
                     WHEN 'MLS'                        THEN 25
                     WHEN 'Ekstraklasa'                THEN 20
                     WHEN '2. Bundesliga'              THEN 20
                     WHEN 'Serie B'                    THEN 15
                     ELSE 0
                   END AS weight
            FROM leagues l
        )
        SELECT
            m.id,
            ht.name AS home_team,
            at.name AS away_team,
            m.date AS kickoff,
            l.name AS league,
            MAX(CASE WHEN p.selection = 'over' THEN p.odds END) AS pinnacle_over,
            MAX(CASE WHEN p.selection = 'under' THEN p.odds END) AS pinnacle_under,
            ABS(MAX(CASE WHEN p.selection = 'over' THEN p.odds END) -
                MAX(CASE WHEN p.selection = 'under' THEN p.odds END)) AS ou_gap,
            COALESCE(ic.total_out, 0) AS total_injuries,
            COALESCE(ic.away_out, 0) AS away_injuries,
            COALESCE(lp.weight, 0) AS league_weight
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        JOIN leagues l ON m.league_id = l.id
        JOIN pinnacle_ou p ON p.match_id = m.id
        LEFT JOIN injury_counts ic ON ic.match_id = m.id
        LEFT JOIN league_priority lp ON lp.league_id = m.league_id
        WHERE m.status = 'scheduled'
          AND DATE(m.date) = %s
        GROUP BY m.id, ht.name, at.name, m.date, l.name, ic.total_out, ic.away_out, lp.weight
        HAVING COUNT(DISTINCT p.selection) = 2
           AND MAX(CASE WHEN p.selection = 'over' THEN p.odds END) IS NOT NULL
           AND MAX(CASE WHEN p.selection = 'under' THEN p.odds END) IS NOT NULL
        ORDER BY (
            COALESCE(lp.weight, 0) * 2.0
            + COALESCE(ic.total_out, 0) * 0.4
            + (1.0 / (ABS(
                MAX(CASE WHEN p.selection = 'over' THEN p.odds END) -
                MAX(CASE WHEN p.selection = 'under' THEN p.odds END)) + 0.01))
        ) DESC
        LIMIT 5
    """, [today, today])

    matches = cur.fetchall()

    if not matches:
        print("No matches with Pinnacle O/U data found for today.")
        conn.close()
        return

    best = matches[0]
    match_id = best["id"]

    # Get injuries for best match
    cur.execute("""
        SELECT player_name, team_side, reason, status
        FROM match_injuries
        WHERE match_id = %s
          AND status IN ('Missing Fixture', 'Questionable')
        ORDER BY team_side, status, player_name
    """, [match_id])
    injuries = cur.fetchall()

    # Get BTTS odds for context
    cur.execute("""
        SELECT DISTINCT ON (bookmaker, selection)
            bookmaker, selection, odds
        FROM odds_snapshots
        WHERE match_id = %s
          AND market = 'btts'
          AND bookmaker IN ('Pinnacle', 'Bet365', 'Betfair')
        ORDER BY bookmaker, selection, timestamp DESC
    """, [match_id])
    btts = cur.fetchall()

    conn.close()

    # Format output
    kickoff_utc = best["kickoff"].strftime("%H:%M UTC")
    over = best["pinnacle_over"]
    under = best["pinnacle_under"]
    gap = best["ou_gap"]

    home_missing = [i["player_name"] for i in injuries if i["team_side"] == "home" and i["status"] == "Missing Fixture"]
    away_missing = [i["player_name"] for i in injuries if i["team_side"] == "away" and i["status"] == "Missing Fixture"]
    away_doubt  = [i["player_name"] for i in injuries if i["team_side"] == "away" and i["status"] == "Questionable"]

    # Lean toward under if away team has 4+ injuries (depleted attack = fewer goals)
    if best["away_injuries"] >= 4:
        lean, lean_odds = "Under", float(under)
    elif under < over:
        lean, lean_odds = "Under", float(under)
    else:
        lean, lean_odds = "Over", float(over)

    # Shorten common league names for tweet
    league_short = {
        "UEFA Europa League": "UEL",
        "UEFA Champions League": "UCL",
        "UEFA Conference League": "UECL",
        "Premier League": "PL",
        "La Liga": "La Liga",
        "Bundesliga": "Bundesliga",
        "Serie A": "Serie A",
        "Ligue 1": "Ligue 1",
    }.get(best["league"], best["league"])

    print("\n" + "="*60)
    print(f"DAILY TWITTER DRAFT — {date.today()}")
    print("="*60)
    print(f"\nMatch:   {best['home_team']} vs {best['away_team']}")
    print(f"League:  {best['league']}")
    print(f"Kickoff: {kickoff_utc}")
    print(f"Pinnacle O/U 2.5: Over {over} / Under {under} (gap: {gap:.3f})")
    print(f"Total injuries: {best['total_injuries']} | Away out: {best['away_injuries']}")

    if away_missing:
        print(f"Away missing: {', '.join(away_missing)}")
    if away_doubt:
        print(f"Away doubtful: {', '.join(away_doubt)}")
    if home_missing:
        print(f"Home missing: {', '.join(home_missing)}")

    if btts:
        print("\nBTTS odds:")
        for row in btts:
            print(f"  {row['bookmaker']} {row['selection']}: {row['odds']}")

    # Draft tweet — keep under 280 chars
    # Pick top 3 away names (last name only to save space)
    def last_name(full):
        parts = full.strip().split()
        return parts[-1] if parts else full

    top_away = [last_name(n) for n in (away_missing + away_doubt)[:3]]
    injury_line = f"Away missing: {', '.join(top_away)}" if top_away else ""

    tweet = f"""{best['home_team']} vs {best['away_team']} {kickoff_utc} ({league_short})

Pinnacle O/U 2.5: {float(over):.2f}/{float(under):.2f} — near coin flip
{injury_line}

{lean} @ {lean_odds:.2f} with this injury list.

oddsintel.app #valuebets #footballtips"""

    print("\n" + "-"*60)
    print("TWEET DRAFT:")
    print("-"*60)
    print(tweet)
    print(f"\nCharacter count: {len(tweet)}/280")
    print("-"*60)

if __name__ == "__main__":
    find_best_match()
