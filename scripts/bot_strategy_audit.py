#!/usr/bin/env python3
"""
Bot strategy audit — funnel analysis for all prematch and inplay bots.

For each bot: shows how many match candidates survive each filter gate
over the last N days, plus current ROI/CLV and limiting gate identification.

Usage:
    python3 scripts/bot_strategy_audit.py                  # all bots
    python3 scripts/bot_strategy_audit.py --bot bot_aggressive
    python3 scripts/bot_strategy_audit.py --bot inplay_j
    python3 scripts/bot_strategy_audit.py --days 30
    python3 scripts/bot_strategy_audit.py --type prematch  # prematch only
    python3 scripts/bot_strategy_audit.py --type inplay    # inplay only
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import get_conn
from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG as BOT_CONFIGS


# ── ANSI colours ────────────────────────────────────────────────────────────

def _g(s):  return f"\033[32m{s}\033[0m"
def _y(s):  return f"\033[33m{s}\033[0m"
def _r(s):  return f"\033[31m{s}\033[0m"
def _b(s):  return f"\033[1m{s}\033[0m"
def _dim(s): return f"\033[2m{s}\033[0m"

def _pct_color(n, total):
    if total == 0:
        return _dim("  0.0%")
    p = n / total * 100
    s = f"{p:5.1f}%"
    if p >= 50:
        return _g(s)
    if p >= 10:
        return _y(s)
    return _r(s)

def _roi_color(roi):
    if roi is None:
        return _dim("  n/a")
    s = f"{roi:+.1f}%"
    if roi >= 3:
        return _g(s)
    if roi >= 0:
        return _y(s)
    return _r(s)


# ── Prematch funnel ──────────────────────────────────────────────────────────

PREMATCH_MARKET_TO_PREDICTION = {
    "1x2":  [("1x2_home", "home"), ("1x2_draw", "draw"), ("1x2_away", "away")],
    "ou":   [("over25", "over"), ("under25", "under")],
    "ou15": [("over15", "over"), ("under15", "under")],
    "ou35": [("over35", "over"), ("under35", "under")],
    "btts": [("btts_yes", "yes"), ("btts_no", "no")],
    "dc":   [("1x2_home", "home")],   # DC probs derived from 1x2 at placement time
    "ah":   [("1x2_home", "home")],   # AH probs from Poisson; use 1x2 as proxy
    "dnb":  [("1x2_home", "home")],   # DNB derived from 1x2
}

# Map market → odds_snapshots market string
MARKET_TO_OS = {
    "1x2":  "1x2",
    "ou":   "over_under_25",
    "ou15": "over_under_15",
    "ou35": "over_under_35",
    "btts": "btts",
    "dc":   "double_chance",
    "ah":   "asian_handicap",
    "dnb":  "1x2",  # DNB is computed from 1x2 odds
}


def _prematch_funnel(bot_name: str, config: dict, days: int, conn) -> dict:
    cur = conn.cursor()
    markets = config["markets"]
    tier_filter = config.get("tier_filter")
    league_filter = config.get("league_filter")
    selection_filter = config.get("selection_filter")
    odds_min, odds_max = config["odds_range"]
    min_prob = config["min_prob"]

    # Pick a representative market for odds lookup
    primary_market = markets[0]
    os_market = MARKET_TO_OS.get(primary_market, "1x2")

    # Gate 1: finished matches in window
    cur.execute("""
        SELECT COUNT(DISTINCT m.id)
        FROM matches m JOIN leagues l ON l.id = m.league_id
        WHERE m.date >= NOW() - INTERVAL %(d)s
          AND m.date < NOW()
          AND m.status = 'finished'
    """, {"d": f"{days} days"})
    g1_all = cur.fetchone()[0]

    # Gate 2: after tier filter
    tier_sql = "AND l.tier = ANY(%(tf)s)" if tier_filter else ""
    tf_param = tier_filter or []
    cur.execute(f"""
        SELECT COUNT(DISTINCT m.id)
        FROM matches m JOIN leagues l ON l.id = m.league_id
        WHERE m.date >= NOW() - INTERVAL %(d)s
          AND m.date < NOW()
          AND m.status = 'finished'
          {tier_sql}
    """, {"d": f"{days} days", "tf": tf_param})
    g2_tier = cur.fetchone()[0]

    # Gate 3: after league/country filter
    league_sql = "AND l.country = ANY(%(lf)s)" if league_filter else ""
    lf_param = league_filter or []
    cur.execute(f"""
        SELECT COUNT(DISTINCT m.id)
        FROM matches m JOIN leagues l ON l.id = m.league_id
        WHERE m.date >= NOW() - INTERVAL %(d)s
          AND m.date < NOW()
          AND m.status = 'finished'
          {tier_sql}
          {league_sql}
    """, {"d": f"{days} days", "tf": tf_param, "lf": lf_param})
    g3_league = cur.fetchone()[0]

    # Gate 4: has ensemble prediction for primary market
    pred_market = PREMATCH_MARKET_TO_PREDICTION.get(primary_market, [("1x2_home", "home")])[0][0]
    cur.execute(f"""
        SELECT COUNT(DISTINCT m.id)
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN predictions p ON p.match_id = m.id
          AND p.source = 'ensemble' AND p.market = %(pm)s
        WHERE m.date >= NOW() - INTERVAL %(d)s
          AND m.date < NOW()
          AND m.status = 'finished'
          {tier_sql}
          {league_sql}
    """, {"d": f"{days} days", "tf": tf_param, "lf": lf_param, "pm": pred_market})
    g4_pred = cur.fetchone()[0]

    # Gate 5: has market odds in the odds range (non-Pinnacle)
    cur.execute(f"""
        SELECT COUNT(DISTINCT m.id)
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN predictions p ON p.match_id = m.id
          AND p.source = 'ensemble' AND p.market = %(pm)s
        JOIN LATERAL (
            SELECT MAX(odds) AS best_odds
            FROM odds_snapshots os
            WHERE os.match_id = m.id
              AND os.market = %(osm)s
              AND os.bookmaker NOT IN ('api-football','api-football-live','Pinnacle')
              AND os.is_closing = false
        ) best ON TRUE
        WHERE m.date >= NOW() - INTERVAL %(d)s
          AND m.date < NOW()
          AND m.status = 'finished'
          {tier_sql}
          {league_sql}
          AND best.best_odds IS NOT NULL
          AND best.best_odds >= %(omin)s
          AND best.best_odds <= %(omax)s
    """, {"d": f"{days} days", "tf": tf_param, "lf": lf_param, "pm": pred_market,
          "osm": os_market, "omin": odds_min, "omax": odds_max})
    g5_odds = cur.fetchone()[0]

    # Gate 6: raw edge positive (model prob > implied, before threshold)
    # Use max non-Pinnacle odds as the implied prob denominator
    cur.execute(f"""
        SELECT COUNT(DISTINCT m.id)
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN predictions p ON p.match_id = m.id
          AND p.source = 'ensemble' AND p.market = %(pm)s
        JOIN LATERAL (
            SELECT MAX(odds) AS best_odds
            FROM odds_snapshots os
            WHERE os.match_id = m.id AND os.market = %(osm)s
              AND os.bookmaker NOT IN ('api-football','api-football-live','Pinnacle')
              AND os.is_closing = false
        ) best ON TRUE
        WHERE m.date >= NOW() - INTERVAL %(d)s
          AND m.date < NOW()
          AND m.status = 'finished'
          {tier_sql}
          {league_sql}
          AND best.best_odds >= %(omin)s AND best.best_odds <= %(omax)s
          AND p.model_probability >= %(mp)s
          AND (p.model_probability - 1.0/best.best_odds) > 0
    """, {"d": f"{days} days", "tf": tf_param, "lf": lf_param, "pm": pred_market,
          "osm": os_market, "omin": odds_min, "omax": odds_max, "mp": min_prob})
    g6_edge_pos = cur.fetchone()[0]

    # Gate 7: edge meets bot's minimum threshold
    # Use the lowest tier edge threshold as the floor
    min_edge = min(
        v
        for td in config["edge_thresholds"].values()
        for v in td.values()
    )
    cur.execute(f"""
        SELECT COUNT(DISTINCT m.id)
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN predictions p ON p.match_id = m.id
          AND p.source = 'ensemble' AND p.market = %(pm)s
        JOIN LATERAL (
            SELECT MAX(odds) AS best_odds
            FROM odds_snapshots os
            WHERE os.match_id = m.id AND os.market = %(osm)s
              AND os.bookmaker NOT IN ('api-football','api-football-live','Pinnacle')
              AND os.is_closing = false
        ) best ON TRUE
        WHERE m.date >= NOW() - INTERVAL %(d)s
          AND m.date < NOW()
          AND m.status = 'finished'
          {tier_sql}
          {league_sql}
          AND best.best_odds >= %(omin)s AND best.best_odds <= %(omax)s
          AND p.model_probability >= %(mp)s
          AND (p.model_probability - 1.0/best.best_odds) >= %(me)s
    """, {"d": f"{days} days", "tf": tf_param, "lf": lf_param, "pm": pred_market,
          "osm": os_market, "omin": odds_min, "omax": odds_max, "mp": min_prob, "me": min_edge})
    g7_edge_thresh = cur.fetchone()[0]

    # Gate 8: not Pinnacle-vetoed (model not >12pp above Pinnacle implied)
    VETO_GAP = 0.12
    cur.execute(f"""
        SELECT COUNT(DISTINCT m.id)
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN predictions p ON p.match_id = m.id
          AND p.source = 'ensemble' AND p.market = %(pm)s
        JOIN LATERAL (
            SELECT MAX(odds) AS best_odds
            FROM odds_snapshots os
            WHERE os.match_id = m.id AND os.market = %(osm)s
              AND os.bookmaker NOT IN ('api-football','api-football-live','Pinnacle')
              AND os.is_closing = false
        ) best ON TRUE
        LEFT JOIN LATERAL (
            SELECT MAX(odds) AS pin_odds
            FROM odds_snapshots os
            WHERE os.match_id = m.id AND os.market = %(osm)s
              AND os.bookmaker = 'Pinnacle'
              AND os.is_closing = false
        ) pin ON TRUE
        WHERE m.date >= NOW() - INTERVAL %(d)s
          AND m.date < NOW()
          AND m.status = 'finished'
          {tier_sql}
          {league_sql}
          AND best.best_odds >= %(omin)s AND best.best_odds <= %(omax)s
          AND p.model_probability >= %(mp)s
          AND (p.model_probability - 1.0/best.best_odds) >= %(me)s
          AND (
            pin.pin_odds IS NULL
            OR (p.model_probability - 1.0/pin.pin_odds) <= %(vg)s
          )
    """, {"d": f"{days} days", "tf": tf_param, "lf": lf_param, "pm": pred_market,
          "osm": os_market, "omin": odds_min, "omax": odds_max, "mp": min_prob,
          "me": min_edge, "vg": VETO_GAP})
    g8_pin_veto = cur.fetchone()[0]

    # Actual fires from simulated_bets
    cur.execute("""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE result IN ('won','lost')),
               AVG(edge_percent) FILTER (WHERE result IN ('won','lost')),
               AVG(clv) FILTER (WHERE result IN ('won','lost') AND clv IS NOT NULL),
               SUM(pnl) FILTER (WHERE result IN ('won','lost')),
               SUM(stake) FILTER (WHERE result IN ('won','lost'))
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE b.name = %(bn)s
          AND sb.pick_time >= NOW() - INTERVAL %(d)s
    """, {"bn": bot_name, "d": f"{days} days"})
    r = cur.fetchone()
    fired_total, fired_settled, avg_edge, avg_clv, total_pnl, total_staked = r
    roi = (total_pnl / total_staked * 100) if total_staked and total_staked > 0 else None

    return {
        "g1_all":       g1_all,
        "g2_tier":      g2_tier,
        "g3_league":    g3_league,
        "g4_pred":      g4_pred,
        "g5_odds":      g5_odds,
        "g6_edge_pos":  g6_edge_pos,
        "g7_edge_thresh": g7_edge_thresh,
        "g8_pin_veto":  g8_pin_veto,
        "fired_total":  fired_total or 0,
        "fired_settled": fired_settled or 0,
        "avg_edge":     avg_edge,
        "avg_clv":      avg_clv,
        "roi":          roi,
        "min_edge":     min_edge,
        "markets":      markets,
        "primary_market": primary_market,
    }


def _print_prematch_funnel(bot_name: str, config: dict, f: dict):
    print(f"\n{_b(bot_name)} {_dim('— ' + config['description'])}")
    print(f"  markets={config['markets']}  odds={config['odds_range']}  "
          f"min_prob={config['min_prob']}  min_edge={f['min_edge']:.0%}  "
          f"tier={config.get('tier_filter','all')}  "
          f"leagues={config.get('league_filter','all')}")

    g1 = f["g1_all"]
    stages = [
        ("All finished matches",           f["g1_all"]),
        ("After tier filter",              f["g2_tier"]),
        ("After league filter",            f["g3_league"]),
        ("Has ensemble prediction",        f["g4_pred"]),
        ("Odds in range (non-Pinnacle)",   f["g5_odds"]),
        ("Raw edge > 0",                   f["g6_edge_pos"]),
        (f"Edge ≥ {f['min_edge']:.0%}",   f["g7_edge_thresh"]),
        ("Passes Pinnacle veto",           f["g8_pin_veto"]),
        (f"{'FIRED (placed bets)':30}",    f["fired_total"]),
    ]

    prev = g1
    for label, count in stages:
        drop = prev - count if count <= prev else 0
        drop_str = f"  {_dim('(−' + str(drop) + ')')}" if drop > 0 else ""
        pct = _pct_color(count, g1)
        print(f"  {pct}  {count:>6,}  {label}{drop_str}")
        if label.startswith("FIRED"):
            break
        prev = count

    # Identify limiting gate (biggest absolute drop among configurable gates pred→fired)
    gate_values = [f["g4_pred"], f["g5_odds"], f["g6_edge_pos"], f["g7_edge_thresh"], f["g8_pin_veto"]]
    gate_names  = ["odds out of range", "edge ≤ 0", "edge below threshold", "Pinnacle veto"]
    drops = [gate_values[i-1] - gate_values[i] for i in range(1, len(gate_values))]
    if drops:
        limiting_idx = drops.index(max(drops))
        lname = gate_names[limiting_idx] if limiting_idx < len(gate_names) else "Pinnacle veto"
        print(f"  {_y('→ Limiting gate: ' + lname)} "
              f"{_dim('(drops ' + str(drops[limiting_idx]) + ' candidates)')}")

    # Performance
    settled = f["fired_settled"]
    clv_str = f"avg_clv={f['avg_clv']:+.3f}" if f["avg_clv"] is not None else "avg_clv=n/a"
    edge_str = f"avg_edge={f['avg_edge']:.1%}" if f["avg_edge"] is not None else ""
    roi_str = _roi_color(f["roi"])
    print(f"  settled={settled}  roi={roi_str}  {clv_str}  {edge_str}")


# ── Inplay funnels ────────────────────────────────────────────────────────────
# Each strategy's gates are hard-coded here mirroring _check_strategy_* logic.

INPLAY_STRATEGIES = {
    "inplay_a": {
        "desc": "xG Divergence O2.5 — 0-0 or 1-0/0-1, min 25-35",
        "gates": [
            ("minute 25-35",            "minute BETWEEN 25 AND 35"),
            ("no red card",             "TRUE"),  # approximated — no red card col in snapshots
            ("total_goals ≤ 1",         "(COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1"),
            ("live_ou_25_over IS NOT NULL OR prematch fallback", "live_ou_25_over IS NOT NULL OR TRUE"),
            ("ou_25 ≥ 2.10",            "COALESCE(live_ou_25_over, 1.0) >= 2.10"),
        ],
    },
    "inplay_b": {
        "desc": "BTTS Momentum — trailing team, min 15-40",
        "gates": [
            ("minute 15-40",            "minute BETWEEN 15 AND 40"),
            ("one team trailing",       "(score_home=1 AND score_away=0) OR (score_home=0 AND score_away=1)"),
            ("has live OU 2.5",         "live_ou_25_over IS NOT NULL"),
        ],
    },
    "inplay_c": {
        "desc": "Favourite Comeback — fav trailing by 1",
        "gates": [
            ("minute 25-70",            "minute BETWEEN 25 AND 70"),
            ("score diff = 1",          "ABS(COALESCE(score_home,0)-COALESCE(score_away,0)) = 1"),
            ("has live 1x2",            "live_1x2_home IS NOT NULL OR live_1x2_away IS NOT NULL"),
        ],
    },
    "inplay_d": {
        "desc": "Late Goals Compression O2.5 — min 55-75",
        "gates": [
            ("minute 55-75",            "minute BETWEEN 55 AND 75"),
            ("no red card",             "TRUE"),
            ("total_goals ≤ 1",         "(COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1"),
            ("live_ou_25 ≥ 2.10",       "COALESCE(live_ou_25_over, 0) >= 2.10"),
        ],
    },
    "inplay_e": {
        "desc": "Dead Game Unders — tempo collapse, min 25-50",
        "gates": [
            ("minute 25-50",            "minute BETWEEN 25 AND 50"),
            ("score 0-0",               "COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0"),
            ("has live OU 2.5",         "live_ou_25_under IS NOT NULL"),
            ("ou_25_under ≥ 2.10",      "COALESCE(live_ou_25_under, 0) >= 2.10"),
        ],
    },
    "inplay_g": {
        "desc": "Corner Cluster O2.5 — ≥3 corners last 10min, min 30-70",
        "gates": [
            ("minute 30-70",            "minute BETWEEN 30 AND 70"),
            ("has corner data",         "COALESCE(corners_home,0)+COALESCE(corners_away,0) > 0"),
            ("live_ou_25 available",    "live_ou_25_over IS NOT NULL"),
        ],
    },
    "inplay_h": {
        "desc": "HT Restart Surge O2.5 — 0-0 at HT, min 46-55",
        "gates": [
            ("minute 46-55",            "minute BETWEEN 46 AND 55"),
            ("score 0-0",               "COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0"),
            ("has live OU 2.5",         "live_ou_25_over IS NOT NULL"),
            ("ou_25 ≥ 2.10",            "COALESCE(live_ou_25_over, 0) >= 2.10"),
        ],
    },
    "inplay_i": {
        "desc": "Favourite Stall — 0-0 min 42-65, fav drifted ≥ 3.0",
        "gates": [
            ("minute 42-65",            "minute BETWEEN 42 AND 65"),
            ("score 0-0",               "COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0"),
            ("has live 1x2",            "live_1x2_home IS NOT NULL OR live_1x2_away IS NOT NULL"),
            ("fav_odds ≥ 3.0",          "LEAST(COALESCE(live_1x2_home,999), COALESCE(live_1x2_away,999)) >= 3.0"),
        ],
    },
    "inplay_j": {
        "desc": "Goal Debt O1.5 — 0-0 min 30-52, prematch O25 ≥ 0.55, OU15 ≥ 2.85",
        "gates": [
            ("minute 30-52",            "minute BETWEEN 30 AND 52"),
            ("score 0-0",               "COALESCE(score_home,0)=0 AND COALESCE(score_away,0)=0"),
            # prematch_o25 ≥ 0.62 requires a join — use correlated subquery with
            # explicit table qualification so match_id is unambiguous.
            ("prematch_o25 ≥ 0.55",     "EXISTS(SELECT 1 FROM predictions p2 WHERE p2.match_id=live_match_snapshots.match_id AND p2.source='ensemble' AND p2.market='over25' AND p2.model_probability>=0.55)"),
            ("live_ou_15 ≥ 2.85",       "COALESCE(live_ou_15_over, 0) >= 2.85"),
        ],
    },
    "inplay_l": {
        "desc": "Goal Contagion — 1st goal min 15-35, O25 at remaining Poisson",
        "gates": [
            ("minute 15-35",            "minute BETWEEN 15 AND 35"),
            ("exactly 1 goal scored",   "(COALESCE(score_home,0)+COALESCE(score_away,0)) = 1"),
            ("has live OU 2.5",         "live_ou_25_over IS NOT NULL"),
        ],
    },
    "inplay_m": {
        "desc": "Equalizer Magnet — 1-0/0-1 min 30-60, BTTS ≥ 0.48",
        "gates": [
            ("minute 30-60",            "minute BETWEEN 30 AND 60"),
            ("score 1-0 or 0-1",        "(score_home=1 AND score_away=0) OR (score_home=0 AND score_away=1)"),
            ("ou_25 ≥ 2.40 (live or pm)", "COALESCE(live_ou_25_over, 0) >= 2.40"),
        ],
    },
    "inplay_n": {
        "desc": "Late Favourite Push — 0-0/1-1 min 72-80, home_win_prob ≥ 0.65",
        "gates": [
            ("minute 72-80",            "minute BETWEEN 72 AND 80"),
            ("score 0-0 or 1-1",        "(score_home=0 AND score_away=0) OR (score_home=1 AND score_away=1)"),
            ("has live 1x2 home",       "live_1x2_home IS NOT NULL"),
            ("home odds drifted ≥ 2.20", "COALESCE(live_1x2_home, 0) >= 2.20"),
        ],
    },
    "inplay_q": {
        "desc": "Red Card Overreaction O2.5 — red 15-55, total ≤ 1",
        "gates": [
            ("minute 15-55",            "minute BETWEEN 15 AND 55"),
            ("total_goals ≤ 1",         "(COALESCE(score_home,0)+COALESCE(score_away,0)) <= 1"),
            ("has live OU 2.5",         "live_ou_25_over IS NOT NULL"),
            ("live_ou_25 ≥ 2.30",       "COALESCE(live_ou_25_over, 0) >= 2.30"),
        ],
    },
}


def _inplay_funnel(bot_name: str, days: int, conn) -> dict:
    cur = conn.cursor()
    strategy = INPLAY_STRATEGIES.get(bot_name)
    if not strategy:
        return {}

    base_where = f"captured_at >= NOW() - INTERVAL '{days} days'"

    # Gate 0: all snapshots
    cur.execute(f"SELECT COUNT(*) FROM live_match_snapshots WHERE {base_where}")
    g0 = cur.fetchone()[0]

    counts = [g0]
    cumulative_where = base_where

    for gate_name, gate_sql in strategy["gates"]:
        if gate_sql is None:
            # inplay_j's O25 join — handle specially
            cur.execute(f"""
                SELECT COUNT(*) FROM live_match_snapshots lms
                JOIN predictions p ON p.match_id = lms.match_id
                  AND p.source='ensemble' AND p.market='over_under_25_over'
                WHERE {cumulative_where}
                  AND p.model_probability >= 0.55
            """)
        else:
            cur.execute(f"""
                SELECT COUNT(*) FROM live_match_snapshots
                WHERE {cumulative_where} AND ({gate_sql})
            """)
        n = cur.fetchone()[0]
        counts.append(n)
        cumulative_where += f" AND ({gate_sql})" if gate_sql else ""

    # Actual fires
    cur.execute("""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE result IN ('won','lost')),
               AVG(clv) FILTER (WHERE result IN ('won','lost') AND clv IS NOT NULL),
               SUM(pnl) FILTER (WHERE result IN ('won','lost')),
               SUM(stake) FILTER (WHERE result IN ('won','lost'))
        FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
        WHERE b.name = %(bn)s AND sb.pick_time >= NOW() - INTERVAL %(d)s
    """, {"bn": bot_name, "d": f"{days} days"})
    r = cur.fetchone()
    fired_total, fired_settled = r[0] or 0, r[1] or 0
    avg_clv = r[2]
    roi = (r[3] / r[4] * 100) if r[4] and r[4] > 0 else None

    return {
        "counts":        counts,
        "gate_names":    ["All snapshots"] + [g[0] for g in strategy["gates"]],
        "fired_total":   fired_total,
        "fired_settled": fired_settled,
        "avg_clv":       avg_clv,
        "roi":           roi,
    }


def _print_inplay_funnel(bot_name: str, f: dict):
    if not f:
        print(f"\n{_b(bot_name)}: {_r('strategy not in audit map')}")
        return
    s = INPLAY_STRATEGIES.get(bot_name, {})
    print(f"\n{_b(bot_name)} {_dim('— ' + s.get('desc', ''))}")

    total = f["counts"][0]
    prev = total
    for label, count in zip(f["gate_names"], f["counts"]):
        drop = prev - count if count < prev else 0
        drop_str = f"  {_dim('(−' + str(drop) + ')')}" if drop > 0 else ""
        pct = _pct_color(count, total)
        print(f"  {pct}  {count:>10,}  {label}{drop_str}")
        prev = count

    # Limiting gate (biggest drop)
    drops = [f["counts"][i-1] - f["counts"][i] for i in range(1, len(f["counts"]))]
    if drops:
        li = drops.index(max(drops))
        print(f"  {_y('→ Limiting gate: ' + f['gate_names'][li+1])} "
              f"{_dim('(drops ' + str(drops[li]) + ' candidates)')}")

    fired_rate = f["fired_total"] / (f["counts"][0] or 1) * 100
    roi_str = _roi_color(f["roi"])
    clv_str = f"avg_clv={f['avg_clv']:+.3f}" if f["avg_clv"] is not None else "avg_clv=n/a"
    print(f"  fired={f['fired_total']} ({fired_rate:.3f}% of all snapshots)  "
          f"settled={f['fired_settled']}  roi={roi_str}  {clv_str}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot",  default=None, help="Single bot name")
    parser.add_argument("--days", default=14, type=int, help="Lookback window in days")
    parser.add_argument("--type", default="all", choices=["all","prematch","inplay"])
    args = parser.parse_args()

    print(f"\n{_b('Bot Strategy Audit')} — last {args.days} days\n" + "="*60)

    with get_conn() as conn:
        run_prematch = args.type in ("all", "prematch")
        run_inplay   = args.type in ("all", "inplay")

        if args.bot:
            if args.bot in BOT_CONFIGS:
                config = BOT_CONFIGS[args.bot]
                f = _prematch_funnel(args.bot, config, args.days, conn)
                _print_prematch_funnel(args.bot, config, f)
            elif args.bot in INPLAY_STRATEGIES:
                f = _inplay_funnel(args.bot, args.days, conn)
                _print_inplay_funnel(args.bot, f)
            else:
                print(f"Unknown bot: {args.bot}")
            return

        if run_prematch:
            print(f"\n{_b('── PREMATCH BOTS ──────────────────────────────────────')}")
            for bot_name, config in BOT_CONFIGS.items():
                f = _prematch_funnel(bot_name, config, args.days, conn)
                _print_prematch_funnel(bot_name, config, f)

        if run_inplay:
            print(f"\n{_b('── INPLAY BOTS ────────────────────────────────────────')}")
            for bot_name in INPLAY_STRATEGIES:
                f = _inplay_funnel(bot_name, args.days, conn)
                _print_inplay_funnel(bot_name, f)

    print()


if __name__ == "__main__":
    main()
