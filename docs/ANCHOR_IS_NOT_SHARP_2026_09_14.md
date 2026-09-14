# The anchor is not sharp — and everything anchored on it inherits that

**2026-09-14.** Found by an independent trading audit, verified here on 18,166
fixtures. This is the most consequential finding of the week and it invalidates
the strategy the project was about to pivot to.

## The premise that was never checked

`docs/SYSTEM_MAP.md` §1 states the foundation of every sharp-anchored bot:

> *"Pinnacle is near-true, so a 3% overlay is a **real** 3% — no need for it to be big."*

**That is false in this data.** 1X2 overround, latest pre-kickoff triple, same
timestamp, since 2026-06-01 (`scripts/anchor_sharpness_check.py`):

| book | n | median overround | |
|---|---|---|---|
| **Coolbet** | 7,961 | **7.79%** | ← we bet here |
| **Epicbet** | 3,724 | **8.02%** | ← we bet here |
| Betano | 17,065 | 9.00% | ← we bet here |
| Unibet-Site | 1,730 | 9.08% | ← we bet here |
| **Pinnacle** | 18,166 | **9.18%** | ← **our "sharp" reference** |
| Bet365 | 18,641 | 11.18% | |
| SBO | 9,847 | 15.00% | |

**Our reference book charges more margin than the books we are supposedly
beating it with.** Pinnacle's real 1X2 margin is 2–2.5% at close on majors.

Distribution of the Pinnacle quote itself:

| | fixtures | share |
|---|---|---|
| overround **< 4%** (plausibly a real line) | 1,689 | **9.3%** |
| overround **≥ 9%** (a goodwill quote) | 10,384 | **57.2%** |

Sharpness is a function of **limits**, and limits are a function of **margin**. A
9–13% three-way has no size behind it and therefore carries no information
advantage. It is a *quote*, not a *line*. The audit found the margin splits by
league — 3.0–3.9% on Serie A, EPL, Bundesliga, MLS; **12.7–13.1%** on Polish III
Liga, Estonian Esiliiga A, Norwegian 3. Division — i.e. real Pinnacle exists in
this feed, but almost never on the fixtures our bots fire on.

## The direct test

Paired, same fixture, same moment, Shin-de-vigged, multi-class log-loss
(audit's figures):

| | n | Pinnacle LL | book LL | who is better |
|---|---|---|---|---|
| vs Coolbet | 1,367 | 0.9719 | 0.9721 | **indistinguishable** |
| vs Epicbet | 1,151 | 0.9887 | 0.9854 | **Epicbet** |
| vs Bet365 | 14,771 | 0.9797 | 0.9805 | Pinnacle by 0.08% |

**De-vigged Pinnacle is not a better probability estimate than de-vigged
Coolbet.** The strategy requires it to be 3 *percentage points* better.

## Three consequences, each independently fatal to the pivot

### 1. The replay loses money once the clocks are aligned

The audit rebuilt the rule over 65,476 candidate legs / 3.5 months (vs our 5 days).
**Median quote gap: 0 minutes across all candidates — 360 minutes on the SELECTED
ones.** Constrain both quotes to within 15 minutes:

| | n | ROI | t |
|---|---|---|---|
| edge ≥1%, odds ≥2.20 | 405 | **−16.5%** | **−2.11** |
| edge ≥3%, odds ≥2.20 | 112 | −14.2% | −0.98 |

The positive numbers came from comparing a six-hour-old book quote against a
Pinnacle quote at kickoff. **The "edge" was the gap.**

### 2. The placebo destroys the framing

Same rule, different anchors, time-aligned, edge ≥3%:

| anchor | ROI | | anchor | ROI |
|---|---|---|---|---|
| **Pinnacle** | **−14.2%** | | 10Bet | +24.1% |
| Betfair | −27.9% | | Superbet | +19.8% |
| Bet365 | −17.0% | | Marathonbet | +6.9% |

**Pinnacle is the second-worst of ten.** If "Pinnacle is sharp" were true it would
dominate. A random scatter is what you get measuring disagreement between two
equally-soft prices. **We had pre-registration and fold-robustness but no negative
control.**

### 3. `clv` is a raw price ratio, not EV

`workers/jobs/settlement.py:613` — `clv = odds / closing_odds - 1`. No de-vig.

Beating a soft book's close by 8.5% when that book carries ~8% margin is **≈ +0.5%
EV, not +8.5%**. This dissolves a paradox flagged earlier as unexplained: the
"+9.21% CLV but −14.5% ROI" figures were never in tension — correctly converted,
the CLV always said *about break-even*, and the realised ROI agreed.

## What this retires

- **The sharp-anchor pivot as the project's strategy.** Master list #5/#6 were to
  make it measurable; measuring it more precisely does not make it exist.
- **`clv_pinnacle` as a fair-value metric** on any fixture where the Pinnacle
  overround is wide — 57% of them.
- **The 3% sharp floor**, which was derived from the near-true premise.

## What survives

- Porting `DIRECT-BOOK-CLV` (#6) is still worth half a day — a ledger with an
  undefined closing book is indefensible. It is hygiene, not the deciding
  instrument.
- **Derivative markets** (corners, team totals, 1H lines) are where low-tier
  softness actually lives, because the book bolts them on rather than buying them
  from the same supplier that prices 1X2. `bot_corners_paper_shadow_v1`,
  `bot_team_total_paper_shadow_v1`, `bot_1h_1x2_paper_shadow_v1` point there —
  but they are anchored on the same non-sharp Pinnacle and must be re-derived
  against a **multi-book consensus**.
- **Exclude club friendlies unconditionally.** 15% of selected picks; ROI −45.1%
  (t=−3.10). This is a prior, not a discovery.

## The three process guards this should have had

1. **Overround as a standing column** on every anchored comparison. Margin is the
   cheapest proxy for sharpness and it was never checked in four months.
2. **A negative control.** Every anchored strategy re-run against three junk
   anchors. Cheaper and more decisive than any amount of accrual.
3. **Time alignment.** Anchor and book quotes within 15 minutes, or the
   comparison measures latency rather than price.
