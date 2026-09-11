# Betting Gate Decisions — the single source of truth for edge/odds floors

**Read this before changing any real-money edge or odds floor, and before running
"a quick backtest" to re-decide one.** This doc exists because we kept running
*different* backtests that gave *different* answers and kept changing the floors —
the fix is a fixed method + a recorded decision, not another ad-hoc run.
(Established 2026-09-09 after the 1x2 10-vs-13 churn.)

## ⭐ THE FLOOR TABLE — what the data supports, per market and bet type (2026-09-11)

**This is the answer section. Everything below it is method and history.**
Produced by `scripts/floor_grid_sweep.py` over every settled PRE-MATCH pick in
both ledgers, model-edge only (`--filter edge_kind=model`), executable price,
walk-forward folds. Read the **n** column before the ROI column.

### How to read the evidence column

ROI alone cannot decide anything at our sample sizes (~9,300 settled bets are
needed for +/-2% on ROI; ~334 for CLV). So each row is judged on **ROI and CLV
moving TOGETHER as the edge floor rises**. That pairing is the strongest
evidence available to us:
* **both rise** -> a real edge, and the floor is doing work.
* **both flat/negative** -> dead, regardless of sample size.
* **CLV up, ROI down** -> edge is probably real, variance has not converged.
  Do NOT retire on ROI alone (this is the `bot_summer_specialist` lesson, and
  the standing kill rule is `ROI < -5% AND CLV < 0%` — both conditions).
* **CLV negative** -> we are taking worse prices than the close. Nothing to
  salvage at any floor.

### 1x2 — by selection band (the band IS the bet type here)

| band | n | edge floor | odds floor | verdict |
|---|---|---|---|---|
| **home-underdog** | **1,966** | **12-13%** (live: 10% real-money / 13% pooled) | **2.80, keep** | ✅ **REAL EDGE — the only one we have.** ROI and CLV both rise monotonically: 0% -> +5.0/+2.7 · 10% -> +11.4/+4.8 · 12% -> +16.9/+8.7 · 13% -> +19.8/+10.2 · 15% -> +20.8/+12.3 (n=802 at 13%). The 10% real-money floor is the *volume* end of a real gradient, not a mistake — but 12-13% is where CLV roughly doubles. |
| **draw** | 381 | **do not bet** | — | ❌ **DEAD.** ROI −18% to −38% AND **CLV NEGATIVE at every floor** (−3.1 to −4.4). Negative CLV means we take worse prices than the close, so no floor rescues it. NB the 105k fixture-level basis shows a broad positive DRAW frame — that is a **de-vig/line-shop** edge, not ours (see the scale section). Our model has no draw edge. |
| **away** | 1,603 | **hold, do not bet, do not retire** | — | ⚠️ **THE INTERESTING ONE.** ROI negative (−6.5% -> −1.2%) but **CLV strongly positive and rising: +6.0 -> +14.9 -> +16.9 -> +20.3%**. That is the "edge is real, variance has not converged" signature, and the standing kill rule does not fire (it needs CLV < 0 too). Let it accrue as paper; revisit at n≈3,000. |
| **home-mid 2.00-2.80** | 1,201 | ⚠️ **CORRECTED 2026-09-11 — see below** | currently excluded by the 2.80 odds floor | ⚠️ **My earlier "−12.0%, do not bet" was measured at the 13% floor and does NOT hold at the live 10% floor.** At **edge >= 10%** this band reads **+12.7% ROI / +8.9% CLV on n=509** — better than home-underdog on BOTH metrics (+11.4/+4.8) — and positive in both large datasets independently (+14.7% n=217 shadow, +13.0% n=160 sim/all). It is NOT fold-robust in either, so it is a volume-vs-robustness trade, not a free win. But it is not a losing band at 10%, and the 2.80 odds floor is currently excluding it. |
| **home-fav <2.00** | 336 | **do not bet** | excluded by the 2.80 odds floor | ❌ Zero robust cells in all four datasets; −17.3% at 13%. Excluded automatically by the odds floor. |

**So the whole 1x2 policy is: `home AND odds >= <floor> AND edge >= 10-13%`.** The
odds floor does the selection work and no exclusion list is needed — the owner's
original insight, and it holds. **But what that floor should BE is now an open
question, and 2.80 looks too high.**

#### The 1x2 odds floor: what 2.80 actually excludes, at the live 10% edge floor

| odds band | n | ROI | CLVpin | excluded by 2.80? |
|---|---|---|---|---|
| **<2.00 home-fav** | 223 | **−9.7%** | +9.7% | yes — **correct**, negative in both large datasets (−6.8%, −14.4%) |
| **2.00–2.80 home-mid** | 509 | **+12.7%** | **+8.9%** | yes — **and that looks like a mistake** |
| >=2.80 home-dog | 1,475 | +11.4% | +4.8% | no (kept) |

**Only the <2.00 band earns exclusion.** A **2.00** odds floor would remove
exactly the losing band and keep ~509 bets reading +12.7%/+8.9%. The odds LADDER
supports the same reading: at edge>=10%, home CLV is *highest* at the low floors
(+6.2% at 1.80) and **declines** as the floor rises (+4.8% at 2.80) — so 2.80 is
not earning its keep on CLV at all; its whole justification is removing home-favs,
and 2.00 does that too.

#### ⛔ FINAL — 2.80 vs 2.00 IS NOT ANSWERABLE FROM DATA. STOP RE-RUNNING IT.

Owner, after being given three different answers: *"you just said 2.0 is better,
then run audit and now say 2.8 is better? if i ask you to do it third time, will
you again change your mind? ... data has been the same always, so why are you
changing your mind every time?"* — and *"largest n = 377, its like a data of one
match day?"*

Both observations are correct, and this section exists so nobody re-opens this.

**Why the answer kept moving.** The data never changed. The METHOD did, and each
added test moved the point estimate:

| # | claim | what was missing |
|---|---|---|
| 1 | "odds floor wants 3.20" | no significance test; per-GROUP folds (a bug) |
| 2 | "2.80 too high, 2.00 supported" | no folds, no significance — a bare ROI on n=509 |
| 3 | "keep 2.80" | applied fold-robustness to the CHALLENGER ONLY |

Claim 3 was the worst of the three, because the test was **asymmetric**. Run it
on both gates, on one shared timeline:

| gate | n | ROI | f1 | f2 | f3 | fold-robust? |
|---|---|---|---|---|---|---|
| A odds>=2.80 | 1,475 | +11.4% | +2.9 | **−2.7** | +28.0 | **no** |
| B odds>=2.00 | 1,984 | +11.8% | +8.9 | **−0.3** | +23.8 | **no** |
| (no odds floor) | 2,207 | +9.6% | +9.4 | −3.2 | +22.6 | no |

