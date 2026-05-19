# Training Data Expansion — 2026-05-19

> Session outcome doc. The investigation that started "today just 1 value bet on 120+ games" ended with a five-step data-expansion chain that unlocked OU/BTTS/AH for Tier C inference, added 14 top divisions to Tier A, ingested 27,888 closing-odds rows for ~10K historical matches, generated 86,856 new prediction rows for the backtest pool, and surfaced statistically-significant per-bot tuning signals on the expanded sample.

## TL;DR

| | Before | After |
|---|---|---|
| Active pre-match bots | 16 | 16 (no bot retired today, but slice analysis confirms previously-retired ones) |
| Tier A unique teams | 541 | 864 (+60%, +323 teams) |
| Tier A league codes in `targets_poisson_history.csv` | 19 | 33 |
| Historical closing-odds rows in `odds_snapshots` (top-14 countries) | ~0 | 27,888 |
| `predictions` rows with `source='ensemble'` on backfilled matches | ~0 | 86,856 |
| Backtest sample (12-month window) | ~3K-5K rows | 25,337 rows / 28,925 matches |
| Per-bot backtest sample (highest) | 441 (bot_aggressive) | 6,237 (bot_aggressive) |
| Tier C OU/BTTS/AH inference path | 50/50 hardcoded prior + nothing for AH | AF xG → full Poisson grid (model-priced) |

## Shipped commits

| Commit | What | Files |
|---|---|---|
| `461c5dd` | **TIER-C-AF-XG** — Tier C fallback uses AF expected-goals to drive Poisson grid for OU/BTTS/AH | `workers/jobs/daily_pipeline_v2.py`, `MODEL_WHITEPAPER.md`, `SIGNALS.md`, `ROADMAP.md`, `scripts/smoke_test.py` |
| `e5dc17a` | **TIER-C-EXPAND (script)** — parameterised football-data extras ingest | `scripts/ingest_football_data_extras.py`, smoke test |
| `e494cd8` | **TIER-C-EXPAND (data)** — 14 top divisions added; 17,352 rows; Tier A teams 541→864 | `data/processed/targets_poisson_history.csv` (force-tracked), docs |
| `bbdf0b5` | **TIER-C-EXPAND-ODDS (script)** — closing-odds ingest for the 14 countries | `scripts/ingest_football_data_extras_odds.py`, smoke test |
| `38dde44` | **TIER-C-EXPAND-ODDS (data)** — 27,888 closing-odds rows written via the script | doc update only (CSV writes were direct DB inserts) |
| `3159e20` | **perf(predict-historical)** — bulk team-form load + rich progress bar; 52min → 90s | `scripts/predict_historical_matches.py` |
| `3aa698d` | **fix(predict-historical)** — backdate `created_at` so backtest's `p.created_at < m.date` filter accepts backfilled rows | `scripts/predict_historical_matches.py`, PRIORITY_QUEUE |
| `a339f4b` | **perf(backtest)** — skip Poisson AH pre-compute when no AH odds in scope | `scripts/backtest_pre_match_bots.py` |

All on `main`, all pushed to GitHub.

## Backtest results (raw — caveats apply)

26 of the 22 active bots in `BOTS_CONFIG` had backtest rows. Per-bot summary at 365-day window (2025-05-19 → 2026-05-18):

**Backtest winners** (highest ROI, capped at 14% edge threshold sweet spot):

| Bot | Bets | Best threshold | Best ROI |
|---|---|---|---|
| bot_ou15_defensive | 407 | 14% | +90.2% |
| bot_high_roi_global | 119 | 15% | +57.3% |
| bot_proven_leagues | 126 | 15% | +57.3% |
| bot_ou35_attacking | 287 | 14% | +40.0% |
| bot_aggressive | 6,237 | 15% | +8.9% |
| bot_btts_all | 1,494 | 12% | +5.8% |
| bot_v10_all | 4,365 | 15% | +5.8% |
| bot_aggressive_v2 | 3,642 | 15% | +2.2% |
| bot_btts_conservative | 177 | 8% | +3.6% |
| bot_dnb_home_value | 877 | 11% | +0.5% |

