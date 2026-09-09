# Betting Gate Decisions — the single source of truth for edge/odds floors

**Read this before changing any real-money edge or odds floor, and before running
"a quick backtest" to re-decide one.** This doc exists because we kept running
*different* backtests that gave *different* answers and kept changing the floors —
the fix is a fixed method + a recorded decision, not another ad-hoc run.
(Established 2026-09-09 after the 1x2 10-vs-13 churn.)

## The canonical method (use THIS, nothing else, for a floor decision)

Run `scripts/edge_floor_backtest.py --market <m> --folds 3`. A floor is
**decided on the EXECUTABLE basis** ("bots' actual picks", ideally the
active/calibrated slice), and it is **acceptable only if it is fold-robust —
positive in EVERY walk-forward fold**, not merely positive pooled.

**What NOT to decide on** (every one of these produced a *different* answer and a
needless change in the past):
- **The idealized / best-of-books basis.** It shows +50–150% ROI (O/U) because
  taking the best of many books selects the most-mispriced book — the line-shop
  mirage (§55, §52). Its *monotonicity* (higher floor → higher ROI) is the only
  signal; its *magnitude* is fantasy and its "robust ✓" is not executable.
- **Total profit at flat stake.** More volume at a lower floor makes more total €
  while being *less* ROI-robust. That's a volume objective, not an edge decision.
  (This is exactly how BOT-CONFIG-GOLDEN-MIDDLE briefly argued for 0.10.)
- **A small recent window** (e.g. one bot's last ~370 picks). Favorable regimes
  make a non-robust floor look robust. Use the full executable universe.

**Rule: no floor change without re-running the above on the executable basis AND
updating the table below in the same commit.** If the executable basis and the
idealized basis disagree, the executable basis wins.

## Decisions (as of 2026-09-09)

| Market | Edge floor | Odds floor | Verdict on the executable basis | Evidence |
|---|---|---|---|---|
| **1x2** | **13%** | **2.80** | **13% fold-robust; 10% is NOT** (negative fold: all-bots f1 −1.8%, calibrated f2 −3.6%). Keep 13%. | `edge_floor_backtest --market 1x2`: exec all-bots ≥13% +8.3% ✓ / ≥10% +3.1% ✗; calibrated ≥13% +15.9% ✓ / ≥10% +15.1% ✗ |
| **O/U 2.5** | **8%** | **1.80** | **8% fold-robust** (and robust down to ~5%). Keep 8%. | `edge_floor_backtest --market o/u`: exec all-bots ≥8% +13.8% ✓ / calibrated ≥8% +18.5% ✓; 13% breaks (f3 −5.3%) |
| Asian handicap | — (not placed) | — | No fold-robust cell at any floor → not placed | see AH-VIABILITY-REVIEW (closed) |

### The 1x2 10-vs-13 tradeoff, recorded so it isn't re-litigated
10% makes **more total profit** (more volume — it fires ~45% more often, which is
why the live 1x2 bot "finds 0" at 13% on a quiet day) but is **not fold-robust**
(a negative fold on the executable basis). 13% is **lower volume, higher and
robust ROI**. On the executable basis that governs real money, **13% wins.** If we
ever want the volume, it is a deliberate *volume-for-robustness* trade and an
owner decision — not a "the backtest said 10%" change, because the backtest that
said 10% used total-profit or a small window, not fold-robust executable ROI.

## Why the same question kept giving different answers (the actual bug)

Three axes were varying silently between runs, and none was written down:
1. **Basis** — executable (real) vs idealized best-of-books (inflated mirage).
2. **Metric** — fold-robust ROI vs pooled ROI vs total profit.
3. **Sample** — full executable universe vs one bot's recent ~370 picks.

Pick different values on those axes and you get −21%, +3%, +8%, +24%, or +93% for
what feels like "the same test." Fixing the axes (executable · fold-robust ROI ·
full universe) makes the answer stable and reproducible. That is the whole point
of this doc.

## Related
- `scripts/edge_floor_backtest.py` — the canonical tool (executable + idealized +
  walk-forward folds + the STALE-BEST-ODDS / DISTINCT-ON guards).
- ANALYSIS_GOTCHAS §52 (line-shop mirage), §55 (single-book vs best-of-books).
- `docs/BOOK_AGNOSTIC_EDGE_ENGINE.md` — the trigger engine (a *different* selection
  whose −21% is the model-vs-Coolbet adverse-selection problem, not a floor issue).
- The placer reads these floors from `coolbet_placer._MIN_EDGE_BY_MARKET` /
  `_MIN_ODDS_BY_MARKET`; the mirror jobs mirror them; the smoke test
  `BOT-CONFIG-GOLDEN-MIDDLE` pins the 1x2 value.
