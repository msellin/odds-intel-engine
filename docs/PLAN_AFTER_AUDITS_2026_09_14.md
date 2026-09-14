# Plan after the three audits — 2026-09-14

Three independent audit agents (data forensics, replication referee, OWN-path
viability) reported today. This is what they settled, what is still open, and
what we do next. Every number below was recomputed from raw DB or source by
someone other than whoever first claimed it.

**Why this doc exists:** the owner asked *"how can I be sure the last finding is
really the correct one, as every next audit overthrows previous ones"*. The
answer turned out to be that they mostly **layered** rather than contradicted —
but three of the most consequential numbers had been committed as prose with no
script behind them, and those are exactly the three that moved. See §6.

---

## 1. What is SETTLED

| # | Finding | Ruling | Where it bites |
|---|---|---|---|
| 1 | The 1X2 model adds nothing to the market (α = 0.0000) | **TRUE** — and it survives swapping the benchmark to our OWN scraped books (Coolbet α=0.0685 but the blend is *worse* OOS; Epicbet α=0.0000) | Stop 1X2 model-anchored staking |
| 2 | Residual AUC < 0.5 is a mechanical artefact, not "anti-predictive" | **TRUE** — a model with literally zero information produces residual AUC 0.3453; we observed 0.3775 | Retire residual AUC as a directional diagnostic; α is the right instrument |
| 3 | The O/U Platt calibrator manufactured ~8–9pp of published "edge" | **TRUE** | Already fixed (mig 335) |
| 4 | `clv` is a raw price ratio with no de-vig | **TRUE**, best-verified claim in the record | `EV ≈ (1+clv)/(1+m) − 1`, m ≈ 7.6%. Reprices **every** CLV figure ever published |
| 5 | CLV *does* predict ROI (the "R²=0.07" refutation was a units error — those were correlations) | **TRUE** | Keep gating on CLV; correct its *level*, don't weaken the gate |
| 6 | The retrain met its gate (+2.43% vs base rate) | **TRUE**, robust across split points | Retrain is legitimate — but beating a constant is the floor for being a model, not an edge |
| 7 | ELO/form leak fix + the four defect fixes | **TRUE**, all eight items verified | Done |
| 8 | Pinnacle is the **widest** book in the feed | **FALSE** — fixture-mix artefact | Retracted; see SYSTEM_MAP correction |
| 9 | Pinnacle is **near-true (2–2.5%) on our slate** | **FALSE** — ~9.2% median on our fixtures | The 3% sharp floor is **unsupported** |
| 10 | AF feed is broadly unfaithful | **FALSE as stated** — faithful on majors; unfaithful **per-book** (Unibet 33.1% phantom-high, Kambi 38%, Bet365 same mode) | Fix provenance per book, not wholesale distrust |

