"""inplay_e ECE re-check — 2026-06-03 audit.

bot_inplay_e was promoted to maturity_label='calibrated' in migration 134
based on backtest performance. We have NO explicit live ECE verdict in
PRIORITY_QUEUE for inplay_e (only the I/J/L cohort got that treatment).
This audit re-computes its current calibration against all settled live
bets and reports per-bucket drift.

Gate (same as INPLAY-CALIBRATION-IJL): ECE < 5%.
- If ECE ≥ 5%: file recalibration follow-up, consider demoting.
- If ECE < 5% AND per-bucket gaps all small/negative: confirm calibration.
- If ECE < 5% BUT one bucket has large positive gap (over-confident):
  cap or veto that bucket, file investigation.

Run: python3 scripts/inplay_e_ece_recheck.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402


def compute_ece(probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 20) -> float:
    bin_edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    total = len(probs)
    for i in range(n_bins):
        mask = (probs >= bin_edges[i]) & (probs < bin_edges[i + 1])
        if mask.sum() == 0:
            continue
        bin_acc = outcomes[mask].mean()
        bin_conf = probs[mask].mean()
        ece += (mask.sum() / total) * abs(bin_acc - bin_conf)
    return float(ece)


def bucketed_table(probs: np.ndarray, outcomes: np.ndarray, edges: list[float]) -> None:
    """Print predicted vs actual hit rate per bucket."""
    print(f"  {'bucket':<14} {'N':>5} {'mean_pred':>10} {'actual_hit':>10} {'gap (pred-act)':>16}")
    print("  " + "-" * 65)
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (probs >= lo) & (probs < hi if hi < 1.0 else probs <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        pred = probs[mask].mean()
        actual = outcomes[mask].mean()
        gap = pred - actual
        sign = "↑over-conf" if gap > 0.03 else ("↓under-conf" if gap < -0.03 else "calibrated")
        print(f"  {f'{lo:.2f}-{hi:.2f}':<14} {n:>5} {pred:>10.3f} {actual:>10.3f} {gap:>+16.3f} {sign}")


def main() -> int:
    rows = execute_query(
        """SELECT model_probability::float AS p,
                  CASE WHEN result::text = 'won' THEN 1 ELSE 0 END AS y,
                  result::text AS result,
                  selection, odds_at_pick, stake, pnl, pick_time, market
           FROM simulated_bets
           WHERE bot_id = (SELECT id FROM bots WHERE name = 'inplay_e')
             AND result::text IN ('won', 'lost')
             AND model_probability IS NOT NULL"""
    )
    if not rows:
        print("No settled inplay_e bets found.")
        return 1

    probs = np.array([r["p"] for r in rows], dtype=float)
    ys = np.array([r["y"] for r in rows], dtype=int)
    n = len(probs)
    wins = int(ys.sum())
    losses = n - wins
    avg_pred = probs.mean()
    actual_hit = wins / n
    total_staked = sum(float(r["stake"] or 0) for r in rows)
    total_pnl = sum(float(r["pnl"] or 0) for r in rows)
    roi = total_pnl / total_staked * 100 if total_staked else 0

    print("━━━ inplay_e calibration re-check (2026-06-03) ━━━")
    print()
    print(f"  Settled bets:   {n}  ({wins}W / {losses}L)")
    print(f"  Avg predicted:  {avg_pred:.3f}")
    print(f"  Actual hit:     {actual_hit:.3f}")
    print(f"  Headline gap:   {avg_pred - actual_hit:+.3f}  ({'over-confident' if avg_pred > actual_hit else 'under-confident'})")
    print(f"  Staked / PnL:   ${total_staked:.2f} / ${total_pnl:+.2f}")
    print(f"  ROI:            {roi:+.2f}%")
    print()

    ece_20 = compute_ece(probs, ys, n_bins=20)
    ece_10 = compute_ece(probs, ys, n_bins=10)
    ece_5 = compute_ece(probs, ys, n_bins=5)
    print(f"  ECE (20 bins):  {ece_20:.4f}  {'✗ FAIL (≥5%)' if ece_20 >= 0.05 else '✓ pass (<5%)'}")
    print(f"  ECE (10 bins):  {ece_10:.4f}")
    print(f"  ECE (5 bins):   {ece_5:.4f}")
    print()

    print("━━━ Per-bucket breakdown (10 buckets, predicted-vs-actual) ━━━")
    bucketed_table(probs, ys, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])

    print()
    print("━━━ Split by selection (over_under direction) ━━━")
    by_sel: dict[str, list] = {}
    for r in rows:
        sel = r["selection"] or "?"
        by_sel.setdefault(sel, []).append(r)
    for sel, sel_rows in sorted(by_sel.items(), key=lambda kv: -len(kv[1])):
        if len(sel_rows) < 10:
            continue
        sp = np.array([r["p"] for r in sel_rows], dtype=float)
        sy = np.array([1 if r["result"] == "won" else 0 for r in sel_rows], dtype=int)
        s_ece = compute_ece(sp, sy, n_bins=10)
        s_n = len(sel_rows)
        s_avg_p = sp.mean()
        s_hit = sy.mean()
        s_staked = sum(float(r["stake"] or 0) for r in sel_rows)
        s_pnl = sum(float(r["pnl"] or 0) for r in sel_rows)
        s_roi = s_pnl / s_staked * 100 if s_staked else 0
        flag = "✗" if s_ece >= 0.05 else "✓"
        print(f"  {flag} {sel:<14} N={s_n:>4}  pred={s_avg_p:.3f}  hit={s_hit:.3f}  ECE={s_ece:.4f}  ROI={s_roi:+.2f}%")

    print()
    print("━━━ Split by minute window (xg_source / live state proxy) ━━━")
    # inplay_e fires window 25-30 min per migration 120. Bucket by minute_at_pick if available.
    minute_rows = execute_query(
        """SELECT model_probability::float AS p,
                  CASE WHEN result::text='won' THEN 1 ELSE 0 END AS y,
                  COALESCE(match_minute_at_pick, -1) AS minute
           FROM simulated_bets
           WHERE bot_id = (SELECT id FROM bots WHERE name='inplay_e')
             AND result::text IN ('won','lost')
             AND model_probability IS NOT NULL"""
    )
    buckets = {"≤25 (pre-window)": [], "26-30 (window)": [], "31-50 (legacy)": [], ">50 / unknown": []}
    for r in minute_rows:
        m = int(r["minute"] or -1)
        if m == -1 or m > 50:
            buckets[">50 / unknown"].append(r)
        elif m <= 25:
            buckets["≤25 (pre-window)"].append(r)
        elif m <= 30:
            buckets["26-30 (window)"].append(r)
        else:
            buckets["31-50 (legacy)"].append(r)
    for name, brows in buckets.items():
        if len(brows) < 5:
            print(f"  {name:<20} N={len(brows):>4}  (too few to compute ECE)")
            continue
        bp = np.array([r["p"] for r in brows], dtype=float)
        by = np.array([r["y"] for r in brows], dtype=int)
        b_ece = compute_ece(bp, by, n_bins=10)
        print(f"  {name:<20} N={len(brows):>4}  pred={bp.mean():.3f} hit={by.mean():.3f} ECE={b_ece:.4f}")

    print()
    if ece_20 < 0.05:
        print(f"  VERDICT: ✓ ECE {ece_20:.4f} < 5% gate. Calibration holds.")
    elif ece_20 < 0.08:
        print(f"  VERDICT: ⚠ ECE {ece_20:.4f} above 5% gate but below 8% — marginal drift.")
        print(f"           File INPLAY-E-RECALIBRATE-WATCH (P2) and re-check in 30 days.")
    else:
        print(f"  VERDICT: ✗ ECE {ece_20:.4f} ≥ 8% — significant drift.")
        print(f"           Demote inplay_e from 'calibrated' until recalibrated.")
        print(f"           File INPLAY-E-DEMOTE (P0).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
