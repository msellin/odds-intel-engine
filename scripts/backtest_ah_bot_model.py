"""AH-BOT-MODEL backtest — uses the production Poisson + Dixon-Coles model
(via daily_pipeline_v2._ah_model_prob) instead of the naive 1X2-derivation
that AH-BOT-PROTOTYPE used. Goal: find any edge threshold where ROI is
consistently positive on the full 8,868-match Pinnacle AH closing universe.

Pipeline:
  1. Pull all matches since 2024-01-01 with paired Pinnacle AH closing
     (home + away) + ensemble 1X2 prediction + finished score.
  2. Use _solve_lambdas_calibrated(p_home, p_draw) to back out (exp_h, exp_a).
  3. Call _ah_model_prob(exp_h, exp_a, selection, handicap_line) to get our
     fair home/away probability at the actual handicap line.
  4. Compare to Pinnacle's devig'd AH implied → edge per side.
  5. Take the side with edge > threshold. Settle against actual score.

Handles all line types via _ah_model_prob: whole, half, quarter (x.25, x.75).

Run:
  python3 scripts/backtest_ah_bot_model.py
  python3 scripts/backtest_ah_bot_model.py --since 2024-01-01
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.jobs.daily_pipeline_v2 import (  # noqa: E402
    _ah_model_prob,
    _solve_lambdas_calibrated,
)


def _settle_ah_outcome(score_home: int, score_away: int,
                       handicap_line: float, selection: str) -> float:
    """Return outcome flag for a 1-unit AH bet:
       +1  full win, +0.5 half-win, 0 push, −0.5 half-loss, −1 full loss.
    Mirrors the math in workers/jobs/settlement_pipeline.py — supports
    whole / half / quarter (x.25 + x.75) lines.
    """
    sign = 1 if selection == "home" else -1
    # margin from the bettor's perspective
    margin = sign * (score_home - score_away) + handicap_line if selection == "home" \
        else sign * (score_home - score_away) - handicap_line
    # For away with line=-0.75 (i.e. home gets -0.75 → away gets +0.75):
    # margin_away = -(home - away) + 0.75 from away view
    if selection == "away":
        margin = -(score_home - score_away) - handicap_line

    floor_m = math.floor(margin)
    frac = margin - floor_m

    if abs(frac) < 0.01 or abs(frac - 1.0) < 0.01:
        # whole-number margin → push at 0
        if margin > 0.001: return 1.0
        if margin < -0.001: return -1.0
        return 0.0
    if abs(frac - 0.5) < 0.01:
        # half line — clean win/loss
        return 1.0 if margin > 0 else -1.0
    if abs(frac - 0.25) < 0.01:
        # x.25 line — half-loss when margin == floor_m
        if margin > 0.5: return 1.0   # full win
        if margin < -0.5: return -1.0  # full loss
        return -0.5                    # half-loss at integer margin below
    if abs(frac - 0.75) < 0.01:
        # x.75 line — half-win when margin == floor_m + 1 (i.e. -0.25 < margin < 0.25 + 1)
        if margin > 0.5: return 1.0
        if margin < -0.5: return -1.0
        return 0.5
    # Unknown fractional — treat as half-line
    return 1.0 if margin > 0 else -1.0


def _pnl(outcome_flag: float, odds: float) -> float:
    if outcome_flag > 0:
        return outcome_flag * (odds - 1.0)
    return outcome_flag


def load_universe(since: str):
    rows = execute_query(
        """
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
            AND h.handicap_line IS NOT NULL
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
        WHERE m.status='finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
          AND m.date >= %s::timestamptz
          AND pp.p_h IS NOT NULL AND pp.p_d IS NOT NULL AND pp.p_a IS NOT NULL
        """,
        [since],
    )
    out = []
    for r in rows:
        try:
            out.append({
                "line": float(r["handicap_line"]),
                "oh": float(r["ah_home_odds"]),
                "oa": float(r["ah_away_odds"]),
                "p_h": float(r["p_h"]),
                "p_d": float(r["p_d"]),
                "p_a": float(r["p_a"]),
                "sh": int(r["score_home"]),
                "sa": int(r["score_away"]),
            })
        except (TypeError, ValueError):
            continue
    return out


def implied_devig_two_way(odds_a: float, odds_b: float) -> tuple[float, float]:
    ia, ib = 1.0 / odds_a, 1.0 / odds_b
    s = ia + ib
    return ia / s, ib / s


def precompute_edges(universe: list[dict]) -> tuple[list[dict], int]:
    """Per-match: derive lambdas, compute fair AH probs, devig Pinnacle implied,
    compute home & away edges, and settle the outcome. Result is a flat list
    of decision-ready rows that any threshold sweep can iterate in memory
    without re-running scipy.minimize or _ah_model_prob.
    """
    decisions = []
    n_skipped = 0
    for i, row in enumerate(universe):
        lambdas = _solve_lambdas_calibrated(row["p_h"], row["p_d"])
        if lambdas is None:
            n_skipped += 1
            continue
        exp_h, exp_a = lambdas
        fair_home = _ah_model_prob(exp_h, exp_a, "home", row["line"])
        fair_away = 1.0 - fair_home
        imp_home, imp_away = implied_devig_two_way(row["oh"], row["oa"])
        flag_home = _settle_ah_outcome(row["sh"], row["sa"], row["line"], "home")
        flag_away = _settle_ah_outcome(row["sh"], row["sa"], row["line"], "away")
        decisions.append({
            "line": row["line"],
            "edge_home": fair_home - imp_home,
            "edge_away": fair_away - imp_away,
            "pnl_home": _pnl(flag_home, row["oh"]),
            "pnl_away": _pnl(flag_away, row["oa"]),
        })
        if (i + 1) % 1000 == 0:
            print(f"  precomputed {i+1:,}/{len(universe):,}", flush=True)
    return decisions, n_skipped


def run_threshold_sweep(decisions: list[dict], threshold: float) -> dict:
    home_pnl = 0.0; away_pnl = 0.0
    n_home = 0; n_away = 0
    for d in decisions:
        if d["edge_home"] > threshold and d["edge_home"] >= d["edge_away"]:
            home_pnl += d["pnl_home"]
            n_home += 1
        elif d["edge_away"] > threshold:
            away_pnl += d["pnl_away"]
            n_away += 1
    total = n_home + n_away
    return {
        "threshold": threshold,
        "n_total": total,
        "n_home": n_home,
        "n_away": n_away,
        "pnl": home_pnl + away_pnl,
        "roi": (home_pnl + away_pnl) / total if total else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--out", default="dev/active/ah-bot-model-results.md")
    args = ap.parse_args()

    print(f"AH-BOT-MODEL backtest — since={args.since}")
    universe = load_universe(args.since)
    print(f"  Universe: {len(universe):,} matches with full AH coverage + ensemble pred\n")

    print("  Precomputing per-match Poisson + Dixon-Coles + edges...", flush=True)
    decisions, n_skipped = precompute_edges(universe)
    print(f"  Decisions ready: {len(decisions):,} matches, {n_skipped:,} skipped (lambda solver fail)\n", flush=True)

    sweep = [-0.02, 0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15]
    results = [run_threshold_sweep(decisions, t) for t in sweep]

    # Save first, print after — never lose the work to a format bug
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        f.write("# AH-BOT-MODEL — Backtest Results\n\n")
        f.write(f"Universe: **{len(universe):,} matches** with paired Pinnacle AH closing + "
                f"ensemble 1X2 prediction, since {args.since}. ALL handicap lines accepted "
                f"(whole / half / quarter) — `_ah_model_prob()` handles each push-correctly.\n\n")
        f.write("Model path: `ensemble (p_h, p_d, p_a)` → `_solve_lambdas_calibrated()` → "
                "`(exp_h, exp_a)` → `_ah_model_prob(exp_h, exp_a, selection, line)`. "
                "This is the production Poisson + Dixon-Coles AH function from "
                "`workers/jobs/daily_pipeline_v2.py:1158`.\n\n")
        f.write("## Edge threshold sweep\n\n")
        f.write("| Edge threshold | N bets | N home | N away | P&L (units) | ROI |\n")
        f.write("|---:|---:|---:|---:|---:|---:|\n")
        for r in results:
            roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "—"
            f.write(f"| {r['threshold']:+.3f} | {r['n_total']:,} | {r['n_home']:,} | "
                    f"{r['n_away']:,} | {r['pnl']:+.2f} | {roi} |\n")
        f.write(f"\nSkipped {n_skipped:,} matches (lambda solver failed).\n\n")
        viable = [r for r in results if r["n_total"] >= 200 and r["roi"] is not None]
        best = max(viable, key=lambda r: r["roi"], default=None)
        if best and best["roi"] > 0:
            f.write(f"## ✅ Potentially viable\n\n")
            f.write(f"Best threshold with ≥200 bets: **{best['threshold']:+.3f}** → "
                    f"{best['n_total']:,} bets, ROI **{best['roi']*100:+.2f}%**. "
                    f"Worth a real-money paper-trade trial before configuring as a live bot.\n")
        elif best:
            f.write(f"## ❌ No positive-ROI threshold\n\n")
            f.write(f"Best threshold with ≥200 bets: **{best['threshold']:+.3f}** → "
                    f"{best['n_total']:,} bets, ROI **{best['roi']*100:+.2f}%** "
                    f"(negative). Production AH function + reverse-derived expected goals "
                    f"does not beat Pinnacle's AH closing. Pinnacle AH is sharp enough that "
                    f"this model finds no edge.\n\n")
            f.write("Implications: an AH bot would need a *different* model — one that adds "
                    "signals Pinnacle isn't pricing (line movement, lineup news, drift, "
                    "weather, referee). Or a completely separate goals model trained on AH "
                    "specifically rather than re-derived from 1X2.\n")
    print(f"Wrote {out_path}", flush=True)

    # Now print to console
    print()
    header = f"  {'thresh':>9s}  {'n_total':>8s}  {'n_home':>7s}  {'n_away':>7s}  {'PnL':>10s}  {'ROI':>8s}"
    print(header)
    for r in results:
        roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "—"
        print(f"  {r['threshold']:>+9.3f}  {r['n_total']:>8,}  {r['n_home']:>7,}  {r['n_away']:>7,}  {r['pnl']:>+10.2f}  {roi:>8s}")
    print(f"\n  (Skipped {n_skipped:,} matches where lambda solver failed)")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        f.write("# AH-BOT-MODEL — Backtest Results\n\n")
        f.write(f"Universe: **{len(universe):,} matches** with paired Pinnacle AH closing + "
                f"ensemble 1X2 prediction, since {args.since}. ALL handicap lines accepted "
                f"(whole / half / quarter) — `_ah_model_prob()` handles each push-correctly.\n\n")
        f.write("Model path: `ensemble (p_h, p_d, p_a)` → `_solve_lambdas_calibrated()` → "
                "`(exp_h, exp_a)` → `_ah_model_prob(exp_h, exp_a, selection, line)`. "
                "This is the production Poisson + Dixon-Coles AH function from "
                "`workers/jobs/daily_pipeline_v2.py:1158`.\n\n")
        f.write("## Edge threshold sweep\n\n")
        f.write("| Edge threshold | N bets | N home | N away | P&L (units) | ROI |\n")
        f.write("|---:|---:|---:|---:|---:|---:|\n")
        for r in results:
            roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "—"
            f.write(f"| {r['threshold']:+.3f} | {r['n_total']:,} | {r['n_home']:,} | "
                    f"{r['n_away']:,} | {r['pnl']:+.2f} | {roi} |\n")
        f.write(f"\nSkipped {n_skipped:,} matches (lambda solver failed).\n\n")
        # Pick best
        viable = [r for r in results if r["n_total"] >= 200 and r["roi"] is not None]
        best = max(viable, key=lambda r: r["roi"], default=None)
        if best and best["roi"] > 0:
            f.write(f"## ✅ Potentially viable\n\n")
            f.write(f"Best threshold with ≥200 bets: **{best['threshold']:+.3f}** → "
                    f"{best['n_total']:,} bets, ROI **{best['roi']*100:+.2f}%**. "
                    f"Worth a real-money paper-trade trial before configuring as a live bot.\n")
        elif best:
            f.write(f"## ❌ No positive-ROI threshold\n\n")
            f.write(f"Best threshold with ≥200 bets: **{best['threshold']:+.3f}** → "
                    f"{best['n_total']:,} bets, ROI **{best['roi']*100:+.2f}%** "
                    f"(negative). Production AH function + reverse-derived expected goals "
                    f"does not beat Pinnacle's AH closing. Pinnacle AH is sharp enough that "
                    f"this model finds no edge.\n\n")
            f.write("Implications: an AH bot would need a *different* model — one that adds "
                    "signals Pinnacle isn't pricing (line movement, lineup news, drift, "
                    "weather, referee). Or a completely separate goals model trained on AH "
                    "specifically rather than re-derived from 1X2.\n")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
