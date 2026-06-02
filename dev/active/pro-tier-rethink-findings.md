# Pro Tier Rethink — Findings (deferred post-WC)

> **Status:** ⏸ Deferred until after the World Cup traffic spike (post-2026-07-19)
> **Created:** 2026-06-02
> **Why deferred:** WC prep is higher priority + time-bound. Pro tier is timeless work.
> **Don't lose:** the validation findings below. Re-validate against `simulated_bets` (not the backtest CSV) before committing to any pivot.

## The problem (in user's words)

> "pro users can't even make a single bet today as they don't know what to bet on"

Current TIER_ACCESS_MATRIX.md line 86 says Pro gets "directional (match + market + edge tier, no selection)". Pro pays €4.99/mo to learn "there's value on this match's BTTS market" but not whether to bet yes or no, or at what odds. It's a strictly-worse-than-Elite tease tier.

## What we tried in the spike (2026-06-02)

### Hypothesis 1 — "Top-5 highest-confidence picks per day"

**The pitch:** show Pro users the top 5 most-confident value bets per day. Curate by edge × prob ranking. Promise "AI's best picks today."

**90-day simulated_bets sample (initial check):**
- Top-5 by combo: n=155, **+20.29% ROI**, CLV +19.0%
- Looked great → led to hypothesis being floated

**3-year backtest validation (the killer):**
- Top-5 by combo: n=3,979, **-10.25% ROI**, WR 30.5%
- Top-5 by edge: n=3,979, -12.36% ROI
- Top-5 by prob: n=3,979, -4.70% ROI
- All cuts worse than the -2.32% baseline
- 12 winning months / 28 losing months over 40 months
- Worst drawdown: -4,525 units

**Conclusion: KILLED.** The 90d result was variance + bot retirements + recent calibration changes. Per-day ranking heuristics don't work because the bot leaderboard already does the per-day curation. Picking the "top of top" is anti-selection — the highest-edge bets are where the model disagrees most with the market, and the market is usually right.

### Hypothesis 2 — "95% confidence bankers"

**The pitch:** filter to picks with very high model probability (≥0.85). Market on hit rate ("80%+ winners"), not edge.

**All-time results by probability threshold:**
| prob ≥ | WR | ROI |
|---|---|---|
| 0.70 | 51.4% | -1.03% |
| 0.75 | 52.3% | -1.07% |
| 0.80 | 52.1% | -2.50% |
| 0.85 | **66.0%** | -2.33% |
| 0.90 | **66.0%** | -4.05% |

**Conclusion: KILLED.** Model is overconfident at the high end. WR caps at 66% no matter how confident the model gets. Cannot honestly market "80%+ hit rate." Known issue — see `platt-overconfidence-deepdive-findings.md`.

### Hypothesis 3 — "Pick of the Day" (free-tier hook)

**The pitch:** single highest-confidence pick per day. Free product, email signup hook into Pro.

**2026 results:**
- Top-1 highest-prob/day: n=136, ROI **+4.85%**, WR 50.0%, avg odds 1.81
- Top-3 highest-prob/day: n=403, ROI **+3.45%**, WR 47.4%, avg odds 1.91

**Conclusion: PROMISING for free tier.** Real signal, honest 50% WR at avg odds 1.81. Marketable as "AI's pick of the day."

**Caveat:** this is from the same backtest CSV that overstated retired-bot ROI. Need to re-validate against `simulated_bets` before commitment.

### Hypothesis 4 — Bot-maturity gate (calibrated + active bots only)

**The pitch:** mirror the CHERRY-PICK-PLACER gate. Pro sees picks from `maturity_label IN ('calibrated','active')` bots only.

**2026 YTD results:**
| Cohort | n | ROI | Monthly stability |
|---|---|---|---|
| All bots baseline | 30,044 | +0.73% | — |
| Calibrated (4 bots) | 4,895 | +1.26% | — |
| Active-label (11 bots) | 2,490 | -0.23% | — |
| **Calibrated + active (15 bots)** | **7,385** | **+0.76%** | **3/5 months losing** |

Monthly Pro-feed: Jan +5.79% / Feb -11.69% / Mar -4.05% / Apr -14.75% / May +1.49%.

**Conclusion: MARGINAL.** Profitable but barely. Not stable month-over-month. Doesn't justify Pro pricing standalone.

### Hypothesis 5 — Bot-maturity gate + prob threshold intersection

**The pitch:** combine "proven bot" with "moderate confidence." Sweet spot below the calibration breakdown.

