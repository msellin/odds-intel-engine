# CLV-BY-ODDS DE-VIG ARTEFACT CHECK — pre-registered 2026-09-05

## The claim under test

De-vigged Pinnacle CLV rises monotonically with odds on our settled picks
(active bots, prematch, singles):

| odds | n | CLV | t |
|---|---|---|---|
| 1.0-1.8 | 52 | +1.29% | +1.40 |
| 1.8-2.2 | 65 | +0.91% | +0.63 |
| 2.2-2.8 | 175 | +0.53% | +0.54 |
| 2.8-3.5 | 213 | +4.53% | +4.16 |
| 3.5+ | 111 | +16.57% | +5.78 |

Read literally: "our edge lives above ~2.8 and is zero below it." That would be
directly actionable for the operator's own staking, which is why it must be
checked before being acted on.

## Why it might not be real

`workers/model/devig.py` documents the exact hazard: proportional de-vig
"manufactures apparent edge on exactly the selections that lose — draws and away
dogs", because bookmakers load margin onto longshots. The pipeline correctly
uses Shin. But Shin is a MODEL of that loading, not a measurement of it, so a
residual longshot bias can survive it — and it would produce precisely this
shape: CLV rising with odds.

## Falsifiable predictions

**If the slope is a de-vig/measurement artefact:**
- P1. The same rising-with-odds slope appears on selections we did NOT pick
  (a placebo cohort has no reason to carry our edge).
- P2. The slope is much steeper under proportional de-vig than under Shin,
  i.e. the method visibly drives it.
- P3. The long-odds buckets are dominated by draws/away-dogs in 3-way markets,
  the selections devig.py names.

**If the edge is real:**
- P4. Placebo cohort is flat while our picks slope.
- P5. The slope survives on 2-way markets (o/u), where the docstring says
  proportional-vs-Shin barely differs and favourite-longshot has "nowhere to
  hide".
- P6. It is visible in ROI direction too, not only CLV (weak test — ROI is
  underpowered here, sd 1.42, so treat as directional only).

## Decision rule (set BEFORE looking)

- Confirmed artefact if P1 holds (placebo slopes similarly).
- Real but confounded if placebo is flat but the effect vanishes on 2-way
  markets (i.e. it is a 3-way-specific de-vig residual).
- Real if placebo is flat AND the effect survives in o/u.

## Known caveats going in

- `clv_pinnacle_devig` coverage varies by bucket: 88.1 / 97.0 / 90.2 / **74.5** /
  82.2 percent. The strongest bucket has the WORST coverage, so a selection
  effect on which rows have a Pinnacle close is live.
- Column is written only by `scripts/backfill_simulated_clv_devig.py`, never by
  settlement — so it is a one-off backfill, not a maintained column.
- Do not read ROI at these n (gotcha 8).