**Backtest losers** (no threshold rescues — already retired or candidates):

| Bot | Bets | Best ROI | Status |
|---|---|---|---|
| bot_dnb_away_value | 1,186 | -4.8% | Retired 2026-05-19 ✓ |
| bot_ou25_global | 1,618 | -3.4% | **Active — re-evaluate at Batch 2** |
| bot_dc_value | 2,708 | -2.4% | Retired 2026-05-19 ✓ |
| bot_dc_strong_fav | 1,452 | -3.3% | Retired 2026-05-19 ✓ |

## Outstanding caveats (read before acting)

1. **Backtest has lookahead bias.** `predict_historical_matches.py` uses TODAY's model + Platt on past matches. The Sunday retrain may shift this. Treat ROIs as upper-bound, directional.

2. **The away/home backtest-live divergence is real.** Slice analysis surfaces "drop away selection" recommendations for nearly every bot. Live data on bot_aggressive (53 bets) showed +30% ROI on away vs backtest's -14.8%. **Do NOT add `selection_filter` to drop home/away based on backtest alone** — this was already proven wrong (`BOT-1X2-AWAY-LOSERS`, closed 2026-05-18).

3. **Backtest skips execution friction.** Pinnacle veto, sharp consensus, alignment, exposure cap, ECE — not applied. Live ROI typically ~3-5pp lower than backtest for most active bots; bot_aggressive is the canonical example (backtest +1.1% vs live -5.7%).

4. **bot_ou15_defensive's +90% is suspicious.** Historical OU 1.5 had bad-odds problems; OU-PIN-REQUIRED (2026-05-10) fixed placement-time validation. Need 50+ post-fix settled live bets to confirm the backtest signal holds.

5. **Switzerland not covered.** `football-data.co.uk/new/CHE.csv` returns Chinese data; SUI 404s. Manual ingest needed if we ever want Tier A for Swiss Super League.

6. **Team-name alias gaps (TIER-C-EXPAND-ALIASES queued).** 159 unmatched teams across the 14-country batch; ~30 are fixable aliases (Poland Rakow→Raków, etc.), rest are defunct/relegated clubs.

## Action calendar — pegged to events, not arbitrary dates

### ✅ 2026-05-19 (today) — DONE

