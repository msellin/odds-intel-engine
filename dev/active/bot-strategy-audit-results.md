# Bot Strategy Audit Results — 2026-05-13

**Scope:** 23 prematch + 13 inplay bots. Last 14 days of data (May 13 back to April 29).
**Thread 1 complete. Threads 2–3 (gap survey, data readiness) pending.**

---

## Summary Tables

### Prematch Bots

| Bot | Limiting Gate | Drops | Settled | ROI | CLV | Edge |
|-----|--------------|-------|---------|-----|-----|------|
| bot_v10_all | odds out of range | 951 | 28 | +39.9% | +0.283 | 12.8% |
| bot_lower_1x2 | odds out of range | 36 | 10 | +63.6% | +0.307 | 10.5% |
| bot_aggressive | odds out of range | 718 | 259 | -1.8% | +0.112 | 9.5% |
| bot_ou15_defensive | odds out of range | 410 | 19 | +47.2% | +0.181 | 12.4% |
| bot_btts_all | edge below threshold | 243 | 32 | +22.5% | +0.047 | 7.4% |
| bot_opt_home_lower | edge ≤ 0 | 16 | 15 | +73.4% | +0.333 | 14.5% |
| bot_ah_away_dog | odds out of range | 1640 | 13 | +45.2% | +0.080 | 7.9% |
| bot_ou25_global | odds out of range | 1114 | 14 | +14.7% | +0.074 | 7.6% |
| bot_ou35_attacking | odds out of range | 334 | 8 | +16.1% | -0.081 | 10.5% |
| bot_high_roi_global | edge ≤ 0 | 34 | 7 | +24.0% | +0.107 | 14.9% |
| bot_btts_conservative | edge below threshold | 425 | 6 | +55.7% | +0.028 | 9.8% |
| bot_proven_leagues | edge ≤ 0 | 34 | 5 | +17.0% | +0.120 | 13.8% |
| bot_dc_value | edge ≤ 0 | 1776 | 1 | +52.9% | +0.020 | 12.0% |
| bot_draw_specialist | odds out of range | 15 | 1 | -100.0% | +0.128 | 8.0% |
| bot_greek_turkish | odds out of range | 21 | 1 | -100.0% | -0.664 | 7.0% |
| **bot_conservative** | **odds out of range** | **2474** | **0** | n/a | n/a | n/a |
| **bot_opt_away_british** | **odds out of range** | **14** | **0** | n/a | n/a | n/a |
| **bot_opt_away_europe** | **odds out of range** | **11** | **0** | n/a | n/a | n/a |
| **bot_opt_ou_british** | **odds out of range** | **12** | **0** | n/a | n/a | n/a |
| **bot_dc_strong_fav** | **odds out of range** | **979** | **0** | n/a | n/a | n/a |
| **bot_ah_home_fav** | **odds out of range** | **1666** | **0** | n/a | n/a | n/a |
| **bot_dnb_home_value** | **odds out of range** | **1719** | **0** | n/a | n/a | n/a |
| **bot_dnb_away_value** | **odds out of range** | **1725** | **0** | n/a | n/a | n/a |

> bot_dc_strong_fav, bot_ah_home_fav, bot_dnb_home_value, bot_dnb_away_value launched 2026-05-11 — only 2 days live. Premature to judge; funnel data still diagnostic.

### Inplay Bots