**NEITHER gate is fold-robust.** Rejecting the challenger for failing a bar the
incumbent also fails is status-quo bias wearing the costume of rigour.

**Why it is unanswerable — the power calculation.** Returns have sd ≈ 1.5-1.6 at
these prices, and the observed A-vs-B gap is **+0.33 pp of ROI**:

| to detect a difference of | bets needed PER ARM (80% power) |
|---|---|
| 0.4 pp (what we observe) | **2,551,717** |
| 1.0 pp | 408,275 |
| 2.0 pp | 102,069 |
| *we have* | *1,475 / 1,984* |

At ~1,000 fixtures a day and our pick rate, the honest figure is **centuries**.
This is not "hard" or "needs more data soon" — **the A-vs-B question cannot be
settled by ROI, ever.** Every point estimate any sweep produces for it is noise,
which is exactly why three sweeps gave three answers.

**The one nearby question that IS reachable:** whether the marginal 2.00-2.80
band differs from ZERO needs **736 bets**. We have **509**. That is ~45% more,
i.e. months, not centuries. It is the only version of this worth waiting for.

**THE DECISION RULE, therefore, is not ROI.** Pick on a basis the data can
actually support:
* **CLV** — the band beats the kept band in every dataset (+8.7% vs +3.8% pooled),
  and CLV converges ~28x faster. This favours **2.00**.
* **Volume vs concentration** — 2.00 adds ~35% more bets at an indistinguishable
  ROI. A preference, not a finding.
* **Blast radius** — 2.80 stakes fewer, longer-priced bets.

**Recorded position: keep 2.80, because nothing justifies the churn of changing
it — NOT because it is better.** It is not better. It is indistinguishable, and
it will remain indistinguishable. If the owner prefers 2.00 for the CLV or the
volume, that is a fully legitimate call and needs no further backtest.

**Confirmed on the 105k fixture-level basis too, and the FUNNEL is the reason
this can never be answered.** Owner asked directly: *"when you look at the 100k
fixtures we have, odds 2.0 vs 2.8 are no different in terms of ROI%, when they
both share same 10% edge floor?"* — correct:

| | n | ROI | SE | t |
|---|---|---|---|---|
| A odds>=2.80 (home) | 1,215 | +22.8% | 10.5 | 2.16 |
| B odds>=2.00 (home) | 1,354 | +21.4% | 9.5 | 2.26 |
| A odds>=2.80 (all selections) | 4,051 | +13.7% | 5.0 | 2.73 |
| B odds>=2.00 (all selections) | 4,283 | +13.4% | 4.8 | 2.80 |

Differences of −1.4 pp and −0.3 pp against standard errors of ~10 and ~5. And
note the SIGN IS OPPOSITE to the pick-level data (which had B above A, +11.8 vs
+11.4). **A difference that flips sign between datasets is noise**, and that is
about as clean a demonstration as this repo will ever produce.

**Why 105,456 does not help — the funnel:**

```
all 1x2 fixture-level synthetic bets   105,456
  ... HOME only                         35,152
  ... AND edge >= 10%                    1,391   <- 98.7% gone, and the ODDS
  ... AND odds >= 2.00                   1,354      floor has not applied yet
  ... AND odds >= 2.80                   1,215
```

**The odds floor is the LAST and SMALLEST filter in the chain.** By the time
`home` and `edge >= 10%` have run, 1,391 bets remain, and the entire 2.00-vs-2.80
choice touches **139 of them**. Starting from 105k, or a million, does not change
that: the edge floor removes ~99% first, so the odds floor can only ever be
decided on the residue. This is structural, not a shortage of history.

**Do not re-run this comparison expecting a different answer.** If someone does,
the result they get will differ from all of the above, and that variation IS the
finding.

#### RESOLVED 2026-09-11 — `scripts/odds_floor_ab.py`, every dataset size

Owner: *"theres a big diff on 2.0 vs 2.8, can you just run a sweep. 10%, 2.8 vs
2.0 odds floor on every size of data, up to 100k."*

**Comparing GATE A to GATE B head-to-head is the wrong test.** B is exactly A
plus the 2.00–2.80 band, so B is a volume-weighted blend of A and the band and
is pulled toward A by construction — it will look "similar to A" however good
or bad the band is. **The entire difference IS the marginal band**, so that is
what the tool measures, with a standard error and a bootstrap CI (fixed seed).

**The marginal 2.00–2.80 band at edge >= 10%:**

| dataset | n | ROI | SE | t | CLVpin | bootstrap 95% CI | folds |
|---|---|---|---|---|---|---|---|
| sim/calibrated | 58 | +5.4% | 16.2 | 0.33 | +9.8% | [−27.1, +36.6] | +58.1 / −34.8 / −1.6 |
| sim/cohort | 74 | +12.2% | 14.3 | 0.85 | +8.9% | [−16.2, +39.9] | +56.5 / −18.3 / −1.8 |
| sim/all-prematch | 160 | +13.0% | 9.7 | 1.33 | +8.0% | [−5.9, +32.0] | +41.9 / −14.0 / +8.7 |
| shadow/all-prematch | 217 | +14.7% | 8.4 | 1.74 | +9.2% | [−1.3, +30.2] | +38.7 / −3.1 / +10.0 |
| **PICK-LEVEL POOLED** | **377** | **+13.9%** | 6.4 | **2.19** | **+8.7%** | **[+1.8, +26.3]** | +53.8 / −21.0 / +9.0 |
| idealized 105k *(odds-blind)* | 139 | +9.9% | 10.1 | 0.98 | n/a | [−10.4, +30.0] | +6.7 / +5.2 / +14.7 |

**VERDICT: keep 2.80. The band is suggestive but fails the standing rule.**

* **It is NOT fold-robust in ANY pick-level dataset.** The pattern is identical
  everywhere — a large positive first window, then a **negative** one, then a
  small positive: pooled `+53.8 / −21.0 / +9.0`. One early window carries the
  whole result, and it lost money in a later one. This repo's rule is that a
  floor is adoptable only if positive in EVERY fold, written precisely after a
  15% floor was adopted and reverted inside a day.
* Only the POOLED row clears |t| > 2, and **pooling is not extra evidence
  here** — it unions `sim/all-prematch` and `shadow/all-prematch`, which cover
  the same period and largely the same fixtures under different bots. The four
  rows are not four independent confirmations; the consistent fold shape across
  them is one regime seen four times.
