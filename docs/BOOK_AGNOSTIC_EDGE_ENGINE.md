# Book-Agnostic Edge Engine — the correct architecture for OWN betting

**Status: Stage A BUILT (2026-09-09); Stage B (matcher) + validation pending.**
This is the target architecture for how we decide what to bet with our own money
at Coolbet (and, next, Unibet). It replaces the current "mirror the /picks page
into Coolbet bots" approach, which selects the wrong universe of bets. Stage A
(`pick_triggers` table + `workers/jobs/pick_triggers.py`, migration 319) writes
the fair-value windows hourly; it is paper — no book odds, no placement.

## The one-line idea

> **The model publishes a fair value per fixture; each bookmaker checks its own
> odds against it and decides whether *that* game is a bet.**

The model does NOT pick "the bets." It emits, for every fixture × market ×
selection it can predict, a **calibrated probability** — book-independent raw
data. Then **each book** (Coolbet, Unibet, …) matches *its own* live odds against
that fair value and lights up the games where *its* price clears our edge + odds
floor. Coolbet's bet list and Unibet's bet list are computed from the **same**
model output but **different** odds, because they are different books.

## Why the current approach is wrong

Today the Coolbet bots (`bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1`)
**copy from `simulated_bets`** — the calibrated cohort's picks — and filter on
`edge_percent >= floor`. That `edge_percent` was computed against the *pipeline's*
odds (a reference book / de-vigged Pinnacle), **not Coolbet's**. Two failures
follow:

1. **We select the wrong universe.** A game is only considered for Coolbet if it
   first cleared the floor at the *reference* odds. But Coolbet's prices are
   usually *higher*, so a game that's only 8% edge at the reference book — and is
   therefore dropped — could be 15% at Coolbet. **We never even look at it.**
2. **We don't know what a pick was priced against.** The mirrored pick carries a
   reference `edge_percent`; the operator can't tell which book/odds created it.

The placer itself is fine — it re-reads Coolbet's live price and re-checks the
floor (`min_odds_for` vs `outcome.odds`). The bug is one stage earlier: **the
selection is anchored to the /picks page, which is built for customers against
reference odds, not to Coolbet's own board.**

## The evidence (measured 2026-09-09)

1x2 market, last 45 days, our **calibrated** model (isotonic on 336k settled 1x2
predictions) evaluated against **Coolbet's own odds** vs the current mirror:

| Selection basis | Qualifying picks (edge≥13% & odds≥2.80) |
|---|---|
| Current /picks mirror (`simulated_bets` reference edge) | **75** |
| **Coolbet-native** (`cal_prob − 1/Coolbet_odds`) | **7,901 across 2,549 matches** |

~**100× more selection surface** — the mirror is discarding almost the entire
Coolbet-native universe.

**CRITICAL CAVEAT — candidates ≠ profitable bets.** 7,901 is the count of places
where our calibrated model *disagrees with Coolbet* by ≥13%. It is NOT 7,901 good
bets. The 1x2 model's discrimination AUC is *below* the market's (see
MARKET_DATA_MAP / the discrimination diagnostics), so **many of those
disagreements are the model being wrong, not Coolbet mispricing.** The expanded
universe is real and worth capturing, but its **ROI must be validated
out-of-sample** and the edge/odds gates re-tuned on the *Coolbet-native* set —
more picks is not more profit until a held-out backtest says so. This is the same
line-shop-mirage trap (§52): "model disagrees with the sharp price" is a candidate
signal, never an automatic bet.

## The architecture — three decoupled stages

### Stage A — Fair value (model / betting phase, book-agnostic)
For every upcoming fixture × market × selection the model predicts, compute the
**calibrated probability** and derive a **trigger window**:

```
min_odds = max( 1 / (cal_prob − edge_floor) ,  odds_floor )   # enough edge + odds floor
max_odds = min_odds × OUTLIER_MULT                            # cap stale/erroneous prices
```

