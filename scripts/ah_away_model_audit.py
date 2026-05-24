"""AH-AWAY-MODEL-AUDIT (2026-05-24).

Diagnose why bot_ah_away_dog backtest is -31.8% ROI on 601 bets (May 12-24)
while bot_ah_home_fav is +3.1% on 281 bets. Same calibrated-lambda solver,
opposite selection — asymmetric.

Slices:
  1) Hit rate by (selection × handicap_line) — find the worst lines
  2) Hit rate by league_tier
  3) Hit rate by edge bucket — does high-edge mean MORE wrong?
  4) Same-match comparison: when both bots placed in same match,
     does the home side win when the away side loses?
  5) Underlying 1X2 Platt calibration on away outcomes — is `1x2_away`
     systematically over-predicting?
  6) Lambda solver sanity — for a sample of matches, compute raw Poisson
     lambdas vs the calibrated ones; does the solver pull lambdas asymmetric?

Reads from the backtest CSV produced by backtest_ah_silent_period.py + the
DB for the 1X2 Platt and lambda checks.
"""
from __future__ import annotations
import os, sys, csv
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn

console = Console()
csv_path = Path(__file__).resolve().parent.parent / "dev" / "active" / "backtest-ah-silent-period.csv"
if not csv_path.exists():
    console.print(f"[red]Backtest CSV missing — run scripts/backtest_ah_silent_period.py first[/red]")
    sys.exit(1)

# Load CSV
rows = []
with csv_path.open() as f:
    for r in csv.DictReader(f):
        rows.append(r)
console.print(f"[bold]Loaded {len(rows):,} backtest rows[/bold]\n")

# Coerce
for r in rows:
    r["edge_pct"] = float(r["edge_pct"])
    r["model_prob"] = float(r["model_prob"])
    r["odds_at_pick"] = float(r["odds_at_pick"])
    r["pnl"] = float(r["pnl"])
    r["tier"] = int(r["tier"]) if r["tier"] else 0
    r["handicap_line"] = float(r["handicap_line"])

away = [r for r in rows if r["bot"] == "bot_ah_away_dog"]
home = [r for r in rows if r["bot"] == "bot_ah_home_fav"]
console.print(f"  Away rows: {len(away):,}   Home rows: {len(home):,}\n")


def summarize(rows, label_fn):
    """Return {label: (n, w, l, v, roi_pct)}."""
    buckets = defaultdict(lambda: {"n":0, "w":0, "l":0, "v":0, "pnl":0.0, "stake":0.0})
    for r in rows:
        k = label_fn(r)
        if k is None: continue
        b = buckets[k]
        b["n"] += 1
        if r["result"] == "won":  b["w"] += 1
        elif r["result"] == "lost": b["l"] += 1
        else: b["v"] += 1
        b["pnl"] += r["pnl"]
        b["stake"] += 10.0
    return buckets


def render_table(buckets, header_key):
    print(f"  {header_key:<18}{'n':>5}{'W/L/V':>11}{'hit%':>8}{'ROI%':>8}")
    print("  " + "─"*54)
    for k, b in sorted(buckets.items()):
        settled = b["w"] + b["l"]
        hr = 100*b["w"]/settled if settled > 0 else 0
        roi = 100*b["pnl"]/b["stake"] if b["stake"] > 0 else 0
        print(f"  {str(k):<18}{b['n']:>5}{b['w']:>3}/{b['l']:>3}/{b['v']:>3}{hr:>7.1f}{roi:>+8.1f}")
    print()


# ────────────────────────────────────────────────────────────────────────────
console.print("[bold cyan]Slice 1: Hit rate by handicap_line[/bold cyan]")
console.print("[dim]Away (bot_ah_away_dog) — positive handicaps = away underdog gets a head start, negative = strong away favorite[/dim]")
render_table(summarize(away, lambda r: f"{r['handicap_line']:+.1f}"), "handicap_line")
console.print("[dim]Home (bot_ah_home_fav) — negative handicaps = home favorite gives a handicap, positive = home underdog[/dim]")
render_table(summarize(home, lambda r: f"{r['handicap_line']:+.1f}"), "handicap_line")

# ────────────────────────────────────────────────────────────────────────────
console.print("[bold cyan]Slice 2: Hit rate by league tier[/bold cyan]")
console.print("[dim]Away:[/dim]"); render_table(summarize(away, lambda r: f"T{r['tier']}"), "tier")
console.print("[dim]Home:[/dim]"); render_table(summarize(home, lambda r: f"T{r['tier']}"), "tier")

# ────────────────────────────────────────────────────────────────────────────
console.print("[bold cyan]Slice 3: Hit rate by edge bucket[/bold cyan]")
console.print("[dim]Is high-edge MORE wrong? (would mean overconfident model)[/dim]")
def edge_bucket(r):
    e = r["edge_pct"]
    if e < 0.05: return "<5%"
    if e < 0.08: return "5-8%"
    if e < 0.12: return "8-12%"
    if e < 0.17: return "12-17%"
    return ">17%"
console.print("[dim]Away:[/dim]"); render_table(summarize(away, edge_bucket), "edge_bucket")
console.print("[dim]Home:[/dim]"); render_table(summarize(home, edge_bucket), "edge_bucket")

# ────────────────────────────────────────────────────────────────────────────
console.print("[bold cyan]Slice 4: Same-match home vs away outcome[/bold cyan]")
console.print("[dim]When both bots placed on the same match, did they balance (one wins, one loses)?[/dim]")
home_by_mid = {r["match_id"]: r for r in home}
both_count = 0
pairs = []
for r in away:
    h = home_by_mid.get(r["match_id"])
    if h:
        both_count += 1
        pairs.append((r["result"], h["result"]))
