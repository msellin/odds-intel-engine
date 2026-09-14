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
- ~~**Derivative markets** (corners, team totals, 1H lines) are where low-tier
  softness actually lives, because the book bolts them on rather than buying them
  from the same supplier that prices 1X2.~~ **❌ RETRACTED 2026-09-14, same day,
  by `docs/OWN_MARKET_EXPANSION_2026_09_14.md` + commit `6c743ea`.** The premise
  is measured false *at our books*: **Coolbet prices corners, cards and team
  totals at a flat 8.00% — identical to its own 1X2** — and Epicbet's 1H 1X2
  (6.19%) is **tighter** than its own 1X2. A flat margin across every derivative
  is the signature of an **automated derivation engine**: the book computes one
  goals model and applies a fixed margin to every projection of it. That means
  the derivatives carry no *independent* error to exploit — they are a
  deterministic transform of numbers we have already measured as efficient.
  Results are in and null: corners **n=51, +4.0%, CI [−38.8, +46.9]** (~15 years
  to power); team totals **CLV +0.64%, t=0.68**; 1H market AUC 0.66 vs model
  0.54; cards **−76% ROI on n=22** with a −4.1pp settlement bias (z=−3.0).
  **Do not re-open this on the strength of the retracted paragraph above.**
  `bot_corners_paper_shadow_v1`, `bot_team_total_paper_shadow_v1` and
  `bot_1h_1x2_paper_shadow_v1` are the instruments that returned the null.
- **Exclude club friendlies unconditionally.** 15% of selected picks; ROI −45.1%
  (t=−3.10). This is a prior, not a discovery.

## Follow-up the same day: is the 9.18% our FEED, or is it Pinnacle?

**Answer: mostly Pinnacle. The feed adds ~0.8pp of lag, and none of it where it
matters.** `AF-PINNACLE-NOT-PINNACLE-2026-09-14`'s paired test, run against
Pinnacle's own guest API (`scripts/` probe, Mac-only — Cloudflare WAF-blocks the
VPS), **n=92 fixtures across 39 leagues**, overround only (no de-vig, no
max-over-selections, so none of the usual biases apply):

```
median real Pinnacle overround : 5.72%
median AF  "Pinnacle" overround: 6.69%
MEDIAN PAIRED DELTA (AF - real): +0.81pp   95% CI [+0.38, +1.02]
AF wider on 73/92 (79%); effectively identical on 15/92
```

**It is LAG, not distortion** — the delta is a clean dose-response in the age of
our stored row:

| AF row age | n | delta |
|---|---|---|
| < 1h | 13 | **+0.02pp** |
| 1–6h | 70 | +0.81pp |
| 6–24h | 9 | +1.20pp |

A fresh AF row is indistinguishable from real Pinnacle. (Caveat: age is partly a
proxy for time-to-kickoff, so the two cannot be fully separated — both are lag.)

**And the split by anchor quality confirms this document's thesis from a new
direction rather than overturning it:**

| band | n | real | AF | delta | median limit |
|---|---|---|---|---|---|
| sharp (<4%) | 11 | 3.57% | 3.84% | **+0.10pp** | $1,800 |
| mid (4–6%) | 42 | 5.53% | 6.53% | +1.05pp | $500 |
| wide (6–9%) | 23 | 6.40% | 7.71% | +1.08pp | $400 |
| **goodwill (≥9%)** | 16 | **9.26%** | **9.28%** | **+0.00pp** | $200 |

Where our feed says 9.28%, **real Pinnacle says 9.26%.** The goodwill quotes were
never a feed artefact — Pinnacle genuinely charges 9%+ on those fixtures, with
$200 limits behind them. §"The premise that was never checked" stands, and a live
Pinnacle feed would not move it by a basis point.

**Consequence: scraping Pinnacle does not revive the sharp-anchor strategy.** It
would make edges *shrink* (our feed inflates), i.e. remove picks rather than
create them. The surviving action is the one now shipped —
`PICKS-ANCHOR-QUALITY-GATE`, which uses the `anchor_overround` column that was
already being computed and discarded.

## The three process guards this should have had

1. **Overround as a standing column** on every anchored comparison. Margin is the
   cheapest proxy for sharpness and it was never checked in four months.
2. **A negative control.** Every anchored strategy re-run against three junk
   anchors. Cheaper and more decisive than any amount of accrual.
3. **Time alignment.** Anchor and book quotes within 15 minutes, or the
   comparison measures latency rather than price.
