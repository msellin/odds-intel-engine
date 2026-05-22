#!/usr/bin/env python3
"""
Inplay bot config review:
  1. inplay_p: why do so many bets have unknown minute?
  2. Firing rate per bot — how often does each strategy actually bet?
  3. Funnel analysis per non-firing bot using live_match_snapshots
     (what % of snapshots pass each key filter?)

Usage: python scripts/inplay_config_review.py
"""

import json
from collections import defaultdict
from workers.api_clients.db import execute_query


# ── 1. inplay_p: debug unknown minute ────────────────────────────────────────

def debug_inplay_p():
    print("\n" + "="*60)
    print("  inplay_p — unknown minute investigation")
    print("="*60)

    rows = execute_query("""
        SELECT b.id, b.result, b.odds_at_pick, b.reasoning
        FROM simulated_bets b
        JOIN bots bo ON b.bot_id = bo.id
        WHERE bo.name = 'inplay_p'
          AND b.result NOT IN ('pending', 'void')
        ORDER BY b.pick_time
        LIMIT 60
    """, [])

    has_minute = 0
    no_minute = 0
    samples_no_minute = []
    samples_with_minute = []

    for r in rows:
        rsn = r["reasoning"]
        if rsn:
            if isinstance(rsn, str):
                try:
                    rsn = json.loads(rsn)
                except Exception:
                    rsn = {}
        else:
            rsn = {}

        minute = rsn.get("minute")
        if minute is not None:
            has_minute += 1
            if len(samples_with_minute) < 2:
                samples_with_minute.append({"minute": minute, "score": rsn.get("score"), "odds": r["odds_at_pick"]})
        else:
            no_minute += 1
            if len(samples_no_minute) < 3:
                samples_no_minute.append({"keys": list(rsn.keys()), "sample": dict(list(rsn.items())[:6])})

    print(f"\n  Bets with minute: {has_minute}")
    print(f"  Bets without minute: {no_minute}")

    if samples_with_minute:
        print(f"\n  Sample WITH minute: {samples_with_minute}")
    if samples_no_minute:
        print(f"\n  Sample WITHOUT minute (keys + values):")
        for s in samples_no_minute:
            print(f"    keys: {s['keys']}")
            print(f"    sample: {s['sample']}")


# ── 2. Firing rate per bot ────────────────────────────────────────────────────

def firing_rates():
    print("\n" + "="*60)
    print("  Firing rate per inplay bot (bets per week)")
    print("="*60)

    rows = execute_query("""
        SELECT
            bo.name,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE b.result NOT IN ('pending','void')) AS settled,
            MIN(b.pick_time) AS first_bet,
            MAX(b.pick_time) AS last_bet,
            EXTRACT(EPOCH FROM (MAX(b.pick_time) - MIN(b.pick_time))) / 604800.0 AS weeks_active
        FROM simulated_bets b
        JOIN bots bo ON b.bot_id = bo.id
        WHERE bo.name LIKE 'inplay_%%'
          AND b.result != 'void'
        GROUP BY bo.name
        ORDER BY total DESC
    """, [])

    print(f"\n  {'Bot':<25} {'Total':>6} {'Settled':>8} {'Weeks':>6} {'Bets/wk':>8}")
    print(f"  {'-'*55}")
    for r in rows:
        weeks = float(r["weeks_active"] or 0)
        bpw = r["total"] / weeks if weeks > 0.1 else r["total"]
        print(f"  {r['name']:<25} {r['total']:>6} {r['settled']:>8} {weeks:>6.1f} {bpw:>8.1f}")


# ── 3. Funnel analysis per non-firing bot ────────────────────────────────────

