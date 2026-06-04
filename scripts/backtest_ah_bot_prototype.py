"""AH-BOT-PROTOTYPE backtest — can our ensemble 1X2 prediction find positive
expected value in Pinnacle's Asian Handicap closing market?

Universe: matches since 2024-01-01 with all four:
  • `predictions` row, source='ensemble' (model 1X2)
  • Pinnacle 1X2 closing rows (control implied)
  • Pinnacle Asian Handicap closing rows, handicap_line ∈ {-0.5, -0.25, 0, +0.25, +0.5}
    (the subset of AH lines that can be derived cleanly from 1X2 alone —
    half/quarter lines straddling 0)
  • finished match with score

For each match we compute our "fair" home-AH probability from the 1X2 probs,
compare to Pinnacle's devig'd AH implied, take whichever side has edge >
threshold, and settle against the actual score.

Settlement rules (AH):
  margin = home_score − away_score + handicap_line
  margin > +0.25 → home covers full
  margin ∈ (-0.25, +0.25) → push (returns stake at integer 0; half push/loss
    at quarter)
  margin < -0.25 → away covers full

For our line set:
  -0.5: clean (no push, no half) — bet wins outright on home-by-1+, loses on draw/away
  +0.5: clean — wins on home OR draw, loses on away
  0.0:  draw = push (stake returned); home win = win, away win = lose
  -0.25: HALF the stake at -0.5, HALF at 0. So home-by-1+ wins both halves;
         draw → -0.5 half loses + 0 half pushes (net half loss); away → both lose.
  +0.25: HALF at +0.5, HALF at 0. Home-by-1+ wins both; draw → +0.5 half wins +
         0 half pushes (net half win); away → both lose.

Output: edge threshold sweep with n, ROI, win rate, mean edge.

Run:
  python3 scripts/backtest_ah_bot_prototype.py
  python3 scripts/backtest_ah_bot_prototype.py --since 2024-01-01
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402


# Lines we can derive from 1X2 alone (half + quarter straddling 0)
DERIVABLE_LINES = {-0.5, -0.25, 0.0, 0.25, 0.5}


def model_home_ah_prob(p_h: float, p_d: float, p_a: float, line: float) -> float:
    """Our model's probability that the home side covers the AH at `line`."""
    # Re-normalise just in case the source isn't perfectly summing to 1
    s = p_h + p_d + p_a
    if s <= 0:
        return 0.0
    p_h, p_d, p_a = p_h / s, p_d / s, p_a / s

    if line == -0.5:
        return p_h
    if line == 0.5:
        return p_h + p_d
    if line == 0.0:
        # Draw = push; effectively a "draw no bet" world
        denom = p_h + p_a
        return p_h / denom if denom > 0 else 0.5
    if line == -0.25:
        # half at -0.5, half at 0.0 — return the EV-weighted probability
        # treating push as 0.5 (since half-stake at 0 returns stake)
        dnb = p_h / (p_h + p_a) if (p_h + p_a) > 0 else 0.5
        return 0.5 * p_h + 0.5 * dnb
    if line == 0.25:
        # half at +0.5, half at 0.0
        dnb = p_h / (p_h + p_a) if (p_h + p_a) > 0 else 0.5
        return 0.5 * (p_h + p_d) + 0.5 * dnb
    raise ValueError(f"Line {line} not derivable from 1X2 — use a goals model")


