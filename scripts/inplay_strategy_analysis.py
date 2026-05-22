#!/usr/bin/env python3
"""
Inplay strategy deep-dive analysis.

Focus: inplay_c (Favourite Comeback) + all other inplay bots.
Questions:
  1. When exactly do we bet? (minute distribution)
  2. ROI by minute bucket — is late-game (>55min) losing?
  3. For inplay_c losses: did the game end as draw or underdog win?
  4. "Draw instead of win" hypothesis: what would draw odds have looked like?
  5. Per-bot summary with hit rate and ROI.

Usage:
    python scripts/inplay_strategy_analysis.py
    python scripts/inplay_strategy_analysis.py --bot inplay_c
    python scripts/inplay_strategy_analysis.py --bot inplay_o
"""

import argparse
import json
import sys
from collections import defaultdict

from workers.api_clients.db import execute_query


def _parse_minute(reasoning: dict) -> int | None:
    """Extract bet placement minute from reasoning JSON."""
    m = reasoning.get("minute")
    return int(m) if m is not None else None


def _parse_score(reasoning: dict) -> tuple[int, int] | None:
    """Extract score at bet placement from reasoning JSON."""
    s = reasoning.get("score")
    if s and "-" in str(s):
        parts = str(s).split("-")
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return None


def load_bets(bot_filter: str | None = None) -> list[dict]:
    """Load all settled inplay simulated_bets with match outcome."""
    where = "AND b.bot_id IN (SELECT id FROM bots WHERE name LIKE 'inplay_%%')"
    if bot_filter:
        where = f"AND b.bot_id = (SELECT id FROM bots WHERE name = '{bot_filter}')"

    rows = execute_query(f"""
        SELECT
            b.id,
            bo.name                  AS bot_name,
            b.match_id,
            b.market,
            b.selection,
            b.odds_at_pick           AS odds,
            b.result,
            b.pnl,
            b.stake,
            b.reasoning,
            m.score_home             AS final_home,
            m.score_away             AS final_away,
            m.date                   AS kickoff_time
        FROM simulated_bets b
        JOIN bots bo ON b.bot_id = bo.id
        LEFT JOIN matches m ON b.match_id = m.id
        WHERE b.result NOT IN ('pending', 'void')
          AND b.result IS NOT NULL
          {where}
        ORDER BY b.pick_time
    """, [])
    return rows


def bucket_label(minute: int | None) -> str:
    if minute is None:
        return "unknown"
    if minute <= 30:
        return "≤30"
    if minute <= 40:
        return "31-40"
    if minute <= 50:
        return "41-50"
    if minute <= 60:
        return "51-60"
    if minute <= 70:
        return "61-70"
    return "71+"


def analyse_bot(bot_name: str, bets: list[dict]):
    if not bets:
        print(f"\n  {bot_name}: no settled bets")
        return

    total = len(bets)
    won = sum(1 for b in bets if b["result"] == "won")
    pnl = sum(float(b["pnl"] or 0) for b in bets)
    staked = sum(float(b["stake"] or 5) for b in bets)
    roi = pnl / staked * 100 if staked else 0
    avg_odds = sum(float(b["odds"] or 0) for b in bets) / total

    print(f"\n{'='*60}")
    print(f"  {bot_name}  ({total} bets, {won} won, {won/total*100:.0f}% hit rate)")
    print(f"  ROI: {roi:+.1f}%   P&L: {pnl:+.2f}€   avg odds: {avg_odds:.2f}")
    print(f"{'='*60}")

    # ── Minute distribution ──────────────────────────────────────────────────
    buckets: dict[str, list[dict]] = defaultdict(list)
    for b in bets:
        rsn = {}
        if b["reasoning"]:
            try:
                rsn = json.loads(b["reasoning"]) if isinstance(b["reasoning"], str) else b["reasoning"]
            except Exception:
                pass
        b["_minute"] = _parse_minute(rsn)
        b["_score"] = _parse_score(rsn)
        buckets[bucket_label(b["_minute"])].append(b)

    print("\n  By placement minute:")
    print(f"  {'Bucket':<10} {'Bets':>5} {'Won':>5} {'Hit%':>6} {'ROI%':>7} {'AvgOdds':>9}")
    print(f"  {'-'*50}")
    for label in ["≤30", "31-40", "41-50", "51-60", "61-70", "71+", "unknown"]:
        bs = buckets.get(label, [])
        if not bs:
            continue
        bw = sum(1 for b in bs if b["result"] == "won")
        bp = sum(float(b["pnl"] or 0) for b in bs)
        bst = sum(float(b["stake"] or 5) for b in bs)
        broi = bp / bst * 100 if bst else 0
        badds = sum(float(b["odds"] or 0) for b in bs) / len(bs)
        print(f"  {label:<10} {len(bs):>5} {bw:>5} {bw/len(bs)*100:>5.0f}% {broi:>+6.1f}% {badds:>9.2f}")

    # ── For inplay_c: final score breakdown of losses ────────────────────────
    if bot_name == "inplay_c":
        _analyse_c_losses(bets)

    # ── For inplay_o: final score breakdown ─────────────────────────────────
    if bot_name == "inplay_o":
        _analyse_o_outcomes(bets)


