"""Backtest the LOW-CONFIDENCE AH-away zone (calibrated_prob 0.30-0.45).

AH-AWAY-MODEL-AUDIT slice-5 found:
  pred_bucket   actual    Δ
  30-40%        31.7%     well-calibrated (Δ -1.0pp)
  40-50%        36.0%     OVER by 8.9pp
  50-60%        37.1%     OVER by 17.1pp
  >=60%         47.8%     OVER by 18.1pp

So 1X2_away Platt is well-calibrated at 30-40% predicted, over-predicts above 40%.
The existing AH-away bot's min_prob=0.50 lands it in the WORST-calibrated zone.

This script: re-run the silent-period AH backtest but with min_prob=0.30, then
slice the output to (cal_prob in [0.30, 0.45], selection=away) — testing if
betting AH-away in the well-calibrated zone is +EV.

Same as backtest_ah_silent_period.py but with min_prob 0.50 → 0.30. No other
changes. Single bulk queries, no per-row inserts. Rich progress bar.
"""
from __future__ import annotations
import os, sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

from workers.jobs.daily_pipeline_v2 import (
    _solve_lambdas_calibrated, _ah_model_prob, _load_dc_rho_cache,
)

console = Console()
PINNACLE_VETO_GAP_AH = 0.22  # AH-VETO-WIDEN — applies identically here
ODDS_MIN, ODDS_MAX = 1.70, 2.50
MIN_PROB_LOW = 0.30   # ← KEY CHANGE — was 0.50
HALF_LINES = {-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5}
FLAT_STAKE = 10.0
WINDOW_START = "2026-05-12"
WINDOW_END = "2026-05-24"

conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.cursor().execute("SET statement_timeout='300s'")
dict_cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

console.print(f"[bold]Low-conf backtest:[/bold] {WINDOW_START} → {WINDOW_END}  (min_prob >= {MIN_PROB_LOW})")

dict_cur.execute("""
    WITH preds AS (
        SELECT match_id,
               MAX(model_probability) FILTER (WHERE market='1x2_home' AND source='ensemble') AS p_home,
               MAX(model_probability) FILTER (WHERE market='1x2_draw' AND source='ensemble') AS p_draw
        FROM predictions
        WHERE created_at >= %s AND created_at <= %s::date + INTERVAL '1 day'
        GROUP BY match_id
    )
    SELECT m.id AS match_id, m.date::date AS day, m.score_home, m.score_away,
           l.tier, p.p_home, p.p_draw
    FROM matches m
    JOIN preds p ON p.match_id = m.id
    LEFT JOIN leagues l ON l.id = m.league_id
    WHERE m.status='finished' AND m.date::date >= %s AND m.date::date <= %s
      AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
      AND p.p_home IS NOT NULL AND p.p_draw IS NOT NULL
      AND l.tier IN (1,2,3)
""", (WINDOW_START, WINDOW_END, WINDOW_START, WINDOW_END))
matches = dict_cur.fetchall()
console.print(f"  {len(matches):,} settled tier 1-3 matches with preds")
if not matches:
    sys.exit(0)

match_ids = [str(m["match_id"]) for m in matches]
dict_cur.execute("""
    SELECT match_id, selection, handicap_line, MAX(odds) AS best_odds
    FROM odds_snapshots
    WHERE match_id = ANY(%s::uuid[])
      AND market='asian_handicap'
      AND selection='away'
      AND handicap_line IN (-1.5,-1.0,-0.5,0.0,0.5,1.0,1.5)
      -- AF-ISLIVE-CALLSITE-FIXES-2026-09-05 / gotcha 37: pre-match bound.
      -- MAX(odds) over post-KO rows fabricates edge (p99 +4.10pp).
      AND minutes_to_kickoff > 0
    GROUP BY 1,2,3
""", (match_ids,))
odds_rows = dict_cur.fetchall()
console.print(f"  {len(odds_rows):,} AH-away half-line rows\n")
odds_by_match: dict[str, list] = {}
for r in odds_rows:
    odds_by_match.setdefault(str(r["match_id"]), []).append(r)

tier_rho_cache = _load_dc_rho_cache()
rows_out = []  # all rows we'd consider — slice by cal_prob bucket post-hoc

