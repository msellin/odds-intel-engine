# /picks + Telegram — open items before the next batch

Parked 2026-09-14 while the OWN track is priority. Work through these BEFORE
publishing tomorrow's picks and BEFORE publishing anything about the method
change.

## 1. The draw-probability finding — the rule is sounder than it looked

Owner spotted that Erbil v Al-Karma was 2.80 on Unibet and asked what edge that
was. Working it through produced a genuine result, and a correction of my own
first reaction.

**At Unibet there was no bet at all:** home @2.80 = −13.4%, draw @2.35 = −3.9%,
away @3.00 = −15.4%. All three negative. We published draw @3.00 at **Bet365**.

Every book on the fixture:

| book | home | draw | away | overround |
|---|---|---|---|---|
| Pinnacle | 2.86 | **2.21** | 3.11 | 12.37% |
| Unibet-Site | 2.80 | 2.35 | 3.00 | 11.60% |
| Betano | 2.75 | 2.45 | 2.95 | 11.08% |
| 1xBet / Marathonbet | 2.62 | 2.65 | 2.83 | 11.24% |
| William Hill | 2.50 | 2.80 | 2.62 | 13.88% |
| Bet365 | 2.40 | **3.00** | 2.75 | 11.36% |

The draw spans **2.21 to 3.00**. My first reading was that a de-vigged draw
probability of **0.4089** is implausible — football draws run 22–30%. **That was
wrong, and the data says so:**

* Pinnacle prices a draw below 2.50 on **0.46%** of fixtures (379 of 81,749).
* When it does, the draw **actually landed 42.5%** of the time (71 of 167 settled).

So the anchor is well calibrated on exactly this rare case. Pinnacle identifies
high-draw fixtures accurately and the soft books do not follow it down. **That is
a real edge mechanism, not noise**, and it is the strongest single argument for
the sharp anchor found so far.

**But do not over-read it.** The pick still sits in the 8–15% edge band where the
measured realisation ratio is **0.28** — the band where big sharp edges live on
wide-margin triples. Both things are true: the mechanism is real, and the band
is the weakest one. **Next step: re-measure the realisation ratio SPLIT BY
whether the selection is a Pinnacle-identified rare case (draw < 2.50) or just a
wide-triple artefact.** If the rare-draw subset carries the band, that is a
filter worth having; if not, the band should be capped.

## 2. Channel bio is now false — fix before the next batch

The @oddsintelpicks bio reads:

> "Pre-match football picks **from our model**. Every pick logged before kickoff,
> verified on GitHub + Bitcoin. Live track record: oddsintel.app/performance"

Two problems, both ours:

* **"from our model" is no longer true.** The published rule uses NO model — it
  prices against the de-vigged sharp line. The residual test fits α = 0.0000 for
  the model over the market.
* **"Live track record: /performance" is misleading.** That page carries the
  model-anchored record from 2 surviving bots of 46, priced on an edge the O/U
  calibrator manufactured. It does not describe the method now being published.

The "logged before kickoff, verified on GitHub + Bitcoin" half is still true and
worth keeping — it is the strongest honest claim we have.

## 3. Show the old-metric comparison in brackets (owner's idea)

For a few days/weeks, render each pick as:

> 📈 Edge vs sharp line: +5.3% (comparable to a previous ~12% model edge)

so readers can recalibrate. **Before shipping this, settle whether the mapping is
defensible.** The bucket comparison I have is directionally clear but the CIs
overlap badly:

| claimed band | OLD model edge (realised money) | NEW sharp edge (backtest) |
|---|---|---|
| 3–5% | n=219 −5.56% [−22.0, +10.9] | n=522 +3.42% [−7.4, +14.3] |
| 5–8% | n=831 −3.81% [−12.7, +5.1] | n=359 **+9.19%** [−4.0, +22.4] |
| 8–10% | n=817 −4.91% [−13.6, +3.7] | n=103 +9.71% [−14.2, +33.6] |
| 10–13% | n=953 +2.06% [−6.3, +10.4] | n=111 −16.72% [−41.4, +8.0] |

