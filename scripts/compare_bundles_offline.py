"""Offline comparison of trained XGBoost bundles on a held-out window.

The Sunday weekly retrain produces a bundle, registers it in model_versions,
runs compare_models.py — but compare_models.py needs OVERLAPPING predictions
in the predictions table to do its work. Since candidate bundles aren't
shadow-inferred, every weekly compare produces "0 overlap" and the candidate
goes unevaluated.

This script bypasses the predictions table by loading each bundle directly
and scoring a held-out window of settled matches via match_feature_vectors.

Held-out window: 2026-05-18 → 2026-05-24
  - v14 (trained 2026-05-11):   fully out-of-sample ✓
  - v20260517 (trained 5/17):   fully out-of-sample ✓
  - v20260524 (trained today):  IN-sample (training cut = ~5/23) — caveat
                                 noted in output. Compare cautiously.

Outputs per market (1X2 home/draw/away, O/U 2.5 over/under):
  log_loss, Brier, hit_rate, predicted_prob_avg vs actual_rate
"""
from __future__ import annotations
import os, sys, math
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
import joblib
import numpy as np
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "soccer"
VERSIONS = ["v14", "v20260517", "v20260524"]
WINDOW_START = "2026-05-18"
WINDOW_END = "2026-05-24"

# ── 1. Hydrate missing bundles from Supabase Storage ─────────────────────────
from workers.model.storage import ensure_local_bundle
console.print("[bold]Step 1: ensure local bundles[/bold]")
for v in VERSIONS:
    bp = MODELS_DIR / v
    if (bp / "feature_cols.pkl").exists():
        console.print(f"  {v}: local ✓")
    else:
        console.print(f"  {v}: hydrating from Storage...")
        ok = ensure_local_bundle(v, MODELS_DIR)
        console.print(f"  {v}: {'hydrated ✓' if ok else 'FAILED'}")


# ── 2. Load each bundle ──────────────────────────────────────────────────────
console.print("\n[bold]Step 2: load bundles[/bold]")
bundles: dict[str, dict] = {}
for v in VERSIONS:
    bp = MODELS_DIR / v
    if not (bp / "feature_cols.pkl").exists():
        console.print(f"  {v}: skip (no feature_cols.pkl)")
        continue
    bundles[v] = {
        "feature_cols": joblib.load(bp / "feature_cols.pkl"),
        "result_1x2":   joblib.load(bp / "result_1x2.pkl"),
        "over_under":   joblib.load(bp / "over_under.pkl"),
    }
    console.print(f"  {v}: {len(bundles[v]['feature_cols'])} features")


# ── 3. Pull held-out matches + MFV rows ──────────────────────────────────────
console.print(f"\n[bold]Step 3: held-out matches {WINDOW_START} → {WINDOW_END}[/bold]")
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.cursor().execute("SET statement_timeout='180s'")
dc = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
dc.execute("""
    SELECT mfv.*, m.score_home, m.score_away, l.tier
    FROM match_feature_vectors mfv
    JOIN matches m ON m.id = mfv.match_id
    LEFT JOIN leagues l ON l.id = m.league_id
    WHERE mfv.match_date >= %s AND mfv.match_date <= %s
      AND m.status='finished' AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
""", (WINDOW_START, WINDOW_END))
rows = dc.fetchall()
console.print(f"  {len(rows):,} settled matches with MFV rows")
conn.close()
if not rows:
    console.print("[red]No held-out matches found. Exiting.[/red]"); sys.exit(0)


# ── 4. Build feature-row helper (same logic as xgboost_ensemble._build_row_from_mfv) ──
def build_row(raw: dict, feature_cols, tier: int) -> dict:
    row = {}
    for col in feature_cols:
        if col == "tier":
            row[col] = tier
            continue
        if col.endswith("_missing"):
            base = col[: -len("_missing")]
            row[col] = 1 if (raw.get(base) is None) else 0
            continue
        v = raw.get(col)
        try:
            row[col] = 0.0 if v is None else float(v)
        except (TypeError, ValueError):
            row[col] = 0.0
    return row


# ── 5. Predict + collect metrics ─────────────────────────────────────────────
def truth_1x2(sh, sa):
    if sh > sa: return [1, 0, 0]
    if sh == sa: return [0, 1, 0]
    return [0, 0, 1]