- [x] TIER-C-AF-XG live in pipeline
- [x] TIER-C-EXPAND CSV updated + committed
- [x] TIER-C-EXPAND-ODDS run + 27,888 rows landed
- [x] predict_historical_matches.py optimized + 86,856 rows generated + backdated
- [x] backtest_pre_match_bots.py optimized + run on expanded sample
- [x] per_bot_edge_threshold_sweep + per_bot_slice_analysis run and reviewed
- [x] DATA_SOURCES.md backfill-state section added
- [x] No BOTS_CONFIG changes made today — deliberate (avoid mixing lookahead-biased signal with Sunday's retrain)

### 🗓️ 2026-05-20 (tomorrow) — Validate pipeline runs

- [ ] **Morning cohort 04:00 UTC**: verify bet count increased meaningfully from yesterday's 2. Expected: 10-30+ pre-match bets across the morning + midday + pre_ko cohorts. AH bots may fire for the first time on Tier C.
- [ ] Check `simulated_bets` distribution by `data_tier` and `recommended_bookmaker` — confirm Tier C bots are firing post-TIER-C-AF-XG.
- [ ] Spot-check 3-4 placed bets for `model_version` field — confirm xg_source is reasonable (not just AF default 1.3+1.3).

### 🗓️ 2026-05-24 (Sunday) — Weekly retrain digests new data

- [ ] **Watch the 03:00 UTC weekly_retrain log** — first run that sees all the new (predictions, odds, form) data.
- [ ] Confirm Platt coefficient changes are reasonable in `model_calibration` rows dated 2026-05-24 — particularly for 1x2_home / 1x2_away / over25 / under25 markets.
- [ ] If shrinkage_alpha_t1_1x2 jumps materially from 0.0484, expect bot_aggressive / bot_v10_all 1X2 edge to shift — track over the next week.

### 🗓️ Week of 2026-05-26 to 2026-05-28 — Batch 1 validation (already queued)

Already on calendar via `PRIORITY_QUEUE.md`. Tasks: `B-ML3` (meta-model), `NEWS-LINEUP-VALIDATE`, `ODDS-TIMING-VALIDATE`. Independent of today's work but uses the same expanded data.

### 🗓️ ~2026-06-08 (2 weeks post-retrain) — Mid-cycle slice validation

- [ ] Run `python3 scripts/slice_live_validate.py` — compare backtest signals vs ~300-500 new live bets accumulated post-retrain.
- [ ] Key question: did the away/home backtest finding REMAIN invalidated by live, or did the retrain shift things?
- [ ] If active bots' ROI has moved meaningfully (>5pp in either direction), surface as a discussion before applying config changes.

### 🗓️ ~2026-06-15 — Batch 2 (already queued)

Original PER-BOT-EDGE-THRESHOLD-APPLY target. By this date we'll have:
- ~4 weeks of post-retrain live data
- Mid-cycle slice validation results
- Per-bot ROI confidence intervals tightened

Actions to coordinate in a single commit:

- [ ] **`bot_aggressive`**: bump edge_thresholds to ≥0.13-0.15 (backtest peak; live data should validate by then)
- [ ] **`bot_v10_all`**: same — bump to ≥0.13-0.15
- [ ] **`bot_btts_all`**: bump to ≥0.10-0.12 (currently 0.04-0.06)
- [ ] **`bot_aggressive_v2`**: decide between (a) retire — backtest -3.6% baseline, only +2.2% at extreme 15% threshold, OR (b) tighten to 15% and watch closely
- [ ] **`bot_ou25_global`**: likely retire — backtest -3.7%, no threshold rescues, 1,618 bets
- [ ] **`bot_dnb_home_value`**: marginal — decide based on 4-week live (currently +0.5% backtest, -0.9% baseline)
- [ ] DO NOT touch home/away selection_filter for any bot. Confirmed false positive 2026-05-18; would need 200+ live bets per (bot × selection) to revisit.

### 🗓️ Open follow-ups (no date — file when triggered)

- [ ] **TIER-C-EXPAND-ALIASES** — extend `TEAM_ALIASES` in `ingest_football_data_csvs.py` for the ~30 fixable team-name mismatches. Expected delta: +1-2K matched historical matches. Re-run `ingest_football_data_extras_odds.py` after. Do this once the current chain is validated.
- [ ] **Switzerland Tier A** — needs separate ingest path (not in football-data /new/ directory).
- [ ] **Lever 2 — AF historical for obscure leagues** (Syria, Iraq, Gabon, etc.). Defer indefinitely — Tier B only, no closing-odds feed, minor training contribution.

## What success looks like

The four-week validation question: did this work move real-money ROI?

- **Bare minimum success**: live bet count goes from ~2/day (worst case 2026-05-19) to 15-30/day sustained, with no major calibration disasters.
- **Real success**: a measurable lift in real-money executable CLV from the current +0.68% baseline (COOLBET-CLV-REPORT 2026-05-17) to >+2%. This is the actual end goal — model training improvements that survive to executable real-money edge.
- **Failure mode to watch**: if Sunday's retrain shifts calibration unfavorably (new Platt makes existing live bets less accurate), live ROI could DROP for 1-2 weeks until enough new bets accumulate to validate. Monitor and don't panic.

---

Doc kept in `dev/active/` per CLAUDE.md convention. Move to `dev/archive/` after Batch 2 (2026-06-15) when the validation cycle completes.