A specific numeric equivalence ("5% now ≈ 12% before") is **not** supported at
these CIs. Options: (a) state it qualitatively without numbers, (b) wait for
forward-test data, or (c) publish the realisation-ratio framing (12 cents on the
dollar vs 74) which is a like-for-like statement about *method*, not a promise
about a specific pick. **(c) is the defensible one.**

## 4. Audit the methodology pages before publishing the method change

If we are going to publish how the method works, every page describing the old
method becomes a liability. Sweep at minimum: `/performance`, any "how it works"
/ methodology page, the channel bio, the site's own edge glossary, and
`docs/SYSTEM_MAP.md` §1 (already corrected 2026-09-14).

## 5. Settlement must land before tomorrow's batch

`picks_forward_test.outcome` / `clv_margin_corrected` are unpopulated. Today's 8
settle tonight. Without settlement the pre-registered stopping rules (n=200/400
on CLV, n=800 on ROI) can never fire and the forward test is inert. Being handled
by the parallel PICKS agent — **verify it before publishing batch 2.**

---

## 6. ⚠️ "EDGE" MEANS TWO DIFFERENT THINGS IN OUR OWN CODE — fix before batch 2

Found 2026-09-14 while diagnosing why the OWN trigger bots produced 4 picks when
9 qualified. They did not disagree about the fixtures; they disagree about what
"edge" means.

| where | formula | Erbil draw @3.00 |
|---|---|---|
| `pick_generator.py:239`, `pick_triggers.min_odds`, `SYSTEM_MAP` §1 | `P − 1/odds` (**probability difference**) | **+7.6%** |
| `scripts/publish_picks_forward_test.py` (what we PUBLISHED today) | `P × odds − 1` (**expected ROI**) | **+22.7%** |

Both were called "edge". They are not the same quantity and they do not even
rank picks the same way — the ROI form is looser at long odds by a factor of the
odds themselves (`ROI_edge = prob_edge × odds`). That is why a 3% floor on one
admits roughly twice the picks of a 3% floor on the other.

**This invalidated my first model-vs-sharp comparison** (I compared a probability
difference against an ROI and called the ratio a "realisation rate"). Corrected,
in one unit:

| old model edge (prob diff) | implied expected ROI | ACTUALLY realised | ratio |
|---|---|---|---|
| +6.16% (n=831) | +17.58% | **−3.81%** | −0.22 |
| +8.50% (n=817) | +22.66% | **−4.91%** | −0.22 |
| +10.81% (n=953) | +29.19% | **+2.06%** | +0.07 |
| +15.51% (n=925) | +45.21% | **+5.76%** | +0.13 |

versus the sharp rule, whose edge already IS expected ROI: claimed +7.43%,
realised +5.54%, ratio **0.74**.

**The honest, positive, publishable sentence:**

> A "12% model edge" was a promise of about +30% return that delivered about
> +2%. A "6% sharp edge" is a promise of +6% that delivers about +5.5%. The new
> number is smaller because it is honest.

### Decisions needed before batch 2

1. **Pick ONE definition and name it.** Recommendation: keep expected ROI for the
   public line — it is the number a reader can act on — but **rename the label**
   from "Edge vs sharp line" to "Expected return". Leave `P − 1/odds` as the
   internal gate quantity and never print it to readers under the same word.
2. **Reconcile the floors.** A 3% ROI floor and a 3% probability floor are
   different gates. Decide which the forward test is pre-registered on (it is
   currently ROI — `publish_picks_forward_test.py` — and the pre-registration
   doc must say so explicitly) and make `pick_triggers` agree or be explicitly
   labelled as a different strategy.
3. **Correct `docs/SYSTEM_MAP.md` §1**, which defines sharp edge as
   `P_sharp − 1/book_odds` while the live publisher uses the ROI form.
4. **Note the distribution point.** Today's published picks read BIGGER than the
   old ones (+22.7%, +20.8% vs the old 8–15%), not smaller. The story is not
   "numbers got smaller" — it is "the distribution got wider, and the SMALL ones
   are the trustworthy ones" (realisation ratio 1.14–1.26 in the 3–8% band,
   0.28 above 8%).
