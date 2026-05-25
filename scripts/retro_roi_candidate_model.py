"""Retroactive ROI: would `v_20260525_signals` have changed which bets fired?

Loads every settled 1x2 simulated_bet from the last 30 days. Re-scores each
match with the candidate model. Compares:
  - did the candidate produce a different calibrated_prob?
  - would the edge gate (5% min) still have fired the bet?
  - of the bets that would NOT have fired with the candidate, what was their
    actual ROI under production?

The key question: would the candidate have FILTERED OUT the losing bets
that we actually placed, OR would it have skipped winners too?

Run: python3 scripts/retro_roi_candidate_model.py [--version v_20260525_signals]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import joblib
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()
MODELS_ROOT = Path(__file__).resolve().parent.parent / "data" / "models" / "soccer"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v_20260525_signals")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    bundle_dir = MODELS_ROOT / args.version
    console.print(f"[bold]Retro ROI — candidate bundle: {args.version}[/bold]")
    if not bundle_dir.exists():
        console.print(f"[red]Bundle not at {bundle_dir}[/red]")
        sys.exit(1)
    model = joblib.load(bundle_dir / "result_1x2.pkl")
    feature_cols = joblib.load(bundle_dir / "feature_cols.pkl")

    rows = execute_query("""
        SELECT sb.id, sb.match_id, sb.selection, sb.odds_at_pick,
               sb.calibrated_prob AS prod_prob,
               sb.edge_percent AS prod_edge,
               sb.pnl, sb.stake, sb.result,
               mfv.*
        FROM simulated_bets sb
        JOIN match_feature_vectors mfv ON mfv.match_id = sb.match_id
        WHERE sb.market = '1x2'
          AND sb.result IN ('won','lost','void')
          AND sb.pick_time >= NOW() - (%s || ' days')::interval
          AND sb.calibrated_prob IS NOT NULL
          AND sb.odds_at_pick IS NOT NULL
    """, (args.days,))
    if not rows:
        console.print("[yellow]No settled 1x2 bets.[/yellow]")
        sys.exit(0)
    df = pd.DataFrame(rows)
    console.print(f"  Loaded {len(df):,} settled 1x2 bets")

    # Build features (subset to model's columns, fill NaN with median)
    X = df.reindex(columns=[c for c in feature_cols if c in df.columns]).copy()
    for c in feature_cols:
        if c not in X.columns:
            X[c] = np.nan
    X = X[feature_cols]
    for c in X.columns:
        if c.endswith("_missing"):
            X[c] = X[c].fillna(False).astype(int) if X[c].dtype != int else X[c]
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    X = X.fillna(X.median(numeric_only=True))

    # Score: 3-class probas. Map to selection.
    proba = model.predict_proba(X)
    sel_idx = {"home": 0, "draw": 1, "away": 2}
    cand_probs = []
    for i, sel in enumerate(df["selection"]):
        idx = sel_idx.get(sel)
        cand_probs.append(proba[i, idx] if idx is not None else None)
    df["cand_prob"] = cand_probs
    df["cand_edge"] = df["cand_prob"] - (1.0 / df["odds_at_pick"].astype(float))

    # Group bets into:
    #   FIRES_BOTH:     both prod and candidate would fire (edge >= 5%)
    #   ONLY_PROD:      prod fires, candidate wouldn't
    #   ONLY_CAND:      candidate would fire, prod wouldn't (untestable — prod didn't fire)
    #   FIRES_NEITHER:  neither — these aren't in our data (prod fired so we have them)
    EDGE_GATE = 0.05
    df["prod_fires"] = df["prod_edge"].astype(float) / 100 >= EDGE_GATE  # prod_edge is in %, convert
    df["cand_fires"] = df["cand_edge"].astype(float) >= EDGE_GATE

    # Note: ALL rows in df DID fire under prod (they're settled bets).
    # So prod_fires==True for everything. We're really partitioning by cand_fires.
    both = df[df["cand_fires"]]
    only_prod = df[~df["cand_fires"]]

    def _stats(grp):
        n = len(grp)
        if n == 0:
            return {"n": 0, "roi": 0.0, "pnl": 0.0}
        stake = grp["stake"].astype(float).sum()
        pnl = grp["pnl"].astype(float).sum()
        return {"n": n, "roi": (pnl / stake * 100) if stake else 0, "pnl": pnl}

    s_both = _stats(both)
    s_only_prod = _stats(only_prod)
    s_all = _stats(df)

    t = Table(title=f"Retroactive ROI — last {args.days} days, candidate {args.version}")
    for c in ("partition", "n bets", "ROI %", "P&L €"):
        t.add_column(c)
    t.add_row("ALL bets (prod fired all)",  str(s_all['n']),       f"{s_all['roi']:+.2f}%",       f"€{s_all['pnl']:+.2f}")
    t.add_row("Candidate would ALSO fire",  str(s_both['n']),      f"{s_both['roi']:+.2f}%",      f"€{s_both['pnl']:+.2f}")
    t.add_row("Candidate would SKIP",       str(s_only_prod['n']), f"{s_only_prod['roi']:+.2f}%", f"€{s_only_prod['pnl']:+.2f}")
    console.print(t)

    # Verdict
    console.print(f"\n[bold]Reading:[/bold]")
    if s_only_prod['n'] == 0:
        console.print("  Candidate fires on every bet prod fired — no filtering signal.")
    else:
        skip_roi = s_only_prod['roi']
        keep_roi = s_both['roi']
        delta = keep_roi - skip_roi
        if skip_roi < keep_roi - 5:
            console.print(f"[green]  ✓ Candidate would skip the losers: skipped bets ROI {skip_roi:+.2f}% vs kept {keep_roi:+.2f}% ({delta:+.1f}pp lift)[/green]")
        elif skip_roi > keep_roi + 5:
            console.print(f"[red]  ✗ Candidate would skip WINNERS: skipped bets ROI {skip_roi:+.2f}% vs kept {keep_roi:+.2f}%. Don't deploy.[/red]")
        else:
            console.print(f"[yellow]  ≈ Neutral: skipped ROI {skip_roi:+.2f}% vs kept {keep_roi:+.2f}% — candidate similar bet-selection power[/yellow]")


if __name__ == "__main__":
    main()