def _ah_settle(handicap_line: float, sh: int, sa: int) -> tuple[str, float]:
    margin = sa - sh + handicap_line  # away perspective
    if abs(margin % 1.0) < 1e-9:
        if margin > 0: return "won", 1.0
        if margin < 0: return "lost", -1.0
        return "void", 0.0
    if margin > 0: return "won", 1.0
    return "lost", -1.0

with Progress(TextColumn("[progress.description]{task.description}"),
              BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
              TimeRemainingColumn(), console=console) as progress:
    task = progress.add_task("Low-conf backtest", total=len(matches))
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
        tier = m.get("tier") or 1
        me = 0.06 if tier == 3 else 0.05  # edge threshold

        for o in odds_by_match.get(mid, []):
            hl = float(o["handicap_line"])
            if hl not in HALF_LINES: continue
            odds = float(o["best_odds"])
            if not (ODDS_MIN <= odds <= ODDS_MAX): continue
            mp = _ah_model_prob(exp_h, exp_a, "away", hl, rho=rho)
            if mp < MIN_PROB_LOW: continue        # ← 0.30 floor instead of 0.50
            ip = 1.0 / odds
            edge = mp - ip
            if (mp - ip) > PINNACLE_VETO_GAP_AH: continue
            if edge < me: continue

            result, mult = _ah_settle(hl, sh, sa)
            pnl = (odds - 1) * FLAT_STAKE * mult if result == "won" else \
                  (FLAT_STAKE * mult if result == "lost" else 0.0)
            rows_out.append({
                "day": m["day"], "tier": tier, "hl": hl, "mp": mp,
                "odds": odds, "edge": edge, "result": result, "pnl": pnl,
            })
        progress.advance(task)

conn.close()
console.print(f"\n[green]{len(rows_out):,} away rows passing odds_range+min_prob>=0.30+edge>=5%[/green]\n")

# Slice by cal_prob bucket
def slice_table(rows, label_fn, header="bucket"):
    from collections import defaultdict
    buckets = defaultdict(lambda: {"n":0,"w":0,"l":0,"v":0,"pnl":0.0})
    for r in rows:
        k = label_fn(r)
        if k is None: continue
        b = buckets[k]
        b["n"]+=1
        if r["result"]=="won": b["w"]+=1
        elif r["result"]=="lost": b["l"]+=1
        else: b["v"]+=1
        b["pnl"]+=r["pnl"]
    print(f"  {header:<14}{'n':>5}{'W/L/V':>11}{'hit%':>7}{'PnL':>9}{'ROI%':>8}")
    print("  " + "─"*55)
    for k, b in sorted(buckets.items()):
        settled = b["w"]+b["l"]
        hr = 100*b["w"]/settled if settled else 0
        roi = 100*b["pnl"]/(b["n"]*FLAT_STAKE) if b["n"] else 0
        print(f"  {str(k):<14}{b['n']:>5}{b['w']:>3}/{b['l']:>3}/{b['v']:>3}{hr:>7.1f}{b['pnl']:>+9.0f}{roi:>+8.1f}")

print("Slice by calibrated_prob bucket (away picks):")
def bucket(r):
    if r["mp"] < 0.30:  return "0_<30%"
    if r["mp"] < 0.35:  return "1_30-35%"
    if r["mp"] < 0.40:  return "2_35-40%"
    if r["mp"] < 0.45:  return "3_40-45%"
    if r["mp"] < 0.50:  return "4_45-50%"
    return "5_>=50% (old bot zone)"
slice_table(rows_out, bucket)

print("\nSlice by handicap_line within 30-45% well-calibrated zone:")
well_cal = [r for r in rows_out if 0.30 <= r["mp"] < 0.45]
slice_table(well_cal, lambda r: f"hl={r['hl']:+.1f}", header="handicap")

print(f"\nHeadline — 'bot_ah_away_low_conf' candidate (cal_prob 0.30-0.45 ONLY):")
n = len(well_cal)
w = sum(1 for r in well_cal if r["result"]=="won")
l = sum(1 for r in well_cal if r["result"]=="lost")
v = sum(1 for r in well_cal if r["result"]=="void")
pnl = sum(r["pnl"] for r in well_cal)
stake = n * FLAT_STAKE
roi = 100*pnl/stake if stake else 0
print(f"  n={n}, W/L/V={w}/{l}/{v}, hit={100*w/(w+l) if (w+l) else 0:.1f}%, PnL={pnl:+.0f}, ROI={roi:+.1f}%")
