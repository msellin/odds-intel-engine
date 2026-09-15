# Pre-registration — OWN sharp-tight instrument (`bot_trigger_1x2_sharp_tight_v1`)

**Registered 2026-09-15, before the bot's first pick. Locked.**

## What this is

An **instrument**, not a strategy. It exists to measure one hypothesis that two
independent research rounds agreed was the only survivor — and that both also
believed was probably luck.

```
anchor   Shin de-vig of the Pinnacle 1x2 triple
gate     P_shin − 1/book_odds  ≥  2%          (PROBABILITY difference)
odds     ≤ 2.50
books    Coolbet, Epicbet, Unibet-Site — pooled, one bot
staking  PAPER. Not in PLACEABLE_BOTS, no placer toggle, cannot stake.
```

## Why it exists at all

The first configuration sweep graded **70,200 cells** and concluded nothing
works. It could not have found this one: it swept a constant **expected-ROI**
floor (`P × odds − 1`) while the live gate is a constant **probability-difference**
floor (`P − 1/odds`). Since `roi_edge = prob_edge × odds`, a constant probability
floor is a **curve in odds** — no cell of a constant-ROI grid can express it, and
adding grid dimensions cannot fix a missing functional form
(`ANALYSIS_GOTCHAS` §42). The honest restatement of that sweep is *"none of
70,200 configurations **in that grid**"*.

Swept on the right functional form, this is the only family that survives:

| | n | ROI | 95% CI | folds | OOS |
|---|---|---|---|---|---|
| pooled, prob-edge ≥2%, odds ≤2.50 | 225 | **+17.07%** | [+4.18, +29.95] | no losing fold | **+23.40%** |

## Why it is paper, and why the prior is that this is luck

Stated up front so it cannot be quietly forgotten if the ROI looks good:

1. **It is a twelve-day effect.** +0.99% (n=79) before 2026-09-02 against
   **+25.76%** (n=146) after. Not a line-shopping artefact: **Coolbet alone**, a
   constant pool over 37 days, jumps the same way — **+0.99% (n=79) → +40.02%
   (n=46)**. Not an alignment artefact either: tight and loose quote gaps agree
   *within* each era.
2. **The CLV contradicts the ROI.** On the same legs, margin-corrected
   **own-book** closing-line value reads **−5.36% to −7.56%** beside ROIs of
   +19% to +42%. A randomly-chosen leg is ≈ **−7.2%**, so the selection buys
   about **1.9pp** of closing-line value. That is real — it matches the anchor's
   measured calibration — and it is **nowhere near the 7–8% vig it must clear**.
3. `ANALYSIS_GOTCHAS` §8: CLV converges ~200× faster than ROI. When they
   disagree at n=225, **believe the CLV**.

A +25% ROI beside a −6% EV is the signature of a small sample landing well.

## Stopping rules (LOCKED)

Evaluated on this bot's own settled picks, **own-book** closing line only
(`closing_bookmaker IS NOT NULL` — rows from the retired arbitrary-book fallback
are excluded, see `SHADOW-CLV-NO-ARBITRARY-FALLBACK`), margin corrected per row
via `closing_book_margin()`.

| checkpoint | criterion | action |
|---|---|---|
| n ≥ 300 | margin-corrected own-book CLV **> 0**, CI excluding 0 | **PROMOTE** to a real-money candidate — owner decision, still gated on the OWN kill criterion |
| n ≥ 300 | margin-corrected own-book CLV **< −2%** | **RETIRE** |
| n ≥ 300 | anything between | **KEEP OBSERVING.** Do NOT re-cut the rule. |

**ROI may never promote this bot, at any value.** That is not conservatism, it
is arithmetic: per-bet return sd ≈ 1.3, so confirming a true +3% ROI at 80%
power needs ≈15,600 settled bets. At this bot's volume that is years. ROI cannot
resolve; CLV can. A +40% ROI at n=300 is **not** grounds for promotion and must
not be presented as such.

## What would invalidate the test

* Any change to the gate, the odds cap, the book set or the anchor. Changing any
  of them starts a new instrument with a new name and a new start date.
* The price-ratio band effect is **hygiene, not edge** — it reproduces under a
  junk anchor (−17.23%). Keep the 20% cap; do **not** tighten to 15% chasing
  the number.
* If `bot_coolbet_trigger_sharp_1x2_v1` (the 3% gate, already significantly
  **negative** at −2.84%, t=−2.85 on own-book CLV) is retired, that does not
  retire this one — different gate, and the whole point is that the gate is what
  differs.

## Negative control

The junk-anchor arm already running in `own_sharp_config_sweep.py --control` is
the null for this family. Note what it is **not**: the sweep's original
"junk beats real" claim was withdrawn on verification — the two arms selected
different populations (price-ratio median +15% vs −0.0%, n 550 vs 5,881). At a
**matched** gate and band the real anchor beats junk by **18–33pp in every
cell**. Any future control must match the gate before comparing.

## Amendment 1 — 2026-09-15 — decision-quote freshness (measurement fix, not a rule change)

`OWN-ANCHOR-GATE-VERIFICATION` (2026-09-14) showed the slope this instrument
measures reads **+1.31** on stale decision quotes and **+0.35** once the quote is
required to be ≤60 min old; 26–45% of legs were priced off a quote >4 h stale,
and across a >12 h gap 72% of Coolbet quotes had moved. A stale quote is a price
nobody could take, so a leg priced on one measures nothing about the strategy.

From this date:
* every leg records `shadow_bets.decision_quote_age_min` (migration 355);
* the matcher **refuses** legs whose book quote is older than **60 min**
  (`pick_trigger_matcher.FRESHNESS_MAX_AGE_MIN["sharp_1x2_tight"]`, smoke
  `SHARP-TIGHT-FRESHNESS-REFUSES-STALE`);
* the stopping rule is evaluated on **fresh legs only**, via
  `scripts/sharp_tight_slope.py`: at **n_fresh ≥ 300** — slope CI includes 0 →
  RETIRE; CI excludes 0 AND zero-crossing ≤ +6pp AND ≥2 fresh legs/day → Phase 3
  candidate (owner decision); otherwise keep observing. ROI never promotes.

The gate (prob-edge ≥2%, odds ≤2.50, pooled three books) is unchanged. Legs
written before this date have `decision_quote_age_min IS NULL` and are excluded
from the fresh count by construction.
