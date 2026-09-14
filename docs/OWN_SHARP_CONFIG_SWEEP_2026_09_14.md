# 🤖 OWN — exhaustive SHARP-edge configuration sweep, 2026-09-14

**Script:** `scripts/own_sharp_config_sweep.py` (read-only — touches no bot,
floor, config or table). **Smoke test:** `OWN-SHARP-SWEEP-ASSEMBLE`.

```bash
python3 scripts/own_sharp_config_sweep.py --days 150 --diagnostics --bot-clv
python3 scripts/own_sharp_config_sweep.py --days 150 --control        # junk anchor
python3 scripts/own_sharp_config_sweep.py --days 150 --align-min 15   # alignment ladder
```

> ## ⚠️ CORRECTED after adversarial verification — read §0 first
>
> `docs/OWN_SWEEP_VERIFICATION_2026_09_14.md` was written to break this document.
> It succeeded on four points. **I have re-checked every one of them in my own
> harness rather than accepting them, and all four hold.** The corrections are in
> §0; the rest of this document has been restated accordingly. The harness itself
> replicated to the digit across two independent implementations.

---

## 0. Corrections — what I got wrong, and what replaced it

| # | What this document originally claimed | Status | The corrected number |
|---|---|---|---|
| 1 | *"None of 14,040 (70,200) configurations clears the bar."* | **WITHDRAWN as stated** | None **in that grid**. The grid could not express the gate the live bots use (below). At the live gate a positive family does appear. |
| 2 | *"The real anchor did not beat a randomly permuted one (−9.55% vs −5.80%)."* | **WITHDRAWN** | Unmatched populations. At a **matched** gate the real anchor beats junk by 18–22 pp. |
| 3 | *"23.4% of junk cells exclude zero, so any survivor is noise."* | **RESTATED** | Counted **by sign**, the junk arm's false-**positive** rate is **0.66%**, not 23.4%. Its rejections are the vig. |
| 4 | *"Pooled sharp rule −9.55%, CI [−18.8, −0.3]."* | **RESTATED** | On the two markets we actually trade: **−7.64%, CI [−17.85, +2.58]** — does not exclude zero. |
| 5 | *"Epicbet's first usable aligned data: 2026-08-27."* | **CORRECTED** | **2026-09-02.** (First Epicbet row of any kind is 08-27; first *time-aligned* leg is 09-02.) |

**The mistake that matters is #1, and it is a specific one.** My grid filtered on
an **expected-ROI** floor, `edge = P_shin × odds − 1`. `pick_triggers::_window`
gates the live sharp bots on a **probability-difference** floor,
`P_shin − 1/odds ≥ floor`, implemented as `min_odds = 1/(cal − floor)`. Since
`roi_edge = prob_edge × odds`, a constant probability floor is a **curve in
odds** — 3% prob is a 4.5% ROI floor at 1.50 and a 12% ROI floor at 4.00 — and
**no cell of a constant-floor grid can express a curve.** This is
ANALYSIS_GOTCHAS **§42** ("our `edge` is probability points, not EV") claiming a
fourth victim, and I walked into it having read §42 the same morning. My
`ODDS_BANDS` also had no band ending at 2.00, which is where the effect lives.

**Corrections #2 and #3 share one root cause and it is worth naming.** The junk
anchor passes ~10× as many legs through the same nominal floor, because a random
probability × a book price clears `+3%` constantly while a real overlay of +3% is
a genuine tail. So the "junk arm" at my gate was a 5,880-leg near-flat-back and
the "real arm" a 550-leg tail selection. Comparing their ROIs compares two
populations, and counting their CI rejections without splitting by sign counts
the vig as if it were a false positive. **A control has to be matched on the gate
before its number means anything** — mine was matched on the *harness* but not on
the *selection*, which is a different and weaker thing.

**What survived unchanged:** the harness (§2), the retention and era-selection
limits (§7), the refusal to reproduce the +16.00%, the refusal to move any gate,
and the CLV-fallback fix as the highest-value item (§6).

---

## 1. The question, and the answer

For bets we place ourselves at EMTA-legal, self-scraped books (Coolbet, Epicbet,
Unibet-Site), is there a configuration of the sharp rule with enough volume and a
CI that excludes zero?

**At the ROI-floor family I swept: no.** 70,200 cells, 2,578 reached n ≥ 100,
248 have a CI excluding zero — and **207 of those 248 are negative**. Zero
positive cells at an odds floor ≥ 2.00, zero at edge floor ≥ 5%, zero at
Coolbet, zero at Unibet-Site.

**At the probability-difference gate the live bots actually use: yes, one
family — and it is twelve days old.** Reproduced independently in this harness:

| cell (lead 0, aligned ≤60 min, traded markets) | n | ROI | 95% CI (clustered) | picks/day |
|---|---|---|---|---|
| POOLED, prob-edge ≥2%, odds ≤2.50 | 225 | **+17.07%** | [+4.18, +29.95] | 5.9 |
| POOLED, prob-edge ≥2%, odds ≤2.00 | 109 | +19.67% | [+4.58, +34.77] | 2.9 |
| POOLED, prob-edge ≥3%, odds ≤2.50 | 143 | +16.41% | [+0.33, +32.50] | 3.8 |
| Coolbet, prob-edge ≥2%, odds ≤2.50 | 125 | +15.35% | [−1.81, +32.52] | 3.3 |
| Epicbet, prob-edge ≥2%, odds ≤2.50 | 84 | +32.69% | [+12.36, +53.02] | 7.0 |
| **matched junk control**, POOLED ≥2%, ≤2.50 | 2,878 | **−2.89%** | [−6.72, +0.94] | — |

The matched control is the point: at the **identical** gate the junk anchor reads
−2.89% and the real anchor +17.07%. That is the comparison I should have run.

**But see §3 before believing it.** The entire effect is post-2026-09-02.

## 2. The harness is validated — three checks

**(a) The vig dipstick.** Flat-backing every aligned leg returns ≈ `−m/(1+m)`
against each book's own per-fixture closing margin, on every book and market
(Coolbet 1x2 −9.73% vs −7.14% predicted; Coolbet O/U 2.5 −7.23% vs −7.37%;
Epicbet 1x2 −10.61% vs −7.35%; Epicbet O/U 2.5 −6.10% vs −6.49%; Unibet-Site 1x2
−8.95% vs −7.81%; Unibet-Site O/U 2.5 −5.70% vs −6.64%). O/U lands on
prediction; 1x2 runs ~2pp worse, which is the favourite–longshot bias in unit
staking.

**An independent re-implementation reproduced all six figures to the digit.**
Leg construction, ±2 min assembly, settlement and clustering are right.

**(b) What the dipstick cannot do — and the check that was missing.** The
baseline applies no edge filter, so it is anchor-independent *by design* and
prints identically in both arms. It therefore validates everything **except** the
de-vig and the edge computation, which is where the question lives. I originally
presented it as validating the harness full stop; that was too strong. The
verification supplied the missing half — binning 19,304 1x2 legs by
Shin-de-vigged Pinnacle probability against realised outcomes gives errors of
−1.4pp to +2.9pp across six buckets, while the junk anchor is flat regardless of
its stated probability. **The sharp anchor is well calibrated and carries real
information.**

**(c) The junk anchor, counted by sign.** From this sweep's own control JSON:

| arm | cells n≥100 | CI excludes zero, **positive** | CI excludes zero, **negative** |
|---|---|---|---|
| REAL | 2,578 | **41 (1.59%)** | 207 (8.03%) |
| JUNK | 10,531 | **70 (0.66%)** | 2,396 (22.75%) |

The junk arm's rejections are overwhelmingly negative — they are the vig,
measured precisely because that arm passes ~10× as many legs and buys tighter
intervals. Its false-*positive* rate is 0.66%. My original "23.4%" compared a
positive finding against the wrong tail.

Honesty about the remaining separation: 1.59% vs 0.66% is a factor of 2.4, on
heavily nested cells. It is evidence that the real arm is not pure noise. It is
not, on its own, evidence that any particular cell is real.

## 3. The surviving family is a twelve-day effect

The one thing neither document should skip. Splitting the best cell
(POOLED, prob-edge ≥2%, odds ≤2.50) at **2026-09-02**:

| | n | ROI | 95% CI |
|---|---|---|---|
| full | 225 | +17.07% | [+4.18, +29.95] |
| **before 2026-09-02** | 79 | **+0.99%** | [−20.63, +22.62] |
| **on/after 2026-09-02** | 146 | **+25.76%** | [+9.92, +41.61] |

**It is not an alignment artefact.** Within the old era, tight-gap legs return
−3.54% and loose-gap legs +3.22%; within the new era, tight-gap +23.59% and
loose-gap +30.21%. The split is by date, not by gap.

**And it is not a line-shopping artefact either** — which was my first
hypothesis, because 2026-09-02 is exactly when the pool stops being one book:

| era | qualifying legs by book |
|---|---|
| before 09-02 | Coolbet 79 — *"POOLED" is Coolbet alone* |
| on/after 09-02 | Coolbet 46, Epicbet 84, Unibet-Site 48 |

So I tested **Coolbet alone**, the only book whose history spans both eras and
whose pool width never changed:

| Coolbet only, prob-edge ≥2%, odds ≤2.50 | n | ROI | 95% CI |
|---|---|---|---|
| before 2026-09-02 | 79 | **+0.99%** | [−20.63, +22.62] |
| on/after 2026-09-02 | 46 | **+40.02%** | [+13.42, +66.62] |

**Same jump, same dates, one book, constant pool.** The effect is real in the
data and is not manufactured by best-of-books selection (§52/§55) — but it is
**twelve days long and rests on n=46 at the single book that can see both eras.**
For 26 of the 37 days, the rule returns +0.99% at n=79, which is nothing.

I cannot name a mechanism that changed on 2026-09-02. Retention does not explain
it (the 7-day boundary is 09-07). The honest reading is that this is either a
regime we do not understand or twelve days of good luck, and **n=46 cannot
distinguish those.**

## 4. The price-ratio cap

`ratio = book_odds / anchor_odds − 1`. The whole sweep sits inside production's
ODDS-OUTLIER-FILTER (×1.35 / ×1.30). The 20–35% band — **below** that cap — is
where the money goes, and the coordinator's measurement reproduces here:

| band, publish rule, pooled | n | ROI | junk control |
|---|---|---|---|
| 0–10% | 139 | −3.11% | −2.61% (n=4,036) |
| 10–20% | 389 | −8.20% | +5.69% (n=304) |
| **20–35%** | 167 | **−18.06%** | **−17.23%** (n=91) |

| cumulative cap | n | ROI | 95% CI |
|---|---|---|---|
| ≤35% (production) | 695 | −9.55% | [−18.8, −0.3] |
| ≤25% | 627 | −7.87% | [−17.5, +1.8] |
| ≤20% (PICKS rule v2) | 528 | −6.86% | [−17.3, +3.5] |
| ≤15% | 364 | +3.17% | [−9.1, +15.4] |

**The band effect also reproduces under a junk anchor** (−17.23%, same sign and
size). So a price far above the sharp line loses *regardless of whether our
selection carries information* — it is a fact about where books misprice, not
evidence that the overlay works. The cap is hygiene, not edge.

**Recommendation: keep the PICKS rule's ≤20%. Do not tighten to 15%** — that is
the tightest of five values chosen after seeing the data, and its CI still spans
zero.

## 5. Where the ROI-floor grid's positive cells lived

For the record, since the grid is what was swept: the 41 positive cells sit at
edge floor 1% (39 of 41), odds floor ≤1.50 (41 of 41), lead ≥60 min (27 of 41),
POOLED or Epicbet (41 of 41). The best — `POOLED · 1x2 · home · edge ≥1% · odds
1.01–8.00 · ratio ≤15% · lead ≥60` — reads n=104, +31.67%, CI [+6.6, +56.7], no
losing fold, OOS +43.5%.

It fails on its own terms: own-book CLV is **−5.23% EV** on the same legs, and
removing the `lead ≥60` dimension (an era selection, §7) collapses it to +7.29%,
n=262, CI [−8.1, +22.6], fold 1 −3.7%. It is a worse candidate than the
probability-gate family in §1 and is not recommended.

## 6. The sharp bots' CLV was inflated — confirmed twice

`settle_shadow_bets` prefers the bet's own book for the close
(SHADOW-CLV-BOOKMAKER-FIX-2026-08-26) but **falls back to the unfiltered
`get_closing_odds`**, whose own docstring says comparing a price against an
arbitrary book "makes the resulting CLV structurally positive regardless of
whether the bet had any edge". Those rows carry `closing_bookmaker IS NULL` and
are 26–48% of each bot's CLV. *(Independently reproduced by the verification:
+14.31% unanchored vs +8.28% own-book.)*

And **`m` is not a constant.** Correcting with the closing book's **own**
per-fixture margin (`settlement.closing_book_margin()`) instead of a flat 7.6%:

| bot | close at | n | raw CLV | book's own m | **EV** | t | exec ROI | span |
|---|---|---|---|---|---|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | **Coolbet** | 66 | +4.51% | 7.63% | **−2.84%** | **−2.85** | +10.87% | 09-11..09-13 |
| " | *unanchored* | 42 | +14.78% | — | n/a | | −14.02% | 09-09..09-11 |
| `bot_coolbet_trigger_sharp_ou_v1` | **Coolbet** | 20 | +6.35% | 6.46% | −0.05% | −0.03 | +28.07% | 09-12..09-13 |
| `bot_unibet_trigger_sharp_1x2_v1` | **Unibet-Site** | 67 | +12.05% | 8.60% | **+3.15%** | **+1.67** | −9.03% | 09-11..09-13 |
| `bot_unibet_trigger_sharp_ou_v1` | **Unibet-Site** | 11 | +8.38% | 6.75% | +1.51% | +0.56 | −18.82% | 09-11..09-13 |