* **What genuinely argues FOR the band: CLV.** It beats the kept band on CLV in
  every single dataset (+8.0 to +9.8 vs GATE A's +2.9 to +7.7), and CLV
  converges ~28x faster than ROI. That is a real signal and the reason this is
  logged as a live question rather than closed.

**So it stays 2.80, and this is now a quantified owner override rather than an
open question:** moving to 2.00 buys ~377 extra bets at a pooled +13.9% whose
confidence interval only just clears zero and which lost in one of three time
windows. That is a volume-for-robustness trade — the owner's call by this
document's own rule, never a "the backtest said so" change.

**Re-run:** `python3 scripts/odds_floor_ab.py --edge 10` (or `--edge 13`,
`--lo/--hi` for other pairs). Smoke `ODDS-FLOOR-AB`.

⚠️ **Not a recommendation to change it yet.** home-mid is positive and consistent
but NOT fold-robust in either dataset, which is precisely the volume-for-
robustness trade this document says is an owner decision, never a "the backtest
said so" change. Flagged, quantified, owner-gated.

### O/U goals

| bet type | n | edge floor | odds floor | verdict |
|---|---|---|---|---|
| **o/u 2.5** | **2,129** | **8-12% (live 8% is sound; do NOT go above 12%)** | **1.80 — CONFIRMED, it earns its keep** | ✅ Real but narrower than 1x2. ROI 0% -> +2.1 · 8% -> +6.4 · 10% -> +5.8 · 12% -> **+11.5** · then **COLLAPSES: 13% -> −9.6, 15% -> −18.9**. CLV peaks ~+3.3 at 12%. The collapse above 12% is the important part — a higher floor is NOT safer here. |
| o/u 2.5 **under** | 1,278 | 8% | 2.20 in two datasets | Carries most of the signal (+27.0% @8%/2.20, n=162, 17 robust cells in sim/all). |
| o/u 2.5 **over** | 851 | — | — | Weak: zero robust cells on n=462 in the largest ledger. |
| **o/u 3.5** | **312** | **do not bet** | — | ❌ Zero robust cells in any dataset, either side. `bot_ou35_model_v1` shipped promotion-pending and is **still unvalidated**. |
| **o/u 1.5** | 143 | **do not bet** | — | ❌ Zero robust cells. Never activated; leave it that way. |

#### The O/U odds floor: 1.80 is confirmed

| odds band | n | ROI | CLVpin |
|---|---|---|---|
| **<1.80 (excluded now)** | 222 | **−1.1%** | +1.0% |
| **>=1.80 (kept)** | 1,269 | **+7.7%** | +0.9% |

It excludes a band that is ROI-negative and keeps one at +7.7%, so it does real
work. **And there is no case for moving it up:** above 1.80 the ladder is
NON-MONOTONIC — +7.7 (1.80) → +7.1 (2.00) → +11.9 (2.20) → **+4.0 (2.50)** →
+15.3 (2.80) — while CLV stays flat at ~+1% throughout. A metric that zigzags
while its faster-converging companion stays flat is noise, not a threshold.
That is also why the earlier "O/U wants 2.20" claim was withdrawn.

### Markets we do not bet — and whether the data agrees

| bet type | n | verdict |
|---|---|---|
| **double_chance** | **8,130** (our largest sample) | ❌ **DEAD, decisively.** ROI −5.5% to −7.0% and **CLV −4.8% at EVERY floor**, both flat — the floor changes nothing. Already retired; this confirms it on 8k picks. |
| **asian_handicap** (all lines) | **2,059** | ❌ No fold-robust cell at ANY line or floor (−0.5 n=614, +0.5 n=445, −1 n=221, 0 n=219, −1.5 n=204, …). ROI negative until 15% (+1.4%, n=726). No Pinnacle AH coverage so CLV cannot corroborate. **It still carries a 5% floor in `_MIN_EDGE_BY_MARKET` — retiring it (`None`) is now evidence-backed.** |
| **btts** | 851 | ❌ Zero robust cells. Already retired; confirmed. |
| **corners** (8.5-11.5) | ≤12 per line | ⚠️ **NO EVIDENCE — and worse, its bot is an edge-unit offender** (`bot_corners_paper_shadow_v1` stores edge up to 30.7, breaking the 0..1 convention). Fix the unit before any sweep of it means anything. |
| **team_total / 1x2_1h / draw_no_bet** | ≤22 | ⚠️ No evidence. Too young. |

### What is NOT supported, and was claimed earlier in this same session

* **A 3.20 odds floor for 1x2 and 2.20 for O/U.** Withdrawn. It rested on cells
  of 130-260 bets; CLV by odds band does not corroborate it (the 2.2-2.8 band
  reads as good or better, +18.2% CLVpin); and the only basis at real scale is
  **blind to the odds axis by construction**. Keep 2.80 / 1.80.
* **Any floor derived from the 105k fixture-level basis.** Its edge is
  `best_accessible x P_devig_pinnacle - 1` — a de-vig/line-shop edge, not ours,
  labelled `edge_kind=devig-fixture` precisely so it cannot be mistaken for
  model edge. Its O/U-over column prints **+226.7%**, which is the line-shop
  mirage (§52/§55) being self-evidently unreal. Use it for edge-axis
  *monotonicity* at scale, never for a floor and never for the odds axis.

### The honest limit on all of the above

Model-edge decisions can only rest on picks our model actually made: **18,030
settled pre-match model-edge picks total**, of which 1x2 is 4,346 and o/u 2.5 is
2,129. The ~9,300-for-+/-2% threshold means only `1x2 home-underdog`,
`o/u 2.5`, `double_chance` and `asian_handicap` have samples in the range where
ROI starts to mean much — and two of those are dead. Everything else is a CLV
argument or a hold. Reproduce any row with:

```bash
python3 scripts/floor_grid_sweep.py --no-idealized --filter edge_kind=model \
        --group-by bet_type --summary-only --min-n 100
```

## The canonical method (use THIS, nothing else, for a floor decision)

Run `scripts/edge_floor_backtest.py --market <m> --folds 3`. A floor is
**decided on the EXECUTABLE basis** ("bots' actual picks", ideally the
active/calibrated slice), and it is **acceptable only if it is fold-robust —
positive in EVERY walk-forward fold**, not merely positive pooled.

**What NOT to decide on** (every one of these produced a *different* answer and a
needless change in the past):
- **The idealized / best-of-books basis.** It shows +50–150% ROI (O/U) because
  taking the best of many books selects the most-mispriced book — the line-shop
  mirage (§55, §52). Its *monotonicity* (higher floor → higher ROI) is the only
  signal; its *magnitude* is fantasy and its "robust ✓" is not executable.
- **Total profit at flat stake.** More volume at a lower floor makes more total €
  while being *less* ROI-robust. That's a volume objective, not an edge decision.
  (This is exactly how BOT-CONFIG-GOLDEN-MIDDLE briefly argued for 0.10.)
- **A small recent window** (e.g. one bot's last ~370 picks). Favorable regimes
  make a non-robust floor look robust. Use the full executable universe.

**Rule: no floor change without re-running the above on the executable basis AND
updating the table below in the same commit.** If the executable basis and the
idealized basis disagree, the executable basis wins.

## Decisions (as of 2026-09-09)

| Market | Edge floor | Odds floor | Verdict on the executable basis | Evidence |
|---|---|---|---|---|
| **1x2** | **13%** | **2.80** | **13% fold-robust; 10% is NOT** (negative fold: all-bots f1 −1.8%, calibrated f2 −3.6%). Keep 13%. | `edge_floor_backtest --market 1x2`: exec all-bots ≥13% +8.3% ✓ / ≥10% +3.1% ✗; calibrated ≥13% +15.9% ✓ / ≥10% +15.1% ✗ |
| **O/U 2.5** | **8%** | **1.80** | **8% fold-robust** (and robust down to ~5%). Keep 8%. | `edge_floor_backtest --market o/u`: exec all-bots ≥8% +13.8% ✓ / calibrated ≥8% +18.5% ✓; 13% breaks (f3 −5.3%) |
| Asian handicap | — (not placed) | — | No fold-robust cell at any floor → not placed | see AH-VIABILITY-REVIEW (closed) |

### 11% and 12% also tested (2026-09-09) — neither is fold-robust
Owner asked whether 11% or 12% would let us keep more 1x2 volume without dropping to
13%. Re-ran `edge_floor_backtest` logic with the floor set widened to {9,10,11,12,13,14}%,
folds 3/4/5, both executable slices + the idealized basis. Result: **13% remains the
lowest fold-robust floor.** 11% fails (all-bots f3 −3.1%, calibrated f2 −8.8%). 12% is a
*false pass* — barely positive on one folds=3 fold (+0.2%), collapses to −25.4% at
folds=4, and fails the calibrated slice (f2 −6.0%). The idealized 104k-fixture basis
passes everything ≥8% (best-of-books mirage, §52/§55) — executable basis wins, and it
says 13%. Volume forgone by staying at 13%: ~+18% picks at 12%, ~+41% at 11%, each with
a demonstrated negative fold. **Do not lower below 13%.**

### The 1x2 10-vs-13 tradeoff, recorded so it isn't re-litigated
10% makes **more total profit** (more volume — it fires ~45% more often, which is
why the live 1x2 bot "finds 0" at 13% on a quiet day) but is **not fold-robust**
(a negative fold on the executable basis). 13% is **lower volume, higher and
robust ROI**. On the executable basis that governs real money, **13% wins.** If we
ever want the volume, it is a deliberate *volume-for-robustness* trade and an
owner decision — not a "the backtest said 10%" change, because the backtest that
said 10% used total-profit or a small window, not fold-robust executable ROI.

## Why the same question kept giving different answers (the actual bug)

Three axes were varying silently between runs, and none was written down:
1. **Basis** — executable (real) vs idealized best-of-books (inflated mirage).
2. **Metric** — fold-robust ROI vs pooled ROI vs total profit.
3. **Sample** — full executable universe vs one bot's recent ~370 picks.

Pick different values on those axes and you get −21%, +3%, +8%, +24%, or +93% for
what feels like "the same test." Fixing the axes (executable · fold-robust ROI ·
full universe) makes the answer stable and reproducible. That is the whole point
of this doc.

## Sharp-anchor trigger floors (paper, 2026-09-09)

The sharp-anchor trigger bots (`bot_coolbet_trigger_sharp_1x2_v1` / `_ou_v1`) gate on
a **different edge** — `P_sharp − 1/book_odds` (de-vigged Pinnacle), not the model —
so the 13%/8% model floors do NOT apply to them. A sharp edge is measured against a
near-true line, so its floor is necessarily small.

| Sharp bot | Edge floor | Odds floor | Rationale |
|---|---|---|---|
| sharp 1x2 | **3%** | **1.01** (off) | 3% overlay vs Pinnacle is real (max observed +6.6%); 13% would never fire. Odds floor is a no odds floor (experimental — observing all bands), NOT the model twin's 2.80 — the model's high odds floor is an anti-longshot guard for model over-confidence, which does not apply to a sharp anchor (whose value is often at favourite prices). |
| sharp O/U 2.5 | **3%** | **1.01** (off) | same reasoning; the model twin's 1.80 would exclude sharp edges on shorter prices. |

These are **paper** starting floors, not validated on the executable basis (that's the
whole reason the bots run — to measure whether the sharp anchor finds anything). They
are owner-adjustable and sourced from `workers/jobs/pick_triggers.py`
`_SHARP_MIN_EDGE_BY_MARKET` / `_SHARP_MIN_ODDS_BY_MARKET`. When there is enough
settled volume, decide a real floor with the canonical method above (on the sharp
basis) and record it here. See `docs/SYSTEM_MAP.md` §1 for the two-edges distinction.

## Related
- `scripts/edge_floor_backtest.py` — the canonical tool (executable + idealized +
  walk-forward folds + the STALE-BEST-ODDS / DISTINCT-ON guards).
- ANALYSIS_GOTCHAS §52 (line-shop mirage), §55 (single-book vs best-of-books).
- `docs/BOOK_AGNOSTIC_EDGE_ENGINE.md` — the trigger engine (a *different* selection
  whose −21% is the model-vs-Coolbet adverse-selection problem, not a floor issue).
- The placer reads these floors from `coolbet_placer._MIN_EDGE_BY_MARKET` /
  `_MIN_ODDS_BY_MARKET`; the mirror jobs mirror them; the smoke test
  `BOT-CONFIG-GOLDEN-MIDDLE` pins the 1x2 value.

## 1x2 fav/long split — favourites are a robust loser (FAVLONG-SPLIT-FLOOR-BACKTEST, 2026-09-09)

The "13% is the best 1x2 floor" result was measured on POOLED 1x2. Splitting by the
generation cut (fav = home pick odds <2.0; long = draws/aways/home ≥2.0) via
`scripts/favlong_floor_backtest.py` (same walk-forward `_sweep`, executable price, 3 folds):

| Side | n (all / cohort) | Best fold-robust floor | ROI at 13% | Shape |
|---|---|---|---|---|
| **FAV** (home <2.0) | 137 / 49 | **NONE robust at any floor** | −36% / −17% | negative everywhere; **worse as the floor rises** (high-"edge" favourites are the biggest model errors) |
| **LONG** (draw/away/home ≥2.0) | 1772 / 370 | **13% robust ✓** (also 15/18) | +10.8% / +17.0% | carries all the profit |
| POOLED | 1909 / 419 | 13% robust ✓ | +8.3% / +15.8% | positive only because longs are 93% of picks |

**Conclusion:** the model has **no real edge on home favourites** — they are a fold-robust
loss at every floor, and higher favourite "edge" is noise, not signal. The pooled 13% floor
hides this because longshots dominate the count. **We currently place + publish home-favourite
1x2 bets that clear 13% and they lose.** Candidate remediation (OWNER-GATED — changes real-money
placement + the published record): exclude home favourites from placement (or set their floor
unreachably), keep longs at 13% (explore 15%). Confirm first with the pure odds-band cut
(odds<2.0 either side) since this cut mirrors generation and pools away-favourites into 'long'.

## 1x2 by SELECTION TYPE — the fold-robust edge is home-underdogs only (2026-09-09)

FAVLONG-SPLIT-FLOOR-BACKTEST, extended to all four 1x2 types × floors × sample sizes
(cohort + idealized 10k/25k/35k) + an odds×edge cut on the placed (odds≥2.80) universe.
Interactive matrix: the floor-by-type artifact. Verdict per type:

| Type | Verdict | Evidence |
|---|---|---|
| **home-fav** (home <2.0) | **exclude** — loses at every floor we bet | idealized edge only at 5–8%, dead ≥10% (−7→−12%); cohort negative. Already excluded from real money by the 2.80 odds floor; the change is to the PUBLISHED record. |
| **home-underdog** (home ≥2.80) | **BET — floor 10%** | the one fold-robust engine. On odds≥2.80: robust in BOTH bases from ~8%; 10% is the sweet spot — cohort +21% (n206), idealized +24% (n1198), ~50% more volume than 13%. The 2.80 odds floor already strips the losing low-odds picks. |
| **draw** | **not a model bet — route to sharp triggers** | model bets 0 draws (under-rates them, never clears 12% — §57). The idealized draw edge (8–12% band) is a SHARP/soft-book-mispricing edge vs de-vig Pinnacle, not a model edge → sharp-anchored trigger bots' territory. |
| **away** | **exclude** — no fold-robust edge | idealized away robust at NO floor/size; the cohort's +43→+105% is 10–18 bets of luck. Unreliable → don't stake. |

### PER-SELECTION RE-RUN 2026-09-11 — the pooled floor's remaining job is to admit losers

Owner: *"we shouldn't have the pooled edge floor anymore as it was introducing
home favs."* Re-ran the same methodology **per selection** rather than fav-vs-long
(`scripts/favlong_floor_backtest.py --by-selection`, executable price
`COALESCE(odds_at_pick_live, odds_at_pick)`, walk-forward 3 folds,
active/calibrated cohort n=425). The fav/long split could not answer the question
because LONG lumps AWAYS in with home-underdogs — the two selections it turns on.

| selection | n | ROI @13% (the pooled gate) | robust at any floor? |
|---|---|---|---|
| **home-UNDERDOG** (≥2.80) | 236 | +16.0% (and **+21.0% @10%**, n=212) | **✓ at 0-13% and 18%** |
| **home-MID** (2.00–2.80) | 122 | **−12.0%** | ✗ never — every floor has a losing fold |
| **home-FAV** (<2.00) | 49 | **−17.3%** | ✗ never |
| **AWAY** | 18 | +104.9% | flagged ✓ but see below |
| **DRAW** | 0 | — | model bets no draws (as documented) |

**Two findings.**

1. **The 10% home-underdog floor is emphatically validated** — +21.0% on 212
   executable bets, positive in all three folds (12.1 / 23.1 / 26.7), and the
   sweet spot of the sweep. Nothing to change there.

2. **A band belongs to NEITHER existing cut.** FAVLONG-CUTS defines home-fav as
   `<2.00` and home-underdog as `≥2.80`, so **home picks between 2.00 and 2.80
   fall through to the pooled floor** — and that band is the largest losing group
   we have: n=122, −12.0% at the pooled gate, with **no robust floor at any
   level** (it degrades as the floor rises: +13.3% @8% → +3.2% @12% → −12.0%
   @13%). Nobody had named it. Smoke `FAVLONG-PER-SELECTION` now asserts the
   selection cuts are exhaustive and non-overlapping so a band cannot silently
   fall through again.

**On AWAY: do not read the ✓.** n=18 total, and the "robust" rows are n=10 and
n=8 with fold ROIs of +1.0% / +92.5% / +169.2% — one or two long-odds winners.
This is the identical artefact FAVLONG-CUTS already called out ("the cohort's
+43→+105% is 10–18 bets of luck"), and the robustness flag is not meaningful at
that sample size. No evidence either way; the conservative call stands.

**So the pooled 13% floor now has no defensible job on the publication side:**
every group it admits is either measurably losing on executable pricing
(home-MID −12.0%, home-FAV −17.3%) or carries no evidence (AWAY n=18, DRAW n=0).
Real money is unaffected either way — the 2.80 odds floor plus the home-only
mirror already restrict placement to home-underdogs.

**Cost of retiring it: 34 of 152 published 1x2 picks over 90d (22%)** — of which
23 are in the two measurably-negative bands (home-MID −7.6%, home-FAV −34.8% as
published) and 11 are aways (+35.2% on n=11, i.e. noise). ⚠️ This figure was
stated wrong twice on the way here, both times by me, and the reason is worth
recording: **it moves depending on which price you bin the odds by.** Binning by
`odds_at_pick` gave "20 of 152"; binning by the EXECUTABLE
`COALESCE(odds_at_pick_live, odds_at_pick)` gives 34, because the stale
high-water snapshot pushes picks across the 2.00/2.80 band boundaries. The
canonical method says executable, so **34 / 22% is the number.** (An even earlier
"47%" was measured across ALL `simulated_bets` including non-published bots —
wrong population entirely.)

### FULL GRID SWEEP 2026-09-11 — `scripts/floor_grid_sweep.py`, every market, PRE-MATCH only

The definitive run. Owner: *"do a proper full sweep again, 1x2 and ou markets...
separate bet types as well... use all edge % dimensions and all odds floors...
present it all as very detailed table where all possible combinations are
tested, also over different data sets, up to the largest we can do"*, then *"you
can even include other markets and bet types... even the ones we dont offer
picks [for] or bet on, e.g. AH, corners, cards"*.

`floor_grid_sweep.py` is a **dimensional cube**, not another report: the
(edge x odds) grid inside any grouping of any dimension, over all four
datasets. Use it instead of writing a new script — a new script is how
basis/metric/sample drifted between runs and moved the 1x2 floor four times.
`--list-dims` lists what can be asked; `--dump` writes the fact table so
anything can be asked offline.

**Four method invariants, each added because its absence produced a wrong answer
that same day** (smoke `FLOOR-GRID-CUBE`):

1. **PRE-MATCH ONLY**, enforced in SQL with no override flag. Retired in-play
   bots faked an away result: +15.4% "robust" on n=364 that was really in-play,
   one bot n=14 at +452%.
2. **ONE FOLD PARTITION per scope.** Folds built per group made the robust flag
   depend on the grouping — the SAME 129 bets read robust as `HOME-DOG` and
   not-robust as `HOME (all odds)`.
3. **EDGE-UNIT GUARD.** Six bots store `edge_percent` outside the 0..1 fraction
   convention (up to 67.9): `bot_corners_paper_shadow_v1`,
   `bot_team_total_paper_shadow_v1`, `bot_1h_1x2_paper_shadow_v1`,
   `bot_no_pin_shadow_v1`, `bot_no_pin_home_v1`, `bot_sweep_1x2_home_v1`.
   Three are 1x2 bots, so pooling them clears every floor and inflates exactly
   the high-floor cells. Excluded by default, reported loudly.
4. **EDGE-KIND is a dimension.** Model edge, sharp-anchor edge and line-shop
   edge are different quantities (which is why sharp floors are 3% and model
   floors 13%/8%). Splitting showed **line-shop 1x2 has ZERO robust cells on
   n=920** while model 1x2 is robust — pooled, that was invisible.

#### RESULT — what has a robust frame (model edge, n>=50, positive in all 3 folds)

| dataset | bet type | best cell | ROI | n | #robust cells |
|---|---|---|---|---|---|
| sim/cohort | 1x2 | **10% @ 3.20** | +32.9% | 132 | 27 |
| sim/calibrated | 1x2 | **12% @ 3.20** | +32.3% | 101 | 16 |
| sim/all-prematch | 1x2 | **13% @ 3.20** | +22.8% | 225 | 16 |
| sim/calibrated + cohort | o/u 2.5 | **8% @ 2.20** | +14.3% | 151 | 19 |
| sim/all-prematch | o/u 2.5 | 10% @ 2.20 | +19.4% | 115 | 13 |
| shadow/all-prematch | o/u 2.5 | 5% @ 2.80 | +13.8% | 153 | 9 |

**Two live gates look improvable, and every dataset agrees on the direction:**
* **1x2 odds floor 3.20, not the live 2.80** — the best cell in all three
  `simulated_bets` datasets, independently.
* **O/U odds floor 2.20, not the live 1.80** — best in calibrated, cohort and
  all-prematch.
Both are OWNER-GATED (they change placement and the published record) and both
want a volume estimate before adoption.

#### RESULT — 1x2 by selection (model edge only)

| selection | n (largest set) | verdict |
|---|---|---|
| **home-dog >=2.80** | 201–222 | **13% @ 3.20 robust in BOTH large datasets** (+37.6% / +19.0%, 16 and 24 robust cells) |
| home-mid 2.00–2.80 | 77–140 | fragile — 10 robust cells in shadow, 1 in sim. Not adoptable |
| home-fav <2.00 | 90–150 | 3 cells in shadow only; nothing in sim. Not adoptable |
| **AWAY** | **329** | **ZERO robust cells anywhere in the 90-cell grid, in all four datasets** |
| **DRAW** | **286** | **ZERO robust cells anywhere, in all four datasets** |

That closes the draw/away question with real volume: not "we have no data" —
n=329 and n=286 of pre-match model-edge picks, and **no (edge x odds)
combination at all** produces a fold-robust positive frame.

#### RESULT — markets we do NOT bet or publish: all dead on this evidence

| bet type | n | robust cells |
|---|---|---|
| **double_chance** | **7,859** — the largest group we have | **0** |
| **asian_handicap** — EVERY line (-0.5 n=449, +0.5 n=366, 0 n=165, -1 n=154, -1.5 n=153, …) | 1,573 total | **0 at any line** |
| **btts** | 575 | **0** |
| **o/u 3.5** | 277 | **0** |
| **o/u 1.5** | 123 | **0** |
| corners (all lines) | <=12 per line | no data — its bot is one of the edge-unit offenders |
| team_total / 1x2_1h / draw_no_bet | <=15 | too thin to sweep |

DC and BTTS are already retired, so this confirms those calls on far more data.
**AH is the actionable one:** it still carries a 5% floor in
`_MIN_EDGE_BY_MARKET` and is flagged "no fold-robust floor" — this sweep says
the same thing across 1,573 picks and every individual line, so retiring it
(`None`) is now evidence-backed rather than pending. **O/U 3.5 matters too:**
`bot_ou35_model_v1` was shipped on a promotion-pending basis and 277 settled
picks produce no robust frame.

### UNIFIED-GATE test (owner hypothesis, 2026-09-11) — the MECHANISM is right, the draw/away claim is not

Owner: *"we have 10% floor, but don't bet on home favs (their odds are below 2.8
anyway?)... we keep draw and away and home underdogs, but the odds 2.8+ and the
floor 10% will ensure that nothing suspicious gets past... this is what our big
sweeps discovered actually? we didn't have many draws but it's because they
didn't pass the gates, but the ones that pass are profitable."*

Tested with `scripts/favlong_floor_backtest.py --unified-gate` (new mode): restrict
to executable odds >= 2.80 FIRST, then sweep the edge floor per selection. This
matters because **every earlier sweep measured selections across ALL odds** — the
population that decides the question is each selection *inside* the odds floor.

**THE MECHANISM IS RIGHT, AND IT IS A BETTER FIX THAN THE ONE I PROPOSED.** A home
favourite is priced under ~2.0, so the 2.80 odds floor excludes home-favs
*automatically* — and it also excludes the home-MID (2.00–2.80) band flagged
above. No selection list, no exclusions, no second edge floor. **The reason
home-favs and home-MID leak onto `/picks` and Telegram is simply that the
publication path applies NO odds floor** (`coolbet_signaler` has no
`_min_odds_for` call, and `clears_edge_floor` takes `odds` only to *choose* the
floor, never to gate). Verified: `clears_edge_floor("1x2","home",1.80,0.14)` is
`True`. So the clean fix is **apply the 2.80 odds floor at publication**, which
subsumes the whole "retire the pooled floor / exclude home-favs" discussion.

**THE DRAW/AWAY HALF DOES NOT SURVIVE — and gotcha §47 is why.** The first run
looked like a vindication: on ALL bots at odds>=2.80, AWAY read n=364, **+15.4%
robust ✓ from a 0% floor**, rising to +48.5% ✓ at 13%. Splitting by bot dissolved
it — *"an odds-band effect is a BOT effect until you split by bot"*:

| bot | maturity | sel | n | ROI |
|---|---|---|---|---|
| `inplay_p` | **retired** | away | 56 | −0.7% |
| `inplay_p_v2` | **retired** | away | 42 | +20.9% |
| `inplay_i` | **retired** | away | 21 | −23.1% |
| `inplay_n` | **retired** | away | 16 | −77.5% |
| `inplay_o` | **retired** | away | 14 | **+452.3%** |
| `inplay_c` | **retired** | away | 8 | −100.0% |
| `bot_v10_all` | calibrated | away | **3** | +278.3% |

The entire away result is **retired IN-PLAY bots** — a different bet type, retired
2026-08-21 — with one bot (n=14, +452%) carrying it. Re-running **pre-match only**:

| selection @ odds>=2.80, pre-match | n | @10% | verdict |
|---|---|---|---|
| HOME | 653 | +7.0%, **not robust** (f2 −14.7%); 12% → +16.1% ✓ | see note |
| DRAW | 95 | n=10, −52.5%; **−31.5% overall, negative in every fold** | **loses** |
| AWAY | 85 | **n=5** (+204% on one winner; two folds have zero bets) | **no evidence** |

So draws are not "profitable once they pass" — they lose, −31.5% across 95
pre-match picks at odds>=2.80. And aways at the proposed gate are **5 bets**.

**BUT the selection-bias point is RIGHT, and it cuts both ways.** The
active/calibrated cohort at odds>=2.80 is **236 HOME out of 240** — our own
home-only mirror stopped generating draws and aways, so that slice *cannot* test
the hypothesis at all. The correct response is therefore **not** "draws/aways are
bad" and **not** "adopt them" — it is *generate the evidence*: a paper shadow bot
at the unified gate (all selections, edge>=10%, odds>=2.80) costs nothing and
produces a clean pre-match sample in weeks. Deciding from retired in-play residue
is exactly the mistake the fold-robustness rule exists to prevent.

**One caveat on HOME worth recording:** 10% is robust on the calibrated cohort
(+21.0%, n=212, all folds) but NOT on the wider pre-match universe (+7.0%, f2
−14.7%, n=524), where 12% is the lowest robust floor. The 10% floor's validation
therefore rests on the calibrated slice specifically — which is the right slice
per the canonical method, but it is a thinner base than the headline implies.

**Recommendation:** (1) apply the 2.80 odds floor at publication — it is the
owner's mechanism, it subsumes home-favs *and* home-MID, and it needs no new
constants; (2) keep HOME at 10%; (3) do NOT add draws/aways to any real-money or
published gate yet — generate paper evidence first.

### WHERE the pooled 13% is actually consumed — and it is NOT "better" at any of them

Owner asked, fairly: *"where we use pooled and why its better to use pooled there
than the 10% with exclusions?"* Traced all four consumers on 2026-09-11. **The
honest answer is that pooled is not better anywhere — it is the unfixed
remainder of the same selection-blindness we have been closing, and at three of
the four sites it is demonstrably wrong.**

| # | consumer | has selection? | is pooled defensible? |
|---|---|---|---|
| 1 | `min_edge_for_pick` fallback → signaler, daemon loader, live re-evals, router | **yes** (and odds) | **No.** It only governs home-MID / home-FAV / away / draw — and those read −12.0% / −17.3% / n=18 no-evidence / n=0. The correct rule for them is exclusion, not a floor. |
| 2 | `pick_triggers._emit_model_anchor` (`model_1x2` windows, **paper**) | **yes** | **No, for home.** See below — a structural argument that looks valid and isn't. |
| 3 | `coolbet_prekickoff_alert` (the real-money catch-net) | **yes** (and odds) | **No — wrong in BOTH directions.** Fixed 2026-09-11. |
| 4 | `gen_frontend_floors` → `ENGINE_MIN_EDGE_BY_MARKET['1x2']`, auto-place badge mirror | n/a | Mirrors the pooled value **by design** — it is a mirror, so it is right iff the engine value is right. |

**Site 2 — the one argument that sounds like a reason for pooled, and why it
fails.** Trigger windows compute `min_odds = max(1/(cal − edge_floor),
odds_floor)`. The odds are the **output**, so you cannot condition the floor on
odds the way `min_edge_for_pick` does — which looks like a genuine reason to fall
back to a selection-blind number. It isn't, because **`odds_floor` for 1x2 is
already 2.80**, so *every emitted 1x2 window starts at ≥ 2.80* — meaning a HOME
window lies entirely inside home-underdog territory, where the validated floor is
10%. Using 13% there simply never emits the band between:

| cal_prob | min_odds @13% | min_odds @10% | band never emitted |
|---|---|---|---|
| 0.30 | 5.88 | 5.00 | 5.00–5.88 |
| 0.35 | 4.55 | 4.00 | 4.00–4.55 |
| 0.40 | 3.70 | 3.33 | 3.33–3.70 |
| 0.45 | 3.12 | 2.86 | 2.86–3.12 |

That is the 10–13% edge band on the **one selection with a proven fold-robust
edge** — the same miss as Stevenage v Luton, third location. Paper-only, so it
costs research volume rather than money. Draw/away windows should keep 13%.

**Site 3 — fixed, because it was a real-money safety net that could not see the
real-money band.** `coolbet_prekickoff_alert` exists to shout "this kicks off
soon and the placer is NOT placing it". It gated on the market-only
`_min_edge_for`, so:

* a home-underdog @3.30 with **11% edge — which the placer DOES stake** (floor
  10%) — **did not alert** (pooled floor 13%). The net was blind to exactly the
  band it guards.
* a home-FAV @1.80 with 14% **did** alert, and home-favs publish at −34.8%.

Routed through `clears_edge_floor` — completing EDGE-FLOOR-ALL-CALLERS, which had
missed this caller. That fixes the **miss**. The over-alerting on home-favs is
NOT fixed by it, deliberately: `min_edge_for_pick` still falls back to pooled 13%
for home-favs until POOLED-1X2-FLOOR-RETIRE is approved. Smoke
`PREKICKOFF-SELECTION-AWARE-FLOOR` pins the invariant that **the catch-net must
never be stricter than the placer it guards.**

**So the pooled floor's remaining job, stated plainly:** hold the line for
draw/away trigger windows (where 13% is right and there is no better number yet),
and act as the fallback for selections whose real answer is "don't publish at
all". The second half is the part that wants retiring — a floor standing in for
an exclusion.

### ⚠️ THIS RUN MOSTLY REPRODUCED WORK ALREADY DONE — read before running another

The home-underdog result here (**+21.0%, n=212, robust in all folds**) is the same
number FAVLONG-CUTS already recorded on 2026-09-09 (**+21%, n=206**). The AWAY
caveat here ("n=18, the ✓ is 10 and 8 bets") is the same caveat FAVLONG-CUTS
already wrote ("+43→+105% is 10–18 bets of luck"). Two days apart, same method,
same answers — which is good news about the method and a warning about the habit.

**The 13-vs-10 question is NOT open, and was not open when this run started.**
Both numbers are decided and both are live, because they govern *different
populations*:

| | value | population | where |
|---|---|---|---|
| real-money 1x2 | **10%** | home-underdogs ONLY (home, odds ≥ 2.80) | `place_coolbet_ui.BOT_THRESHOLDS`, the 1x2 mirror, `_MODEL_1X2_HOME_FLOOR` |
| pooled 1x2 | **13%** | all selections — trigger windows + publication | `_MIN_EDGE_BY_MARKET['1x2']` |

They are not two answers to one question. Anyone reading "1x2 floor = 13%" as an
unresolved dispute with 10% is reading a table that is describing both correctly.

**The pooled floor's actual churn history** (`git log -L` on the constant), which
is why this doc exists at all:

| date | commit | pooled 1x2 |
|---|---|---|
| 2026-06-06 | `5de0380` PER-MARKET-EDGE-V2 | 0.03 → **0.10** ("backtest +14% ROI at ≥10%") |
| 2026-09-08 | `121ecaf` BOT-CONFIG-GOLDEN-MIDDLE | 0.10 → **0.15** |
| 2026-09-08 | `c14e87f` same day, reverted | 0.15 → **0.10** ("15% raise was overfit") |
| 2026-09-08 | `f2d553b` EDGE-FLOOR-BACKTEST | 0.10 → **0.13** ("the validated 13%") |
| 2026-09-09 | this doc created + FAVLONG-CUTS | 0.13 **locked**; real money carved to 10% home-underdogs |
| 2026-09-11 | per-selection re-run (below) | **0.13 unchanged — no floor was moved** |

Three changes in a single day, then the method was fixed and the churn stopped.
**So the genuinely NEW content of the 2026-09-11 run is exactly one thing: the
home-MID 2.00–2.80 band.** Every prior sweep split either at 2.00 (fav/long,
which put MID inside LONG where home-underdogs' +17% masked it) or at 2.80
(FAVLONG-CUTS, which left MID unclassified and falling through to the pooled
floor). It was never isolated, which is precisely why it went unnoticed.


**STILL OWNER-GATED, and deliberately not implemented yet** — "retire the pooled
floor" means four different things to its four consumers, one of which would die
silently:

| consumer | effect of `_MIN_EDGE_BY_MARKET['1x2'] = None` |
|---|---|
| `min_edge_for_pick` fallback → signaler / loaders / live re-evals / router | non-home-underdog 1x2 excluded — **the intent** |
| `pick_triggers._emit_model_anchor` (line ~204) | **kills the `model_1x2` trigger family outright** (paper research bots). Needs its own explicit floor first. |
| `coolbet_prekickoff_alert` (line ~166) | selection-blind `_min_edge_for` — would exclude all non-underdog 1x2 |
| `coolbet_placer` log line (~614) | `None * 100` → **TypeError**; must be made None-safe |
| `gen_frontend_floors.py` → `ENGINE_MIN_EDGE_BY_MARKET['1x2']` | becomes `null`; the auto-place badge mirror `COOLBET_AUTO_MIN_EDGE_BY_MARKET['1x2']` expects 0.13 |

Recommended shape when approved: **per-selection floors, not a single pooled
number** — i.e. express the policy as "home-fav: excluded, home-mid: excluded,
away: excluded, draw: sharp-only" rather than one value that happens to gate four
different populations. That matches the standing design rule (callers pass WHO
THEY ARE, never a floor) and makes the home-MID band impossible to overlook again.

**Resulting real-money 1x2 policy (OWNER-GATED — changes placement + published record):**
`bet 1x2 iff selection=home AND odds ≥ 2.80 AND edge ≥ 10%` (home-underdogs only). Exclude
home-favs + aways; draws handled by the sharp trigger family (paper). O/U 2.5 unchanged (8%).
Expected effect: higher ROI AND more bets, concentrated on the robust engine. The pooled
"13% is best" result was correct only because home-underdogs dominate the pooled count and
dragged the favourite/away noise positive.

**IMPLEMENTED 2026-09-09 (FAVLONG-CUTS, owner-approved).** Real-money 1x2 = home-underdogs
@10%, odds≥2.80. Changed: `coolbet_model_1x2_shadow` (mirror) → home + odds≥2.80 + edge≥10%;
`place_coolbet_ui.BOT_THRESHOLDS['bot_coolbet_1x2_model_v1']` 0.13→0.10 (the real-money gate);
`bot_registry` edge_floor 0.13→0.10; frontend PER-BOT `coolbet-edge.ts BOT_EDGE_THRESHOLDS['bot_coolbet_1x2_model_v1']` 0.13→0.10.
**PER-MARKET-EDGE-MIRROR-FIX-2026-09-10:** the frontend also has a POOLED, selection-agnostic mirror
`COOLBET_AUTO_MIN_EDGE_BY_MARKET['1x2']` (the auto-place badge, via `autoMinEdgeFor(market)`); commit 2d86b9c
erroneously dropped THAT to 0.10 too, which made the badge greenlight 1x2 of any selection at 10%. Reverted
to 0.13 — it must mirror the engine's pooled floor, not the per-bot one. The pooled/paper
`_MIN_EDGE_BY_MARKET['1x2']` stays 13% (trigger windows, all-selection; the paper daemon that also read this floor is retired 2026-09-10). Home-favs
already excluded by the 2.80 odds floor; aways/draws excluded by the home-only mirror. O/U unchanged.
Takes effect on the next placer run (bot is toggled ON). Publication side (/performance, grades) →
PICKS-GRADING.

**SIGNAL-PLACER-1X2-ALIGN (2026-09-10).** The Telegram SIGNAL path
(`coolbet_placer.load_qualified_bets`) was still gating 1x2 on the pooled 13%
floor, so home-underdogs in the 10–13% band were placed with real money but never
signaled (Stevenage v Luton, Home @3.48, +12%). Fixed with a selection-aware
signal floor `_signal_min_edge_for`: 1x2 home-underdog (`selection=home AND
odds≥2.80`) → 10% (shares `COOLBET_MODEL_1X2_EDGE_FLOOR` with the mirror);
everything else → the pooled `_min_edge_for`. **The pooled `_MIN_EDGE_BY_MARKET['1x2']`
is unchanged at 13%** — only the signal path gained the home-underdog carve-out, so
draws/aways (not fold-robust at 10%) and the trigger windows are untouched. Home-favs
still fall on the pooled 13% floor for signals and stay excluded from real money by
the 2.80 odds floor; dropping home-favs from SIGNALS too is a separate published-picks
call (see line 116, OWNER-GATED) and was not done here.
