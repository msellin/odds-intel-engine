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
