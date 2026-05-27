"""Tier-unlocked backtest — compare current vs potential post-fix bot coverage.

What this does:
  Queries all simulated_bets (settled) and breaks them down by league tier
  so we can see how bots perform in tier=0 (now-corrected) leagues vs
  tier=1/2 (always-covered) leagues.

What this does NOT do:
  Reconstruct AH/DNB bets from scratch. The odds_snapshots table stores
  'asian_handicap / home' without the specific line (e.g. -0.50), so
  we cannot reliably match raw odds back to predictions.market = 'ah_away_0.50'.
  The tier fix is primarily forward-looking: once migrations 138+140 run,
  the morning pipeline will automatically fire in the corrected leagues.

Usage:
    python3 scripts/backtest_tier_unlocked.py
    python3 scripts/backtest_tier_unlocked.py --show-leagues   # per-league detail
"""
from __future__ import annotations
import os, sys, argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import psycopg2, psycopg2.extras

DB_URL = os.environ["DATABASE_URL"]
STAKE = 10.0

TIER_LABELS = {
    0: "tier=0 (misclassified — fix target)",
    1: "tier=1 (top division)",
    2: "tier=2 (second division)",
    3: "tier=3 (third division)",
}

# Bots that had tier_filter restrictions (primary impact of the tier fix)
GATED_BOTS = {
    "bot_ah_home_fav", "bot_ah_away_dog",
    "bot_dnb_home_value", "bot_dnb_away_value",
}


def q(sql: str, params=None) -> list[dict]:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]


def roi(bets: list) -> float:
    if not bets:
        return 0.0
    return float(sum(b["pnl"] for b in bets)) / (len(bets) * STAKE) * 100


def fmt_roi(r: float) -> str:
    return f"{'+'if r>=0 else ''}{r:.1f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-leagues", action="store_true")
    args = parser.parse_args()

    rows = q("""
        SELECT
            b.name        AS bot_name,
            sb.market,
            sb.pnl,
            sb.result::text AS result,
            m.league_id,
            l.name        AS league_name,
            l.country,
            l.tier
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
        return

    print(f"\nTotal settled bets: {len(rows)}\n")

    # ── 1. Global tier breakdown (all bots) ──────────────────────────────────

    by_tier: dict[int, list] = defaultdict(list)
    for r in rows:
        by_tier[r["tier"]].append(r)

    print("=" * 60)
    print(f"{'TIER':<35}  {'BETS':>6}  {'ROI':>8}")
    print("=" * 60)
    for t in sorted(by_tier):
        bets = by_tier[t]
        label = TIER_LABELS.get(t, f"tier={t}")
        print(f"{label:<35}  {len(bets):>6}  {fmt_roi(roi(bets)):>8}")
    print()

    # ── 2. Tier-gated bots only: tier=0 vs tier≥1 ──────────────────────────

    gated_rows = [r for r in rows if r["bot_name"] in GATED_BOTS]
    t0 = [r for r in gated_rows if r["tier"] == 0]
    t1p = [r for r in gated_rows if r["tier"] >= 1]

    print("=" * 75)
    print("AH + DNB bots (tier_filter restricted) — tier=0 vs tier≥1")
    print("=" * 75)

    by_bot_t0: dict[str, list] = defaultdict(list)
    by_bot_t1: dict[str, list] = defaultdict(list)
    for r in gated_rows:
        if r["tier"] == 0:
            by_bot_t0[r["bot_name"]].append(r)
        else:
            by_bot_t1[r["bot_name"]].append(r)

    print(f"{'BOT':<28}  {'T≥1 BETS':>8}  {'T≥1 ROI':>8}  {'T=0 BETS':>9}  {'T=0 ROI':>8}")
    print("-" * 75)
    for bot in sorted(GATED_BOTS):
        print(
            f"{bot:<28}  "
            f"{len(by_bot_t1.get(bot, [])):>8}  {fmt_roi(roi(by_bot_t1.get(bot, []))):>8}  "
            f"{len(by_bot_t0.get(bot, [])):>9}  {fmt_roi(roi(by_bot_t0.get(bot, []))):>8}"
        )
    print(
        f"{'TOTAL':<28}  "
        f"{len(t1p):>8}  {fmt_roi(roi(t1p)):>8}  "
        f"{len(t0):>9}  {fmt_roi(roi(t0)):>8}"
    )
    print()

    # ── 3. Tier=0 league detail (ALL bots, not just gated) ──────────────────

    all_t0 = by_tier.get(0, [])
    by_league: dict[str, list] = defaultdict(list)
    for r in all_t0:
        key = f"{r['country']} — {r['league_name']}"
        by_league[key].append(r)

    top_leagues = sorted([(k, v) for k, v in by_league.items() if len(v) >= 5],
                         key=lambda x: -len(x[1]))

    print("=" * 70)
    print(f"Tier=0 leagues with ≥5 settled bets (will fire correctly post-fix)")
    print("=" * 70)
    print(f"{'LEAGUE':<40}  {'BETS':>5}  {'ROI':>8}")
    print("-" * 70)
    for league, bets in top_leagues:
        print(f"{league:<40}  {len(bets):>5}  {fmt_roi(roi(bets)):>8}")
    small = [r for k, v in by_league.items() for r in v if len(v) < 5]
    if small:
        print(f"{'(other leagues, <5 bets each)':<40}  {len(small):>5}  {fmt_roi(roi(small)):>8}")

    print()
    print("Note: AH/DNB bots had 0 bets in most tier=0 leagues because tier_filter")
    print("blocked them. The existing tier=0 bets come from bots without tier_filter.")
    print("Going forward, migrations 138+140 fix the tier data so bots will fire.")
    print()

    # ── 4. What new coverage the fix adds (estimate) ────────────────────────

    new_leagues_t0 = q("""
        SELECT l.country, l.name, COUNT(DISTINCT m.id) AS matches
        FROM leagues l
        JOIN matches m ON m.league_id = l.id
        WHERE l.tier = 0 AND l.is_active = true
        GROUP BY l.id, l.country, l.name
        HAVING COUNT(DISTINCT m.id) >= 20
        ORDER BY 3 DESC
        LIMIT 20
    """)

    print("=" * 60)
    print("Leagues still at tier=0 with ≥20 matches (will be fixed by 140)")
    print("These are the markets where bots will start firing post-migration")
    print("=" * 60)
    for r in new_leagues_t0:
        print(f"  {r['matches']:>5} matches  {r['country']:<22} {r['name']}")

    if args.show_leagues:
        print()
        print("=" * 70)
        print("All tier=0 league bet detail")
        print("=" * 70)
        for league, bets in sorted(by_league.items(), key=lambda x: -len(x[1])):
            by_bot_l: dict[str, list] = defaultdict(list)
            for r in bets:
                by_bot_l[r["bot_name"]].append(r)
            print(f"\n{league} ({len(bets)} bets, {fmt_roi(roi(bets))} ROI)")
            for bot_name, bot_bets in sorted(by_bot_l.items()):
                print(f"  {bot_name:<30}  {len(bot_bets):>3} bets  {fmt_roi(roi(bot_bets)):>8}")


if __name__ == "__main__":
    main()
