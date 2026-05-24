# SELF-USE-VALIDATION — Context

> Read first when resuming.
> Last major update: 2026-05-24

## State summary

Created 2026-05-10. **Phases 0, 2, and 2.8 fully shipped. Phase 3 partially run — paused 2026-05-24 to wait for new model. Phase 4 readout planned 2026-06-07.**

- Phase 0: sampling script done, CSV worksheet at `dev/active/self-use-validation-phase0-worksheet.csv`
- Phase 2: DB (migrations 091–092), settlement integration, backend writer, 3 admin pages, 2 API routes all live
- ACCESSIBLE-BM (2026-05-11): engine only uses accessible-bookmaker odds; `recommended_bookmaker` stored per bet; `scripts/daily_picks.py`
- Phase 2.8 (2026-05-11): all 3 remaining tasks shipped:
  - `scripts/real_perf_report.py` — paper vs real P&L with slippage, by-bookmaker, by-market, recent bets
  - Bookmaker display on value-bets page (Elite only): "Bet365: 2.10 · Unibet: 2.05 ← Bet365" per bet
  - Freshness indicator: "Odds verified Xm ago" chip (green <45m, amber <90m, red ≥90m)
- **Phase 3 (2026-05-11 to 2026-05-24):** 476 bets logged, NO real money staked. Mixed data — see Critical Findings.
- **Phase 3.5 (2026-05-24 to 2026-06-07):** new-model evaluation window. Placer continues in `--record` mode, broad rule (all bots, 5% edge). No manual `/admin/place` placements during window. See "What to do on 2026-06-07" below.
- Phase 4 (2026-06-07+): apply pivot matrix to new-model placer data.

## Why this exists

Margus dislikes B2C marketing. SaaS is hard to grow at small numbers. Engine + ML + signals already exist. The ROI math favours self-use *if the bot edge is real*. Open question: can paper-trading edge survive real-world execution (slippage, limits, accessible-bookmaker-only constraint)?

## Key decisions made in conversation

- **Books:** Coolbet (preferred) + Bet365 (secondary). Both accessible from Estonia.
- **Manual placement only** — no third-party tool automates Coolbet, custom auto-placement violates ToS.
- **Stakes during validation:** €1–3 (planned, but Phase 3 ran paper-only — see below)
- **Audience:** superadmin only — gate via `profiles.is_superadmin`
- **Coexist with SaaS** during validation; don't drop SaaS yet
- **Validation budget:** ~6 weeks, 200–250 real bets

## Critical findings — 2026-05-24

### 1. Phase 3 was paper-only, not real-money

Despite the original plan calling for €1–3 real stakes at Coolbet, user logged 476 bets at Coolbet odds without actually wagering real money. So `real_bets` table is **paper trading restricted to Coolbet-accessible odds + a manual or rule-driven selection layer**, not real-money execution data.

**Implications:**
- `slippage_pct` is captured-odds vs Coolbet-offered-odds at the moment of inspection — NOT true slippage from price-moving-between-view-and-click.
- True execution friction (account limits, soft-bans, behavioral cost of placing losers) was never measured.
- The decision matrix (`<0% → don't pivot`) still applies but on a weaker basis than designed.

### 2. The 476 rows are TWO different datasets stacked together

`real_bets` mixes rows from two sources, identifiable by `notes` column:

| Source | n | ROI | Hit | Identification |
|---|---|---|---|---|
| **Placer `--record` (rule-driven)** | 216 (157 settled) | **-8.13%** | 45.9% | `notes LIKE 'auto%'` |
| **Manual `/admin/place` (user-filter)** | 260 (258 settled) | **-10.45%** | 32.2% | `notes` blank or user-typed |
| Combined | 476 | -9.54% | 37.3% | — |

The **placer subset is the cleanest answer to "if I followed the system mechanically with Coolbet pricing, what's the ROI?"** Manual subset has selection bias (user unconsciously picked the worse half).

### 3. Selection bias is real but smaller than initially feared

Manual cohort -10.45% vs placer cohort -8.13% — only ~2.3pp worse. But the per-market breakdown shows much bigger divergence on specific markets — see Finding 5.

### 4. Per-bot signal (placer subset only)

