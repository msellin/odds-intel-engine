"""Tier coverage backtest — quantify how many bets were blocked by tier=0 misclassification.

Queries all settled simulated_bets, joins with fixtures → leagues, and groups
by league tier and bot. Shows bet count and ROI broken out by tier so we can
see the exact bets the tier fix (migration 138) unlocks.

Usage:
    python3 scripts/backtest_tier_coverage.py

Outputs:
  1. ROI by tier summary (all bots combined)
  2. Per-bot breakdown showing tier=0 blocked bets
  3. League-level detail for tier=0 leagues with ≥5 settled bets
"""
from __future__ import annotations
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import psycopg2

DB_URL = os.environ["DATABASE_URL"]


def query(sql: str) -> list[dict]:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def roi(bets: list[dict]) -> float:
    if not bets:
        return 0.0
    total_stake = len(bets) * 10.0
    total_pnl = float(sum(b["pnl"] for b in bets))
    return total_pnl / total_stake * 100


def fmt_roi(r: float) -> str:
    sign = "+" if r >= 0 else ""
    return f"{sign}{r:.1f}%"


# ── Fetch all settled bets with league tier ──────────────────────────────────

rows = query("""
    SELECT
        b.name        AS bot_name,
        sb.market,
        sb.selection,
        sb.odds_at_pick,
        sb.calibrated_prob,
        sb.edge_percent,
        sb.result::text AS result,
        sb.pnl,
        m.league_id,
        l.name        AS league_name,
        l.country     AS league_country,
        l.tier        AS tier
    FROM simulated_bets sb
    JOIN bots    b ON b.id = sb.bot_id
    JOIN matches m ON m.id = sb.match_id
    JOIN leagues l ON l.id = m.league_id
    WHERE sb.result::text IN ('won', 'lost')
      AND sb.pnl IS NOT NULL
    ORDER BY sb.pick_time
""")

if not rows:
    print("No settled bets found.")
    sys.exit(0)

print(f"\nTotal settled bets analysed: {len(rows)}\n")

# ── 1. ROI by tier (all bots) ────────────────────────────────────────────────

by_tier: dict[int, list[dict]] = defaultdict(list)
for r in rows:
    by_tier[r["tier"]].append(r)

print("=" * 55)
print(f"{'TIER':<8}  {'BETS':>6}  {'ROI':>8}  {'NOTES'}")
print("=" * 55)
for tier in sorted(by_tier.keys()):
    bets = by_tier[tier]
    note = ""
    if tier == 0:
        note = "← misclassified (migration 138 target)"
    elif tier == 1:
        note = "top division"
    elif tier == 2:
        note = "second division"
    print(f"tier={tier:<4}  {len(bets):>6}  {fmt_roi(roi(bets)):>8}  {note}")
print()

# ── 2. Per-bot breakdown (tier=0 vs tier>=1) ─────────────────────────────────

bots_t0: dict[str, list[dict]] = defaultdict(list)
bots_t1: dict[str, list[dict]] = defaultdict(list)

for r in rows:
    if r["tier"] == 0:
        bots_t0[r["bot_name"]].append(r)
    else:
        bots_t1[r["bot_name"]].append(r)

all_bots = sorted(set(list(bots_t0.keys()) + list(bots_t1.keys())))

print("=" * 80)
print(f"{'BOT':<30}  {'T≥1 BETS':>8}  {'T≥1 ROI':>8}  {'T=0 BETS':>9}  {'T=0 ROI':>8}")
print("=" * 80)
for bot in all_bots:
    t0 = bots_t0.get(bot, [])
    t1 = bots_t1.get(bot, [])
    print(
        f"{bot:<30}  {len(t1):>8}  {fmt_roi(roi(t1)):>8}  "
        f"{len(t0):>9}  {fmt_roi(roi(t0)):>8}"
    )
print()

# ── 3. League detail — tier=0 with ≥5 settled bets ──────────────────────────

t0_bets = [r for r in rows if r["tier"] == 0]
if not t0_bets:
    print("No tier=0 settled bets — all leagues correctly classified (or no data yet).")
else:
    by_league: dict[str, list[dict]] = defaultdict(list)
    for r in t0_bets:
        key = f"{r['league_country']} — {r['league_name']}"
        by_league[key].append(r)

    qualifying = [(k, v) for k, v in by_league.items() if len(v) >= 5]
    qualifying.sort(key=lambda x: -len(x[1]))

    print("=" * 65)
    print(f"{'LEAGUE':<40}  {'BETS':>5}  {'ROI':>8}")
    print("=" * 65)
    for league, bets in qualifying:
        print(f"{league:<40}  {len(bets):>5}  {fmt_roi(roi(bets)):>8}")

    # leagues with <5 bets summary
    small = [r for k, v in by_league.items() for r in v if len(v) < 5]
    if small:
        print(f"{'(other leagues, <5 bets each)':<40}  {len(small):>5}  {fmt_roi(roi(small)):>8}")

print()

# ── 4. What migration 138 unlocks (post-fix projection) ─────────────────────

tier0_bots = [r for r in t0_bets if r["bot_name"] in {
    "bot_ah_home_fav", "bot_ah_away_dog",
    "bot_dnb_home_value", "bot_dnb_away_value",
}]

if tier0_bots:
    print("=" * 55)
    print("AH + DNB bots — tier=0 bets unlocked by migration 138")
    print("=" * 55)
    by_bot: dict[str, list[dict]] = defaultdict(list)
    for r in tier0_bots:
        by_bot[r["bot_name"]].append(r)
    for bot, bets in sorted(by_bot.items()):
        print(f"  {bot:<30}  {len(bets):>4} bets  {fmt_roi(roi(bets)):>8} ROI")
    print()
    print(f"  Combined: {len(tier0_bots)} additional bets at {fmt_roi(roi(tier0_bots))} ROI")
else:
    print("Note: no AH/DNB tier=0 bets found in settled history.")
    print("This is expected if the tier filter blocked them entirely.")
    print("Post-fix, the pipeline will fire on these leagues going forward.")
