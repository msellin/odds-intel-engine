# SHARP-ANCHOR-SWEEP — pre-registration (locked 2026-09-14, before any run)

Written **before** touching the data. The reason is specific and recent: in the
last week this project has manufactured three edges that looked real and were
artefacts of how they were measured —

- `2505c25`: validating at best-odds-across-books with an edge gate re-creates
  the line-shop selection artefact (it showed our profitable live O/U as −1.6%);
- `5d8985a`: `odds_at_pick` is the highest price ever *seen*, not one on offer —
  and on 2026-09-13 I hit this myself, producing a backtest at +19–24% ROI that
  fell to a different answer once each book's *last* quote was used instead;
- `61cbb97`: widening the candidate source **degraded** the signal, the opposite
  of the intuition that drove the change.

A sweep over edge floors × odds floors × markets × selections × tiers is a
multiple-comparison machine. It will return winners whether or not any exist.
So the hypotheses, the metric, and the stopping rule are fixed here first.

---

## Phase 0 — METRIC VALIDITY. Nothing else runs until this is answered.

**The concern:** sharp edge is `P_sharp − 1/odds` where `P_sharp` is de-vigged
Pinnacle. Our headline CLV is `odds × P_close_devig − 1`, also against Pinnacle.
If Pinnacle moves little between pick and close, then `edge > 0 ⟹ CLV > 0`
**almost by identity**, and "sharp anchor CLV +5.64%, t=+14.2 (n=1,055)" is
partly a restatement of the selection rule rather than evidence about it.

**Test (pre-specified):** on all settled sharp-anchored picks, regress realised
CLV-vs-Pinnacle on sharp edge at pick. Report R², slope, and the share of picks
where `sign(edge) == sign(CLV)`.

| Outcome | Reading | Consequence |
|---|---|---|
| R² > 0.80 | CLV-vs-Pinnacle is essentially the edge restated | **Metric is circular.** It may not be used as the success criterion anywhere below. |
| 0.30–0.80 | Partly mechanical | Usable only as a secondary, never alone |
| < 0.30 | Carries independent information | Usable, still not primary |

**The primary metric is `clv_pinnacle`-independent regardless of the result:**
direct-book CLV — the closing price **at the book we actually bet**, built by
`DIRECT-BOOK-CLV` (`7ac0166`). A soft book closing shorter than our price is a
fact about the book we transacted with, and cannot be an identity of a
Pinnacle-derived selection rule.

⚠️ If Phase 0 returns circular, that does **not** overturn the twin experiment
(`bot_coolbet_trigger_sharp_1x2_v1` +12.0% t=+8.3 vs its retired model twin at
−9.16% t=−14.5 on the *same fixtures and prices*). An identity cannot produce
opposite signs for two rules on one dataset. It would mean the *magnitude* is
inflated and the *direction* survives — report it exactly that way.

---

## Universe (fixed)

- Settled pre-kickoff picks/candidates, **2026-05-01 →** run date.
- Prices: **executable only** — `ACCESSIBLE_BOOKMAKERS` (Coolbet, Unibet-Site,
  Epicbet, Betano). Pinnacle is the anchor and is **never** a bettable price
  (`81bff07`: not accessible here).
- Per book, the **last pre-match quote**, then max across books. Never
  `max(odds)` over all snapshots — that is the STALE-BEST-ODDS artefact.
- Freshness: the production gate (`lag_h ≤ 6`, `age_h ≤ 48`).
- De-vig: **Shin**, not proportional (`LINESHOP-SHIN-DEVIG`) — proportional
  overstates longshots, which is exactly where the sweep will look.
- Split: **time-ordered**, 3 walk-forward folds + a final untouched holdout.
  Never random — team-strength state leaks across a random split.

---

## What is swept, and what is NOT

**Swept (Category A — model-compensation machinery, inapplicable here):**
edge floor, odds floor, market, selection, league tier. These exist in the
model path to curb *model* overconfidence; the sharp path has no model in it
(verified: `pick_trigger_matcher` never calls `calibrate_prob`), so every value
must be re-derived rather than inherited.