print(f"  Matches where BOTH bots placed: {both_count}")
from collections import Counter
pair_counts = Counter(pairs)
print(f"  {'away/home':<22}n")
for (ar, hr), n in sorted(pair_counts.items(), key=lambda x: -x[1]):
    print(f"  {ar+'/'+hr:<22}{n}")
both_lost = pair_counts.get(("lost","lost"), 0)
print(f"\n  Both lost: {both_lost} / {both_count} = {100*both_lost/max(both_count,1):.1f}%  "
      f"(if model is right, we'd expect both bets to balance — both losing is the worst sign)")
print()

# ────────────────────────────────────────────────────────────────────────────
console.print("[bold cyan]Slice 5: Underlying 1X2 Platt calibration on away outcomes[/bold cyan]")
console.print("[dim]Are 1X2_away predictions calibrated? Predicted P(away wins) vs actual P(away wins)[/dim]")
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("SET statement_timeout='60s'")
cur.execute("""
    WITH preds AS (
        SELECT p.match_id,
               p.model_probability::float AS pred_away_p,
               m.result AS outcome
        FROM predictions p
        JOIN matches m ON m.id = p.match_id
        WHERE p.market = '1x2_away'
          AND p.source = 'ensemble'
          AND p.created_at >= '2026-05-12'
          AND p.created_at <= '2026-05-24'
          AND m.status = 'finished'
          AND m.result IS NOT NULL
    )
    SELECT
        CASE
            WHEN pred_away_p < 0.10 THEN '0_<10%'
            WHEN pred_away_p < 0.20 THEN '1_10-20%'
            WHEN pred_away_p < 0.30 THEN '2_20-30%'
            WHEN pred_away_p < 0.40 THEN '3_30-40%'
            WHEN pred_away_p < 0.50 THEN '4_40-50%'
            WHEN pred_away_p < 0.60 THEN '5_50-60%'
            ELSE                        '6_>=60%'
        END AS bucket,
        COUNT(*) AS n,
        ROUND(AVG(pred_away_p)::numeric*100, 1) AS predicted_pct,
        ROUND(100.0 * COUNT(*) FILTER (WHERE outcome='away') / COUNT(*), 1) AS actual_pct
    FROM preds GROUP BY bucket ORDER BY bucket
""")
print(f"  {'pred_bucket':<14}{'n':>6}{'predicted%':>12}{'actual%':>10}{'Δ':>9}")
print("  " + "─"*51)
for r in cur.fetchall():
    delta = float(r[3]) - float(r[2])
    flag = " ←OVER" if delta < -3 else (" ←UNDER" if delta > 3 else "")
    print(f"  {r[0]:<14}{r[1]:>6}{str(r[2]):>12}{str(r[3]):>10}{delta:>+9.1f}{flag}")

# For comparison: 1x2_home Platt calibration on home outcomes
console.print("\n[dim]Compare to 1x2_home calibration on home outcomes (should also be near-perfect):[/dim]")
cur.execute("""
    WITH preds AS (
        SELECT p.model_probability::float AS pred_home_p, m.result AS outcome
        FROM predictions p JOIN matches m ON m.id = p.match_id
        WHERE p.market = '1x2_home' AND p.source = 'ensemble'
          AND p.created_at >= '2026-05-12' AND p.created_at <= '2026-05-24'
          AND m.status = 'finished' AND m.result IS NOT NULL
    )
    SELECT
        CASE
            WHEN pred_home_p < 0.10 THEN '0_<10%'
            WHEN pred_home_p < 0.20 THEN '1_10-20%'
            WHEN pred_home_p < 0.30 THEN '2_20-30%'
            WHEN pred_home_p < 0.40 THEN '3_30-40%'
            WHEN pred_home_p < 0.50 THEN '4_40-50%'
            WHEN pred_home_p < 0.60 THEN '5_50-60%'
            ELSE                        '6_>=60%'
        END AS bucket,
        COUNT(*) AS n,
        ROUND(AVG(pred_home_p)::numeric*100, 1) AS predicted_pct,
        ROUND(100.0 * COUNT(*) FILTER (WHERE outcome='home') / COUNT(*), 1) AS actual_pct
    FROM preds GROUP BY bucket ORDER BY bucket
""")
print(f"  {'pred_bucket':<14}{'n':>6}{'predicted%':>12}{'actual%':>10}{'Δ':>9}")
print("  " + "─"*51)
for r in cur.fetchall():
    delta = float(r[3]) - float(r[2])
    flag = " ←OVER" if delta < -3 else (" ←UNDER" if delta > 3 else "")
    print(f"  {r[0]:<14}{r[1]:>6}{str(r[2]):>12}{str(r[3]):>10}{delta:>+9.1f}{flag}")
print()

cur.close(); conn.close()
console.print("[bold]Audit complete. Interpretation:[/bold]")
console.print("  · If Slice 1 shows AH-away losing across ALL handicap lines → model bias, not line-specific")
console.print("  · If Slice 3 shows high-edge buckets WORSE than low-edge → model overconfident (overcorrection candidate)")
console.print("  · If Slice 4 has high lost/lost rate → matches where neither home nor away covered (high-draw markets)")
console.print("  · If Slice 5 shows 1x2_away over-predicting AWAY outcomes → root cause is upstream Platt fit, not AH-specific")