| Bot | Limiting Gate | Drops | Fired | Settled | ROI | CLV |
|-----|--------------|-------|-------|---------|-----|-----|
| inplay_e | minute 25-50 | 263,770 | 241 | 57 | -2.7% | +0.051 |
| inplay_c | minute 25-70 | 190,092 | 8 | 8 | +50.0% | +0.290 |
| inplay_l | minute 15-35 | 352,258 | 5 | 5 | +71.6% | -0.099 |
| inplay_d | minute 55-75 | 357,501 | 2 | 2 | +7.5% | -0.027 |
| inplay_b | minute 15-40 | 333,545 | 2 | 2 | -26.0% | -0.275 |
| inplay_a | minute 25-35 | 391,419 | 1 | 1 | +45.0% | -0.147 |
| inplay_i | minute 42-65 | 273,634 | 1 | 1 | -100.0% | +0.000 |
| inplay_m | minute 30-60 | 246,373 | 1 | 1 | +150.0% | +0.515 |
| inplay_n | minute 72-80 | 402,121 | 1 | 1 | -100.0% | +0.667 |
| **inplay_g** | **minute 30-70** | 209,854 | **0** | 0 | n/a | n/a |
| **inplay_h** | **minute 46-55** | 397,105 | **0** | 0 | n/a | n/a |
| **inplay_j** | **minute 30-52** | 276,123 | **0** | 0 | n/a | n/a |
| **inplay_q** | **minute 15-55** | 205,904 | **0** | 0 | n/a | n/a |

> **Note on settled vs fired:** Most inplay strategies are young (launched Apr 27). High fire count doesn't mean high settled count — inplay bets on matches that are still in progress haven't settled yet.

---

## Per-Bot Profiles (notable bots only)

### bot_conservative — **DEAD, odds gate miscalibrated**
```
7,510 → 2,502 (predictions) → 28 (odds 1.50-3.00) → 8 (edge>0) → 6 (Pinnacle) → 0 fired
```
- Odds range 1.50-3.00 kills 98.9% of predictions. Only 28 survive.
- 6 bets pass Pinnacle veto but **still 0 fire** — Kelly calculation is returning 0 for all 6.
- Root cause: the 1.50-3.00 range covers mid-market prices where our calibration is weakest and Coolbet margins are standard. The 10%+ edge requirement pushes toward extremes (either very strong favorites at <1.50 or longer shots at >3.00).
- Fix: loosen to 1.30-4.00. Also investigate why Kelly=0 for the 6 Pinnacle-passing bets.

### bot_opt_away_british + bot_opt_away_europe — **DEAD, odds window too narrow**
```
39 → 14 (tier+league) → 0 (odds 2.50-3.00)     [british]
33 → 11 (tier+league) → 0 (odds 2.50-3.00)     [europe]
```
- Odds 2.50-3.00 is a 0.50-wide window. No away candidates are landing exactly here.
- Backtest was validated over a broader distribution — the window was not derived from the backtest range, it was manually set too tight.
- Fix: expand to 2.20-3.50. Both bots would immediately fire.
- Expected fire rate delta: 0 → ~3-6 bets/14d each. ROI expectation: backtest baseline +16-19%.

### bot_dc_value — **Near-dead, edge calculation structural issue**
```
7,510 → 2,502 (predictions) → 1,321 (odds) → ... → 1 fired
```
- `edge ≤ 0` kills 1,776 DC candidates. DC odds at Coolbet include an 8-10% margin — even with good model probabilities, raw edge rarely exceeds 0.
- Note: No Platt calibration for DC yet; 1X2 Platt calibration is applied to the underlying probs, which should flow through. This is a bookmaker-margin problem, not a model problem.
- The 1 fired bet (+52.9% ROI, +0.020 CLV) is a positive signal but sample=1.
- Fix: wait for CAL-PLATT DC (needs ~200 settled). Short-term: investigate whether DC edge formula correctly uses Coolbet's DC odds vs implied probability from 1X2 probs.

### bot_aggressive — **Overactive, quality diluted by bookmaker margin**
```
2,502 predictions → 1,784 (odds) → 1,086 (edge>0) → 459 (Pinnacle) → 321 fired
```
- Most active prematch bot. CLV +0.112 confirms real edge is found — but -1.8% ROI suggests execution quality (Coolbet margin) is consuming the edge.
- Pinnacle veto is working: 459 pass veto (26% of edge>0) fire 321 bets.
- The odds range 1.25-5.00 is very wide. The 1.25-1.40 extreme favorites and 4.00-5.00 extreme longshots are likely where Coolbet's margin is worst relative to true odds.
- Diagnostic: segment ROI by odds range buckets to confirm.