**NOT swept, and not relaxed (Category B — price integrity + blast radius):**
`OU-PIN-REQUIRED`, `OU-PINNACLE-CAP`, the consensus outlier filter, freshness
guards, `ACCESSIBLE_BOOKMAKERS`, and every placement cap
(`MAX_BETS_PER_MATCH/DAY`, stake caps, kickoff cutoff, exposure conflict,
balance confirmation).

The reason is structural, not caution: a line-shop edge **is** "our price versus
the reference price". A corrupt price does not add noise to the signal, it *is*
a fake signal, one-for-one. Every price incident on file — Marathonbet 4.50
against a 2.60 consensus, Bet365 averaging +26.6% on Pinnacle, Coolbet pricing
its "over 2.5" like a 3.0 line — would each mint a spurious sharp edge. Loosening
Category B while hunting a price-difference edge is the one change that could
make this sweep confidently wrong.

---

## PRIMARY hypothesis — confirmatory, ONE test

> **H1: sharp edge ≥ 3% with odds ≥ 2.20 is positive on direct-book CLV,
> out-of-sample, on executable prices.**

Pre-registered because it is the only result so far that **replicated
independently**: `bot_coolbet_trigger_sharp_1x2_v1` (+12.0% → +14.0% at ≥2.2)
and `bot_unibet_trigger_sharp_1x2_v1` (+10.7% → +14.0% at ≥2.2) — two bots,
different books, same threshold, same effect. Replication is the reason this is
confirmatory and everything else below is not.

**Success requires all four:**
1. positive **direct-book CLV** on the holdout;
2. **n ≥ 334** (this repo's own CLV precision threshold — `ANALYSIS_GOTCHAS` #8:
   CLV needs ~334 for ±2%, ROI needs ~9,300);
3. positive in **every** walk-forward fold (fold-robust, per
   `BETTING_GATE_DECISIONS`);
4. not carried by one league, one book, or one month.

**ROI is reported but is NOT the criterion.** At realistic sharp volume we will
never reach n=9,300, so demanding ROI significance guarantees a null regardless
of truth. Saying that now stops it being argued either way afterwards.

---

## SECONDARY grid — exploratory, and labelled as such forever

Edge floor {1, 2, 3, 4, 5, 6, 8}% × odds floor {1.01, 1.80, 2.20, 2.80, 3.20} ×
market {1x2, O/U 2.5} × selection × tier.

**No cell from this grid may be promoted, staked, or published on this run.**
It generates hypotheses for a *future* pre-registration; it does not test them.
Any cell that looks good gets one line in the report and a note that it is
unconfirmed. This is the discipline that `12db2d6` established after the tail
recalibration looked good and did nothing out of sample.

Expected-volume estimate is part of the deliverable: a rule firing twice a week
may be true and useless, and that is a finding, not a footnote.

---

## Stopping rule

- Phase 0 first. If circular, the success metric is direct-book CLV only.
- H1 evaluated **once** on the holdout. No re-tuning after seeing it; a second
  look invalidates the first.
- If H1 fails, the answer is **the sharp anchor is not yet stakeable at these
  thresholds**, reported as such. A null is a real result — the O/U signal
  search returned one on 2026-09-04 and that was the correct outcome.
- Nothing here authorises real money. Promotion is a separate, owner-gated
  decision with its own evidence bar (`docs/BETA_PROMOTION_BAR.md`).

## What would make me distrust a positive result

- Effect concentrated in <3 leagues, one book, or one month.
- Disappears when priced at each book's last quote instead of best-of-books.
- Present on `clv_pinnacle` but absent on direct-book CLV (→ Phase 0 circularity).
- Driven by picks whose price never appeared at a book we can reach.
- A cell that wins only at one grid point with neighbours negative — a cliff,
  not a gradient (`BETTING_GATE_DECISIONS`: "do not chase the 12% number", where
  12% looked best and 13% was −5.7%).
- n < 334, however good the number looks.