def funnel_analysis():
    print("\n" + "="*60)
    print("  Non-firing bot funnel (last 14 days of live snapshots)")
    print("="*60)

    # Total in-play snapshots (all matches, any status) in last 14 days with a known minute
    total = execute_query("""
        SELECT COUNT(*) AS n
        FROM live_match_snapshots
        WHERE captured_at >= NOW() - INTERVAL '14 days'
          AND minute IS NOT NULL
    """, [])
    total_n = total[0]["n"] if total else 0
    print(f"\n  Total live snapshots (14d, with minute): {total_n:,}")

    # ── inplay_a: xG Divergence Over 2.5 ──────────────────────────────────
    _funnel("inplay_a", [
        ("minute 20-40",
         "minute BETWEEN 20 AND 40"),
        ("score ≤1 goal",
         "minute BETWEEN 20 AND 40 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1"),
        ("xG exists + sum ≥0.6",
         "minute BETWEEN 20 AND 40 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND xg_home IS NOT NULL AND xg_away IS NOT NULL AND (xg_home+xg_away) >= 0.6"),
        ("SoT ≥3 (no xG fallback)",
         "minute BETWEEN 20 AND 40 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND (COALESCE(shots_on_target_home,0)+COALESCE(shots_on_target_away,0)) >= 3"),
        ("xG exists OR SoT ≥3 (combined)",
         "minute BETWEEN 20 AND 40 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND (xg_home IS NOT NULL AND (xg_home+xg_away) >= 0.6 OR (COALESCE(shots_on_target_home,0)+COALESCE(shots_on_target_away,0)) >= 3)"),
    ], total_n)

    # ── inplay_d: Late Goals Compression ──────────────────────────────────
    _funnel("inplay_d", [
        ("minute 48-80",
         "minute BETWEEN 48 AND 80"),
        ("score ≤1 goal",
         "minute BETWEEN 48 AND 80 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1"),
        ("xG ≥0.7 OR SoT ≥6",
         "minute BETWEEN 48 AND 80 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND (COALESCE(xg_home,0)+COALESCE(xg_away,0) >= 0.7 OR COALESCE(shots_on_target_home,0)+COALESCE(shots_on_target_away,0) >= 6)"),
        ("live OU odds ≥2.10",
         "minute BETWEEN 48 AND 80 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND live_ou_25_over >= 2.10"),
        ("OU ≥2.10 + xG/SoT combined",
         "minute BETWEEN 48 AND 80 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND live_ou_25_over >= 2.10 AND (COALESCE(xg_home,0)+COALESCE(xg_away,0) >= 0.7 OR COALESCE(shots_on_target_home,0)+COALESCE(shots_on_target_away,0) >= 6)"),
    ], total_n)

    # ── inplay_g: Corner Cluster ──────────────────────────────────────────
    _funnel("inplay_g", [
        ("minute 30-70",
         "minute BETWEEN 30 AND 70"),
        ("score ≤1 goal",
         "minute BETWEEN 30 AND 70 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1"),
        ("OU2.5 over odds ≥2.10",
         "minute BETWEEN 30 AND 70 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND live_ou_25_over >= 2.10"),
        ("corners not null",
         "minute BETWEEN 30 AND 70 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND live_ou_25_over >= 2.10 AND corners_home IS NOT NULL AND corners_away IS NOT NULL"),
        ("OU ≥1.80 (looser odds)",
         "minute BETWEEN 30 AND 70 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1 AND live_ou_25_over >= 1.80 AND corners_home IS NOT NULL AND corners_away IS NOT NULL"),
    ], total_n)

    # ── inplay_h: HT Restart Surge ────────────────────────────────────────
    _funnel("inplay_h", [
        ("minute 46-55",
         "minute BETWEEN 46 AND 55"),
        ("score exactly 0-0",
         "minute BETWEEN 46 AND 55 AND COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0"),
        ("OU2.5 over odds >2.30",
         "minute BETWEEN 46 AND 55 AND COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0 AND live_ou_25_over > 2.30"),
        ("OU2.5 over odds >2.00 (looser)",
         "minute BETWEEN 46 AND 55 AND COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0 AND live_ou_25_over > 2.00"),
        ("OU2.5 over odds available (>1.0)",
         "minute BETWEEN 46 AND 55 AND COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0 AND live_ou_25_over > 1.0"),
    ], total_n)

    # ── inplay_i: Favourite Stall ─────────────────────────────────────────
    _funnel("inplay_i", [
        ("minute 42-65",
         "minute BETWEEN 42 AND 65"),
        ("score exactly 0-0",
         "minute BETWEEN 42 AND 65 AND COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0"),
        ("live 1x2 home OR away ≥3.0",
         "minute BETWEEN 42 AND 65 AND COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0 AND (live_1x2_home >= 3.0 OR live_1x2_away >= 3.0)"),
        ("live 1x2 home OR away ≥2.5",
         "minute BETWEEN 42 AND 65 AND COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0 AND (live_1x2_home >= 2.5 OR live_1x2_away >= 2.5)"),
        ("1x2 not null (how many have odds at all)",
         "minute BETWEEN 42 AND 65 AND COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0 AND live_1x2_home IS NOT NULL"),
    ], total_n)

    # ── inplay_l: Goal Contagion ─────────────────────────────────────────
    _funnel("inplay_l", [
        ("minute 15-35",
         "minute BETWEEN 15 AND 35"),
        ("score exactly 1 goal",
         "minute BETWEEN 15 AND 35 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) = 1"),
        ("OU2.5 over odds >1.0",
         "minute BETWEEN 15 AND 35 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) = 1 AND live_ou_25_over > 1.0"),
        ("OU2.5 over odds ≥1.60 (tighter)",
         "minute BETWEEN 15 AND 35 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) = 1 AND live_ou_25_over >= 1.60"),
        ("extended window 10-40 min",
         "minute BETWEEN 10 AND 40 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) = 1 AND live_ou_25_over > 1.0"),
    ], total_n)

    # ── inplay_m: Equalizer Magnet ────────────────────────────────────────
    _funnel("inplay_m", [
        ("minute 30-60",
         "minute BETWEEN 30 AND 60"),
        ("score exactly 1 goal (1-0 or 0-1)",
         "minute BETWEEN 30 AND 60 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) = 1"),
        ("OU2.5 over odds ≥2.40",
         "minute BETWEEN 30 AND 60 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) = 1 AND live_ou_25_over >= 2.40"),
        ("OU2.5 over odds ≥2.00",
         "minute BETWEEN 30 AND 60 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) = 1 AND live_ou_25_over >= 2.00"),
        ("OU2.5 over odds ≥1.70",
         "minute BETWEEN 30 AND 60 AND (COALESCE(score_home,0)+COALESCE(score_away,0)) = 1 AND live_ou_25_over >= 1.70"),
    ], total_n)

    # ── inplay_n: Late Favourite Push ─────────────────────────────────────
    _funnel("inplay_n", [
        ("minute 72-80",
         "minute BETWEEN 72 AND 80"),
        ("level score (0-0 or 1-1)",
         "minute BETWEEN 72 AND 80 AND (COALESCE(score_home,0) = COALESCE(score_away,0))"),
        ("live home odds ≥2.20",
         "minute BETWEEN 72 AND 80 AND (COALESCE(score_home,0) = COALESCE(score_away,0)) AND live_1x2_home >= 2.20"),
        ("wider: minute 65-82",
         "minute BETWEEN 65 AND 82 AND (COALESCE(score_home,0) = COALESCE(score_away,0)) AND live_1x2_home >= 2.20"),
        ("wider: home OR away ≥2.20",
         "minute BETWEEN 65 AND 82 AND (COALESCE(score_home,0) = COALESCE(score_away,0)) AND (live_1x2_home >= 2.20 OR live_1x2_away >= 2.20)"),
    ], total_n)


def _funnel(bot_name: str, steps: list[tuple[str, str]], total_n: int):
    print(f"\n  --- {bot_name} ---")
    prev = total_n
    for label, where in steps:
        try:
            rows = execute_query(f"""
                SELECT COUNT(*) AS n
                FROM live_match_snapshots lms
                WHERE captured_at >= NOW() - INTERVAL '14 days'
                  AND {where}
            """, [])
            n = rows[0]["n"] if rows else 0
        except Exception as e:
            n = f"ERROR: {e}"
        pct = f"{n/total_n*100:.1f}%" if isinstance(n, int) and total_n > 0 else "—"
        drop = f"(keeps {n/prev*100:.0f}%)" if isinstance(n, int) and isinstance(prev, int) and prev > 0 else ""
        print(f"    {label:<45} {str(n):>7}  {pct:>6}  {drop}")
        if isinstance(n, int):
            prev = n


if __name__ == "__main__":
    debug_inplay_p()
    firing_rates()
    funnel_analysis()
