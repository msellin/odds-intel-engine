"""Backtest what bot_ah_away_dog + bot_ah_home_fav would have placed during their
silent period (2026-05-12 → 2026-05-24), using TODAY's logic.

Mirrors the live pipeline's AH path exactly:
  - AH model prob from _solve_lambdas_calibrated (post AH-HOME-BIAS 2026-05-21)
  - AH-CAL-BYPASS (2026-05-24): no stage-1 shrinkage on AH cal_prob
  - AH-VETO-WIDEN (2026-05-24): 0.22 PIN-VETO-EXT gap for AH
  - AH-NO-QUARTER: skip ±0.25 / ±0.75 lines
  - Bot filters: tier_filter [1,2,3], odds_range (1.70-2.50), min_prob 0.50,
    edge_threshold 0.05 (T1/T2) / 0.06 (T3)
  - Selection filter: "Away" for bot_ah_away_dog, "Home" for bot_ah_home_fav

Skipped (out of scope — same as backtest_pre_match_bots.py honesty caveat):
  - ALN-1 alignment bump (dimension scores not reconstructable cleanly)
  - Kelly stake sizing → flat €10 stake
  - Exposure cap
  - Odds-movement veto

Output: dev/active/backtest-ah-silent-period.csv + console summary.
No production-table writes.

Run in a separate terminal for live progress (uses rich.progress).
"""
from __future__ import annotations
import os, sys, csv
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

# Use the LIVE pipeline's actual helpers (post-fix)
from workers.jobs.daily_pipeline_v2 import (
    _solve_lambdas_calibrated, _ah_model_prob, _load_dc_rho_cache,
)

console = Console()
PINNACLE_VETO_GAP_AH = 0.22  # AH-VETO-WIDEN

BOTS = {
    "bot_ah_away_dog": {
        "selection": "away",
        "tier_thresholds": {1: 0.05, 2: 0.05, 3: 0.06},
    },
    "bot_ah_home_fav": {
        "selection": "home",
        "tier_thresholds": {1: 0.05, 2: 0.05, 3: 0.06},
    },
}
ODDS_MIN, ODDS_MAX = 1.70, 2.50
MIN_PROB = 0.50
HALF_LINES = {-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5}
FLAT_STAKE = 10.0

WINDOW_START = "2026-05-12"
WINDOW_END = "2026-05-24"

conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.cursor().execute("SET statement_timeout='300s'")
dict_cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

console.print(f"[bold]Backtest window:[/bold] {WINDOW_START} → {WINDOW_END}")

# Single bulk fetch: settled matches with both 1X2 ensemble preds + AH snapshots + closing line
console.print("Loading settled matches with 1X2 preds + AH odds...")
dict_cur.execute("""
    WITH preds AS (
        SELECT match_id,
               MAX(model_probability) FILTER (WHERE market='1x2_home' AND source='ensemble') AS p_home,
               MAX(model_probability) FILTER (WHERE market='1x2_draw' AND source='ensemble') AS p_draw,
               MAX(model_probability) FILTER (WHERE market='1x2_away' AND source='ensemble') AS p_away
        FROM predictions
        WHERE created_at >= %s AND created_at <= %s::date + INTERVAL '1 day'
        GROUP BY match_id
    )
    SELECT m.id AS match_id, m.date::date AS day, m.score_home, m.score_away,
           m.league_id, l.tier, l.name AS league_name, l.country,
           p.p_home, p.p_draw, p.p_away
    FROM matches m
    JOIN preds p ON p.match_id = m.id
    LEFT JOIN leagues l ON l.id = m.league_id
    WHERE m.status='finished'
      AND m.date::date >= %s AND m.date::date <= %s
      AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
      AND p.p_home IS NOT NULL AND p.p_draw IS NOT NULL
      AND l.tier IN (1,2,3)
""", (WINDOW_START, WINDOW_END, WINDOW_START, WINDOW_END))
matches = dict_cur.fetchall()
console.print(f"  {len(matches):,} settled matches with both preds + tier 1-3")

if not matches:
    console.print("[red]No matches found — exiting.[/red]"); sys.exit(0)

match_ids = [str(m["match_id"]) for m in matches]
console.print("Loading AH best-book odds (full/half lines only)...")
dict_cur.execute("""
    SELECT match_id, selection, handicap_line, MAX(odds) AS best_odds,
           MAX(is_closing::int) AS has_closing
    FROM odds_snapshots
    WHERE match_id = ANY(%s::uuid[])
      AND market='asian_handicap'
      AND handicap_line IN (-1.5,-1.0,-0.5,0.0,0.5,1.0,1.5)
      -- AF-ISLIVE-CALLSITE-FIXES-2026-09-05 / gotcha 37: pre-match bound.
      -- MAX(odds) over post-KO rows fabricates edge (p99 +4.10pp).
      AND minutes_to_kickoff > 0
    GROUP BY 1,2,3
""", (match_ids,))
odds_rows = dict_cur.fetchall()

# Closing odds for CLV
dict_cur.execute("""
    SELECT match_id, selection, handicap_line, odds AS closing_odds
    FROM odds_snapshots
    WHERE match_id = ANY(%s::uuid[])
      AND market='asian_handicap'
      AND is_closing = TRUE
""", (match_ids,))
closing_rows = dict_cur.fetchall()
closing_map = {(str(r["match_id"]), r["selection"], float(r["handicap_line"])): float(r["closing_odds"])
               for r in closing_rows}

# Index odds by match
odds_by_match: dict[str, list] = {}
for r in odds_rows:
    odds_by_match.setdefault(str(r["match_id"]), []).append(r)

console.print(f"  {len(odds_rows):,} AH best-book rows · {len(closing_map):,} closing lines\n")


