# Sharp-anchor audit — findings, and what to do about each

**For:** the agent running the O/U-calibrator / sharp-anchor investigation.
**From:** a parallel read-only audit, 2026-09-14. No code or DB state was changed.
**Status of your Phase 0:** agreed. We found the circularity and the five-day span
independently. Everything below is *additive* to that, except §6 which is me
retracting one of my own claims before it reaches your training-path review.

Each finding is followed by **FIX** — what can actually be done, with the code
already in the repo that does it.

---

## 1. The circularity haircut is bigger than the R² suggests

You regressed realised `clv_pinnacle` on sharp edge at pick: R² = 0.386. A sharper
cut is to split by **how much Pinnacle actually moved** between our pick and its
close. Where Pinnacle is static, *"our book beats Pinnacle now"* and *"our book beat
Pinnacle at close"* are nearly the same sentence, and CLV is guaranteed by the
selection rule rather than discovered by it.

Sharp 1x2 picks, all with both a Pinnacle price at pick time and a Pinnacle close
(n=197, 100% coverage):

| Pinnacle move, pick → close | n | CLV | t |
|---|---|---|---|
| < 0.5% (static) | 88 | **+14.95%** | +12.6 |
| 0.5 – 2% | 27 | +12.24% | +6.7 |
| 2 – 5% | 21 | +13.90% | +11.4 |
| **> 5% (genuinely moved)** | **61** | **+2.42%** | **+1.1** |

Median |move| = 0.98%; **45% of picks move less than 0.5%**. In the only subset where
CLV is an independent measurement, the edge falls to **+2.42% and loses
significance**. That is a harder haircut than +13.18% → +9.21%.

**FIX — make this cut a standing part of bot evaluation, not a one-off.**
Every CLV number this repo produces should be reportable split by Pinnacle
movement. The data needed is already in `odds_snapshots` (Pinnacle price at pick
time and `is_closing`), and the join is ~15 lines — the query used above is
reproducible from `workers/jobs/settlement.py::get_pinnacle_closing_odds` plus a
`timestamp <= pick_time` lookup. Suggested home: a `--pinnacle-moved` cut in
`scripts/bot_segment_table.py`, so no future sweep can report a Pinnacle-anchored
CLV without showing how much of it came from static lines.

---

## 2. `n ≥ 334` is the wrong gate, and waiting for it will not fix the problem

This is the finding most likely to change your plan.

Observed CLV sd on the sharp population = **0.147**. Required n for t = 2:

| true CLV | n needed |
|---|---|
| +13.2% | **5** |
| +9.2% | **10** |
| +5% | 35 |
| +2% | 216 |

**The repo's 334 is calibrated to detecting a ~2% effect.** At the +9.21% you
measured, n=89 already gives t ≈ 5.9 — which is roughly the t you observed. Power
was never the binding constraint.

The binding constraint is **bias**: circularity (§1) and a single regime. Accruing
to n=334 *inside the same window* will tighten a biased estimate, not correct it.
Your three real time folds are the actual fix; the 334 is ritual in this context
and risks spending weeks pointed at the wrong quantity.

**FIX — replace the constant with a power function.** `required_n(effect, sd, t=2)
= (t·sd/effect)²`, exposed as a helper next to the existing CLV tooling, and quoted
with the effect size it assumes. Keep a *time/regime* condition as the real
promotion gate: N distinct weeks, and a fold structure that spans a model version
change and a book-set change, not merely N rows.

---

## 3. The trigger bots are 20% book-unattributed — and that is your n=140 vs n=89 gap

⚠️ *Corrected during this audit: I first read this as 53% of all shadow bets. That
figure was the `closing_bookmaker` column, not `recommended_bookmaker`. Both gaps
are real; the scales are very different and they matter in different places.*

| column | NULL rate | where it bites |
|---|---|---|
| `recommended_bookmaker`, all shadow bets | **1,619 / 159,827 (1%)** | negligible overall |
| `recommended_bookmaker`, **trigger bots** | **488 / 2,445 (20%)** | the bots you are judging |
| `closing_bookmaker`, all shadow bets | **107,072 / 159,827 (67%)** | §4 — the CLV itself |

On the sharp 1x2 bots specifically the rate is ~33% (65 of 198). That is the gap
between your n=140 and n=89: the missing rows have no recorded venue, so they cannot
be priced at an executable book even in principle.

This is `TRIGGER-BOOK-UNATTRIBUTED`, fixed forward 2026-09-11 but never backfilled.
It is not only an analysis inconvenience — per the fix's own note in
`workers/jobs/pick_trigger_matcher.py`, *settlement's closing-price lookup is
per-book, so with none it fell back to "any book" and computed CLV against a price
we never had.*

**FIX — one exact path, one to avoid.**

* **Exact, do this:** for the eight PER-BOOK trigger bots the venue is determined by
  the bot itself (`BOOK_MARKET_BOTS` maps `(book, market, strategy) → bot`).
  Verified: every one has exactly one non-NULL book and it is the bot's own —
  `bot_coolbet_trigger_*` → Coolbet (752 rows), `bot_unibet_trigger_*` →
  Unibet-Site (628 rows), with **488 NULLs recoverable with certainty** from the bot
  name.
* **Do NOT attempt this:** recovering the book by matching the stored price back to
  `odds_snapshots`. Measured on 400 NULL trigger picks, only **36% resolve to exactly
  one book**; 48% match no stored price and 15% match 2-6 books. That backfill would
  be wrong or ambiguous on two-thirds of rows.
