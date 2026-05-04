"""
Debug why bots aren't placing bets.

Checks:
1. Which bots exist in DB vs BOTS_CONFIG
2. Bet counts per bot
3. Today's match country/tier distribution
4. League filter qualification counts
5. BTTS / O/U 1.5 / O/U 3.5 odds availability
6. Edge threshold sanity checks for near-impossible bots

Run: python3 scripts/debug_betting.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from datetime import date, timedelta
from workers.api_clients.db import execute_query
from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG

today_str = date.today().isoformat()
next_day_str = (date.today() + timedelta(days=1)).isoformat()

# 1. Bots in DB vs code
print("\n=== BOTS IN DB ===")
db_bots = execute_query("SELECT name, current_bankroll, is_active FROM bots ORDER BY name")
db_bot_names = {b["name"] for b in db_bots}
for b in db_bots:
    print(f"  {b['name']:<35} bankroll=€{b['current_bankroll']:.2f}  active={b['is_active']}")

print(f"\n  In BOTS_CONFIG: {len(BOTS_CONFIG)}")
print(f"  In DB:          {len(db_bots)}")
missing = [n for n in BOTS_CONFIG if n not in db_bot_names]
extra   = [n for n in db_bot_names if n not in BOTS_CONFIG]
if missing: print(f"  ⚠️  MISSING FROM DB: {missing}")
if extra:   print(f"  ℹ️  IN DB NOT IN CODE: {extra}")

# 2. Bet counts per bot
print("\n=== BET COUNTS PER BOT ===")
bet_counts = execute_query("""
    SELECT b.name, COUNT(sb.id) as total,
           COUNT(sb.id) FILTER (WHERE sb.result != 'pending') as settled
    FROM bots b
    LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
    GROUP BY b.name ORDER BY total DESC
""")
for row in bet_counts:
    flag = "✅" if row['total'] > 0 else "❌"
    print(f"  {flag} {row['name']:<35} total={row['total']:>3}  settled={row['settled']:>3}")

# 3. Today's match country/tier distribution
print(f"\n=== TODAY'S MATCHES ({today_str}) — COUNTRY/TIER ===")
matches = execute_query("""
    SELECT l.country, l.tier, COUNT(*) as cnt
    FROM matches m
    LEFT JOIN leagues l ON m.league_id = l.id
    WHERE m.date >= %s AND m.date < %s AND m.status IN ('scheduled','live')
    GROUP BY l.country, l.tier ORDER BY cnt DESC
""", (f"{today_str}T00:00:00Z", f"{next_day_str}T00:00:00Z"))
total_today = sum(m['cnt'] for m in matches)
print(f"  Total: {total_today} matches  (showing top 20 by country)")
print(f"  {'Country':<25} {'Tier':>5} {'Matches':>8}")
null_country = sum(m['cnt'] for m in matches if not m['country'])
for m in matches[:20]:
    country = m['country'] or '⚠️ NULL'
    tier = m['tier'] or '?'
    print(f"  {country:<25} {str(tier):>5} {m['cnt']:>8}")
if null_country:
    print(f"\n  ⚠️  {null_country} matches have NULL country — league_filter bots WON'T match these!")

# 4. Specific league filter checks
print("\n=== LEAGUE FILTER QUALIFICATION (country+tier match) ===")
bots_with_filters = {
    k: v for k, v in BOTS_CONFIG.items()
    if v.get("league_filter")
}
for bot_name, config in bots_with_filters.items():
    countries = config["league_filter"]
    tier_filter = config.get("tier_filter")
    rows = execute_query("""
        SELECT COUNT(*) as cnt FROM matches m
        LEFT JOIN leagues l ON m.league_id = l.id
        WHERE m.date >= %s AND m.date < %s
          AND m.status IN ('scheduled','live')
          AND l.country = ANY(%s::text[])
    """, (f"{today_str}T00:00:00Z", f"{next_day_str}T00:00:00Z", countries))
    cnt_country = rows[0]['cnt'] if rows else 0

    if tier_filter:
        rows2 = execute_query("""
            SELECT COUNT(*) as cnt FROM matches m
            LEFT JOIN leagues l ON m.league_id = l.id
            WHERE m.date >= %s AND m.date < %s
              AND m.status IN ('scheduled','live')
              AND l.country = ANY(%s::text[])
              AND l.tier = ANY(%s::int[])
        """, (f"{today_str}T00:00:00Z", f"{next_day_str}T00:00:00Z", countries, tier_filter))
        cnt_tier = rows2[0]['cnt'] if rows2 else 0
        flag = "✅" if cnt_tier > 0 else "❌"
        print(f"  {flag} {bot_name:<35} country_match={cnt_country:>3}  after_tier_filter={cnt_tier:>3}  (tier={tier_filter})")
    else:
        flag = "✅" if cnt_country > 0 else "❌"
        print(f"  {flag} {bot_name:<35} country_match={cnt_country:>3}  (no tier filter)")

# 5. Special market odds
print("\n=== SPECIAL MARKET ODDS TODAY ===")
for market in ["btts", "over_under_15", "over_under_35"]:
    rows = execute_query("""
        SELECT COUNT(DISTINCT os.match_id) as matches, COUNT(*) as rows
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE m.date >= %s AND m.date < %s
          AND os.market = %s AND os.is_closing = false
    """, (f"{today_str}T00:00:00Z", f"{next_day_str}T00:00:00Z", market))
    r = rows[0] if rows else {"matches": 0, "rows": 0}
    flag = "✅" if r['matches'] > 0 else "❌"
    print(f"  {flag} market={market:<20}  matches_with_odds={r['matches']:>4}  total_rows={r['rows']:>5}")

# 6. Away odds in 2.50-3.00 range (bot_opt_away_*)
print("\n=== AWAY ODDS IN 2.50–3.00 RANGE TODAY ===")
rows = execute_query("""
    SELECT l.country, COUNT(DISTINCT os.match_id) as cnt
    FROM odds_snapshots os
    JOIN matches m ON m.id = os.match_id
    LEFT JOIN leagues l ON m.league_id = l.id
    WHERE m.date >= %s AND m.date < %s
      AND os.market = '1x2' AND os.selection = 'away'
      AND os.odds BETWEEN 2.50 AND 3.00
      AND os.is_closing = false
    GROUP BY l.country ORDER BY cnt DESC LIMIT 10
""", (f"{today_str}T00:00:00Z", f"{next_day_str}T00:00:00Z"))
total_away = sum(r['cnt'] for r in rows)
print(f"  Total matches with away odds 2.50-3.00: {total_away}")
for r in rows:
    print(f"    {(r['country'] or 'NULL'):<20} {r['cnt']}")

# 7. bot_opt_home_lower analysis
print("\n=== bot_opt_home_lower ANALYSIS ===")
print("  Requires 20% edge. With tier shrinkage α=0.30 (T2):")
print("  cal_prob = 0.3*model + 0.7*implied")
print("  For home odds 4.00 (implied=0.25): need model_prob > 0.917 (92%!) for 20% edge")
print("  → RECOMMENDATION: reduce edge threshold from 0.20 to 0.08")

print("\n=== DONE ===\n")
