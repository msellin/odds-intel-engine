"""Backtest candidate AH bot configs against the May 12-24 silent-period CSV.

Uses the existing backtest-ah-silent-period.csv as the data source. Each
candidate config is just a filter predicate over those rows. Configs that
fit within the CSV's basic-filter scope (odds 1.70-2.50, min_prob 0.50,
edge >= 5%, tier 1-3) are backtested here. Configs needing different basic
filters would require re-running the upstream backtest — none of today's
proposed designs need that.

Stake assumption: flat €10 per bet, same as the upstream backtest.

Outputs the comparison table to stdout. Decision rule per candidate:
  - SHIP    if ROI > +10% on n >= 100
  - WATCH   if 0% < ROI <= +10% OR n < 100
  - DROP    if ROI <= 0%
"""
from __future__ import annotations
import csv
from pathlib import Path
from collections import defaultdict

csv_path = Path(__file__).resolve().parent.parent / "dev" / "active" / "backtest-ah-silent-period.csv"
rows = list(csv.DictReader(csv_path.open()))
for r in rows:
    r["edge_pct"] = float(r["edge_pct"])
    r["model_prob"] = float(r["model_prob"])
    r["odds_at_pick"] = float(r["odds_at_pick"])
    r["pnl"] = float(r["pnl"])
    r["tier"] = int(r["tier"]) if r["tier"] else 0
    r["handicap_line"] = float(r["handicap_line"])

# ────────────────────────────────────────────────────────────────────────────
# Candidate bot configs (predicate over CSV rows).
# Naming:
#   home / away : selection
#   _dog / _fav : whether the bot picks the underdog or favorite side
#   _pos / _neg : handicap-line sign filter
# ────────────────────────────────────────────────────────────────────────────

CANDIDATES = [
    # ────────────── baselines (existing bots, for reference) ──────────────
    ("bot_ah_away_dog_OLD (all lines)",
     lambda r: r["bot"]=="bot_ah_away_dog"),
    ("bot_ah_away_dog_NEW (hl>=0, just shipped)",
     lambda r: r["bot"]=="bot_ah_away_dog" and r["handicap_line"]>=0),
    ("bot_ah_home_fav (current)",
     lambda r: r["bot"]=="bot_ah_home_fav"),

    # ────────────── tighter variants of existing bots ──────────────
    ("bot_ah_away_dog_TIGHT (hl in {+0.5,+1.0,+1.5})",
     lambda r: r["bot"]=="bot_ah_away_dog" and r["handicap_line"]>=0.5),
    ("bot_ah_away_dog_high_edge (hl>=0, edge>=10%)",
     lambda r: r["bot"]=="bot_ah_away_dog" and r["handicap_line"]>=0 and r["edge_pct"]>=0.10),
    ("bot_ah_home_fav_strict (hl<=0 only)",
     lambda r: r["bot"]=="bot_ah_home_fav" and r["handicap_line"]<=0),
    ("bot_ah_home_fav_no_neg15 (hl>-1.0)",
     lambda r: r["bot"]=="bot_ah_home_fav" and r["handicap_line"]>-1.0),

    # ────────────── NEW BOT PROPOSALS ──────────────
    # bot_ah_home_dog — home gets a head start (positive handicap)
    ("NEW: bot_ah_home_dog (hl>=+0.5)",
     lambda r: r["bot"]=="bot_ah_home_fav" and r["handicap_line"]>=0.5),

    # Even tighter — only strong head-start positions
    ("NEW: bot_ah_home_dog_TIGHT (hl>=+1.0)",
     lambda r: r["bot"]=="bot_ah_home_fav" and r["handicap_line"]>=1.0),

    # AWAY underdog at the sweet spot only (+0.5 was 62% hit / +42% ROI in audit)
    ("NEW: bot_ah_away_sweet_spot (selection=away, hl=+0.5 only)",
     lambda r: r["bot"]=="bot_ah_away_dog" and r["handicap_line"]==0.5),

    # AWAY underdog combined with high-edge filter
    ("NEW: bot_ah_away_dog_low_edge (hl>=0, edge in 5-10%)",
     lambda r: r["bot"]=="bot_ah_away_dog" and r["handicap_line"]>=0
               and 0.05 <= r["edge_pct"] < 0.10),
]


# ────────────────────────────────────────────────────────────────────────────
def summarize(predicate):
    sub = [r for r in rows if predicate(r)]
    n = len(sub)
    w = sum(1 for r in sub if r["result"]=="won")
    l = sum(1 for r in sub if r["result"]=="lost")
    v = sum(1 for r in sub if r["result"]=="void")
    settled = w + l
    hit = 100*w/settled if settled else 0
    pnl = sum(r["pnl"] for r in sub)
    stake = n * 10
    roi = 100*pnl/stake if stake else 0
    return n, w, l, v, hit, pnl, roi


def verdict(n, roi):
    if n < 30:           return "—   ", "too small to judge (n<30)"
    if roi > 10 and n >= 100: return "SHIP", "+ROI on solid sample"
    if roi > 10:         return "WATCH", "+ROI but small sample"
    if roi > 0:          return "WATCH", "marginally +ROI"
    return "DROP", "−ROI"


print("=" * 110)
print("AH BOT CANDIDATE BACKTEST — May 12-24 silent-period CSV")
print("=" * 110)
print()
print(f"{'config':<58}{'n':>5}{'hit%':>7}{'PnL':>9}{'ROI%':>8}{'verdict':>10}  why")
print("-" * 130)

for label, pred in CANDIDATES:
    n, w, l, v, hit, pnl, roi = summarize(pred)
    tag, why = verdict(n, roi)
    print(f"  {label:<56}{n:>5}{hit:>6.1f}%{pnl:>+9.0f}{roi:>+8.1f}{tag:>9}  {why}")

print()
print("=" * 110)
print("Caveats:")
print("  - Flat €10 stake (no Kelly). Real Kelly would amplify wins/losses.")
print("  - 12-day window. Subsets with n<100 are statistically thin.")
print("  - No ALN-1 bump (LOW-aligned bets need edge+1%) → some 5-6% edge bets")
print("    might be filtered live. Sign should remain.")
print("  - All configs share basic filters: odds 1.70-2.50, min_prob 0.50,")
print("    edge >= 5% (T1/T2) or 6% (T3), tier 1-3. Configs needing different")
print("    basic filters (e.g. lower min_prob) require a fresh DB backtest.")
