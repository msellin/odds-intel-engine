# O/U ODDS-FLOOR SWEEP — plan

Parent row: **[[#073]]** in `PRIORITY_QUEUE.md` (filed with this work).

Owner, 2026-09-22: *"is it possible to backtest OU model bot configuration
against historical data? i see that the long failing period came when we only
took higher odds picks for OU... test it backwards with different odds levels...
min threshold 1.8+, 2.0+, 2.2+, 2.4+ etc up until lets say 2.8+"*

## The observation is right about WHEN and confounded about WHY

`bot_v10_ou` settled picks, by era and price:

| era | n | at 2.8+ |
|---|---|---|
| **A pre-bug** (May 8 → Sep 2) | 157 | 25 (16%) |
| **B bug window** (Sep 3 → Sep 12) | 80 | **59 (74%)** |
| **C post-fix** (Sep 13 →) | 15 | 12, **0 with CLV** |

The drawdown on the 90d chart runs Sep 3 → Sep 13. That is exactly
`OU-CALIBRATOR-DOMAIN-MISMATCH` (migration 335): a Platt curve fitted on raw
ensemble probabilities and applied to Pinnacle-shrunk ones, whose entire output
range was [0.303, 0.666]. Its effect was to make `edge = cal_prob − 1/odds`
degenerate into "how far is this price from ~0.45" — **which is maximised by the
longest price on the board**. So the bug MANUFACTURED the high-odds picks.

**"High-odds O/U" and "the bug window" are therefore nearly the same rows**, and
a naive floor sweep over the whole period would re-measure the bug and report it
as an odds-band effect. That is `ANALYSIS_GOTCHAS §47` (an odds-band effect is a
BOT effect until you split) arriving through §39 (never measure across a
calibration change).

## Design

1. **Headline on era A only** — n=157, and CLV coverage is 157/157. Era B is
   reported SEPARATELY and explicitly as the confound, never pooled. Era C is
   n=15 with zero CLV rows: reported as not-yet-measurable, not as a result.
2. **Primary metric is de-vigged Pinnacle CLV**, not ROI (§8: CLV converges
   ~200× faster; every ROI CI in this system spans zero —
   `docs/BETA_PROMOTION_BAR.md`). ROI is reported beside it with a CI, never alone.
3. **Executable price basis** for BOTH the threshold and the return:
   `COALESCE(odds_at_pick_live, odds_at_pick)`. `odds_at_pick` alone is a MAX()
   high-water mark (gotcha §30 / STALE-BEST-ODDS), so thresholding on it asks
   "what if we had filtered on a price nobody offered".
4. **Cumulative floors AND disjoint bands.** The owner asked for floors (1.8+,
   2.0+, …), which are NESTED — so ROI at 2.0+ is a volume-weighted blend that is
   mathematically dragged toward the neighbouring floors and will always look
   "similar" (the framing `scripts/odds_floor_ab.py` had to fix for 1x2). Raising
   a floor one step is exactly "drop this disjoint band", so the bands are what
   actually localise the damage. Report both.
5. **Multiple comparisons.** Six nested floors plus six bands is twelve
   correlated statistics on n=157; picking the best post-hoc is how noise becomes
   a config change. Max-|t| permutation over the whole grid, 2,000 draws, for a
   family-wise p — the design already used by `scripts/odds_band_by_market.py`.
6. **Month-by-month sign** next to every surviving cell. A cell that is positive
   in one month out of five is not a finding.

## What this CAN and CANNOT answer

**CAN:** whether RAISING the floor above 1.8 would have helped, because every
such gate is a subset of picks the bot actually made.

**CANNOT:** whether LOWERING it below the current admitted range would help —
those picks do not exist in the ledger. Answering that needs a gate REPLAY over
all historical O/U odds (the "idealized" basis in
`scripts/edge_floor_backtest.py`), which is a second phase and carries
best-of-books selection bias of its own.

**Very likely CANNOT answer definitively at all.** n=157 clean, against this
repo's own stated bar of n≥334 for a useful CLV read. Expect to report a
direction and an interval, not a decision. Saying so is the deliverable.