def _ah_settle(selection: str, handicap_line: float, sh: int, sa: int) -> tuple[str, float]:
    """Settle a full-or-half AH bet. Returns (result, multiplier-on-stake).
    Multiplier: win = (odds-1); lose = -1; push = 0; halves handled separately.
    """
    if selection == "home":
        margin = sh - sa + handicap_line
    else:
        margin = sa - sh + handicap_line
    if abs(margin % 1.0) < 1e-9:  # whole-number AH line (.0)
        if margin > 0: return "won", 1.0
        if margin < 0: return "lost", -1.0
        return "void", 0.0
    # half line (.5) — pure win or loss, no push
    if margin > 0: return "won", 1.0
    return "lost", -1.0


def _result_pnl(result: str, multiplier: float, odds: float, stake: float) -> float:
    if result == "won":  return stake * (odds - 1) * multiplier
    if result == "lost": return stake * multiplier  # multiplier is negative
    return 0.0


tier_rho_cache = _load_dc_rho_cache()
out_csv = Path(__file__).resolve().parent.parent / "dev" / "active" / "backtest-ah-silent-period.csv"
out_csv.parent.mkdir(parents=True, exist_ok=True)

rows_out = []
bot_summary = {b: {"considered": 0, "placed": 0, "won": 0, "lost": 0, "void": 0,
                    "pnl": 0.0, "stake": 0.0, "clv_sum": 0.0, "clv_n": 0}
               for b in BOTS}

with Progress(TextColumn("[progress.description]{task.description}"),
              BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
              TimeRemainingColumn(), console=console) as progress:
    task = progress.add_task("Backtesting matches", total=len(matches))
    for m in matches:
        mid = str(m["match_id"])
        p_home, p_draw = float(m["p_home"]), float(m["p_draw"])
        if p_home <= 0 or p_draw <= 0:
            progress.advance(task); continue
        cal = _solve_lambdas_calibrated(p_home, p_draw)
        if not cal:
            progress.advance(task); continue
        exp_h, exp_a = cal
        rho = tier_rho_cache.get(m.get("tier", 1))
        sh, sa = int(m["score_home"]), int(m["score_away"])

        for o in odds_by_match.get(mid, []):
            sel = o["selection"]
            hl = float(o["handicap_line"])
            if hl not in HALF_LINES:  # AH-NO-QUARTER
                continue
            odds = float(o["best_odds"])
            if not (ODDS_MIN <= odds <= ODDS_MAX):
                continue
            # AH-VETO-WIDEN guard against absurd lines later

            mp = _ah_model_prob(exp_h, exp_a, sel, hl, rho=rho)
            if mp < MIN_PROB:
                continue
            ip = 1.0 / odds
            edge = mp - ip  # AH-CAL-BYPASS: cal_prob == mp
            # AH-VETO-EXT gap
            if (mp - ip) > PINNACLE_VETO_GAP_AH:
                continue

            # Per-bot
            for bot_name, cfg in BOTS.items():
                if sel != cfg["selection"]:
                    continue
                tier = m.get("tier") or 1
                if tier not in cfg["tier_thresholds"]:
                    continue
                me = cfg["tier_thresholds"][tier]
                bot_summary[bot_name]["considered"] += 1
                if edge < me:
                    continue

                # Settle
                result, mult = _ah_settle(sel, hl, sh, sa)
                pnl = _result_pnl(result, mult, odds, FLAT_STAKE)
                bot_summary[bot_name]["placed"] += 1
                bot_summary[bot_name]["stake"] += FLAT_STAKE
                if result == "won":  bot_summary[bot_name]["won"] += 1
                elif result == "lost": bot_summary[bot_name]["lost"] += 1
                else: bot_summary[bot_name]["void"] += 1
                bot_summary[bot_name]["pnl"] += pnl

                clv_val = None
                co = closing_map.get((mid, sel, hl))
                if co and co > 0:
                    clv_val = odds / co - 1.0
                    bot_summary[bot_name]["clv_sum"] += clv_val
                    bot_summary[bot_name]["clv_n"] += 1

                rows_out.append({
                    "bot": bot_name, "day": m["day"], "match_id": mid,
                    "league": m["league_name"], "country": m["country"], "tier": tier,
                    "score": f"{sh}-{sa}",
                    "selection": sel, "handicap_line": hl, "odds_at_pick": odds,
                    "closing_odds": co, "model_prob": round(mp, 4),
                    "edge_pct": round(edge, 4), "result": result, "pnl": pnl,
                    "clv_pct": round(clv_val, 4) if clv_val is not None else None,
                })
        progress.advance(task)

# Write CSV
with out_csv.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else
                            ["bot","day","match_id","league","country","tier","score",
                             "selection","handicap_line","odds_at_pick","closing_odds",
                             "model_prob","edge_pct","result","pnl","clv_pct"])
    writer.writeheader()
    writer.writerows(rows_out)
console.print(f"\n[green]Wrote {len(rows_out):,} backtest rows to[/green] {out_csv}\n")

console.print("[bold]Summary[/bold]")
print(f"  {'bot':<22}{'considered':>11}{'placed':>8}{'W/L/V':>10}{'ROI%':>8}{'avg_CLV%':>10}")
print("  " + "─"*78)
for bot, s in bot_summary.items():
    roi = 100*s["pnl"]/s["stake"] if s["stake"] > 0 else 0
    avg_clv = 100*s["clv_sum"]/s["clv_n"] if s["clv_n"] > 0 else None
    clv_s = f"{avg_clv:+.2f}" if avg_clv is not None else "—"
    print(f"  {bot:<22}{s['considered']:>11}{s['placed']:>8}"
          f"  {s['won']}/{s['lost']}/{s['void']:<5}{roi:>+7.1f}{clv_s:>10}")

conn.close()
