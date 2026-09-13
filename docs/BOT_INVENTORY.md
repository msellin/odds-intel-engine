# Bot inventory — auto-generated

95 bots · 20 active · 75 retired


CLV is on **placeable books only** (Kambi and Pinnacle excluded — see the script header for why). `best cfg` is fold-robust: positive in every walk-forward fold. Configs on n<30 are marked `thin` and are not recommendations.


| bot | status | n | CLV% | t | ROI% | best fold-robust cfg | cfg CLV | cfg n | live successor | retired reason |
|---|---|---|---|---|---|---|---|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | active | 56 | +12.0 | +8.3 | +17.6 | as-is | +12.0 | 56 | — | — |
| `bot_unibet_trigger_sharp_1x2_v1` | active | 54 | +10.7 | +4.4 | -5.1 | as-is | +10.7 | 54 | — | — |
| `bot_aggressive_v2` | retired 2026-06-06 | 13 | +9.1 | +1.4 | -44.0 | none fold-robust | — | 0 | bot_coolbet_ou_model_v1, bot_coolbet_trigger_ou_v1 | BOT-AGGRESSIVE-V2-DECIDE (2026-06-06): retired after persistent loss across all-time and post-cutoff cohorts.  |
| `bot_proven_leagues` | retired 2026-05-28 | 10 | +3.5 | +0.9 | +57.8 | drop draw | +4.2 | 30 | — | PROVEN-LEAGUES-REFACTOR 2026-05-28: Historical 2005-2015 backtest edge no longer exists in Scotland (-78% Pinn |
| `bot_sweep_ou35_v1` | retired 2026-09-08 | 278 | +3.4 | +6.9 | -2.5 | edge>=13% | +9.4 | 32 | bot_coolbet_trigger_sharp_ou_v1, bot_trigger_ou_sharp_v1 | SHADOW-BOT-CONSOLIDATION 2026-09-08: line-shop signal loses out-of-sample (BOT-2D-AUDIT held-out); model-edge  |
| `bot_sweep_ou25_v1` | retired 2026-09-08 | 330 | +3.3 | +8.0 | -6.9 | edge>=13% | +14.4 | 37 | bot_coolbet_trigger_sharp_ou_v1, bot_trigger_ou_sharp_v1 | SHADOW-BOT-CONSOLIDATION 2026-09-08: line-shop signal loses out-of-sample (BOT-2D-AUDIT held-out); model-edge  |
| `bot_summer_specialist` | retired 2026-09-09 | 24 | +3.3 | +1.4 | -7.4 | edge>=8% | +3.5 | 31 | — | BETA-BOT-AUDIT 2026-09-09: -5.7% ROI, -2.0% CLV, 1x2 leg -52%, 52% v10 duplicate |
| `bot_coolbet_value_v1` | retired 2026-09-08 | 544 | +3.1 | +7.6 | +1.4 | edge>=13% | +12.6 | 65 | — | Line-shop retired 2026-09-08 — loses OOS (§52); the two model-edge Coolbet bots are the real-money path. Could |
| `bot_pin_1x2_home_v1` | retired 2026-09-08 | 433 | +2.9 | +6.7 | -0.6 | edge>=13% | +8.8 | 67 | bot_coolbet_trigger_sharp_1x2_v1, bot_trigger_1x2_sharp_v1 | SHADOW-BOT-CONSOLIDATION 2026-09-08: line-shop signal loses out-of-sample (BOT-2D-AUDIT held-out); model-edge  |
| `bot_v10_all` | active | 298 | +1.7 | +1.5 | +3.9 | edge>=13% | +19.6 | 59 | — | — |
| `bot_ou35_attacking` | retired 2026-05-28 | 7 | +1.5 | +0.4 | -17.9 | none fold-robust | — | 0 | bot_coolbet_trigger_sharp_ou_v1, bot_trigger_ou_sharp_v1 | OU-DC-CONSOLIDATION 2026-05-29: merged into bot_ou_specialist as Over 3.5 Global profile. |
| `bot_sweep_1x2_home_v1` | retired 2026-08-26 | 34 | +1.4 | +0.8 | +35.8 | none fold-robust | — | 0 | bot_1h_1x2_paper_shadow_v1, bot_coolbet_1x2_model_v1 | — |
| `bot_ou15_defensive` | retired 2026-05-24 | 26 | +1.2 | +0.2 | +24.6 | none fold-robust | — | 0 | bot_coolbet_trigger_sharp_ou_v1, bot_trigger_ou_sharp_v1 | BOT-OU15-DIAGNOSE-CLOSE 2026-05-25: 17-day silent period (2026-05-08 → 2026-05-25). All diagnostics ruled out: |
| `bot_high_roi_global_v2` | active | 20 | -0.4 | -0.1 | +36.8 | edge>=10% | +3.1 | 30 | — | — |
| `bot_opt_home_lower` | retired 2026-09-08 | 42 | -0.4 | -0.2 | -13.4 | edge>=10% | +2.7 | 33 | — | SHADOW-BOT-CONSOLIDATION 2026-09-08: no fold-robust profitable 2D frame + duplicate/negative (owner-approved) |
| `bot_lower_1x2` | retired 2026-06-01 | 130 | -0.5 | -0.5 | +13.2 | none fold-robust | — | 0 | bot_1h_1x2_paper_shadow_v1, bot_coolbet_1x2_model_v1 | RETIRE-LOWER-1X2 2026-06-01: stale-flag fix. T2-4 1X2-only. Original 2026-05-17 retirement: shrinkage_alpha_t2 |
| `bot_high_alignment` | retired 2026-06-21 | 757 | -1.8 | -2.2 | -7.1 | edge>=10%+drop x2 | +5.9 | 263 | — | RETIRE-BAD-PERFORMERS 2026-06-21: 60d real n=52 ROI -21.68% PnL -EUR60 vs 60d sim n=499 ROI -1.66% = +20pp div |
| `bot_aggressive` | retired 2026-06-01 | 119 | -2.0 | -2.2 | +7.3 | none fold-robust | — | 0 | — | Replaced by bot_aggressive_v2. -5.7% ROI / -€141 on 441 settled bets — single biggest drag on portfolio ROI. L |
| `bot_proven_leagues_v2` | retired 2026-09-08 | 25 | -3.0 | -1.4 | -10.0 | none fold-robust | — | 0 | bot_coolbet_trigger_sharp_1x2_v1, bot_trigger_1x2_sharp_v1 | SHADOW-BOT-CONSOLIDATION 2026-09-08: no fold-robust profitable 2D frame + duplicate/negative (owner-approved) |
| `bot_no_pin_home_v1` | retired 2026-08-24 | 51 | -3.4 | -1.6 | -2.7 | none fold-robust | — | 0 | bot_coolbet_trigger_sharp_1x2_v1, bot_trigger_1x2_sharp_v1 | PER-BOT-SWEEP-2026-08-24: negative at EVERY edge threshold tested (-5.3% to -7.4% across 0.02-0.20) and in 2 o |
| `bot_ou25_global` | retired 2026-05-28 | 53 | -3.7 | -4.5 | +3.0 | none fold-robust | — | 0 | bot_coolbet_ou_model_v1, bot_coolbet_trigger_ou_v1 | OU25-SPECIALIST 2026-05-29: -6.2% ROI on broad coverage. Replaced by bot_ou25_specialist with confirmed league |
| `bot_dc_value` | retired 2026-05-28 | 1572 | -4.3 | -24.3 | -7.4 | none fold-robust | — | 0 | bot_coolbet_trigger_sharp_ou_v1, bot_trigger_ou_sharp_v1 | OU-DC-CONSOLIDATION 2026-05-29: merged into bot_dc_specialist as DC Global profile. |
| `bot_no_pin_shadow_v1` | retired 2026-08-21 | 74 | -4.3 | -1.9 | -18.9 | none fold-robust | — | 0 | bot_1h_1x2_paper_shadow_v1, bot_coolbet_1x2_model_v1 | SHADOW-BOT-CLEANUP-2026-08-21 — unfiltered no-pin shadow loses on draw/away (see per-selection audit). Refined |
| `bot_dc_strong_fav` | retired 2026-05-28 | 862 | -4.4 | -20.6 | -4.4 | none fold-robust | — | 0 | bot_coolbet_ou_model_v1, bot_coolbet_trigger_ou_v1 | OU-DC-CONSOLIDATION 2026-05-29: redundant subset of bot_dc_value. Retired without merge — fully covered by DC  |
| `bot_dc_specialist` | retired 2026-06-01 | 1518 | -4.4 | -24.2 | -7.2 | none fold-robust | — | 0 | bot_coolbet_ou_model_v1, bot_coolbet_trigger_ou_v1 | RETIRE-DC-SPECIALIST 2026-06-01: -7.53% ROI on 58 settled bets since 2026-05-24 (+3.68% CLV — strong line agre |
| `bot_sweep_1x2_draw_v1` | retired 2026-08-26 | 15 | -4.6 | -4.0 | -56.7 | none fold-robust | — | 0 | bot_1h_1x2_paper_shadow_v1, bot_coolbet_1x2_model_v1 | — |
| `bot_coolbet_ou_model_v1` | active | 15 | -5.1 | -2.8 | -38.8 | none fold-robust | — | 0 | — | — |
| `bot_unibet_trigger_ou_v1` | active | 205 | -7.8 | -23.0 | +5.5 | none fold-robust | — | 0 | — | — |
| `bot_trigger_ou_model_v1` | active | 136 | -8.2 | -16.5 | +3.2 | none fold-robust | — | 0 | — | — |
| `bot_trigger_1x2_model_v1` | active | 370 | -8.4 | -11.9 | -14.6 | none fold-robust | — | 0 | — | — |
| `bot_unibet_trigger_1x2_v1` | active | 302 | -8.5 | -6.8 | -7.0 | none fold-robust | — | 0 | — | — |
| `bot_coolbet_trigger_ou_v1` | active | 343 | -8.7 | -30.9 | -3.4 | none fold-robust | — | 0 | — | — |
| `bot_coolbet_trigger_1x2_v1` | active | 272 | -9.1 | -14.2 | -22.8 | none fold-robust | — | 0 | — | — |
| `bot_1h_1x2_paper_shadow_v1` | active | 170 | — | — | +2.8 | none fold-robust | — | 0 | — | — |
| `bot_acca_leg_shadow` | retired 2026-08-21 | 0 | — | — | — | none fold-robust | — | 0 | bot_coolbet_ou_model_v1, bot_coolbet_trigger_ou_v1 | SHADOW-BOT-CLEANUP-2026-08-21 — hypothetical acca-leg-as-single audit complete. n=532 settled, ROI -9.2%, CLV  |
| `bot_ah_away_dog` | retired 2026-06-06 | 34 | — | — | +17.4 | none fold-robust | — | 0 | bot_coolbet_ou_model_v1, bot_coolbet_trigger_ou_v1 | CLV-BACKFILL 2026-06-06: -29.87% CLV at n=8 (t=-2.50). Taking systematically bad prices vs Pinnacle close. Ver |
| `bot_ah_home_fav` | retired 2026-06-24 | 75 | — | — | -6.7 | none fold-robust | — | 0 | bot_coolbet_ou_model_v1, bot_coolbet_trigger_ou_v1 | AH-RETIRE 2026-06-24: -13.63%% ROI on 132 settled bets (€-94 on €689 staked) since 2026-05-24. One good week ( |
| `bot_btts_all` | retired 2026-09-03 | 79 | — | — | +5.5 | none fold-robust | — | 0 | bot_1h_1x2_paper_shadow_v1, bot_coolbet_1x2_model_v1 | Reactivated 2026-06-21: BTTS Platt calibration landed (ECE 14.9 → 1.4 on n=165). Previous retirement reason (P |
| `bot_btts_conservative` | retired 2026-05-27 | 29 | — | — | +6.4 | none fold-robust | — | 0 | — | Model miscalibrated on BTTS: predicted 62.1% hit rate vs actual 46.5% (15.6pp gap). Edge signal unreliable — c |
| `bot_corners_paper_shadow_v1` | active | 381 | — | — | -3.7 | none fold-robust | — | 0 | — | — |
| `bot_ou35_model_v1` | active | 0 | — | — | — | none fold-robust | — | 0 | — | — |
| `bot_sweep_btts_yes_v1` | retired 2026-09-03 | 28 | — | — | +13.1 | none fold-robust | — | 0 | — | — |
| `bot_team_total_paper_shadow_v1` | active | 297 | — | — | -0.6 | none fold-robust | — | 0 | — | — |
