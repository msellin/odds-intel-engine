# 🤖 OWN — exhaustive SHARP-edge configuration sweep, 2026-09-14

> ## ⚠️ PRELIMINARY — committed early to unblock CI, sweep still running
>
> The smoke test `OWN-SHARP-SWEEP-ASSEMBLE` asserts this file exists, and it was
> swept into `f0a4fac`/`ae7c86a` by a parallel agent's `git add -A` before the
> analysis finished (ANALYSIS_GOTCHAS §46 — the trap it documents, happening
> again). Everything in §1–§7 is **final and reproducible**. Two additions the
> coordinator asked for mid-run are **not yet in**: the **price-ratio cap**
> dimension and the switch of the bot-CLV table from a flat m=7.6% to the
> **closing book's own per-fixture margin**. Both are marked 🚧 below. Nothing
> already stated is expected to move — the recommendation rests on §3 and §4,
> neither of which touches either addition.

**Script:** `scripts/own_sharp_config_sweep.py` (read-only).
**Smoke test:** `OWN-SHARP-SWEEP-ASSEMBLE`.

```bash
python3 scripts/own_sharp_config_sweep.py --days 150 --diagnostics --bot-clv
python3 scripts/own_sharp_config_sweep.py --days 150 --control      # negative control
python3 scripts/own_sharp_config_sweep.py --days 150 --align-min 15 # alignment ladder
```

---

## 1. The question and the answer

**Question.** For bets we place ourselves at EMTA-legal, self-scraped books
(Coolbet, Epicbet, Unibet-Site), is there any configuration of

```
edge = P_shin(Shin-de-vigged Pinnacle) × book_price − 1
```

with enough volume **and** a confidence interval that excludes zero?

**Answer: no. None of 14,040 configurations clears the bar, and the family's
central estimate at these three books is not merely indistinguishable from zero
— it is negative and worse than flat-backing every price the same books offer.**

| construction, all markets pooled, lead 0, alignment ≤60 min | n | ROI | 95% CI (clustered on fixture) |
|---|---|---|---|
| flat-back **every** aligned leg (the vig baseline) | 31,321 | **−7.5%** | decisive |
| the sharp rule, edge ≥3% & odds ≤4.0, at **Coolbet** | 436 | **−13.58%** | [−25.4, −1.8] |
| … at **Epicbet** | 246 | +4.48% | [−10.6, +19.6] |
| … at **Unibet-Site** | 102 | **−23.40%** | [−45.4, −1.5] |
| … **pooled** (best aligned price of the three) | 695 | **−9.55%** | [−18.8, −0.3] |
| … pooled, **top-8/day by edge** (the published rule) | 232 | −9.78% | [−26.0, +6.5] |

The sharp rule at our own books does not beat the vig. It underperforms it.

## 2. Why this disagrees with the number I was given

The brief states: *"time-aligned, betting at those three books, edge≥3%
odds≤4.0: n=92, ROI +16.00%"*. I could not reproduce it, and the construction
above is the one I can defend. Three differences I can name:

1. **n.** The same filter on my leg set is n=695 pooled, n=436/246/102 per book.
   An n of 92 implies a much narrower population — most likely fixtures where
   *all three* books have an aligned quote, which is essentially the Unibet-Site
   era alone (its first row is **2026-09-09**, five days before this was written).
2. **The daily cap is a ranking, not a filter,** so it cannot be a grid cell. I
   report it separately (last row above). It does not rescue the number.
3. **The books disagree with each other.** Epicbet is the only one of the three
   with a positive point estimate, and it is +4.5% with a CI 30 points wide. A
   pooled +16% would have to come from somewhere; it is not in any book here.

I am not asserting the +16% is wrong — I am asserting I cannot reproduce it from
`odds_snapshots` with a construction that survives §1–§7 below, and that the
figure has no committed script behind it (PLAN_AFTER_AUDITS §6, the rule that
exists for exactly this).

## 3. The negative control PASSES — so the harness can be read

Two independent checks, both required before any other number means anything.

**(a) The vig dipstick.** Flat-backing every aligned leg must return
≈ `−m/(1+m)` where `m` is that book's own closing margin. It does, on every
book and market:

| book | market | n | measured ROI | book's close margin | predicted |
|---|---|---|---|---|---|
| Coolbet | 1x2 | 10,538 | −9.73% | 7.69% | −7.14% |
| Coolbet | O/U 2.5 | 6,411 | −7.23% | 7.95% | −7.37% |
| Epicbet | 1x2 | 5,465 | −10.61% | 7.93% | −7.35% |
| Epicbet | O/U 2.5 | 3,400 | −6.10% | 6.94% | −6.49% |
| Unibet-Site | 1x2 | 3,301 | −8.95% | 8.47% | −7.81% |
| Unibet-Site | O/U 2.5 | 1,566 | −5.70% | 7.12% | −6.64% |