def _analyse_c_losses(bets: list[dict]):
    """Break down inplay_c losses: draw vs underdog win vs void."""
    print("\n  inplay_c loss breakdown (what actually happened):")
    losses = [b for b in bets if b["result"] == "lost"]
    if not losses:
        print("  No losses yet.")
        return

    # At bet placement, score was 0-1 or 1-0 (underdog leads).
    # At full time, check final score relative to who was the favourite.
    # selection = 'home' means home was the favourite (was losing 0-1 at bet time)
    # selection = 'away' means away was the favourite (was losing 1-0 at bet time)

    ended_draw = 0
    ended_underdog_won = 0
    ended_other = 0  # more goals, etc.
    no_final = 0

    for b in losses:
        fh = b.get("final_home")
        fa = b.get("final_away")
        if fh is None or fa is None:
            no_final += 1
            continue
        fh, fa = int(fh), int(fa)
        sel = (b["selection"] or "").lower()
        if fh == fa:
            ended_draw += 1
        elif sel == "home" and fa > fh:
            ended_underdog_won += 1  # home was fav, away (underdog) won
        elif sel == "away" and fh > fa:
            ended_underdog_won += 1  # away was fav, home (underdog) won
        else:
            ended_other += 1  # fav equalised but didn't win (draw counted above) or other

    total_losses = len(losses)
    print(f"  Total losses: {total_losses}")
    print(f"  Ended as draw:          {ended_draw:>4}  ({ended_draw/total_losses*100:.0f}%)")
    print(f"  Underdog held on to win:{ended_underdog_won:>4}  ({ended_underdog_won/total_losses*100:.0f}%)")
    print(f"  Other outcome:          {ended_other:>4}  ({ended_other/total_losses*100:.0f}%)")
    if no_final:
        print(f"  No final score data:    {no_final:>4}")

    # ── Draw bet simulation ──────────────────────────────────────────────────
    # If we had bet draw instead of fav win, how many would have won?
    print("\n  'Draw instead of win' simulation (same bets, different selection):")
    print("  (Would have won on: draw outcomes, same odds proxy = fav_win_odds * 0.55)")
    would_win = ended_draw
    # If we had bet draw on all inplay_c bets (won + lost) — how many total draws?
    all_draws = sum(
        1 for b in bets
        if b.get("final_home") is not None and b.get("final_away") is not None
        and int(b["final_home"]) == int(b["final_away"])
    )
    all_bets_with_score = sum(
        1 for b in bets
        if b.get("final_home") is not None
    )
    if all_bets_with_score:
        print(f"  Games ending in draw (all bets):  {all_draws}/{all_bets_with_score} = {all_draws/all_bets_with_score*100:.0f}%")
        # Rough odds estimate: draw odds are typically ~40-55% of the win odds at this stage
        avg_fav_odds = sum(float(b["odds"]) for b in bets) / len(bets)
        approx_draw_odds = avg_fav_odds * 0.55
        print(f"  Avg fav-win odds: {avg_fav_odds:.2f}  → approx draw odds: {approx_draw_odds:.2f}")
        sim_pnl = all_draws * (approx_draw_odds - 1) * 5 - (all_bets_with_score - all_draws) * 5
        sim_roi = sim_pnl / (all_bets_with_score * 5) * 100
        print(f"  Simulated ROI if bet draw: {sim_roi:+.1f}%   P&L: {sim_pnl:+.2f}€")

    # ── By minute for losses only ─────────────────────────────────────────────
    print("\n  Losses by placement minute (is late-game hurting us?):")
    print(f"  {'Bucket':<10} {'Lost':>5} {'→Draw':>7} {'→UDog':>7}")
    print(f"  {'-'*35}")
    minute_buckets: dict[str, dict] = defaultdict(lambda: {"lost": 0, "draw": 0, "udog": 0})
    for b in losses:
        lbl = bucket_label(b["_minute"])
        minute_buckets[lbl]["lost"] += 1
        fh = b.get("final_home")
        fa = b.get("final_away")
        if fh is not None and fa is not None:
            fh, fa = int(fh), int(fa)
            sel = (b["selection"] or "").lower()
            if fh == fa:
                minute_buckets[lbl]["draw"] += 1
            elif (sel == "home" and fa > fh) or (sel == "away" and fh > fa):
                minute_buckets[lbl]["udog"] += 1

    for label in ["≤30", "31-40", "41-50", "51-60", "61-70", "71+", "unknown"]:
        d = minute_buckets.get(label)
        if not d or d["lost"] == 0:
            continue
        print(f"  {label:<10} {d['lost']:>5} {d['draw']:>7} {d['udog']:>7}")