**The flat m inverted a significance verdict.** At m=7.6%,
`bot_unibet_trigger_sharp_1x2_v1` reads t=+2.12 — significant. At its book's
real 8.60% margin it reads t=+1.67 — not. That correction is now pinned by the
smoke test.

On the honest basis **not one of the four is significantly positive**, and the
only significant result is `bot_coolbet_trigger_sharp_1x2_v1` being **negative**.
The whole evidence base is 2026-09-09 → 09-13; the own-book-anchored part is
three days.

## 7. What the data structurally cannot tell anyone

**The usable window is 37 days, and it is a near-closing-price backtest for 33 of
them.** `prune_old_simple` keeps only `is_opening`, `is_closing` and the latest
pre-kickoff row per series after 7 days (ANALYSIS_GOTCHAS §59); at our books
before 2026-09-07 that is literally one row per series.

| book | first **time-aligned** leg |
|---|---|
| Coolbet | 2026-08-07 |
| Epicbet | **2026-09-02** (corrected from 08-27) |
| Unibet-Site | **2026-09-09** |

Three consequences neither this document nor the verification can escape:

1. **Outside the 7-day window the alignment filter is a coverage selection** —
   "aligned ≤60 min" means "the book's last write happened within ~75 min of
   kickoff", a property of the scraper's schedule.
2. **Only near-closing prices can be evaluated.** An edge that is largest *early*
   is invisible to both harnesses. This cuts **against** the negative result.
3. **The live bots cannot be reproduced retrospectively** — they fire on
   transient intraday prices retention has deleted. The backtest and the live
   record measure different things over the same fixtures.

**Own-book CLV — the one metric that converges fast enough to settle this (§8) —
is uncomputable on every lead-0 cell**, because the leg *is* the last surviving
pre-kickoff row. Neither side of this argument can use it there.

## 8. Recommendation

**Unchanged from the original, and now agreed by the verification: paper only,
move no gate.** What changed is the reason — not "nothing works" but "one thing
might, and it is twelve days old".

| # | Action | Why |
|---|---|---|
| 1 | **Move no floor, cap or gate** — not the 3% sharp floor, the 4.0 ceiling, the 2.80 placer floor, the 13% home-dog floor. | §60, a fourth time. The candidate family rests on n=46 at the only book that spans both eras. |
| 2 | **Run the §1 family as a PAPER shadow bot**, tagged so its own-book CLV becomes readable once NEAR-KICKOFF-CAPTURE has been writing long enough: `P_shin − 1/odds ≥ 0.02`, `odds ≤ 2.50`, 1x2 + O/U 2.5, aligned ≤60 min, production outlier guard, ≤20% ratio cap. | It is the only configuration that survives a matched control, and it cannot be settled retrospectively — retention deleted the prices. Forward-running it is the only way to learn anything. **Not a euro of stake.** |
| 3 | **Keep the PICKS rule's ≤20% ratio cap; do not tighten to 15%.** | The leak is real and reproduces, but it reproduces under a junk anchor too. Hygiene, not edge. |
| 4 | **Do not promote `bot_unibet_trigger_sharp_1x2_v1`.** | +3.15% EV, t=+1.67, n=67, three days — and its Coolbet twin on the identical rule is significantly negative. |
| 5 | **Fix the CLV fallback in `settle_shadow_bets`**: leave `clv` NULL when the bet's own book has no closing row. | It manufactures 4–10pp of apparent edge on exactly the bots the OWN path is judged on. Confirmed independently. **Still the highest-value item here**, and a correctness fix rather than a strategy change. Filed, not fixed — `settlement.py` is owned by another agent this session. |

**Confidence.**

* **High** — the harness is correct. Two independent implementations, six
  identical dipstick figures, and a well-calibrated anchor.
* **High** — the sharp-bot CLV headline was inflated by the unanchored-close
  fallback, and no bot is significantly positive once corrected per-fixture.
* **High** — the ROI-floor grid contains no defensible configuration, and my
  original claim that this covered "all" configurations was wrong.
* **Low-to-moderate** — that the probability-gate family is a real edge. It
  survives a matched control and has no losing fold, but 26 of its 37 days return
  +0.99% and the post-09-02 jump rests on n=46 at the one book that can see both
  eras. Detecting the +3% that would actually matter needs ~3.7 years at this
  pick rate.
* **Low** — anything per-book. Epicbet has 11 aligned days, Unibet-Site 4.

**On process.** The thing that caught the real error was not a better statistic,
it was a second agent told to break the result rather than check it. The gate-form
mistake (§0 #1) was invisible from inside my own framing: I swept the dimension I
had named, exhaustively, and reported exhaustiveness — which is precisely how a
grid search launders a missing dimension into a negative result. Worth repeating
on any finding that closes a product line.