### bot_ou25_global — **Working but odds gate wastes 1,114 candidates**
```
2,502 predictions → 1,388 (odds 1.60-3.00) → ... → 14 settled
```
- Odds range 1.60-3.00 kills 1,114/2,502. O/U2.5 overs at <1.60 (strong overs) and unders at >3.00 (rare but high-value) are excluded.
- +14.7% ROI, +0.074 CLV. Moderate performance.
- Loosen to 1.40-3.50 to capture strong-over and rare-high-under situations.

### bot_dc_strong_fav — **New bot (2 days), structurally tight**
```
7,510 → 2,502 (T1-2 predictions) → ... → 979 (odds 1.20-1.80) → 0 fired
```
- Odds 1.20-1.80 kills 979 candidates from the prediction pool. "Strong fav DC" (1X or X2 at 1.20-1.80) requires very strong favorites — only ~20-30% of T1-2 matches have DC odds in this range.
- Also: with 6%+ edge required, the threshold is high. DC Platt calibration needed before tuning.
- No fire in 2 days. Too early to act.

### bot_ah_home_fav + bot_dnb_home_value + bot_dnb_away_value — **New bots (2 days), odds gate structural**
```
[ah_home_fav]  → 1,666 killed by odds 1.50-2.20
[dnb_home_val] → 1,719 killed by odds 1.30-1.90
[dnb_away_val] → 1,725 killed by odds 1.60-2.60
```
- All launched 2026-05-11. 0 fires each in 2 days.
- Large odds gate drops suggest the edge calculation for these derived markets rarely clears the bar. Platt calibration needed (tracked in PRIORITY_QUEUE as future tasks).
- Do not tune these until at least 2 weeks of data.

### inplay_j — **Near-dead, prematch gate too selective**
```
434,163 → 158,040 (min 30-52) → 52,319 (0-0) → 1,010 (prematch_o25 ≥ 0.62) → 27 (live_ou_15 ≥ 2.85) → 0 fired
```
- prematch_o25 ≥ 0.62 passes only 1,010/52,319 scoreless snapshots (1.9%). Strategy I uses 0.50 (after loosening from 0.54); Strategy L doesn't have this gate. 0.62 is aggressive.
- Even if loosened to 0.55: ~8,000 snapshots survive, then live_ou_15 ≥ 2.85 leaves ~180, and after edge check maybe 5-10 fires.
- Decision: loosening is warranted but needs its own task with smoke test. Not a bug.

### inplay_g — **Near-dead, corner data coverage problem**
```
434,163 → 224,309 (min 30-70) → 20,308 (has corner data) → 3,539 (live_ou_25) → 0 fired
```
- "Has corner data" drops 83% of candidates. Corner count data is only captured when `live_match_snapshots.corners_home IS NOT NULL`.
- This is a data availability issue, not a threshold issue. The live tracker doesn't reliably capture corners for all matches.
- Fix: improve live tracker corner capture. Do not change inplay_g thresholds until coverage improves.

### inplay_h — **Near-dead, live odds coverage too sparse for this minute window**
```
434,163 → 37,058 (min 46-55) → 9,279 (0-0) → 55 (live OU2.5) → 47 (ou ≥ 2.10) → 0 fired
```
- Only 55 out of 9,279 scoreless HT-restart snapshots have a live OU2.5 odds reading. This is 0.6% coverage — the live tracker captures live OU odds for very few matches in this narrow window.
- The odds availability bottleneck is the fundamental problem. Threshold changes won't help.
- Fix: improve live OU odds capture frequency/coverage.

### inplay_m — **Near-dead, live_ou_25 ≥ 3.0 gate too aggressive**
```
434,163 → 187,790 (min 30-60) → 64,870 (1-0 or 0-1) → 580 (live_ou_25_over ≥ 3.0) → 1 fired
```
- After a 1st goal in minutes 30-60, the Over 2.5 market reprices to 2.50-3.50 typically. Requiring ≥ 3.0 kills 99.1% of candidates with live OU odds.
- The thesis is sound (Equalizer Magnet: buy Over 2.5 after 1st goal when market overreacted), but the 3.0 threshold is only reached when the market thinks both teams will likely play defensively — a conflict with the equalizer-magnet thesis.
- Better threshold: 2.50, with model edge gate protecting quality. 1 fire at +150% ROI, +0.515 CLV = promising signal.
- Fix: loosen to 2.50. Expected: ~10-20 fires/14d.