Store one row per (fixture, market, selection, bot):
`coolbet_pick_triggers(match_id, market, selection, cal_prob, edge_floor,
odds_floor, min_odds, max_odds, bot_name, computed_at, expires_at)`.

This is **book-independent** — it is our fair value and the price band at which
any book's offer becomes a bet. `cal_prob` is fixed once the fixture is predicted,
so `min_odds`/`max_odds` are computed **once** per fixture.

### Stage B — Per-book match (cheap, runs after each book's sweep)
Each book's odds sweep (Coolbet FS sweep today; Unibet next) lands live prices in
`odds_snapshots`. A tiny **matcher** (DB-only, no HTTP) joins the latest price per
(match, market, selection) against the trigger window:

```
if  min_odds ≤ book_odds ≤ max_odds   →  emit a pick for THAT book (shadow_bets)
```

No model call, no probability math at match time — just a comparison. It's a
**standing limit order**: the model sets the trigger price; the sweep is the market
feed that fills it.

### Stage C — Placement (unchanged)
The placer already re-reads the live price on the match page and re-applies
`min_odds_for` before staking, so it remains the final real-money gate. Nothing
changes here.

## Why this is the right shape

- **No missed games.** *Every* predicted fixture has a trigger; any book price in
  the window fires — including games that only become +EV at that book's price.
- **The book's own odds are the trigger** — exactly what each book's sweep
  provides. The FS sweep earns its keep here: it lands Coolbet's odds so the
  matcher can use them.
- **Drift-aware for free.** Each new sweep re-checks the live price against the
  fixed window, so odds drifting *up* into range later are caught (this also
  answers the standing "do we capture drifting odds?" worry — structurally, yes).
- **Cheap at sweep time.** The expensive model math is precomputed once per
  fixture; the match is a bounded DB join.
- **Auditable.** You can see the standing trigger for any game and exactly why a
  book price did or didn't qualify, and every emitted pick records the book odds
  it fired on.
- **Book-agnostic → scales to Unibet with ~zero model work.** Stage A is shared.
  Adding Unibet is: point the matcher at Unibet's `odds_snapshots` rows and add a
  Unibet placer. Same fair value, different odds source. This is the market×book
  matrix vision made concrete.

## The window, precisely

`edge = cal_prob − 1/odds` (edge is in **probability points**, not ROI — see the
min-odds note below). A selection qualifies when the book's odds give at least the
required edge **and** clear the market's raw odds floor, and are not an outlier:

- `min_odds = max( 1/(cal_prob − edge_floor), odds_floor )`
  - the edge-floor term: at this price edge = exactly `edge_floor`.
  - the odds-floor term: the validated per-market floor (e.g. 2.80 for 1x2).
- `max_odds = min_odds × OUTLIER_MULT` (e.g. ×1.6): a price far above fair value
  is more likely a stale/erroneous quote than a gift — the existing outlier guard,
  expressed as the top of the window.

Worked example, 1x2, `cal_prob=0.40`, `edge_floor=0.13`, `odds_floor=2.80`:
`min_odds = max(1/(0.40−0.13), 2.80) = max(3.70, 2.80) = 3.70`; window `[3.70, ~6.0]`.
A Coolbet price of 3.70–6.0 fires; 3.50 is skipped (edge < 13%); 8.0 is skipped
(outlier). **Note this differs from the public /picks `min_odds` = 1/cal_prob =
2.50, whose edge is 0 — the public floor is fair value; the betting floor bakes in
the required edge.** (ANALYSIS_GOTCHAS: two min-odds, two meanings.)

## Relationship to the OU35 bot (the working template)

`bot_ou35_model_v1` (`workers/jobs/ou35_model_shadow.py`, built 2026-09-08)
already does the *inline* version of this: it fits calibration, reads Coolbet's
swept O/U 3.5 odds, computes `cal − 1/coolbet_odds`, and writes a pick when it
clears the floor. The trigger-table design is the same correctness, **decoupled
and efficient**: precompute the window (Stage A) instead of recomputing the edge
inside every generator, and let a shared matcher (Stage B) serve every book.

