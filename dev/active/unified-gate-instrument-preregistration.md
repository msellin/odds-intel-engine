# UNIFIED-GATE 1x2 INSTRUMENT — pre-registration

Parent row: **`PRIORITY_QUEUE.md` [[#033]] UNIFIED-GATE-1X2**
Bot: `bot_unified_gate_1x2_paper_v1` · Locked **2026-09-22**, before the first pick.

## The question

The owner's hypothesis, in their words:

> *"we have 10% floor, but don't bet on home favs (their odds are below 2.8
> anyways?)... we keep draw and away and home underdogs, but the odds 2.8+ and
> the floor 10% will ensure that nothing suspicious gets past."*

The **home** half is already settled and the mechanism is right: a home favourite
prices under ~2.0, so a 2.80 odds floor excludes home-favs automatically — no
exclusion list, no second edge floor. That beat the alternative proposal and is
not what this instrument is for.

The **draw/away** half has never been tested, and cannot be tested on existing
data.

## Why existing data cannot answer it

At odds ≥ 2.80 the calibrated cohort is **236 HOME out of 240**. Our own
home-only mirror stopped generating draws and aways, so the population that would
have to carry the test is ~98% home.

Every draw/away figure quoted in this project so far inherits that. "Draws are
−31.5% over n=95, negative in every fold" came from a pre-match slice whose
composition was set by which bot happened to be generating, not by the rule under
test; "aways +15.4%, n=364" dissolved on splitting by bot into **retired in-play
bots**, with `inplay_o` (n=14, +452%) carrying the result.

No amount of further analysis on those rows fixes this. Only new rows do. That is
the entire reason this bot exists.

## The rule, locked

| | |
|---|---|
| Market | 1x2 |
| Selections | **home, draw AND away** — no exclusions |
| Edge floor | **10% flat, every selection** |
| Odds floor | **2.80** |
| Anchor | model (`prob_source='predictions'`, per-selection isotonic calibration) |
| Books | Coolbet, Unibet-Site (best clearing book wins, recorded per pick) |
| Edge ceiling | **none** — model-anchored, where a 20% edge is ordinary |
| Stake | paper, 1 unit, never real money, never published |

**The flat floor is the hypothesis, not an oversight.** The registry's
selection-aware floor is 10% home / 13% draw+away. Inheriting it would make the
instrument test the thing it is supposed to be compared against.

## What would change a decision

Pre-registered so the answer cannot be chosen after the fact:

* **PRIMARY metric: margin-corrected own-book CLV**, not ROI. Per-bet return sd
  at these odds is ≈1.3, so a true +3% ROI needs ~15,600 bets to resolve; CLV
  converges ~200× faster (ANALYSIS_GOTCHAS §8). ROI is reported and may never on
  its own promote anything.
* **Minimum n = 300 settled picks, PER SELECTION.** A pooled n=300 that is again
  90% home answers nothing — the failure this instrument exists to escape. If
  draws or aways have not reached 300, the answer for that selection is "still
  unknown", not "no".
* **The comparison is within this bot**, draw vs away vs home at the same gate on
  the same days. Comparing against the old 236-of-240 cohort would reintroduce
  exactly the composition problem being escaped.
* **Adopt a selection into any gate only if** its margin-corrected own-book CLV
  is > 0 at t ≥ 2 on n ≥ 300, AND it holds in each of at least two
  time-ordered folds. One favourable window is not a result — that is how the
  favourite-band row ([[#061]]) produced +8.59% that later came back −6.43%.
* **Report `closing_fresh` alongside every CLV figure.** After [[#024]], a CLV
  computed against a stale own-book quote is a self-comparison scored as zero;
  on the sharp population that was 51 of 93 rows. Any verdict here must state
  what fraction of its sample carried a genuine close.
* **Stopping rule:** if at n ≥ 300 per selection no selection clears, close the
  row as a negative result and do NOT re-run on a longer window.

## What this instrument is NOT

* Not a candidate for real money. It is not in `PLACEABLE_BOTS` and promotion is
  not on the table at any ROI.
* Not a publication feed. `show_on_picks` is FALSE; nothing it emits reaches
  Telegram or `/picks`.
* Not evidence for a 2.80 **publication** floor. That question was measured
  separately on 2026-09-22 and found unsupported on both published arms
  (sharp n=810, t=−0.14; model n=652, t=−0.40), while costing 61% of published
  picks. See [[#033]].

## One dependency worth recording

This instrument **could not have been built correctly before 2026-09-22.** Until
`SHARP-FLOOR-STACKED-ON-MODEL-FLOOR` was fixed that morning ([[#007]]),
`best_price_router.decide_book` re-imposed the selection-aware registry floor on
top of any bot's explicit `edge_floor`. Draws and aways would have run at 13%
while this document and the config both said 10%. It would have produced
clean-looking numbers answering a different question — and nothing would have
flagged it, because no single declared value would have been wrong.