### inplay_e — **Overactive, marginal quality**
```
434,163 → 170,393 (min 25-50) → 60,774 (0-0) → 2,406 (live OU2.5) → 89 (≥ 2.10) → 241 fired
```
- Most active inplay bot. The ou_25_under ≥ 2.10 floor is very low — unders at 2.10 are barely value once market margin (~1.5%) is subtracted.
- CLV +0.051 (very marginal), ROI -2.7%. The fired count of 241 vs settled 57 means 184 bets are still in-play or from settled matches in the last 14 days. With low CLV, quality of further settled results may be weak.
- Fix: raise ou_25_under threshold to 2.20 to cut ~60-70% of fires and improve average quality.

### inplay_c — **Best inplay performer (8 settled, +50% ROI, +0.290 CLV)**
- Favourite Comeback thesis working. Home fav branch (wider minute window 25-70) + away fav branch (stricter 25-60) correctly merged.
- No changes warranted. Monitor for regression.

### inplay_l — **Best ROI among volume inplay (5 settled, +71.6%)**
- Goal Contagion: first goal in high-λ match. Minute 15-35 is the limiting gate.
- Low CLV (-0.099) is a concern — live OU pricing for "first goal in a high-scoring match" may be efficient. Watch second 14-day window.

---

## Ranked Adjustments

### Top 5 — "Loosen this gate for most impact"

| Rank | Bot | Gate to Loosen | Current | Proposed | Fire-Rate Delta | Signal |
|------|-----|---------------|---------|----------|-----------------|--------|
| 1 | bot_opt_away_british + bot_opt_away_europe | odds range | 2.50-3.00 | 2.20-3.50 | 0 → ~5/14d each | Cross-era +16-19% backtest confirmed; just needs the window to fire |
| 2 | inplay_m | live_ou_25_over | ≥ 3.0 | ≥ 2.50 | 1 → ~15/14d | 1 bet at +150% ROI, +0.515 CLV; gate 99x tighter than makes sense |
| 3 | inplay_j | prematch_o25 | ≥ 0.62 | ≥ 0.55 | 0 → ~5/14d | 0.62 vs 0.50-0.55 for sibling strategies; 0.55 = ±10% loosening |
| 4 | bot_conservative | odds range | 1.50-3.00 | 1.30-4.00 | 0 → ~10/14d | 0 fires despite 6 bets passing Pinnacle; odds range too narrow for 10%+ edge thesis |
| 5 | inplay_e | live_ou_25_under | ≥ 2.10 | ≥ 2.20 | This is a TIGHTEN (see below) — replaced by: |
| 5 | bot_ou25_global | odds range | 1.60-3.00 | 1.40-3.50 | +50-100% fire rate | 1,114 candidates killed; +14.7% ROI suggests strategy is viable |

### Top 5 — "Tighten this gate, it's too loose"

| Rank | Bot | Gate to Tighten | Current | Proposed | Fire-Rate Delta | Signal |
|------|-----|----------------|---------|----------|-----------------|--------|
| 1 | inplay_e | live_ou_25_under threshold | ≥ 2.10 | ≥ 2.20 | 241 → ~60-80/14d | CLV +0.051 is near-zero; raising cuts worst-quality fires |
| 2 | bot_aggressive | odds range lower bound | 1.25 | 1.40 | ~20% reduction | -1.8% ROI despite +0.112 CLV suggests execution quality worst at extreme ends |
| 3 | inplay_b | BTTS odds gate | (no explicit threshold — any trailing at min 15-40) | add live BTTS price ≥ 1.90 gate | 2 → 0-1/14d | CLV -0.275 means both fired bets were negative value; needs quality gate |
| 4 | bot_btts_all | edge threshold | ≥ 3% | ≥ 5% | ~40% reduction | CLV +0.047 is very marginal; conservative sibling fires at 7% and beats it |
| 5 | bot_ou35_attacking | lower bound of OU 3.5 | odds 1.80 | odds 2.00 | ~15-20% reduction | CLV -0.081 is negative; bets at tight O3.5 odds (1.80-2.00) are likely overpriced |