## Implementation plan (paper-first, no real-money change without validation)

1. **Stage A ✅ BUILT 2026-09-09:** `pick_triggers` table (migration 319) +
   `workers/jobs/pick_triggers.py` (`compute_triggers`), scheduled hourly at :05.
   Calibrates (isotonic on settled history), sources the edge/odds floors from
   the placer (`_min_edge_for`/`_min_odds_for` — no drift), writes a window per
   upcoming 1x2 + O/U 2.5 fixture×selection. First run: 1,002 windows.
2. **Stage B:** a post-sweep matcher job that emits `shadow_bets` for in-window
   book prices. Run it **paper/OFF** alongside the current mirror.
3. **VALIDATE:** backtest realized OOS ROI on the Coolbet-native set at the
   current gates; **re-tune edge/odds floors on this universe** (do NOT assume the
   reference-odds floors transfer). Confirm it beats the narrow mirror on ROI, not
   just on volume. Guard against the model-error inflation the caveat describes.
4. **Cut over:** once validated, retire the `simulated_bets`-copy mirrors
   (`coolbet_model_1x2_shadow`, `coolbet_model_ou_shadow`) in favour of the
   matcher, and fold the bots into the (signal × market × book) scheme
   (COOLBET-BOTS-REFACTOR).
5. **Scale:** add Unibet as a second book on Stage B once its placeable feed
   exists (UNIBET-AUTO-PLACER).

## What this absorbs / relates to

- **Replaces** the `simulated_bets`-copy mirror jobs (the selection bug above).
- **Absorbs** COOLBET-BOTS-REFACTOR (the clean (signal × market × book) unit
  falls out of this) and the market×book matrix vision.
- **Feeds** UNIBET-AUTO-PLACER: Unibet reuses Stage A untouched.
- **Trims the FS footprint** indirectly: Stage A knowing we only bet 1x2 + O/U
  means the sweep only needs those markets' odds, not the whole 1000-market
  sidebets board (ties into COOLBET-SWEEP-SCOPE-REDESIGN + the Imperva work).

## Backtest result — the wide selection is NOT profitable at current gates (2026-09-09)

`scripts/trigger_engine_backtest.py` — held-out OOS (calibration fit on TRAIN,
windows + Coolbet's historical odds + grading on untouched TEST):

| Market | Picks (TEST) | ROI (OOS) | Win% | Avg odds | Fold-robust? |
|---|---|---|---|---|---|
| **1x2** | 1,470 | **−21.2%** | 14% | 5.87 | ❌ negative every fold |
| **O/U 2.5** | 228 | +4.3% | 39% | 2.69 | ❌ not robust (+15/+2/−5) |

**The naive wide 1x2 trigger selection LOSES −21% out of sample** — the caveat
above, realised. At Coolbet's higher odds many more games clear "13% edge," but
they are exactly the spots where the model over-estimates vs the sharper market
(it wins 14% at 5.87 odds where it needs 17%). **You cannot profitably widen
selection with a model that is less accurate than the book** (our 1x2/OU AUC is
below the market's). This also deflates the earlier narrow "+48% 1x2" number as a
small-n / selection-favourable cohort artifact.

**Consequence — Stage B stays PAPER; do NOT promote it to real money.** The
architecture is right, but the wide selection at the current gates is a loser. To
make it bettable one of these must happen first, each proven on this backtest:
1. **A much tighter gate** (higher edge floor AND/OR a narrower odds band) that
   isolates a fold-robust profitable core — the backtest is the search tool.
2. **A sharper model** (features/calibration — MODEL-EDGE-IMPROVEMENT) so the
   model actually beats the book on the wider set.
3. Accept that only a NARROW band is profitable and gate to it.

The paper matcher + backtest are now the instruments to find that gate. Until one
of the above validates, the live real-money path stays the existing (narrow)
model-edge bots, and the trigger engine only accrues paper for measurement.
