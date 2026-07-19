# Phase 1 diagnostics — three zombie bots (2026-07-19)

Follow-up to `dev/active/backtest-2026-07-19` results. Three bots with a
massive backtest-vs-real firing gap. This doc identifies WHERE each
bot's funnel drops. No prod changes here — investigation only. Fixes
post-vacation.

## Bot 1 — `inplay_o` (Underdog Hold)

**Real vs backtest**: 2 bets in 21d real, 96 in 30d backtest → **~32× gap**

**Strategy** (`workers/jobs/inplay_bot.py:2970`):
- Minute 25-55
- Score exactly 1-0 (home) or 0-1 (away)
- Leading team prematch win prob < 35% (must be true underdog leading)
- Live odds for leader ≥ 2.80
- No red card
- Poisson edge ≥ 4%

**Candidate universe** (last 30d): **51,454 live_match_snapshots** matched
just conditions "score 1-0/0-1 at min 25-55". Not the shortage.

**InplayBot logs** show 20-25 K snapshots evaluated per 8-min cycle,
3-6 bets fire, `funnel since-last: [empty]`. Empty funnel means the
rejection is happening BEFORE the counters we track, or the counters
reset too fast to be captured.

**Most likely production cuts** (backtest skips these):
1. **Prematch xG data missing**. Strategy uses `pm.get("prematch_xg_home") or 1.1`
   fallback. Backtest with fallback = wider edge calc. Real prod might
   reject when xG data is missing (via `MIN_LEAGUE_XG_MATCHES=3` or
   similar upstream gate).
2. **Live odds staleness check** — replay skips `_score_recheck`.
3. **Sharp consensus / Pinnacle veto** on inplay bets (if applied).
4. **Existing-bet dedupe** — real prod tracks "already bet on this
   match+strategy_key"; backtest replay may deduplicate differently.

**Phase 2 fix scope**:
- Add per-strategy funnel counters (currently generic across all inplay
  strategies). ~30 min.
- One 24h run with verbose per-strategy logging → identify exact drop
  stage. Then loosen the specific gate if it's a soft filter, or accept
  the strategy's naturally-rare firing rate if it's the underdog +
  odds combo doing the cutting.

## Bot 2 — `bot_dnb_specialist` (CALIBRATED)

**Real vs backtest**: 0 bets in 21d real, 63 in 45d backtest

**Config** (`workers/jobs/daily_pipeline_v2.py:913`) — 2 profiles, tight
league whitelists:
- **DNB Home**: 5 leagues (Austria Bundesliga, Mexico Liga MX, Russia
  Premier, Israel Liga Leumit, Uruguay Segunda) · edge ≥ 5% · odds
  1.30-1.90 · min_prob 0.60
- **DNB Away**: 5 leagues (England L2, Sweden Allsvenskan, Brazil Serie
  B, England Championship, Argentina Primera Nacional) · edge ≥ 5-6% ·
  odds 1.60-2.60 · min_prob 0.50

**Primary cutter**: **seasonal — most whitelisted leagues on summer
break**. Of the 10 whitelisted leagues:
- Season-active (mid-July): Mexico Liga MX, Sweden Allsvenskan, Brazil
  Serie B, Argentina Primera Nacional, Uruguay Segunda — ~5
- On break: Austria Bundesliga (starts late July), Russia Premier (Aug),
  Israel Liga Leumit (break), England L2 + Championship (pre-season) — 5

So half the whitelist is off-season. Combined with tight edge≥5% +
odds 1.30-2.60 + min_prob 0.50-0.60, current daily match supply on
active whitelist leagues isn't producing qualifying candidates.

Backtest window (45d, 2026-06-04 → -07-18) included late-May season
data where these leagues were active.

**Verdict**: **Not a bug — expected seasonal behavior**. Bot will wake
up naturally when whitelisted leagues restart (Austria late July,
Israel Aug, Russia Aug, England L2/Champ Aug). No action needed.

**Optional Phase 2**: consider adding summer-active leagues to the
whitelist to keep the bot firing year-round. Would need shadow-bets
signal on those additions first.

## Bot 3 — `bot_btts_all` (beta)

**Real vs backtest**: 0 bets in 21d real, 548 in 45d backtest → **large
gap even in overlapping time window**

**Config** (`workers/jobs/daily_pipeline_v2.py:338`):
- Markets: `btts`
- Edge threshold: **12% across all tiers** (was tightened from 3-4% via
  the 2026-05-25 PER-BOT-EDGE-THRESHOLD-APPLY sweep: baseline -0.3% →
  +5.8% ROI at 12%, n=331)
- Odds: 2.00-2.80
- Min prob: 0.30

**Primary cutter**: **calibration gap**. The backtest tool computes
edge from **raw model probability** (per its docstring: skips Pinnacle
veto, sharp_consensus, and calibration stack). Real prod applies BTTS
Platt calibration (fitted 2026-05-27, offline holdout n=139) which
systematically compresses probability estimates toward the mean.

**Downstream effect**: raw model may predict BTTS Yes at 62% (edge 12%
against 50% implied); calibrated prob lands at ~55% (edge 5%); fails
the 12% threshold → 0 bets.

The 12% threshold was chosen against **raw** edges in the sweep, but
is applied to **calibrated** edges in production. Mismatch.

**Phase 2 fix scope**:
1. **Correct diagnosis first**: run a query on `predictions` +
   `simulated_bets` shadow to compute the calibrated-edge distribution
   for BTTS candidates in prod. If most cluster at 4-8%, that confirms
   the calibration gap.
2. **Fix option A**: loosen threshold to 6-7% edge (calibrated) and
   see if ROI holds. Small — one BOTS_CONFIG edit.
3. **Fix option B**: re-run the sweep against calibrated edges, not
   raw. Pick the actual optimum on production data.

## Summary

| Bot | Root cause | Fix urgency | Fix effort |
|---|---|---|---|
| inplay_o | Unclear — need per-strategy funnel logging | Medium (real +ROI signal) | Investigate: 30m. Fix: 0-2h depending on finding. |
| bot_dnb_specialist | Seasonal — half whitelist on break | **Skip** — will wake up naturally | 0 |
| bot_btts_all | Calibration gap (12% raw threshold vs calibrated prod) | Medium-high | 30m: query calibrated-edge dist. 15m: loosen threshold. |

## Post-vacation queue

- **FIX-INPLAY-O-FUNNEL-VISIBILITY** — instrument per-strategy funnel
  counters, run 24h, identify + close the drop stage.
- **FIX-BTTS-THRESHOLD-CALIBRATED** — re-sweep on calibrated edges,
  ship new BOTS_CONFIG threshold.
- **NO-ACTION-DNB-SPECIALIST** — wait for autumn league restarts.

## Files touched

None. Read-only investigation.