O/U lands on prediction. 1x2 runs ~2pp worse than the margin alone, which is the
favourite–longshot bias: flat-staking all three outcomes overweights the longshot
in unit terms. Expected, and in the right direction.

**(b) The junk anchor.** Identical harness, identical legs, identical outcomes —
but each leg's de-vigged anchor is taken from a **different fixture's** Pinnacle
triple (seeded permutation within market). It should lose roughly the vig:

| | n | ROI |
|---|---|---|
| junk anchor, publish rule, pooled all markets | 9,022 | **−5.80%** (t=−4.54) |
| junk anchor, Coolbet | 7,340 | −6.28% (t=−4.36) |
| junk anchor, Epicbet | 3,798 | −2.42% |
| junk anchor, Unibet-Site | 1,613 | +0.01% |

It loses about the vig. **The harness is sound.**

And the comparison that matters: the **real** anchor pooled reads **−9.55%**, the
**junk** anchor pooled reads **−5.80%**. Selecting on a real de-vigged Pinnacle
overlay, at these books, did not beat selecting on a randomly-permuted one.

**The control also demonstrates the multiple-comparisons hazard in situ.** With
a junk anchor, **2,466 of 10,531** evaluated cells (23.4%) have a clustered CI
excluding zero. Every one is noise by construction. Any grid search over this
data that reports "a cell with p<0.05" and stops there has reported nothing.

## 4. What the grid actually found

14,040 cells tested: 6 edge floors × 15 odds bands × 3 lead times × 4 book
settings (3 books + pooled) × 13 market/selection combinations. 671 reached
n ≥ 100 on the full sample.

**91 of 671 (13.6%) exclude zero.** Under the control's own null that number is
not remarkable. Every one of the survivors fails at least one of the three tests
that separate a finding from a fluctuation:

| survivor | n | ROI | folds (time-ordered thirds) | margin-corrected own-book CLV |
|---|---|---|---|---|
| POOLED 1x2 ALL, edge≥1%, 1.01–2.50, lead 60 | 121 | +25.09% | **−14.5%** / +35.3% / +31.9% | **−5.43%** |
| POOLED 1x2 ALL, edge≥1%, 1.50–2.50, lead 60 | 106 | +25.51% | **−18.2%** / +38.5% / +31.6% | **−5.09%** |
| POOLED 1x2 home, edge≥1%, 1.01–8.00, lead 60 | 149 | +26.66% | **−2.0%** / +37.1% / +29.3% | **−5.63%** |
| Epicbet O/U 2.5 ALL, edge≥1%, 1.01–4.00, lead 0 | 102 | +26.48% | +39.6% / +30.8% / **−5.9%** | n/a |
| Epicbet 1x2 ALL, edge≥1%, 1.01–2.50, lead 0 | 122 | +18.03% | +27.3% / +10.4% / +15.9% | n/a |

Three things kill them:

1. **Not fold-robust.** Every 1x2 survivor loses in fold 1. The one cell that is
   positive in all three folds (Epicbet 1x2 ALL) has a CI of [+0.6, +35.5] — a
   34-point-wide interval whose lower bound is 0.55pp from zero, at n=122.
2. **The CLV contradicts the ROI.** On the same legs, own-book closing-line value
   is **negative in every case** once corrected for the closing book's margin
   (−5.1% to −5.6%). A +25% ROI alongside a −5% EV is the signature of a small
   sample landing well, not of an edge. CLV converges ~200× faster than ROI
   (ANALYSIS_GOTCHAS §8) — believe the CLV.
3. **They live at `edge ≥ 1%`, i.e. essentially no floor**, and at `lead 60`,
   which after retention (§7) is an era selection rather than a timing choice.

**Out-of-sample.** Selecting finalists on a per-book time-ordered in-sample 70%
and measuring them on the untouched 30%: the best IS cells are POOLED 1x2 home
at +24.9% IS → +28.4% OOS **at n=41** (no power), while the two IS cells that
reach n≥100 on both sides (POOLED 1x2 ALL, edge≥3%, ≤2.50) go **+18.2% IS →
+3.0% OOS**. Epicbet 1x2 home goes **+16.8% IS → −15.4% OOS**.