def truth_ou25(sh, sa):
    total = sh + sa
    return [1, 0] if total > 2.5 else [0, 1]  # [over, under]

def safe_log(p, eps=1e-7):
    return math.log(max(eps, min(1 - eps, p)))


console.print(f"\n[bold]Step 4: score {len(rows):,} matches with {len(bundles)} bundles[/bold]")

# metrics[version][market] = {"ll":sum, "brier":sum, "hits":int, "n":int, "pred_sum":float}
metrics = defaultdict(lambda: defaultdict(lambda: {"ll": 0.0, "brier": 0.0, "hits": 0, "n": 0, "pred_sum": 0.0}))

with Progress(TextColumn("[progress.description]{task.description}"),
              BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
              console=console) as progress:
    task = progress.add_task("scoring", total=len(rows))
    for r in rows:
        sh, sa = int(r["score_home"]), int(r["score_away"])
        truth_3 = truth_1x2(sh, sa)
        truth_2 = truth_ou25(sh, sa)
        tier = r.get("tier") or 1
        for v, b in bundles.items():
            row = build_row(dict(r), b["feature_cols"], tier)
            X = np.array([[row[c] for c in b["feature_cols"]]], dtype=float)
            try:
                probs_1x2 = b["result_1x2"].predict_proba(X)[0]  # 3 classes
                probs_ou = b["over_under"].predict_proba(X)[0]   # 2 classes
            except Exception:
                continue
            # 1X2
            for i, mkt in enumerate(["1x2_home", "1x2_draw", "1x2_away"]):
                p = float(probs_1x2[i])
                m = metrics[v][mkt]
                m["n"] += 1
                m["pred_sum"] += p
                m["ll"] += -safe_log(p) if truth_3[i] else -safe_log(1 - p)
                m["brier"] += (p - truth_3[i]) ** 2
                m["hits"] += truth_3[i]
            # O/U 2.5
            for i, mkt in enumerate(["over25", "under25"]):
                p = float(probs_ou[i])
                m = metrics[v][mkt]
                m["n"] += 1
                m["pred_sum"] += p
                m["ll"] += -safe_log(p) if truth_2[i] else -safe_log(1 - p)
                m["brier"] += (p - truth_2[i]) ** 2
                m["hits"] += truth_2[i]
        progress.advance(task)


# ── 6. Print comparison table ────────────────────────────────────────────────
console.print(f"\n[bold]Step 5: results — held-out {WINDOW_START} → {WINDOW_END}[/bold]\n")
markets = ["1x2_home", "1x2_draw", "1x2_away", "over25", "under25"]
header = f"  {'market':<12}" + "".join(f"{'log_loss':>12}{'Brier':>10}{'hit%':>7}{'pred%':>7}{'Δ_cal':>7}" for _ in VERSIONS)
print(f"  {'':<12}" + "".join(f"{v.center(43)}" for v in VERSIONS))
print(header)
print("  " + "─" * (12 + 43 * len(VERSIONS)))
for mkt in markets:
    line = f"  {mkt:<12}"
    for v in VERSIONS:
        m = metrics[v].get(mkt)
        if not m or m["n"] == 0:
            line += f"{'—':>43}"
            continue
        ll = m["ll"] / m["n"]
        brier = m["brier"] / m["n"]
        hit_rate = 100 * m["hits"] / m["n"]
        pred_rate = 100 * m["pred_sum"] / m["n"]
        delta_cal = pred_rate - hit_rate
        line += f"{ll:>12.4f}{brier:>10.4f}{hit_rate:>6.1f}%{pred_rate:>6.1f}%{delta_cal:>+7.1f}"
    print(line)

print()
print("Legend:")
print("  log_loss: lower is better")
print("  Brier:    lower is better (mean squared probability error)")
print("  hit%:     actual outcome rate in this window")
print("  pred%:    model's average predicted probability")
print("  Δ_cal:    pred% - hit% (calibration delta; near 0 = well-calibrated)")
print()
print("Caveat: v20260524 was trained today (5/24) and likely saw 5/18-5/23 in training")
print("        → its numbers may understate true generalization error. v14 vs v20260517")
print("        is the fair comparison (both fully out-of-sample on this window).")
