"""Retroactive backtest — all-market ROI for leagues previously blocked by tier=0.

Now that migrations 138+140 fix the tier classifications, this script simulates
what bets would have fired on historical settled matches in those leagues, using:
  - predictions.model_probability  — XGBoost/Poisson model probs
  - odds_snapshots (is_closing=false, best across bookmakers) — opening price
  - matches.result — actual outcome

Markets covered:
  1x2 (home/draw/away), AH (with exact handicap_line match), DNB (derived from
  1x2 odds), OU 0.5/1.5/2.5/3.5. BTTS/DC excluded (retired or low confidence).

Run:
    python3 scripts/backtest_tier_unlocked.py
    python3 scripts/backtest_tier_unlocked.py --market ah
    python3 scripts/backtest_tier_unlocked.py --market dnb
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

# Bot-like edge thresholds and odds ranges, mirroring daily_pipeline_v2.py configs
# for the tier-gated bots. Used to decide which historical bets would have fired.
BOT_FILTERS = {
    "bot_ah_home_fav":  {"sel": "home", "min_edge": 0.05, "odds_lo": 1.50, "odds_hi": 2.20, "min_prob": 0.55, "hl_max": -0.5},
    "bot_ah_away_dog":  {"sel": "away", "min_edge": 0.05, "odds_lo": 1.70, "odds_hi": 2.50, "min_prob": 0.50, "hl_min": 0.5},
    "bot_dnb_home_value": {"sel": "Home", "min_edge": 0.05, "odds_lo": 1.20, "odds_hi": 2.00, "min_prob": 0.55},
    "bot_dnb_away_value": {"sel": "Away", "min_edge": 0.05, "odds_lo": 1.40, "odds_hi": 2.50, "min_prob": 0.45},
}

ACCESSIBLE_BOOKMAKERS = {
    "Bet365", "William Hill", "Bwin", "Unibet", "Pinnacle",
    "1xBet", "Betfair Exchange", "Betway", "188Bet", "10Bet",
}


def q(sql: str, params=None) -> list[dict]:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]


def roi(bets: list[dict]) -> float:
    if not bets:
        return 0.0
    return float(sum(b["pnl"] for b in bets)) / (len(bets) * STAKE) * 100


def fmt(r: float) -> str:
    return f"{'+'if r>=0 else ''}{r:.1f}%"


def _result_won(match_result: str, selection: str, market: str) -> bool | None:
    """Return True=won, False=lost, None=void."""
    r = match_result.lower() if match_result else ""
    if r in ("", "ns", "tbd", "canc", "pst", "abd"):
        return None
    s = selection.lower()
    m = market.lower()

    if m in ("1x2", "draw_no_bet", "asian_handicap"):
        if r == "home" and s == "home":
            return True
        if r == "away" and s == "away":
            return True
        if r == "draw":
            if m == "draw_no_bet":
                return None  # push
            return s == "draw"
        return False
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="all",
                        choices=["all", "ah", "dnb", "1x2", "ou"],
                        help="Restrict to one market family")
    args = parser.parse_args()

    # ── Fetch settled matches in tier=0 leagues ───────────────────────────────
    # After migrations 138+140, these will move to tier=1/2 — but the DB still
    # shows tier=0 until GitHub Actions applies the migrations. Run this before
    # or after; the match/prediction data is already there.
    settled = q("""
        SELECT
            m.id          AS match_id,
            m.result::text AS match_result,
            m.date,
            l.name        AS league_name,
            l.country,
            l.tier
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.is_active = true
          AND l.tier = 0
          AND m.result IS NOT NULL
          AND m.result::text NOT IN ('', 'NS', 'TBD', 'CANC', 'PST', 'ABD', 'AWD', 'WO')
        ORDER BY m.date DESC
    """)

    if not settled:
        print("No settled matches in tier=0 leagues. Migrations may have already run.")
        print("Re-run with --tier=1 to see post-fix data, or check DB tier values.")
        return

    match_ids = [str(r["match_id"]) for r in settled]
    result_by_id = {str(r["match_id"]): r for r in settled}
    print(f"\nSettled tier=0 matches: {len(settled)}\n")

    # ── Fetch predictions ─────────────────────────────────────────────────────
    preds = q("""
        SELECT match_id, market, model_probability
        FROM predictions
        WHERE match_id = ANY(%s::uuid[])
          AND model_probability IS NOT NULL
    """, (match_ids,))

    # Index: match_id -> [pred rows]
    preds_by_match: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        preds_by_match[str(p["match_id"])].append(p)

    # ── Fetch best opening odds per match ─────────────────────────────────────
    # AH: keyed by (match_id, selection, handicap_line)
    # Others: keyed by (match_id, market, selection)
    snap_rows = q("""
        SELECT match_id, market, selection, handicap_line, MAX(odds) AS best_odds
        FROM odds_snapshots
        WHERE match_id = ANY(%s::uuid[])
          AND is_closing = false
          AND odds BETWEEN 1.05 AND 30.0
          AND bookmaker = ANY(%s)
        GROUP BY match_id, market, selection, handicap_line
    """, (match_ids, list(ACCESSIBLE_BOOKMAKERS)))

    # flat odds index
    odds_flat: dict[tuple, float] = {}
    ah_odds: dict[tuple, float] = {}  # (match_id, selection, handicap_line) -> best_odds
    for s in snap_rows:
        mid = str(s["match_id"])
        mkt = str(s["market"])
        sel = str(s["selection"])
        best = float(s["best_odds"])
        if mkt == "asian_handicap" and s["handicap_line"] is not None:
            ah_odds[(mid, sel, float(s["handicap_line"]))] = best
        else:
            odds_flat[(mid, mkt, sel)] = best

    # ── Simulate bets ──────────────────────────────────────────────────────────
    simulated: list[dict] = []
    skipped_no_odds = 0

    def _add(mid: str, bot: str, mkt: str, sel: str, model_prob: float, odds: float, edge: float):
        cfg = BOT_FILTERS.get(bot, {})
        if model_prob < cfg.get("min_prob", 0):
            return
        if not (cfg.get("odds_lo", 1.0) <= odds <= cfg.get("odds_hi", 99.0)):
            return
        if edge < cfg.get("min_edge", 0.05):
            return
        match = result_by_id[mid]
        won = _result_won(match["match_result"], sel, mkt)
        if won is None:
            return
        pnl = STAKE * (odds - 1) if won else -STAKE
        simulated.append({
            "bot": bot, "market": mkt, "selection": sel,
            "odds": odds, "model_prob": model_prob, "edge": edge,
            "won": won, "pnl": pnl,
            "league": f"{match['country']} — {match['league_name']}",
            "tier": match["tier"],
        })

    for mid, pred_list in preds_by_match.items():
        # Group 1x2 predictions for this match (need all three for DNB derivation)
        probs_1x2: dict[str, float] = {}
        odds_1x2:  dict[str, float] = {}
        for p in pred_list:
            mkt = str(p["market"])
            mp = float(p["model_probability"])
            if mkt in ("1x2_home", "1x2_draw", "1x2_away"):
                side = mkt.split("_")[1]  # home/draw/away
                probs_1x2[side] = mp
                # find odds — odds_snapshots uses market='1x2', selection='Home'/'Draw'/'Away'
                for sel_variant in (side.title(), side.lower(), side.upper()):
                    o = odds_flat.get((mid, "1x2", sel_variant))
                    if o:
                        odds_1x2[side] = o
                        break

        for p in pred_list:
            mkt = str(p["market"])
            mp = float(p["model_probability"])

            # ── AH ──
            if args.market in ("all", "ah") and mkt.startswith("ah_"):
                parts = mkt.split("_")
                if len(parts) == 3:
                    side = parts[1]  # home or away
                    try:
                        hl = float(parts[2])
                    except ValueError:
                        continue
                    # Check line filter for each bot
                    for bot_name in ("bot_ah_home_fav", "bot_ah_away_dog"):
                        cfg = BOT_FILTERS[bot_name]
                        if cfg["sel"] != side:
                            continue
                        if "hl_max" in cfg and hl > cfg["hl_max"]:
                            continue
                        if "hl_min" in cfg and hl < cfg["hl_min"]:
                            continue
                        odds = ah_odds.get((mid, side, hl))
                        if not odds:
                            skipped_no_odds += 1
                            continue
                        ip = 1.0 / odds
                        edge = mp - ip
                        _add(mid, bot_name, "asian_handicap", side, mp, odds, edge)

            # ── 1x2 ──
            elif args.market in ("all", "1x2") and mkt in ("1x2_home", "1x2_draw", "1x2_away"):
                side = mkt.split("_")[1]
                odds = odds_1x2.get(side)
                if not odds:
                    skipped_no_odds += 1
                    continue
                ip = 1.0 / odds
                edge = mp - ip
                # No specific 1x2 bot in gated list — but include for completeness
                if edge >= 0.05 and 1.40 <= odds <= 3.50 and mp >= 0.40:
                    won = _result_won(result_by_id[mid]["match_result"], side, "1x2")
                    if won is None:
                        continue
                    pnl = STAKE * (odds - 1) if won else -STAKE
                    simulated.append({
                        "bot": f"1x2_{side}", "market": "1x2", "selection": side,
                        "odds": odds, "model_prob": mp, "edge": edge,
                        "won": won, "pnl": pnl,
                        "league": f"{result_by_id[mid]['country']} — {result_by_id[mid]['league_name']}",
                        "tier": result_by_id[mid]["tier"],
                    })

        # ── DNB (derived from 1x2) ──
        if args.market in ("all", "dnb") and len(probs_1x2) == 3 and len(odds_1x2) >= 2:
            hp = probs_1x2.get("home", 0)
            dp = probs_1x2.get("draw", 0)
            ap = probs_1x2.get("away", 0)
            ho = odds_1x2.get("home")
            ao = odds_1x2.get("away")
            if ho and ao:
                # DNB derived odds: home_dnb_odds = (home + away) / away
                dnb_h_odds = (ho + ao) / ao
                dnb_a_odds = (ho + ao) / ho
                denom = hp + ap
                if denom > 0:
                    dnb_h_prob = hp / denom
                    dnb_a_prob = ap / denom
                    for bot_name, sel_label, odds, model_prob in [
                        ("bot_dnb_home_value", "Home", dnb_h_odds, dnb_h_prob),
                        ("bot_dnb_away_value", "Away", dnb_a_odds, dnb_a_prob),
                    ]:
                        ip = 1.0 / odds
                        edge = model_prob - ip
                        _add(mid, bot_name, "draw_no_bet", sel_label, model_prob, odds, edge)

    # ── Print results ─────────────────────────────────────────────────────────
    if not simulated:
        print(f"No qualifying bets found. Skipped {skipped_no_odds} predictions with no odds.")
        return

    print(f"Qualifying bets found: {len(simulated)}  (skipped {skipped_no_odds} with no odds)\n")

    by_bot: dict[str, list] = defaultdict(list)
    by_league: dict[str, list] = defaultdict(list)
    for b in simulated:
        by_bot[b["bot"]].append(b)
        by_league[b["league"]].append(b)

    # ── Bot summary ──
    print("=" * 65)
    print(f"{'BOT / MARKET':<32}  {'BETS':>5}  {'ROI':>8}  {'P&L':>8}")
    print("=" * 65)
    for bot, bets in sorted(by_bot.items()):
        total_pnl = sum(b["pnl"] for b in bets)
        print(f"{bot:<32}  {len(bets):>5}  {fmt(roi(bets)):>8}  {total_pnl:>+8.1f}")
    total_pnl = sum(b["pnl"] for b in simulated)
    print(f"{'TOTAL':<32}  {len(simulated):>5}  {fmt(roi(simulated)):>8}  {total_pnl:>+8.1f}")

    # ── League breakdown (≥5 bets) ──
    print()
    qualifying = [(k, v) for k, v in by_league.items() if len(v) >= 5]
    if qualifying:
        qualifying.sort(key=lambda x: -len(x[1]))
        print("=" * 65)
        print(f"{'LEAGUE':<40}  {'BETS':>5}  {'ROI':>8}")
        print("=" * 65)
        for league, bets in qualifying[:25]:
            print(f"{league:<40}  {len(bets):>5}  {fmt(roi(bets)):>8}")
        small = [b for k, v in by_league.items() for b in v if len(v) < 5]
        if small:
            print(f"{'(other leagues, <5 bets each)':<40}  {len(small):>5}  {fmt(roi(small)):>8}")

    # ── Compare with live paper trading for same bots ──
    live_bots = [b for b in BOT_FILTERS if b in {b["bot"] for b in simulated}]
    if live_bots:
        live = q("""
            SELECT bots.name AS bot_name, sb.pnl
            FROM simulated_bets sb
            JOIN bots ON bots.id = sb.bot_id
            WHERE bots.name = ANY(%s)
              AND sb.result::text IN ('won', 'lost')
              AND sb.pnl IS NOT NULL
        """, (live_bots,))
        live_by_bot: dict[str, list] = defaultdict(list)
        for r in live:
            live_by_bot[r["bot_name"]].append(float(r["pnl"]))

        print()
        print("=" * 80)
        print("Comparison: live paper (tier≥1) vs this backtest (tier=0 unlocked)")
        print("=" * 80)
        print(f"{'BOT':<30}  {'LIVE BETS':>9}  {'LIVE ROI':>8}  {'RETRO BETS':>10}  {'RETRO ROI':>9}")
        print("-" * 80)
        for bot in sorted(set(b["bot"] for b in simulated) & set(BOT_FILTERS.keys())):
            live_pnls = live_by_bot.get(bot, [])
            live_roi = (sum(live_pnls) / (len(live_pnls) * STAKE) * 100) if live_pnls else 0
            retro = by_bot.get(bot, [])
            print(
                f"{bot:<30}  {len(live_pnls):>9}  {fmt(live_roi):>8}  "
                f"{len(retro):>10}  {fmt(roi(retro)):>9}"
            )


if __name__ == "__main__":
    main()