def settle_home_ah(score_home: int, score_away: int, line: float) -> float:
    """Return P&L per 1 unit stake on the home side at line `line`.
    +odds_minus_one on win, -1 on loss, 0 on push, ±0.5 on half-win/half-loss.
    The actual odds multiplier is applied by the caller — this returns
    {+1, +0.5, 0, -0.5, -1} (a unit-stake outcome flag, NOT the P&L).
    """
    margin = (score_home - score_away) + line
    if line in (-0.5, 0.5):
        return 1.0 if margin > 0 else -1.0
    if line == 0.0:
        if margin > 0: return 1.0
        if margin < 0: return -1.0
        return 0.0  # push
    if line == -0.25:
        # half at -0.5 (margin > 0 means home wins by 1+ at -0.5) → wins
        # half at 0 (margin > 0 means home win → wins; draw → push)
        half_05 = 1.0 if (score_home - score_away) >= 1 else -1.0
        half_00 = (1.0 if score_home > score_away else (-1.0 if score_home < score_away else 0.0))
        return 0.5 * half_05 + 0.5 * half_00
    if line == 0.25:
        # half at +0.5 (home wins OR draws), half at 0 (home win = win, draw = push)
        half_p05 = 1.0 if score_home >= score_away else -1.0
        half_00 = (1.0 if score_home > score_away else (-1.0 if score_home < score_away else 0.0))
        return 0.5 * half_p05 + 0.5 * half_00
    raise ValueError(f"Unsupported line {line}")


def pnl_for_bet(side: str, odds: float, outcome_flag: float) -> float:
    """Convert unit-stake outcome flag {+1,+0.5,0,-0.5,-1} into actual P&L on
    a 1-unit stake at decimal `odds`.

    + flag means our side won the corresponding fraction; -flag means lost.
    Even on half-push/half-loss situations, the loss half stakes the unit.
    """
    # For + flag: profit = flag × (odds − 1)
    # For - flag: loss   = flag × 1 (i.e. flag is negative)
    if outcome_flag > 0:
        return outcome_flag * (odds - 1.0)
    return outcome_flag  # already negative for losses, 0 for push


def load_universe(since: str):
    lines_csv = ",".join(str(x) for x in sorted(DERIVABLE_LINES))
    rows = execute_query(
        f"""
        WITH ah_pair AS (
          SELECT h.match_id, h.handicap_line,
                 h.odds AS ah_home_odds, a.odds AS ah_away_odds
          FROM odds_snapshots h
          JOIN odds_snapshots a ON a.match_id = h.match_id
            AND a.bookmaker = h.bookmaker AND a.market = h.market
            AND a.handicap_line = h.handicap_line
            AND a.is_closing = h.is_closing
            AND a.selection = 'away'
          WHERE h.bookmaker='Pinnacle' AND h.market='asian_handicap'
            AND h.is_closing=true AND h.selection='home'
            AND h.handicap_line IN ({lines_csv})
        ),
        pred_pivot AS (
          SELECT match_id,
                 MAX(CASE WHEN market='1x2_home' THEN model_probability END) AS p_h,
                 MAX(CASE WHEN market='1x2_draw' THEN model_probability END) AS p_d,
                 MAX(CASE WHEN market='1x2_away' THEN model_probability END) AS p_a
          FROM predictions
          WHERE source='ensemble' AND market IN ('1x2_home','1x2_draw','1x2_away')
          GROUP BY match_id
        )
        SELECT m.id::text AS mid, m.score_home, m.score_away,
               ap.handicap_line, ap.ah_home_odds, ap.ah_away_odds,
               pp.p_h, pp.p_d, pp.p_a
        FROM ah_pair ap
        JOIN matches m ON m.id = ap.match_id
        JOIN pred_pivot pp ON pp.match_id = ap.match_id
        WHERE m.status = 'finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
          AND m.date >= %s::timestamptz
          AND pp.p_h IS NOT NULL AND pp.p_d IS NOT NULL AND pp.p_a IS NOT NULL
        """,
        [since],
    )
    out = []
    for r in rows:
        try:
            line = float(r["handicap_line"])
            p_h, p_d, p_a = float(r["p_h"]), float(r["p_d"]), float(r["p_a"])
            out.append({
                "mid": r["mid"],
                "line": line,
                "ah_home_odds": float(r["ah_home_odds"]),
                "ah_away_odds": float(r["ah_away_odds"]),
                "p_h": p_h, "p_d": p_d, "p_a": p_a,
                "sh": int(r["score_home"]), "sa": int(r["score_away"]),
            })
        except (TypeError, ValueError):
            continue
    return out


def implied_devig_two_way(odds_a: float, odds_b: float) -> tuple[float, float]:
    ia, ib = 1.0 / odds_a, 1.0 / odds_b
    s = ia + ib
    return ia / s, ib / s