| Bot | n | ROI | Verdict |
|---|---|---|---|
| `bot_ah_away_dog` | 6 | +40.3% | promising but tiny N |
| `bot_ah_home_fav` | 9 | +35.8% | promising but tiny N |
| `bot_btts_all` | 47 | -0.5% | flat |
| `bot_aggressive` | 58 | -5.9% | slightly -EV at decent N |
| `bot_aggressive_v2` | 9 | -41.1% | broken |
| `bot_dc_value` | 6 | -61.0% | broken |
| `bot_ou35_attacking` | 7 | -85.9% | kill candidate |

**Cross-check:** `bot_aggressive` is -5.9% in placer (n=58) AND -5.9% in manual (n=188). Identical at very different sample sizes → strong real-signal indicator. This bot doesn't have edge regardless of selection mechanism.

### 5. "Away 1X2 +112.7%" was a selection-bias mirage

The headline winner from the manual cohort does NOT survive the placer's rule-driven sample:

| Market / Selection | Manual ROI | Placer ROI |
|---|---|---|
| 1x2 away | **+112.7% (n=21)** | **-100% (n=11)** |
| 1x2 home | -43.6% (n=50) | +7.8% (n=24) |
| o/u under 2.5 | +2.2% (n=48) | **+44.7% (n=20)** |
| o/u over 2.5 | +5.3% (n=30) | -32.2% (n=21) |
| btts no | -41.0% (n=10) | +3.6% (n=18) |

**Only `o/u under 2.5` shows positive ROI in both subsets** — that's the cleanest market signal. Everything else either flips sign or is too thin to call.

### 6. Modeling agent flagged today's data is partly bug-artifact