def _analyse_o_outcomes(bets: list[dict]):
    """For inplay_o (Underdog Hold): break down losses."""
    print("\n  inplay_o loss breakdown:")
    losses = [b for b in bets if b["result"] == "lost"]
    if not losses:
        print("  No losses yet.")
        return

    ended_draw = 0
    ended_fav_comeback = 0
    ended_other = 0

    for b in losses:
        fh = b.get("final_home")
        fa = b.get("final_away")
        if fh is None or fa is None:
            continue
        fh, fa = int(fh), int(fa)
        sel = (b["selection"] or "").lower()
        if fh == fa:
            ended_draw += 1
        elif sel == "home" and fh > fa:
            ended_fav_comeback += 1  # home was underdog leading, away (fav) won
        elif sel == "away" and fa > fh:
            ended_fav_comeback += 1
        else:
            ended_other += 1

    tl = len(losses)
    print(f"  Total losses: {tl}")
    print(f"  Favourite came back to win: {ended_fav_comeback:>3}  ({ended_fav_comeback/tl*100:.0f}%)")
    print(f"  Ended as draw:             {ended_draw:>3}  ({ended_draw/tl*100:.0f}%)")
    print(f"  Other:                     {ended_other:>3}  ({ended_other/tl*100:.0f}%)")


def print_all_bots_summary(all_bets: list[dict]):
    """Print one-line summary per bot sorted by ROI."""
    by_bot: dict[str, list] = defaultdict(list)
    for b in all_bets:
        by_bot[b["bot_name"]].append(b)

    print("\n" + "="*70)
    print("  ALL INPLAY BOTS SUMMARY")
    print("="*70)
    print(f"  {'Bot':<25} {'N':>5} {'Won':>5} {'Hit%':>6} {'ROI%':>7} {'P&L':>8} {'AvgOdds':>9}")
    print(f"  {'-'*65}")

    rows = []
    for bot_name, bets in by_bot.items():
        total = len(bets)
        won = sum(1 for b in bets if b["result"] == "won")
        pnl = sum(float(b["pnl"] or 0) for b in bets)
        staked = sum(float(b["stake"] or 5) for b in bets)
        roi = pnl / staked * 100 if staked else 0
        avg_odds = sum(float(b["odds"] or 0) for b in bets) / total if total else 0
        rows.append((bot_name, total, won, roi, pnl, avg_odds))

    for bot_name, total, won, roi, pnl, avg_odds in sorted(rows, key=lambda x: -x[3]):
        hit = won / total * 100 if total else 0
        print(f"  {bot_name:<25} {total:>5} {won:>5} {hit:>5.0f}% {roi:>+6.1f}% {pnl:>+7.2f}€ {avg_odds:>9.2f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", help="Focus on a specific bot (e.g. inplay_c)")
    args = parser.parse_args()

    print("Loading inplay bets...")
    all_bets = load_bets()
    if not all_bets:
        print("No settled inplay bets found.")
        sys.exit(0)

    print(f"Loaded {len(all_bets)} settled inplay bets")

    print_all_bots_summary(all_bets)

    if args.bot:
        bot_bets = [b for b in all_bets if b["bot_name"] == args.bot]
        analyse_bot(args.bot, bot_bets)
    else:
        by_bot: dict[str, list] = defaultdict(list)
        for b in all_bets:
            by_bot[b["bot_name"]].append(b)
        for bot_name in sorted(by_bot.keys()):
            analyse_bot(bot_name, by_bot[bot_name])


if __name__ == "__main__":
    main()