**2026 results:**
| Filter | n | ROI | WR |
|---|---|---|---|
| Pro-feed + prob ≥ 0.60 | 597 | +9.94% | 48.6% |
| **Pro-feed + prob ≥ 0.65** | **314** | **+10.36%** | **52.2%** |
| Pro-feed + prob ≥ 0.70 | 138 | -1.79% | 48.6% |
| Pro-feed + prob ≥ 0.75 | 31 | -17.71% | 41.9% |

**Conclusion: BEST CANDIDATE so far** but with caveats:
1. n=314 over 5 months is real but thin. One bad month can swing it 3-5pp.
2. Source is the backtest CSV — which we now know inflates retired-bot ROI. Re-validate against live `simulated_bets`.
3. Falls apart sharply above prob 0.70 (calibration breakdown). Narrow operating window.
4. ~2 picks/day average — see "Free-vs-Pro gap problem" below.

## The Free-vs-Pro gap problem (user-raised, 2026-06-02)

> "free gets 2 bets and pro only 5? are users willing to pay for 3 extra picks?"

Real concern. If Free Pick of the Day = 1 pick/day and Pro = 2-5 picks/day, the gap is too narrow to justify €5/mo. Either:

- **Pro needs more value beyond pick volume** — odds comparison, signal depth, ROI per bot, custom alerts, history of past picks with full transparency
- **OR rethink the split entirely** — maybe Free gets nothing actionable (just predictions, no picks), Pro gets the picks
- **OR Pro = "data" not "bets"** — odds comparison, signal intelligence, prediction history. Stop trying to package picks as the differentiator.
- **OR introduce a third axis** — depth (Free shallow / Pro analytical / Elite power-user)

This is the strategic question to answer when this work resumes post-WC.

## Critical caveats to carry forward

1. **The backtest CSV (`backtest-2023plus.csv`) overstates ROI for production-retired bots.** Confirmed via retired-bot investigation: `bot_high_roi_global` backtest +51% / live -49%, `bot_proven_leagues` backtest +46% / live -67%. **Don't validate Pro tier hypotheses on the backtest CSV alone.** Re-run against `simulated_bets` (production) before committing.

2. **The model has a calibration ceiling at ~66% WR.** Don't propose hit-rate-based products. See `platt-overconfidence-deepdive-findings.md`.

3. **Per-day ranking is anti-selection.** Confirmed across 3 years on every metric tried. Don't reopen "Top N picks per day" hypotheses.

4. **The bots themselves are the right unit of curation.** The infrastructure (`bots.maturity_label`, CHERRY-PICK-PLACER) already exists. Whatever Pro becomes, it should plug into this — not invent new ranking heuristics.

## Where to start when this resumes

1. **Re-run Hypothesis 4 and 5 against live `simulated_bets`** (not the backtest CSV). Same SQL pattern, swap the data source. If the +10.36% holds, the bot-maturity + prob-threshold pivot is real. If it collapses, need a different angle.
2. **Decide the Free-vs-Pro gap strategy.** Is Pro selling picks, data, or analysis depth? That decision precedes any tier UI work.
3. **Audit current Pro UI** — what specifically would change? The TIER_ACCESS_MATRIX.md line 86 "directional, no selection" rule is what's broken. Replacing it with full pick visibility is table stakes regardless of cohort.
4. **Consider the retired-bot revisit:** `bot_ou15_defensive` had live +30% ROI / +50% CLV before going silent. Worth checking after the 2026-06-08 calibration retrain — if it re-fires, that's a real Pro-feed addition.

## Open product questions

- Pro as "fewer picks, full transparency" vs "more picks, less depth"?
- Should Pro include access to **prediction history with full provenance** (every past pick, win/loss, CLV, signal at time of pick) — a transparency-led pitch rather than a curation pitch?
- Is there a market for "follow your favorite bot" (user picks 1-3 bots, gets only those bots' picks)?
- Could there be a "tracked tip-of-the-day" product separate from value bets — emailed daily, public ROI ledger, free first month then €X/mo?
- How much of the gap is fixed by simply unbreaking Pro UI (add side+odds visibility) without changing the cohort at all?

## Files / data referenced

- Backtest data: `dev/active/backtest-2023plus.csv` (45,955 rows, 2023-02 → 2026-05)
- Live production data: `simulated_bets` table
- Tier matrix: `TIER_ACCESS_MATRIX.md` (lines 86-92 are the broken rules)
- Bot maturity gate: `bots.maturity_label` column, used by CHERRY-PICK-PLACER (env-gated, see `dev/active/cherry-pick-placer-plan.md`)
- Calibration deepdive: `dev/active/platt-overconfidence-deepdive-findings.md` (referenced in memory)
- Validation scripts (temp, reproduce): `/tmp/top5_validate.py`, `/tmp/pro_validation.py`