* Book-agnostic bots (`bot_trigger_*_model_v1`, `bot_trigger_*_sharp_v1`) genuinely
  vary their book and are **not** recoverable. Fix forward, measure forward.

---

## 4. The non-circular metric you want already exists — it was just never ported to shadow bets

`DIRECT-BOOK-CLV` (migration 332, 2026-09-11) fixed exactly this problem for
`real_bets`: CLV against the close **at the bet's own book**, NULL when that book has
no fresh close, never an arbitrary book. `workers/jobs/settlement.py::_direct_book_close`
implements it, with a freshness bound and an explicit *"a missing close is NULL, not
a guess"* contract.

**The shadow path never got it.** `settlement.py` still calls
`get_closing_odds(match_id, market, selection)` with **no bookmaker argument** — and
that function's own docstring says the unfiltered form *"is not well defined… which
book wins can change between two runs of the same query."*

So `shadow_bets.clv` — the ledger every bot decision in this project is made from —
is measured against whichever of ~13 books sorted last at kickoff.

**FIX — port `_direct_book_close` to the shadow settlement path.** This is the
single highest-value change available, because it gives you the metric your
pre-registration actually needs:

* it is **not** Pinnacle-derived, so it cannot be an identity of a Pinnacle-based
  selection rule — it answers §1 structurally rather than by a haircut;
* `shadow_bets` **already has** the `closing_odds` and `closing_bookmaker` columns;
* the evidence that the per-book close was never used is in those columns
  themselves — **`clv` is populated on 99.6% of rows (159,221 of 159,827) while
  `closing_bookmaker` is NULL on 67% of them.** A CLV computed without knowing which
  book closed is a CLV against an arbitrary book, by construction;
* it applies to **159,827 settled rows**, not 89 — which is how you get real time
  folds without waiting weeks.

Required work: (a) add `closing_minutes_before_ko` to `shadow_bets` — `real_bets`
has it, `shadow_bets` does not; (b) switch the shadow branch to the per-book lookup
keyed on `recommended_bookmaker`; (c) a backfill script modelled on
`scripts/backfill_real_bets_direct_clv.py`; (d) accept NULL where the book is
unknown (§3) rather than falling back.

⚠️ Expect the historical shadow CLV record to **move** when this lands, possibly a
lot. That is the point, but it means any bot decision made on the old numbers —
including the three 1x2 retirements of 2026-09-14 — should be re-checked afterwards
rather than assumed still valid.

---

## 5. Your twin argument survives — I tested the thing that could have killed it

If the model and sharp anchors were a monotone transform of one another, opposite
signs on the same fixtures would be an artefact rather than evidence. They are not.

On **1,812 paired rows** from `pick_triggers` (same fixture, same selection, both
strategies, *unselected* — so no range restriction):

* Pearson **0.507**, Spearman **0.502**
* **63% of rows differ by more than 5pp**
* mean |difference| = 0.099, means nearly identical (0.334 vs 0.333)

They are genuinely different numbers. The twin experiment is your strongest
remaining evidence and it holds. **FIX:** none needed — this is confirmatory.

---

## 6. RETRACTION — and a correction to one of your claims

**Mine first.** I was about to report *"CLV rises monotonically across `cal_prob`
quintiles (−11.05% → −6.53%), so the model has ordering information even though its
level is broken."* **That does not survive.** `corr(cal_prob, odds) = −0.504`, so
`cal_prob` is substantially just "short odds", and this repo separately established
that CLV improves as odds shorten. Controlling for odds, `cal_prob` orders CLV in
only **one of three** odds bands (and that one on n=24-26):

| band | cal_prob terciles (CLV) | ordered? |
|---|---|---|
| odds 2.8–3.6 | −7.84% / −5.14% / −3.31% | rises (thin) |
| odds 3.6–4.5 | −10.44% / −3.83% / −8.43% | no |
| odds 4.5+ | −11.15% / −9.43% / −9.79% | no |

Treat "the model has usable ranking information" as **unsupported**. Do not carry it
into the training-path review.

**Yours second.** The claim that *"1x2 has the same [compressed] shape, held safe
only by the Pinnacle veto"* is **not confirmed on live data**. The `1x2_home` sigmoid
range is [0.297, 0.679], but measured on 3,632 live 1x2 picks, `cal_prob` spans
**[0.139, 0.852]** with only **1.22× compression** against the raw model (sd 0.1061
vs 0.1293). Whatever the parameters imply, 1x2 is not behaving like the O/U case in
production. Worth re-deriving before that claim carries weight.

---

## Suggested order of work

| # | Action | Why first / why wait |
|---|---|---|
| 1 | **Port DIRECT-BOOK-CLV to `shadow_bets`** (§4) | Unblocks everything else: a non-circular metric on 112k rows instead of 89, and real time folds without waiting. |
| 2 | **Backfill `recommended_bookmaker` for the 8 per-book trigger bots** (§3) | Exact and cheap; #1 is NULL-blocked without it. |
| 3 | **Swap the `n ≥ 334` gate for power + regime conditions** (§2) | Stops weeks being spent accruing against the wrong constraint. |
| 4 | **Add the Pinnacle-movement cut to bot evaluation** (§1) | Makes the circularity visible by default rather than by audit. |
| 5 | Re-check the 2026-09-14 retirements once #1 lands | They were decided on the arbitrary-book CLV. |
| 6 | Re-derive the 1x2 compression claim (§6) | It is load-bearing for the training review and currently unsupported. |

**Not recommended:** staking on the sharp anchor, publishing it, re-deriving the 2.2
odds floor from the window that produced it, or running an exploratory grid over
five days. Your own four rules on those are right.