def run_backtest(universe: list[dict], threshold: float) -> dict:
    home_pnl = 0.0; away_pnl = 0.0
    n_home = 0; n_away = 0
    won_units = 0.0; lost_units = 0.0
    push_units = 0.0
    for row in universe:
        # Our fair home AH probability
        fair_home = model_home_ah_prob(row["p_h"], row["p_d"], row["p_a"], row["line"])
        fair_away = 1.0 - fair_home

        # Pinnacle's devig'd implied
        imp_home, imp_away = implied_devig_two_way(row["ah_home_odds"], row["ah_away_odds"])

        edge_home = fair_home - imp_home
        edge_away = fair_away - imp_away

        # Take the highest-edge side that exceeds threshold
        if edge_home > threshold and edge_home >= edge_away:
            flag = settle_home_ah(row["sh"], row["sa"], row["line"])
            home_pnl += pnl_for_bet("home", row["ah_home_odds"], flag)
            n_home += 1
        elif edge_away > threshold:
            # Settle home flag and flip
            flag = -settle_home_ah(row["sh"], row["sa"], row["line"])
            away_pnl += pnl_for_bet("away", row["ah_away_odds"], flag)
            n_away += 1

    total_bets = n_home + n_away
    total_pnl = home_pnl + away_pnl
    roi = total_pnl / total_bets if total_bets else None
    return {
        "threshold": threshold,
        "n_total": total_bets,
        "n_home": n_home,
        "n_away": n_away,
        "pnl": total_pnl,
        "roi": roi,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--out", default="dev/active/ah-bot-prototype-results.md")
    args = ap.parse_args()

    print(f"AH-BOT-PROTOTYPE backtest — since={args.since}")
    universe = load_universe(args.since)
    print(f"  Universe: {len(universe):,} matches with derivable AH lines + ensemble pred\n")

    sweep = [-0.05, -0.02, 0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15]
    results = [run_backtest(universe, t) for t in sweep]

    print(f"  {'threshold':>10s}  {'n_total':>8s}  {'n_home':>7s}  {'n_away':>7s}  {'PnL':>10s}  {'ROI':>8s}")
    for r in results:
        roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "—"
        print(f"  {r['threshold']:>+10.3f}  {r['n_total']:>8,}  {r['n_home']:>7,}  {r['n_away']:>7,}  {r['pnl']:>+10.2f}  {roi:>8s}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        f.write("# AH-BOT-PROTOTYPE — Backtest Results\n\n")
        f.write(f"Universe: **{len(universe):,} matches** with paired Pinnacle AH closing "
                f"+ ensemble 1X2 prediction, since {args.since}. "
                f"Lines restricted to {{-0.5, -0.25, 0, +0.25, +0.5}} — the subset derivable "
                f"from 1X2 probabilities alone (half + quarter straddling 0).\n\n")
        f.write("## Edge threshold sweep\n\n")
        f.write("| Edge threshold | N bets | N home | N away | P&L (units) | ROI |\n")
        f.write("|---:|---:|---:|---:|---:|---:|\n")
        for r in results:
            roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "—"
            f.write(f"| {r['threshold']:+.3f} | {r['n_total']:,} | {r['n_home']:,} | "
                    f"{r['n_away']:,} | {r['pnl']:+.2f} | {roi} |\n")
        f.write("\n## Interpretation\n\n")
        best = max((r for r in results if r["n_total"] >= 200 and r["roi"] is not None),
                   key=lambda r: r["roi"], default=None)
        if best:
            f.write(f"Best threshold with ≥200 bets: **{best['threshold']:+.3f}** → "
                    f"{best['n_total']:,} bets, ROI **{best['roi']*100:+.2f}%**.\n\n")
        f.write("Compare to vig-bound baseline of roughly **−2 to −3% ROI** at zero edge "
                "(Pinnacle AH margin). An AH bot is only viable if some threshold sustains "
                "ROI clearly above zero with reasonable bet volume.\n")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