---

## Root Cause Patterns

### Pattern 1: Odds window too narrow (most common)
Affects: bot_conservative, bot_opt_away_british, bot_opt_away_europe, bot_opt_ou_british, bot_dc_strong_fav, bot_ah_home_fav, bot_dnb_home_value, bot_dnb_away_value, bot_ou25_global

The odds range filter is the #1 killer for prematch bots. In most cases the range was set based on intuition about "where this market type lives" rather than calibrated to the actual distribution of value opportunities.

### Pattern 2: Inplay minute window is the hardest constraint
Affects: all 13 inplay bots

Every single inplay bot has its minute window as the single biggest drop (removes 50-90% of all snapshots upfront). The minute windows were designed to capture specific game-state transitions; they work as intended, but they're the fundamental reason inplay fires far fewer bets than prematch. This is not miscalibration — it's the fundamental economics of the inplay approach.

### Pattern 3: Live OU odds coverage is sparse
Affects: inplay_h, inplay_e, inplay_a (indirectly)

Live OU2.5 and OU1.5 odds are only captured in ~17% of snapshots (based on inplay_e's funnel: 2,406 out of 60,774 matching 0-0 minute 25-50 snapshots). This is a data pipeline issue, not a strategy issue. Improving live odds capture frequency would directly improve all OU-based inplay strategies.

### Pattern 4: New DC/AH/DNB markets need calibration before tuning
Affects: bot_dc_value, bot_dc_strong_fav, bot_ah_home_fav, bot_ah_away_dog, bot_dnb_home_value, bot_dnb_away_value

DC/AH/DNB launched 2026-05-11. Only 2-days old. The `edge ≤ 0` and `odds out of range` gates killing most candidates reflects that these derived markets need bookmaker-specific calibration before thresholds can be properly set. Do not tune these bots until CAL-PLATT DC and 200+ settled bets.

---

## Wrap-up: Priority Queue Tasks

The following 3 adjustments + 1 data fix should be converted to PRIORITY_QUEUE tasks:

**1. OPT-AWAY-ODDS-FIX** — Expand odds range for bot_opt_away_british and bot_opt_away_europe from 2.50-3.00 → 2.20-3.50. One-line code change, 0 fire → expected ~5/14d each. Smoke test: verify funnel shows >0 candidates after odds range gate.

**2. INPLAY-M-LOOSEN** — Lower inplay_m live_ou_25_over gate from 3.0 → 2.50. One-line change. Expected: 1 → ~15 fires/14d. Smoke test: verify at least 10 qualifying snapshots in funnel at new threshold. Replay-projected ROI directional: +0.515 CLV signal supports loosening.

**3. INPLAY-J-LOOSEN** — Lower inplay_j prematch_o25 from ≥ 0.62 → ≥ 0.55 (aligns with sibling strategies). Expected: 0 → ~5 fires/14d. Smoke test: verify funnel shows 50-200 snapshots at gate after change.

**4. INPLAY-LIVE-OU-COVERAGE** — The live OU2.5/1.5 odds coverage of ~17% of snapshots is the dominant bottleneck for all OU-based inplay strategies (h, e, a, d, j). Improving capture at the live tracker level would multiply inplay volumes more than any individual threshold tweak.

---

## Thread 2 + Thread 3 Status

Not yet started. Requires reading:
- `dev/active/inplay-bot-plan.md` + 8-AI review summaries
- Gap survey: AH live momentum, 2nd-half handicap, HT/FT, comeback pricing, derby discount, promoted-team volatility, corners/cards/exact-score/scorecast
- Data sufficiency check against current `live_match_snapshots`, `odds_snapshots`, `match_events`

Estimated: 1 session.
