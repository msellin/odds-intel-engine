# Pre-registration — per-book sharp gate (`OWN-BOOK-SPLIT-GATE`)

**Registered 2026-09-14, before any gate is changed and before the instrument
has taken a single pick under the split. Locked.**

Evidence: `docs/OWN_SEGMENT_SIGNAL_SEARCH_2026_09_14.md` §3.
Parent instrument: `dev/active/own-sharp-tight-preregistration.md`.

## What this is

An **instrument**, not a strategy. It measures ONE hypothesis:

> The sharp anchor's closing-line value is **not constant across our three
> books**. Epicbet's line responds to sharp disagreement at roughly half the
> rate of Coolbet's and Unibet-Site's, so a pooled gate is over-permissive at
> Epicbet and (mildly) over-restrictive at the other two.

```
anchor   Shin de-vig of the Pinnacle complement, as of the decision moment
measure  margin-corrected OWN-BOOK CLV = (1+clv)/(1+m) - 1, m per row
split    one arm per book: Coolbet | Epicbet | Unibet-Site
gate     UNCHANGED at the live value in every arm. This registration changes
         NOTHING about what is picked. It only requires that the CLV ledger be
         cut per book from the first pick onward.
staking  PAPER. Not in PLACEABLE_BOTS. Cannot stake under any outcome below.
```

**The gate is deliberately not moved yet.** Moving it and splitting it in the
same step would confound the two, and §2 of the evidence doc shows the pooled
break-even gate is itself unresolved (+3.1% untrimmed vs +6.0% at a 1% trim).
Measure the split at the incumbent gate first.

## What was measured, and why the prior is still "probably one week of weather"

| window | lead | Coolbet slope | Epicbet slope | Unibet-Site slope |
|---|---|---|---|---|
| 09-07…09-13 | 3h | +1.145 ±0.137 | **+0.578 ±0.233** | +2.062 ±0.391 |
| 09-07…09-13 | 6h | +1.558 ±0.181 | **+0.571 ±0.296** | +1.898 ±0.132 |
| 09-10…09-13 | 3h | +1.564 ±0.172 | **+0.657 ±0.291** | +2.168 ±0.368 |
| 09-10…09-13 | 6h | +1.971 ±0.199 | **+0.753 ±0.335** | +1.898 ±0.132 |

Coolbet − Epicbet: t = +2.09 / +2.85 / +2.68 / +3.13. Unibet-Site − Epicbet:
t = +3.26 / +4.10 / +3.22 / +3.18. Unibet-Site − Coolbet is **not** distinguishable
once the window is matched (t = −0.30 at 6h), and the apparent Unibet-Site
advantage disappears under a 1% trim.

Stated up front so it cannot be quietly forgotten if the split looks good:

1. **It is a seven-day effect, and it cannot currently be anything else.**
   Retention leaves one pre-kickoff row per series at these books after 7 days,
   and that row is the close — so own-book CLV before 2026-09-07 is zero by
   construction, not merely noisy. Every slope above is a within-week estimate.
2. **The three feeds are of very different ages** (Coolbet from 2026-08, Epicbet
   from 2026-09-04, Unibet-Site from 2026-09-09). The matched-window rows exist
   precisely because an unmatched comparison is a book × era confound.
3. **Even the winning arms are negative.** At the live 2% gate the 1%-trimmed
   prediction is Coolbet −2.27%, Unibet-Site −2.33%, Epicbet −5.59%. The
   hypothesis is about the *difference between books*, not about any book being
   placeable. Nothing here makes a bot stakeable.

## Stopping rules (LOCKED)

Evaluated on `shadow_bets_unique`, own-book rows only (`closing_bookmaker IS NOT
NULL` — arbitrary-book fallback rows excluded), margin corrected per row via
`closing_book_margin()`, cluster-robust SEs on `match_id`, **cut per book**.

| checkpoint | criterion | action |
|---|---|---|
| n ≥ 300 **per book arm** | Epicbet's mc-CLV is below the pooled Coolbet+Unibet-Site arm, CI on the **difference** excluding 0 | **SPLIT THE GATE** — raise Epicbet's gate toward its measured break-even; owner decision, still paper |
| n ≥ 300 per arm | the difference CI includes 0 | **KEEP POOLED.** Do not re-cut by lead time, market, or odds band looking for it |
| any arm | that arm's mc-CLV > 0 with CI excluding 0 **and** ≥ 3 distinct weeks **and** the window spans a book-set or model-version change | eligible to be proposed as a real-money candidate — owner decision, still gated on the OWN kill criterion |
| — | anything else | **KEEP OBSERVING** |

**ROI may never promote or split this instrument, at any value.** Per-bet return
sd ≈ 1.3: confirming a true +3% ROI at 80% power needs ≈15,600 settled bets. ROI
cannot resolve this; margin-corrected own-book CLV can.

**A single week may never satisfy the third row**, by construction — the "≥3
distinct weeks spanning a regime change" clause exists because
`SHARP_ANCHOR_AUDIT_2026_09_14` §2 established that the binding constraint is
regime, not n, and accruing rows inside one window tightens a biased estimate
rather than correcting it.

## Whatever test is applied to Epicbet is applied to the others in the same run

`ANALYSIS_GOTCHAS` 60: rejecting a change for failing a bar the incumbent also
fails is status-quo bias dressed as rigour. Every checkpoint above prints all
three arms, or it does not count.

## What would invalidate the test

* Any change to the anchor, the de-vig, the gate value, the odds cap, or the
  book set. Any of them starts a new instrument with a new name and start date.
* A retention change that lengthens the own-book price history **improves** this
  test and does not invalidate it — but the pre-change and post-change windows
  must be reported separately, never pooled, because they measure different
  things (`ANALYSIS_GOTCHAS` 39).
* If the Epicbet gap is later found to track a market-mix difference rather than
  the book (Epicbet quotes a wider market set), the hypothesis is **withdrawn**,
  not re-cut. The matched-window rows control for era, not for market mix.

## Negative control

`scripts/own_anchor_placebo.py` is the null for this family: the same ladder with
the Pinnacle de-vig drawn from a **different fixture in the same odds decile**,
so every mechanical route from the soft price survives and only the information
is destroyed. It returns slope **+0.007, t=+0.22**, flat across all nine bands,
against **+1.314, t=+4.86** for the real anchor. Any future control must match
the gate and the odds stratum before comparing — the withdrawn "junk beats real"
claim came from comparing two arms that had selected different populations.