**The single fact carrying the most load is the book-margin conversion (#4).** It
reprices the bot ledger, the published track record, and every promotion gate.
It is also now the best-verified thing in the record — the highest-leverage fact
is the most solid one. That is the good news.

## 2. What is NOT settled — and must not be asserted either way

* **Whether the sharp anchor has any edge at all.** Time alignment removes the
  apparent one (settled). "It loses money" is **not** established: reproductions
  give t = −1.7 to −2.4. Honest position: *no demonstrated edge*, not
  *demonstrated loss*.
* **Self-scraped books on both sides of the sharp test.** n = 4 to 57. No power.
* **O/U and BTTS model anchoring.** Untested on the clean bundle. The goalline
  α of 0.15–0.29 is the one live instrument suggesting the model contributes
  something somewhere.
* **Whether AF inflates margins for books other than Unibet.** Established for
  Unibet only. There is no independent Pinnacle scrape, so the same question
  cannot currently be asked of Pinnacle.

## 3. 👥 PICKS — the path that is alive

**Basis (owner's ruling, 2026-09-14):** for PICKS, a price that was capturable
somewhere at some point is acceptable. EMTA legality constrains **OWN** only —
we have no Estonian readers. A price *no book ever offered*, however, still
fails this bar, which is why the phantom feeds stay excluded.

**The rule, and its honest number:**

```
edge = P_shin(Pinnacle) × best_book_price − 1   ≥ 3%
odds ≤ 4.0
anchor and bet quote within 60 min
top 8 per day by edge
```

| construction | n | ROI | 95% CI |
|---|---|---|---|
| unaligned (**inflated — do not quote**) | 4,339 | +8.47% | [+4.7, +12.2] |
| **time-aligned ≤60 min** | 1,661 | **+5.54%** | **[−0.7, +11.7]** |
| aligned ≤15 min | 1,611 | +5.55% | [−0.7, +11.9] |
| aligned ≤5 min | 1,577 | +5.46% | [−0.9, +11.8] |

It is **stable under tightening** — that matters; a staleness artefact keeps
bleeding as you align harder, and this does not. But the CI includes zero.

**It passes the tests that kill most backtests:**
* *Anti-selection control:* edge≥3% → +11.7%; edge 0..3% → +0.6%; edge −3%..0 →
  −3.1%; edge < −3% → **−6.8%** (n=49,159). Monotone across the whole range. A
  pure price-basis artefact would make the rejected bucket profitable too.
* *Phantom-book control:* odds-capped, the edge is the same size on our own
  verified scrapes (+8.79%) as on all books (+8.47%). Not a phantom harvest.
* *Publish-time honesty:* survives using only quotes ≥4h before kickoff.

**What it is NOT:** a model. It uses no model output at all. That is consistent
with finding #1 (α=0) rather than in tension with it.

**Decision: publish, labelled as a forward test, with no track-record claim.**
The channel has been dark since the calibrator was removed. This rule is at
worst honest-neutral and at best +5.5%; the thing it replaces was actively
manufacturing edge. Pre-register the stopping rule before the first post.

## 4. 🤖 OWN — the path that is blocked, for a structural reason

The OWN audit's verdict, which I accept:

> Line shopping across every book Estonia allows recovers about **1.3 percentage
> points of a 7.8-point margin**.

* De-vigged 1X2 probabilities across EMTA-legal books agree to a median of
  **0.62–1.01pp**, while the books charge 7–9pp. There is no dispersion to
  harvest.
* The bolt-on markets are not soft: Coolbet prices corners, cards and team
  totals at a flat **8.00%**, the same as its 1X2. Epicbet's 1H 1X2 (6.19%) is
  *tighter* than its own 1X2.
* Cards looked like the exception and is a trap — the books are pricing
  **different quantities** (implied P(over) on `cards_ou_65`: Coolbet 0.176 vs
  Bet365 0.482). Building the cards bot would have been betting a units mismatch.
* Corners are settleable on only **16.7%** of finished fixtures.
  > **⚠️ CORRECTED 2026-09-14 — wrong denominator.** That is the rate over ALL
  > finished fixtures; on the slate a bot could actually bet (Pinnacle AND
  > Coolbet/Epicbet both quoting corners) it is **92.2%**. Corners still fails,
  > at gate 4 for lack of any measurable edge, not at settleability. See
  > `docs/OWN_MARKET_EXPANSION_2026_09_14.md`.
* Confirmed real money: **139 settled placements, €1,390 staked, −€97.50,
  ROI −7.01%**.
* Confirming a true +3% ROI at 80% power needs **≈15,600 settled bets** ≈ 9 years
  at the current rate.

**A structural blocker nobody had logged:** of 12,482 market-fixtures where ≥2
self-scraped books both quote, only **733 (5.9%)** have quotes within 15 minutes
of each other. The scrapers run on independent cadences. **Today we cannot
compute a trustworthy cross-book comparison at all, let alone act on one.**

**The cheap test that settles OWN:** poll Coolbet, Epicbet and Unibet-Site on a
**single synchronised clock** for two weeks, then measure best-of-3 de-vigged
overround on fixtures all three price.
**Kill criterion: if the median stays above ~2%, close the automated-betting
product.** Best estimate from currently-alignable data: 6.0–6.5%, i.e. it fails
wide. Worth two weeks of no new code to settle honestly.

## 5. Shipped today

* **`ACCESSIBLE_BOOKMAKERS`: `"Unibet"` → `"Unibet-Site"`.** The AF feed is
  phantom-high on 33.1% of selections **and stopped writing entirely on
  2026-09-12 10:00 UTC**. For two days the placeable set silently named a dead
  book while our live scrape sat outside it. New guard
  `ACCESSIBLE-BOOKMAKERS-FEEDS-ALIVE` fails if any book we will stake at has
  written nothing in 36h.
* **`KNOWN = {"elo_diff"}` removed from the leakage canary.** Commit `68bf158`
  claimed this exception was removed; it never was, leaving the canary blind to
  the exact column it was written for. The MFV rebuild has landed, `elo_diff`
  now scores 0.21 against a market-derived 0.37, and the test passes with an
  empty exception set.
* **`SYSTEM_MAP.md` §1 corrected** — the "Pinnacle is near-true, so a 3% overlay
  is a real 3%" premise is false and the floor derived from it is unsupported.
  The ANCHOR_IS_NOT_SHARP finding had never been rippled into SYSTEM_MAP.

## 6. Process change — the actual root cause of the "battle"

Three of the most consequential numbers this week — the −16.5% replay, the
10-anchor placebo, and the paired log-loss table — were committed as **prose in
a doc with no script behind them**. Every claim that shipped with a runnable
script (`residual_test.py`, `recalibration_gate_test.py`,
`anchor_sharpness_check.py`) reproduced on the first attempt. The correlation is
perfect.

**Rule, effective now: a number that changes a strategy does not enter a doc
without the script that produced it, committed alongside.**

Second rule, from the finding that broke claim #8: **any cross-book comparison
that is not paired on fixture and matched on time is measuring coverage, not
price.** This is now `ANALYSIS_GOTCHAS` §10's scope extended from Brier to every
cross-book statistic.

## 7. Correction to our own scale claims

`shadow_bets` holds **159,827 settled raw rows but 19,350 unique** — an **8.43×
dedup factor** (`shadow_bets_unique` is a view). Every scale claim in the fix
plan quotes raw counts, including P0-1's promise of *"a non-circular metric on
~112k rows"*. The real payoff is **~7,100 rows**. Any t-statistic computed on the
raw table is inflated ~2.9×. P0-1's priority must be re-derived.