**Power.** Per-bet return sd is 1.3–1.6 here. The best cells report needing
n = 118–257 to detect their *own* point estimates at 80% power, which they
nearly reach — but that is the power to detect a +25% ROI, and nobody believes
+25% is the truth. To detect a **+3%** true ROI at 80% power needs
**≈ 15,000 settled bets**, which at the 3–11 picks/day these cells produce is
**4 to 14 years**.

## 5. Alignment, and the odds-band contradiction

**Alignment ladder** (POOLED 1x2, edge≥3%, odds≤4.0):

| anchor↔bet gap | n | ROI | span |
|---|---|---|---|
| unbounded ("unaligned") | 695 | −4.52% | 117d |
| ≤ 60 min | 420 | −10.32% | 38d |
| ≤ 15 min | 258 | −7.12% | 38d |

Negative at every alignment. The unaligned row spans a longer period and is
therefore not a like-for-like comparison — which is itself the point: the
*apparent* improvement from relaxing alignment is a sample change, not a result.

**The odds dimension, in both directions.** The coordinator flagged that the
live OWN sharp bots show short odds (1.0–2.0) profitable and the middle band
negative, against the PICKS rule's cap at 4.0 on the opposite logic. On this
backtest the two are **different populations, and neither pattern survives**:

* The cells that reach significance at the OWN books are **short-priced**
  (ceiling 2.50) — consistent with the bots' short-band result, not with a
  longshot story.
* But those same cells are the ones with **negative margin-corrected CLV** and a
  losing first fold. The short band looks good on ROI and bad on CLV.