`v20260524_market` shipped 2026-05-24 with five bug fixes (see modeling agent's briefing of same date):
- WEEKLY-RETRAIN-OU-FEATURES — cron dropped 14 market features → hits Over 2.5 -42.7%
- AH-CAL-BYPASS — double-shrinkage silently killed AH
- AH-HOME-LINE-FILTER + AH-AWAY-LINE-FILTER-TIGHTEN — +0/negative-fav lines structurally miscalibrated
- MARKET-EVAL-BTTS-AH — eval scored only 1X2+OU for weeks
- DC-CASE-FIX + DC-RESULTKEY-FIX — every double_chance bet silently failing at placement

Not directly addressed: Home 1X2 -40.9% / Draw 1X2 -25%. May be a separate model quality issue (Platt calibration drift or favourite-longshot bias) that doesn't get fixed by today's work.

Expected new-model pick distribution: 70-80% overlap with current picks, ~20-30% different. Enough that the old baseline is no longer the right comparison.

## Decision — 2026-05-24

**Pause Phase 4 verdict for 2 weeks.** Draw conclusions from new-model data, not from the old/buggy-model dataset.

### What runs during the window (2026-05-24 → 2026-06-07)

| Track | Mode | Cadence | Action |
|---|---|---|---|
| Coolbet placer `--record` | broad rule (all bots, 5% edge min) | ~3x/day at morning/midday/pre-KO when JWT is fresh | Keep running |
| `/admin/place` manual | — | OFF | Do NOT use during window — adds noise on top of clean signal |
| Bots (paper) | normal | every betting refresh | Keep running |
| Real money | — | OFF | No staking until Phase 4 decision |

### Why broad, not narrow

The placer is the **measurement instrument**. Narrowing it to "only known-good bots" before measuring the new model would re-introduce selection bias of the same kind that produced the Away 1X2 mirage. Once we have new-model data showing which bots survived the bug fixes, *then* narrow the placer to that locked list for the optional real-money phase.

### Why no `/admin/place`

Adds selection-biased rows to the same `real_bets` table that contains the placer's clean signal. The analysis can still split them via `notes LIKE 'auto%'`, but cleaner to just not generate more biased data during the new-model baseline window.

### Cadence math

At ~3.6 placer rows/day historical rate, a 2-week window produces ~50 fresh placer rows. Target 100+ for tighter signal — that requires ~7 rows/day, hit by running 3x/day at the bots' actual betting windows (morning 06:00 / midday 11:30 / pre-KO 15:30 UTC) when JWT is fresh.

## What to do on 2026-06-07

1. Run `python3 scripts/real_perf_split_by_source.py --days 14` to get new-model-only placer data
2. Compare new-model placer ROI vs the 2026-05-24 baseline of -8.13%
3. Compare per-bot ROI on new model vs old model — flag any bot that flipped sign
4. **Decision matrix (placer subset only, ≥7 days of new-model data, ≥50 fresh rows):**

| Placer ROI on new model | Action |
|---|---|
| Still ≤ -5% | Phase 4 verdict: edge isn't there. Apply pivot matrix → likely "no pivot, SaaS continues". File `bot-edge-debug.md`. |
| -5% to 0% | Marginal. Extend window 2-4 weeks. Consider narrowing to confirmed positive bots only. |
| 0% to +5% | Promising. Lock the bot list to positive ROI ones, run another window with that narrowed rule. |
| > +5% | Strong. Consider flipping placer to `--execute` on the narrowed list for real-money execution-friction measurement. This is the only thing that answers the original Phase 3 question. |

5. If verdict is "extend" or "real money": update this context file with the new plan. If verdict is "no pivot": move all files to `dev/done/` and close PRIORITY_QUEUE entry.

## Files in this set

- `self-use-validation-plan.md` — full plan, decisions, phase breakdown
- `self-use-validation-tasks.md` — checklist
- `self-use-validation-context.md` — this file
- `scripts/real_perf_report.py` — combined performance report
- `scripts/real_perf_split_by_source.py` — split by placer vs manual subset (2026-05-24)

## Next concrete step

User: run the Coolbet placer (`--record`) 3x/day during the 2-week window. No manual placements. No real money. On 2026-06-07, run `real_perf_split_by_source.py --days 14` and read this file's "What to do on 2026-06-07" section.

## Decision log

- 2026-05-10: Plan created. PRIORITY_QUEUE entry filed.
- 2026-05-10: **Phases 0.1, 2.1, 2.2, 2.3, 2.4, 2.5 all shipped in one session.** Sampling script + migrations applied + settlement wired + backend writer + 3 admin pages (`/admin/place`, `/admin/real-bets`, bot-dashboard columns) + 2 API routes + 3 backend smoke tests. Engine pushed: ef2a671. Web pushed: d26ed3e + 7352858.
- 2026-05-10: Phase 0.3/0.4/0.5 (CSV worksheet) marked SUPERSEDED — `/admin/place` modal captures captured_odds + actual_odds on every real bet, so `real_bets.slippage_pct` IS the proxy-quality measurement.
- 2026-05-11: **ACCESSIBLE-BM shipped.** Core measurement fix: engine now only aggregates odds from EU/Estonia-accessible bookmakers (Bet365, Unibet, Betano, Marathonbet, 10Bet, 888Sport, Pinnacle). Previous reported CLV of +12.56% was inflated by SBO/Dafabet/1xBet odds the user can never reach. `recommended_bookmaker` stored on every new `simulated_bets` row (migration 094). `scripts/daily_picks.py` for morning ritual. Engine pushed: 0b05d3b.
- 2026-05-11: **Strategic context:** Betfair Exchange blocked for Estonia (Dec 2025). Pinnacle API closed (July 2025). No automatable book available to Estonian residents. Both automation-era tasks (Super Elite tier) deferred until 500+ users. Current focus: validate real edge via manual betting at Coolbet + Bet365.
- 2026-05-11: **Phase 2.8 complete.** REAL-MONEY-TRACKER (`real_perf_report.py`), BOOKMAKER-DISPLAY (Elite value-bets page, server-side `getValueBetBookOdds`), FRESHNESS-INDICATOR (header chip, `getOddsVerifiedAt`). All Phase 2 work is done. Phase 3 (manual betting) is the only next step.
- 2026-05-24: **Phase 3 mid-run review.** User clarified Phase 3 was paper-only (no real money staked despite plan). 476 bets logged at Coolbet odds: 216 placer (`--record`) + 260 manual `/admin/place`. Placer subset -8.13%, manual subset -10.45%. Selection bias confirmed but smaller than feared (~2.3pp gap). Per-market "Away 1X2 +112.7%" mirage debunked — placer shows -100% on same market. Only `o/u under 2.5` is positive in both subsets. `bot_aggressive` confirmed -5.9% across both subsets at n=246 (real signal, no edge).
- 2026-05-24: **Modeling agent ships `v20260524_market` with 5 bug fixes** (OU features, AH calibration, AH line filter, BTTS/AH eval, double_chance placement). Affects OU/AH/BTTS markets. 1X2 home/draw losses likely NOT explained by today's fixes.
- 2026-05-24: **Decision: pause Phase 4 for 2 weeks.** Wait for new-model placer data. Window 2026-05-24 → 2026-06-07. Broad placer rule (all bots, 5% edge). No `/admin/place` manual. No real money. Created `scripts/real_perf_split_by_source.py` for the 06-07 readout.