* Raising the **floor** to 2.80 (the placer's live gate) leaves no cell with
  n ≥ 100 and a CI excluding zero in any market at any book.

So: **the short-odds pattern reproduces, the long-odds pattern does not, and the
short-odds pattern does not survive out-of-sample or on CLV.** Neither the 4.0
ceiling nor its inverse is supported by this data. Do not change either on the
strength of it.

🚧 **Still to run:** the price-ratio cap as a sweep dimension
(none / 35% / 25% / 20% / 15%). The coordinator's measurement puts the leak in
the 20–35% band, below production's 35% filter. My harness already applies the
production guard (Pinnacle × 1.35 for 1x2, × 1.30 for O/U) per leg, so the
20–35% band is **inside** my sample and tightening it can only help. That makes
it a candidate for the one thing that could move §1 — it is the next run.

## 6. 🚧 The claim this sweep was built on needs correcting

The brief's premise is that the four SHARP-anchored trigger bots are the fleet's
only CLV-positive engines, at +0.84% / +5.24% / +0.27% / +2.09% margin-corrected
EV. **Those figures reproduce exactly** from `shadow_bets_unique` — and they are
still wrong, for two reasons that the split below makes visible.

`settle_shadow_bets` prefers the bet's own book for the close
(SHADOW-CLV-BOOKMAKER-FIX-2026-08-26) but **falls back to the unfiltered
`get_closing_odds`**, whose own docstring says comparing a price against an
arbitrary book "makes the resulting CLV structurally positive regardless of
whether the bet had any edge". Rows where that fallback fired carry
`closing_bookmaker IS NULL` — and they are 26–48% of each bot's CLV rows:

| bot | close anchored at | n | raw CLV | EV (m=7.6% flat) | t | span |
|---|---|---|---|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | **Coolbet** | 66 | +4.51% | **−2.87%** | −3.13 | 09-11..09-13 |
| " | *(unanchored)* | 42 | +14.78% | +6.67% | +3.65 | 09-09..09-11 |
| `bot_coolbet_trigger_sharp_ou_v1` | **Coolbet** | 20 | +6.35% | **−1.16%** | −0.68 | 09-12..09-13 |
| " | *(unanchored)* | 12 | +10.47% | +2.67% | +1.40 | 09-09..09-11 |
| `bot_unibet_trigger_sharp_1x2_v1` | **Unibet-Site** | 67 | +12.05% | **+4.13%** | +2.12 | 09-11..09-13 |
| " | *(unanchored)* | 23 | +16.69% | +8.45% | +2.60 | 09-09..09-11 |
| `bot_unibet_trigger_sharp_ou_v1` | **Unibet-Site** | 11 | +8.38% | **+0.72%** | +0.25 | 09-11..09-13 |
| " | *(unanchored)* | 10 | +11.46% | +3.58% | +1.19 | 09-09..09-11 |

Three consequences:

1. **The unanchored rows read 4–10pp higher than the own-book rows on every
   bot.** That is not a coincidence in four of four; it is the artefact the
   docstring predicts. Pooling the two is what produced the headline figures.
2. **On own-book close, `bot_coolbet_trigger_sharp_1x2_v1` is significantly
   NEGATIVE** (−2.87%, t=−3.13), not +0.84%. Only `bot_unibet_trigger_sharp_1x2_v1`
   is positive with |t| > 2, at n=67.
3. **The whole evidence base is 2026-09-09 → 2026-09-13 — five days, and the
   own-book-anchored part is three.** A CLV measured over three days is not a
   track record. `odds_at_pick` equals `odds_at_pick_live` on every one of these
   rows, so at least the price basis is clean.

🚧 These EVs still use the flat m = 7.6%. The correct denominator is the closing
book's **own** margin per fixture (`settlement.closing_book_margin()`), and the
per-book spread (Coolbet 7.8%, Epicbet 8.0%, Unibet-Site 10.5%) is wide enough to
move the Unibet numbers materially — plausibly to flip
`bot_unibet_trigger_sharp_1x2_v1` from +4.1% toward +1.4%. The sweep's own
leg-level CLV already uses the per-fixture book margin; only this table does not.

## 7. What this data cannot tell you

**The usable window is 38 days, not 150.** `prune_old_simple` keeps only
`is_opening`, `is_closing` and the latest pre-kickoff row per series after 7 days
(ANALYSIS_GOTCHAS §59), and our books had almost no `is_closing` anchors before
2026-09-11. Worse, pre-kickoff Coolbet 1x2 coverage is effectively **zero before
2026-08-03** (2–9 fixtures/week, against 300–2,000 after). So:

| book | first usable pre-kickoff, time-alignable data |
|---|---|
| Coolbet | 2026-08-03 |
| Epicbet | 2026-08-27 |
| Unibet-Site | **2026-09-09** |

**The lead-time dimension is not honestly sweepable.** Outside the 7-day
retention window each series has exactly one surviving quote, so requiring a
quote ≥60 or ≥240 min before kickoff selects on which era a fixture is from. The
`lead 60` cells in §4 are contaminated by exactly this, which is a second reason
not to believe them.

**Own-book CLV is undefined on `lead 0` legs** — the leg *is* the last surviving
pre-kickoff row, so its CLV is 0 by construction. It is computed only where a
strictly later own-book quote exists, which is why several cells show `n/a`.

## 8. Recommendation

**Do not build, promote, or stake anything on the sharp-edge rule at Coolbet,
Epicbet or Unibet-Site. None of the 14,040 configurations clears the bar.**

Specifically:

* **Do not change any floor, cap or gate on the strength of this sweep.** That
  includes the 3% sharp floor, the 4.0 odds ceiling, the 2.80 placer floor and
  the 13% home-dog floor. The sweep supports none of them and refutes none of
  them; it says the whole family is indistinguishable from the vig.
* **Do not promote `bot_unibet_trigger_sharp_1x2_v1`** on its +4.1% own-book EV.
  n=67 over three days, and its Coolbet twin on the identical rule is
  significantly negative. Two bots on one rule pointing opposite ways at n≈66 is
  a sample-size statement, not a book-selection finding.
* **Do fix the CLV fallback.** `settle_shadow_bets` writing an arbitrary-book
  close into `clv` when the own book has no closing row is manufacturing 4–10pp
  of apparent edge on the exact bots the OWN path is being judged on. Leaving
  `clv` NULL is the honest behaviour, and `closing_bookmaker` already records
  which happened. **This is the single highest-value change in this document**
  and it is a correctness fix, not a strategy change. (Filed, not fixed here —
  `settlement.py` is being edited by another agent.)
* **Keep collecting.** Every conclusion here is bounded by a 38-day window and a
  five-day Unibet-Site history. The right move is to let the paper bots run and
  re-read this in eight weeks, not to act now.

**Confidence.** High that no configuration in this grid is demonstrably
profitable — the negative control, the vig baseline and the fold/CLV
contradictions all agree. Moderate that the family is genuinely negative rather
than merely undemonstrated: the pooled −9.55% has a CI that barely excludes zero
and the junk anchor loses less, which is suggestive but not decisive. Low on
anything per-book — Unibet-Site has five days of history and Epicbet's positive
point estimate cannot be separated from noise.

**A negative result is the result.** The OWN automated-betting path was already
closed on structural grounds (`OWN_PATH_VERDICT_2026_09_14.md`: best-of-three
residual overround 5.66%, 2.8× the kill threshold). This sweep is the
complementary test — not "is there dispersion to harvest" but "does the sharp
overlay pay" — and it returns the same answer from the other direction.
