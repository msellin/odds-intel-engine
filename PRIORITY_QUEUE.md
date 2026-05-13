# OddsIntel — Master Priority Queue

> Single source of truth for ALL open tasks. Every actionable item across all docs lives here.
> Other docs may describe features but ONLY this file tracks task status.
> Last updated: 2026-05-11 — **MODEL-SIGNALS shipped.** `is_opening` flag on odds_snapshots (migration 096 + store_odds + fetch_odds + pruner). Weather at kickoff via Open-Meteo (migration 097 + fetch_weather.py + MFV wiring + FEATURE_COLS). Referee stats now rebuilt nightly from settlement. Pinnacle skipped (5% coverage = zero lift). `build_match_feature_vectors_live(client, date_str)` writes a `match_feature_vectors` row for every pre-KO match (status != 'finished') so v10+ XGBoost inference (`_build_row_from_mfv`) finds a row instead of returning None and falling back to Poisson. Wired into `run_morning` between the morning signals batch and the prediction loop — covers the morning pipeline AND every betting_refresh (both flow through `run_morning(skip_fetch=True)`). The two builders share an extracted `_build_mfv_rows_for_matches` helper. Confirmed safe before predictions: v10's `train.py:FEATURE_COLS` contains zero prediction-source columns. End-to-end verified: 3,575 pre-KO MFV rows written for 2026-05-10 (96% with ELO, 17% with opening odds — opening_implied coverage rises across the day). 3 new smoke tests (live builder real DB call, status-filter source guard, run_morning ordering wire-through). Closes the last hidden critical-path blocker on ML-PIPELINE-UNIFY Stage 4c shadow deploy. **Earlier 2026-05-10:** ML follow-up batch — 6 of 6 shipped. (1) **`ML-INFERENCE-MFV-WIRE`** done — `xgboost_ensemble.get_xgboost_prediction` now dispatches by schema: v10+ bundles read MFV by `match_id`, v9* bundles use the legacy cache. Smoke-tested both paths. Live shadow deploy is now technically unblocked. (2) **Fold-5 anomaly** — investigated, NO leakage; random-shuffled KFold gives uniform 0.72-0.74 log_loss vs TimeSeriesSplit fold-5's 0.48. Real regime effect from feature populations + end-of-season clarity. v10 CV mean (0.7578) is conservative; live performance likely closer to 0.5. (3) Migration 087 applied via `supabase db push` — 35,247 historical predictions tagged `v9a_202425`. (4) **v10 calibration** — `scripts/fit_platt_offline.py` saved Platt coefficients alongside v10: 1X2 already well-calibrated, draw/OU/BTTS tighten 3-5%. (5) **v11_pinnacle** trained — Pinnacle pre-match 1X2 added as features. **Net lift: zero** at current 5% Pinnacle coverage. `_missing` indicators carry signal, prob values get 0% importance. Don't adopt; revisit when coverage grows. (6) **`ML-BLEND-DYNAMIC`** — per-tier 1X2 blend weights via `fit_blend_weights.py`; tier 1 Poisson=0.5574, tiers 2-4 deferred (insufficient paired data). **Striking side-result**: tier 1 1X2 shrinkage alpha optimised from 0.20 → 0.0025 — for top-tier 1X2, raw bookmaker implied basically beats our model; goal-line markets are where our edge lives (tier 1 alpha 0.35 → 0.81). 4 new smoke tests; all 16 ML tests pass. **Earlier 2026-05-10:** ML pipeline unification batch shipped. Stage 0d (`scripts/backfill_team_season_stats.py`) walks `match_stats` joined to `matches` and writes one row per (team, league, season) via the same `store_team_season_stats` writer fetch_enrichment uses — first run produced 9,473 (team, league, season) groups. Stage 0e (`scripts/backfill_mfv_historical.py`) re-armed; ready to run after 0d finishes. Stage 1c — `workers/model/train.py` now trains `home_goals.pkl` + `away_goals.pkl` Poisson regressors (`count:poisson` objective) so the version bundle is self-contained. Stage 2a/2b — `valid = X.notna().all(axis=1)` row-drop replaced with per-league mean imputation + `<col>_missing` indicators for h2h / opening-odds / referee features (the columns where missingness carries signal — promoted teams have no H2H, pre-2026-Q2 matches have no opening-odds capture). Stage 5a/5b — `workers/scheduler.py` schedules `weekly_retrain` Sunday 03:00 UTC, runs `train.py --version v{YYYYMMDD}` then auto-invokes `compare_models.py {new} {production}`; promotion stays manual. Stage 6a — `scripts/backtest_pre_match_bots.py` replays each active pre-match bot's edge/threshold/range/min_prob/league filter against pre-kickoff `predictions` + max-odds `odds_snapshots`, writes per-(bot, match, candidate) CSV. **Scope honest:** skips calibration / Pinnacle veto / Kelly / exposure cap / sharp_consensus gate — those depend on real-time caches not reconstructable from history. Output is directional ("did this bot ever have edge here?"), not faithful replay. Smoke tests `ML-PIPELINE-UNIFY Stage 2a / 1c / 0d / 5a / 6a` guard each piece. **Discovered gap, not fixed in this batch:** `xgboost_ensemble.py:155-178` builds inference rows from Kaggle-era column names (`home_elo`, `h_*`, `a_*`) that don't match `match_feature_vectors`'s `elo_home` / `form_ppg_home` / etc. — a v10 model trained on the new schema can be CV-compared offline (Stage 4a/4b), but cannot be live-loaded without an inference-side rewrite. Logged as `ML-INFERENCE-MFV-WIRE` follow-up. **Earlier 2026-05-10:** Historical backfill finished (all 134 L/S complete after BACKFILL-LIVELOCK fix; per-dim AF-permanent-gap escape, `stats_attempted`/`events_attempted` tracking, 2%/5% tolerance split). Final coverage: 47,228 finished matches, match_stats 73.4% (terminal — AF won't fill remaining gaps), match_events 93.4%. Unblocks ML-RETRAIN-1 (5K → ~30K row training set now ready). BOT-AGGREGATES-CAP shipped (getAllBets `.limit(500)` → `.range(0, 19999)`, ceiling 20k, smoke test guard against silent-truncation regression; BOT-AGGREGATES-SSOT queued as follow-up architectural fix). AF-FETCHES-AUDIT + AUDIT-AF-ENDPOINTS shipped (per-endpoint JSONB attribution on api_budget_log, migration 086, /sidelined bulk via `?players=A-B-C` 20-id chunks; /standings, /transfers, /coachs all reject bulk per probe). P0 + P1 + 5 P2 strategies shipped + P2 calibration batch shipped. P0/P1 (earlier today): E proxy disabled (179 bad bets voided), `_funnel` heartbeat, `_remaining_goals_prob` helper, `retired_at` bot retirement, `live_next10_*` capture. P2 strategy batch: M (Equalizer Magnet), N (Late Favourite Push), H refined with dual-line ladder, Q (Red Card Overreaction). G + LOOSEN-THRESHOLDS already shipped 2026-05-08. Full inplay replay across 11-day window (live odds only flow from 2026-05-07): 46 bets / 32W-14L / +378% ROI dominated by M. Caveat — M's ROI is upper bound (1.3+1.3 fallback inflates edge in low-data leagues). **Latest (this commit)** — P2 calibration batch: TIME-DECAY-PRIOR (`w_live = 1 - exp(-minute/30)` rate-blend), PERIOD-RATES (0.85×/1.20× period multipliers), LAMBDA-STATE (per-team multipliers for N + total-goal multipliers for J/L/M/Q), EMA-LIVE-XG (5-min half-life smoothing on cand['xg_home/away'] before strategies run). All 7 inline-`lambda_remaining` strategies (A/C/D/E/G/H/Q) now route through new `_scaled_remaining_lam` helper so the calibration stack lands once. Replay diff vs baseline (`dev/active/inplay-backfill-summary-BASELINE.txt`) is the validation signal — re-run `python3 scripts/replay_inplay.py --backfill --from 2026-04-27 --to 2026-05-09`. Migrations 079, 080, 081, 082, 084 apply via GH Actions on push.

**Column guide:**
- **☑** — `⬜` not started · `🔄` in progress · `✅` done
- **Ready?** — `✅ Ready` pick up now · `⏳ Waiting [reason]` blocked

---

## 📋 Open Work — Priority Overview

> All currently open (⬜) tasks in one place. Priority reflects *right now* (2026-05-12 — self-use validation Phase 3). Update when focus shifts.
>
> **Priorities:** P0 = blocking or high data-loss risk · P1 = do before paid launch · P2 = do when accumulating data / useful now · P3 = defer until triggered

| ID | Pri | Effort | Why now / When to do |
|----|-----|--------|----------------------|
| **🗓️ DATA-GATED BATCH WINDOWS** (added 2026-05-13) | | | |
| Batch 1 — Validation Session | P0 | ~3-4h | **~2026-05-26 → 28** — resolve together: `B-ML3` (meta-model), `NEWS-LINEUP-VALIDATE` (AUC gate for B-ML3), `ODDS-TIMING-VALIDATE` (CLV by hours-before-KO). B-ML3 + NEWS already share the date by design; ODDS-TIMING is 2 days later and uses different SQL — same context window. |
| Batch 2 — Per-bot Timing Session | P1 | ~4-6h | **~2026-06-15** — resolve together: `BET-TIMING-MONITOR` **Phase 3** (build `scripts/bot_timing_recommendation.py`; per-bot × cohort factorial ROI from 30 days of shadow_bets), `CAL-PLATT-UPGRADE-VALIDATE` (if O/U Platt has fit by then), `ENG-15` (30-day league inefficiency index). May 28's odds-timing analysis serves as a directional sanity check for Phase 3's per-bot answer. |
| **VALIDATION** | | | |
| SELF-USE-VALIDATION (Phase 3) | P0 | 4-6 wks elapsed | Core goal — accumulate 250 real bets |
| ODDS-TIMING-VALIDATE | P1 | 1h | Run ~2026-05-28 — **Batch 1**. Different question from BET-TIMING-MONITOR Phase 3 (CLV-on-placed-bets vs factorial-shadow-ROI) — both stay valid, run both. |
| INPLAY-ODDS-SOURCE | P1 | 2h research | Need per-bookmaker live odds before inplay push notifications are actionable |
| INPLAY-AUTO-ESTONIAN | P1 | 2h research | Research automation path before inplay is proven — don't wait until last minute |
| **QUICK AF SAVINGS (~2h total, saves ~550 calls/day)** | | | |
| AF-CACHE-H2H | P1 | ✅ Done 2026-05-12 | 7-day cross-match H2H cache in fetch_h2h |
| AF-CACHE-TEAM-STATS | P1 | ✅ Done 2026-05-12 | Same-day DB cache in fetch_team_stats |
| AF-STANDINGS-DAILY | P1 | ✅ Done 2026-05-12 | Standings nightly 23:30 UTC only |
| **RELIABILITY** | | | |
| BARE-EXCEPT-AUDIT | P1 | 1h | Direct follow-up to AUDIT-SILENT-EXCEPT — audit remaining storage-path swallows |
| BACKUP-RESTORE-DRILL | P1 | 1h | Untested backup = no backup; you're on Supabase Pro PITR, verify it works |
| BOT-AGGREGATES-SSOT | P1 | 2-3h | /admin/bots and /performance diverge by construction; fix before paid launch |
| SCHEMA-DRIFT-SMOKE | P1 | 30m | Cheap: 30-min smoke catches column-rename silent failures |
| MODEL-DRIFT-ALERT | P2 | 1h | Z-score on daily predictions — catches broken feature pipeline before bots drain |
| AF-COVERAGE-AUDIT | P2 | 1h | Validate AF coverage flags → gate live poller events/lineups → saves calls |
| AF-QUOTA-AUDIT | P2 | 3-4h | On Mega now (150K/day); monitor + throttle system before next peak Saturday |
| MEMORY-MONITORING | P2 | 30m | Railway pod OOM emits no Python exception — heartbeat is only defense |
| OBS-SENTRY-BACKEND | P2 | 1.5h | Useful only when external users exist; premature at single-user stage |
| JOB-TIMEOUT | P3 | 2h | Hung jobs hold conns; nice to have but healthchecks.io catches the symptom already |
| WORKER-SPLIT-LIVEPOLLER | P3 | 30m+click | LivePoller crash isolation; defer unless cascade failures reappear |
| BOT-STRATEGY-DEEP-REVIEW | ✅ Done 2026-05-13 | ~3-4 days | All 3 threads complete. Results + ranked lists in `dev/active/bot-strategy-audit-results.md`. 7 follow-up tasks queued below (4 adjustments + 3 new strategies). |
| OPT-AWAY-ODDS-FIX | P2 | 30m | Expand odds range for `bot_opt_away_british` + `bot_opt_away_europe` from 2.50-3.00 → 2.20-3.50. Both bots have 0 fires in 14d because the 0.50-wide window passes 0 candidates. Backtest-confirmed strategies (+16-19% cross-era ROI) just need the window to fire. Smoke test: funnel shows >0 candidates at new range. |
| INPLAY-M-LOOSEN | P2 | 30m | Lower `inplay_m` (Equalizer Magnet) `live_ou_25_over` gate from ≥ 3.0 → ≥ 2.50. Currently kills 99.1% of candidates (64,290/64,870). 1 fire at +150% ROI, +0.515 CLV — strongest signal on any inplay bot. Expected: 1 → ~15 fires/14d. Smoke test: verify ≥10 qualifying snapshots at new threshold. |
| INPLAY-J-LOOSEN | P2 | 30m | Lower `inplay_j` `prematch_o25` gate from ≥ 0.62 → ≥ 0.55 (aligns with sibling strategies; I uses 0.50, no other uses 0.62). Currently passes only 1,010/52,319 scoreless snapshots (1.9%). Expected: 0 → ~5 fires/14d. Smoke test: funnel shows 50-200 snapshots surviving gate at new threshold. |
| INPLAY-LIVE-OU-COVERAGE | P1 | 3-4h | Live OU2.5/1.5 odds are only present in ~9% of `live_match_snapshots` — the single biggest bottleneck for all OU-based inplay bots (e, h, a, d, j). Live stats (shots/possession/xG/corners) also at 5-9%. Audit why: are we not fetching from AF live odds/stats correctly? Are we dropping rows? Which leagues have coverage? Fix would unlock 5 existing + 3 new strategies. |
| INPLAY-POST-EQUALIZER | P2 | 2-3h | New strategy: after 1-1 equalizer (min 30-75), buy the equalizing team to win. Live 1x2 (21.6% coverage) + score tracking (100%) — data ready now. Thesis: narrative bias keeps winner-odds too high after equalizer; market slow to reprice. Expected ~3-6 fires/day. Smoke test: verify funnel shows candidates + strategy fires on historical snapshots. |
| INPLAY-UNDERDOG-HOLD | P2 | 2-3h | New strategy: underdog leading 1-0 at min 25-55, prematch model probability < 35%, live underdog 1x2 ≥ 2.80. Simplified version of original Strategy J (no xG needed). Data ready: live 1x2 21.6%. Thesis: narrative bias ("favourite will come back") keeps underdog win odds inflated even for deserved leads. Expected ~2-5 fires/day. |
| LIVE-STATS-COVERAGE | P1 | 3-4h | Investigate why shots/possession/xG/corners populate in only 5-9% of `live_match_snapshots`. AF live stats should arrive in `/fixtures?live=all` response. Determine: which leagues have AF stats coverage? Is the parser dropping them? Is this a rate-limit issue? Fixing this unlocks Shot Quality Under + Possession Trap Under + Corner Pressure Over (3 medium-high confidence strategies from the 8-AI review panel). |
| **INPLAY ML (data-gated)** | | | |
| INPLAY-CALIBRATION-IJL | P2 | 1h | Run ~June when bots I/J/L reach 50+ settled bets each |
| INPLAY-BACKFILL-PERSIST | P2 | 3h | After backfill review; adds `is_backfill` flag to simulated_bets |
| INPLAY-HT-REPRICING | P3 | 2h | After I/J/L reach 50 bets; narrow window strategy |
| INPLAY-SOFT-GATES | P3 | 8h | Composite scoring to replace hard thresholds — wait for more data |
| INPLAY-LAYER-ARCH | P3 | 4h | Architectural refactor; wait until ≥4 strategies are firing consistently |
| INPLAY-NEW-POSSESSION-SWING | P3 | 4h | After corner+HT strategies are validated |
| INPLAY-DIXON-COLES | P3 | 4h | After 1500+ bets |
| **DEFERRED / TRIGGERED** | | | |
| LIVE-SNAPSHOTS-PRUNE | Defer | 2-3h | DB storage is cheap; defer until query slowdowns observed |
| OU-LINE-DRIFT-INVESTIGATE | Defer | 4h | Lower priority now Pinnacle-required gate is in place |
| BULK-STORE-AUDIT | Defer | 2-3h | No hot paths observed post BULK-STORE-ODDS/PREDICTIONS wins |
| JOB-IDEMPOTENT | Defer | 6h | Heavy audit; do only if re-run bugs surface in production |
| NORDIC-BOOKS-INTEGRATION | Defer | 1-2d | Only if Nordic real-money bettability becomes a product requirement |
| BOT-AGGREGATES-SSOT | P1 | 2-3h | See above |
| STAGING-ENV | Defer | 3h | Build after first paid subscription |
| SUPPORT-RUNBOOK | Defer | 1h | Write after first edge case fires, not before |
| FAIL-OPEN-DEGRADATION | Defer | 3-4h | Polish; AF failure already degrades gracefully via exception boundaries |
| USER-DEGRADATION-UX | Defer | 2h | UX polish; not blocking for personal-tool use |
| EMAIL-DELIVERY-CHECK | P2 | 1h | Verify Resend DKIM/SPF before sending to more than a handful of users |

---

## 🎯 2-Week Sprint — Market Expansion + Inplay Fix (filed 2026-05-11)

> Context: 2-week window to prove the project. Pre-match CLV is +13% (real). Inplay bots placed ~0 live bets despite good replay. Focus: more pre-match bet types (quick) + unblock inplay (diagnostic first). Search for "2-WEEK SPRINT" to find all tasks in this group.
>
> **Data reality check (2026-05-11):**
> - `double_chance` market: already in DB — 119K rows, 11 books, 7 days. No code needed to start.
> - `asian_handicap` market: **not in DB at all** — AF parses it but we don't store it yet.
> - `draw_no_bet` market: not in DB — derivable from 1X2 odds mathematically.
> - Inplay bots: most show 0 bets in 14 live days despite good replay numbers.

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| DC-BOTS | **[2-WEEK SPRINT] Double Chance bots** | 3-4h | ✅ Done 2026-05-11 | ✅ Ready | `bot_dc_value` (all leagues, 5%/4%/3% edge, 1.25-2.20 odds) + `bot_dc_strong_fav` (T1-2, 1X/X2 only, 6%+ edge, 1.20-1.80 odds). DC probs derived inline: `1X = home_prob + draw_prob`, etc. `MARKET_TO_FIELD` maps `double_chance_1x/x2/12`. `settle_bet_result` handles `market == "double_chance"`. Both bots in morning cohort. |
| AH-PARSE | ✅ Done 2026-05-11 — **[2-WEEK SPRINT] Asian Handicap — parse + store** — Bug: old parser expected `value="Home"` + separate `handicap` field; AF actually ships `value="Home -1.25"` (embedded). Fixed to exact `bet_name == "Asian Handicap"` match + split on first space. 7-day backfill run: 240K rows, lines -6.5 to +7.5, balanced home/away. | 2-3h | ✅ Ready | Must happen before AH-BOTS. Only parsing/storage — no model or bot work. |
| RO1-DATA-FIX | ✅ Done 2026-05-12 — **Romanian Liga I data added to targets_poisson_history.csv** — FCSB was Tier B (global fallback), mixing in European matches → inverted expected goals (FCSB 23% home win vs Slobozia 48%). Added 1,590 Liga I rows (2020–2025) with Pinnacle closing odds from football-data.co.uk. FCSB now Tier A: GA 1.40→0.60 (Liga I only) → FCSB win 48%, Slobozia win 25% (was 23%/48%). Script: `scripts/add_romanian_league_data.py`. | 30m | ✅ Done | |
| AH-DISPLAY-FIX | ✅ Done 2026-05-11 — **AH model % display fix** — value-bets page was showing `model_probability` (raw push-adjusted AH probability, up to 95%) instead of `calibrated_prob` (actual probability used for edge/kelly, typically 55-65%). Root cause: `_ah_model_prob` computes a push-normalized conditional probability (e.g., P(home doesn't win by 3+) excluding push at margin=2), which inflates vs simple probability. Fixed `engine-data.ts:toBet` to prefer `calibrated_prob ?? model_probability`. Also fixed `getPlaceableBets` and `getAllBets`. | 30m | ✅ Ready | FCSB vs Slobozia "away -2 = 95% model" triggered the investigation. The `cal_prob` shown is the calibrated number that drives edge and Kelly. |
| AH-BOTS | ✅ Done 2026-05-11 — **[2-WEEK SPRINT] Asian Handicap bots** — `_ah_model_prob` prices all line types (whole/half/x.25/x.75 quarter) with EV-adjusted fair probabilities. Whole-line push → void (stake returned). `ah_lines` list populated from DB (LATERAL subquery) and AF bulk path. `bot_ah_home_fav` (T1-2, home selection, 5%+ edge, 1.50-2.20 odds) + `bot_ah_away_dog` (T1-3, away selection, 5%+ edge, 1.70-2.50 odds). DC mkt label bug fixed (was "DC" → now "double_chance" for correct settlement routing). **2026-05-12: quarter lines (±.25/±.75) removed from candidate generation (AH-NO-QUARTER)** — Coolbet only offers full and half lines; quarter-line paper bets were unplaceable and distorted Kelly stakes + league exposure cap for adjacent half-line bets. Re-enable if a quarter-line book is added. | 3-4h | ✅ Ready | |
| INPLAY-LIVE-DEBUG | ✅ Done 2026-05-11 — **[2-WEEK SPRINT] Why aren't inplay bots firing in live conditions?** Root cause: live OU2.5 odds coverage only 12.3% of snapshots; strategies returned None when `live_ou_25_over` was None. Fix: added `_resolve_odds(live, prematch, min_val)` helper with prematch fallback. Added per-strategy `_strategy_stats` (tried/fired) logged on heartbeat. Prematch SQL extended with LATERAL subquery for `prematch_ou25_over`/`prematch_ou15_over`. Strategies A,B,D,E,G,H,J,L,M updated to use `_resolve_odds`. Drift-detection strategies I,N,C intentionally excluded (floor-based drift wouldn't work with prematch odds). | 2-3h | ✅ Ready | **Critical for 2-week window** — if inplay can be unblocked, bet frequency triples. If not, confirms pre-match is the only live signal. |
| BOT-PERF-MONITOR | ✅ Done 2026-05-11 — **[2-WEEK SPRINT] Bot performance report** — `scripts/bot_perf_report.py` with 5 slices: summary, by-bot (sorted by CLV), by-market+selection, by-league-tier, top-leagues. `--days N` for recency window, `--bot NAME` for drill-down, `--min-bets` for significance floor. CLV colour-coded (bold green ≥2%, green ≥0.5%, yellow, red). Initial run (2026-05-11): 399 settled, avg CLV +12.56%, T1 leagues +14.8% CLV, 1X2 home strongest single market (+15.3% CLV / 206 bets). | 2-3h | ✅ Ready | Key output: T1 leagues dominate; 1X2 home + OU under 2.5 are the highest-CLV markets with sufficient sample. |
| INPLAY-AUTO-ESTONIAN | **[2-WEEK SPRINT] Find viable path for Estonian resident to automate personal live bet placement.** Goal: by the time inplay bots are proven profitable (~20+ live bets), have a tested automation path ready. Research questions: (1) **Betfair Exchange** — does betfair.com load + allow registration directly from Estonia without VPN? If yes, this is the answer: Betfair has a public API explicitly built for algorithmic betting, documented at `betfair.github.io/API-NG-sample-code/`. (2) **Smarkets / Betdaq** — similar betting exchanges, check Estonia geo-access. (3) **Kambi API** (Coolbet is Kambi-platform) — Coolbet already accepts Estonian customers. Does Kambi expose a betting API for account holders? Check Coolbet ToS for API/bot clauses. (4) **Betfair streaming API** — push-based, updates every 50ms, better than polling for live execution. (5) **Legal status** — Estonian Gambling Act allows personal online gambling on licensed operators. Confirm which exchanges hold MGA/UKGC/Estonian license. **Output:** shortlist of 1-2 viable options with: geo-accessible ✅/❌, API docs link, bet placement supported ✅/❌, any ToS bot restrictions. Focus on options that don't require VPN — VPN creates account closure risk at withdrawal. | 2h research | ⬜ | ✅ Ready | Prerequisite for any real automation. Do this before building the placement layer. |
| INPLAY-ODDS-SOURCE | **[2-WEEK SPRINT] Investigate live odds sources for inplay signal verification + automation roadmap.** AF `/odds/live` is single-aggregate ("api-football-live" pseudo-bookmaker) — useless for "go to Bet365". Need per-bookmaker live odds to: (1) populate `recommended_bookmaker` on inplay bets, (2) send actionable push notifications "M fired: OU2.5 at Bet365 2.05 — check now". **Three candidates to evaluate:** (a) **The Odds API live** (`the-odds-api.com/liveapi/guides/v4/`) — polling, read-only, per-bookmaker including Bet365/Unibet/Pinnacle, credit-based cost. Check: football EU coverage, OU 2.5 + 1x2 live market availability, cost at ~10 calls/hour (only poll when inplay bot has active candidates); (b) **AF `/odds/live` with bookmaker param** — check if AF supports `?bookmaker=Bet365` or per-book breakdown in the raw response before we discard it; (c) **Betfair Exchange API** — read + write (can actually place bets), sharper prices, 5% commission. Longer-term automation target. Evaluate feasibility for Estonian resident + small stakes. **Output:** recommendation on which source(s) to integrate, estimated AF quota cost for live odds polling, and a 2-step automation roadmap (step 1: per-book signal + push notification; step 2: Betfair auto-placement). | 2h research | ⬜ | ✅ Ready | |
| DNB-COMPUTE | ✅ Done 2026-05-11 — **[2-WEEK SPRINT] Draw No Bet — computed from 1X2 odds** — DNB home = implied home_prob / (home_prob + away_prob) normalised to remove draw. No need to store separate DNB odds (AF has them but adds AF quota cost). Compute at placement time: if model's P(home)/(P(home)+P(away)) vs DNB-implied_prob shows edge > threshold, place. Add `bot_dnb_home_value` + `bot_dnb_away_value`. Use best 1X2 home/away odds from `odds_snapshots` to construct DNB price. | 2h | ✅ Ready | Lower priority than DC-BOTS since DC data is already stored. DNB computation is fully derived. |

---

## ⭐ Top Priority — Strategic Pivot Validation (2026-05-10)

> Decision tree: validate whether bot paper-trading edge survives real-money execution at Coolbet+Bet365. If yes, pivot the product from B2C SaaS to a personal betting tool. Coexists with SaaS during validation.

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| SELF-USE-VALIDATION | Strategic pivot exploration: validate whether bot paper-trading edge survives real-money execution at Coolbet (preferred) + Bet365 (secondary) — both Estonia-accessible. If real, drop B2C SaaS direction and convert engine to a personal betting tool. **4 phases:** Phase 0 free Unibet-vs-Coolbet sample on upcoming matches (Coolbet doesn't publish historical odds — must look forward, not backward), Phase 1 optional Coolbet API via $0–30/mo The Odds API if Unibet proxy is bad, Phase 2 superadmin `/admin/place` view + `real_bets` table + manual logging UI + Coolbet/Bet365 columns on bot dashboard, Phase 3 4–6 weeks of €1–3 stakes + 250-bet cohort report, Phase 4 pivot decision per ROI matrix. **Key data inventory:** Bet365 (422K rows fresh) + Unibet (443K rows fresh, Kambi-platform proxy for Coolbet) already in `odds_snapshots`. Phase 0 sampling script `scripts/sample_coolbet_proxy_check.py` shipped — pulls all pending bets on not-yet-started matches, joins Unibet + Bet365 + Pinnacle at pick time, outputs CSV worksheet for manual coolbet.ee comparison. Manual placement only (Smartbet.io doesn't support Coolbet, custom auto-placement violates ToS). **Decision matrix** (real ROI over 200+ bets): <0% don't pivot · 0–3% marginal · 3–8% scale stakes · >8% pivot fully. Plan + tasks + context in `dev/active/self-use-validation-{plan,tasks,context}.md`. | Phase 0: 1 evening · Phase 2: ~3d · Phase 3: 4–6 weeks elapsed | 🔄 In Progress | ✅ Ready | Don't drop SaaS during validation. €500 worst-case bankroll cap. Critical discipline: log every "couldn't place" reason — execution friction is exactly what paper trading hides. The OU-PINNACLE-CAP shipped this morning is the first defense against measurement-artifact edges; real-money tests are the second. |
| ACCESSIBLE-BM | ✅ Done 2026-05-11 — **Restrict edge calculation to accessible bookmakers.** Added `ACCESSIBLE_BOOKMAKERS = frozenset({"Bet365", "Unibet", "Betano", "Marathonbet", "10Bet", "888Sport", "Pinnacle"})` to `daily_pipeline_v2.py`. Odds aggregation loop now skips inaccessible books (SBO, Dafabet, 1xBet, etc.) when building `best[mid][key]`. New `best_bookmaker[mid][key]` dict tracks which accessible book had the best odds. `recommended_bookmaker` passed to `store_bet()` and stored in `simulated_bets` (migration 094). `accessible_bookmakers` registry table from migration 091 (seeds Coolbet + Bet365). Fix addresses the core measurement problem: reported CLV of +12.56% was inflated by inaccessible-book odds — with only accessible books, real edge is lower but honest. | 2h | ✅ Ready | Key change: `bm_sources` still tracks all sources for display, but edge math only uses accessible books. `best_bookmaker` bubbles up through `bet_candidates` tuple (extended from 11 to 13 elements) so `os_market`/`os_selection` are available at `store_bet` call site. |
| DAILY-PICKS | ✅ Done 2026-05-11 — **Morning report for manual betting validation.** `scripts/daily_picks.py` — shows today's pending picks with kickoff time, match, market, odds, edge, calibrated prob, and `recommended_bookmaker`. Groups by bookmaker at top (count + avg edge). `--date`, `--min-edge` (default 3%), `--bookmaker` flags. Designed for morning ritual: run script, check Bet365/Unibet, place manually, log in `real_bets` table. | 1h | ✅ Ready | Relies on `recommended_bookmaker` being populated by ACCESSIBLE-BM (above). Old bets before 094 will show 'unknown' — only new pipeline runs populate the column. |
| REAL-MONEY-TRACKER | ✅ Done 2026-05-11 — `scripts/real_perf_report.py`: 5 sections (summary, paper vs real via simulated_bet_id join, by bookmaker, by market, recent bets). `--days`, `--bookmaker`, `--min-bets` flags. `/admin/place` + `/admin/real-bets` already live from earlier session. | 3d | ✅ Done 2026-05-11 | ✅ Ready | `real_perf_report.py` completes the paper-vs-real triangle: pipeline places (accessible books), user logs actual odds, script shows slippage + real vs paper ROI. |
| BOOKMAKER-DISPLAY | ✅ Done 2026-05-12 — `getValueBetBookOdds()` now fetches Bet365 + Unibet + Pinnacle. `BookOddsLine` upgraded: shows "Best now Pinnacle 2.08 +4.7% live" (color-coded: green ≥5%, amber 2-5%, red <2%) + all 3 books below. Stale dimming (`opacity-50`) on rows where live edge <2% or kickoff <45min. `isEdgeStale()` + `getBestNow()` helpers. | 3h | ✅ Done 2026-05-12 | ✅ Ready | Both tracks: personal betting (user sees exact book to check) + signal product (users trust picks more when named bookmaker is shown). |
| BET-TIMING-ANALYSIS | ✅ Done 2026-05-12 — Added 13:30 + 17:30 betting refresh slots. `scripts/odds_timing_analysis.py` built: Part 1 CLV by hours-before-KO (works now, 398 bets), Part 2 absolute time-of-day drift (needs data), Part 3 intraday per-match trajectory. First run: all windows +CLV, 2–4h highest at +23.7% but n=77 too small. Pruner upgraded to hourly retention — 25% reduction vs 41% compact. | 2h | ✅ Done 2026-05-12 | ✅ Ready | |
| ODDS-TIMING-VALIDATE | **Re-run `scripts/odds_timing_analysis.py` after ~2026-05-28 to validate timing theory with 2+ weeks of hourly snapshot data.** Two hypotheses: (1) match-relative: do bets placed 2–4h before KO consistently beat 8–12h? Need 200+ bets/bucket. (2) absolute time-of-day: is there a daily window when odds peak across all games regardless of kickoff? **When done:** if pattern found → adjust betting refresh schedule. If no pattern → run `python scripts/prune_odds_snapshots.py --mode compact --apply` to reclaim DB space. Either outcome, compact-prune after validation. | 1h | ⬜ | ⏳ Waiting — target 2026-05-28 (2 weeks of hourly data) | |
| FRESHNESS-INDICATOR | ✅ Done 2026-05-11 — "Odds verified Xm ago" chip in value-bets header. Green <45m, amber <90m, red ≥90m. Server-side `getOddsVerifiedAt()` on Elite path. | 1h | ✅ Done 2026-05-11 | ✅ Ready | Simple: MAX(odds_snapshots.timestamp) across today's match IDs. |

---

## Reliability Hardening — Pre-Launch (4-AI Review, 2026-05-08)

> Origin: pool-exhaustion outage 2026-05-08. Dashboard at 0 for 11h before discovery. Root cause: `db.py:get_conn()` only returned the connection on success or connection-level errors — any other exception leaked it. With `maxconn=10` and InplayBot polling every 30s, the pool died and every subsequent job (Fixtures, Enrichment, Odds, Predictions, Betting, Settlement, ops snapshot, budget logger) failed with `pool exhausted`. The deeper lesson: one faulty subsystem permanently degraded the whole platform without isolation, alerting, or auto-recovery.
>
> List below is consolidated from 4 independent AI reviews. Strong consensus marked ✅ where 3+ reviewers agreed. Sharp disagreements resolved with reasoning in Notes column.

### P0 — Reddit Launch Blockers (~6h, this Friday)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| POOL-LEAK-FIX | `db.py:get_conn()` rewrite — `try/finally`, rollback on app exception, return conn always. Skip-cycle on `PoolError` in InplayBot with terse log line. **Keep `maxconn=10`** (R4 was right — bumping to 20 hides the next leak; loud failure at 10 is a diagnostic feature, not a bug). | 1.5h | ✅ Done 2026-05-08 | ✅ Ready | Commit `eb53c3e`. 2 new smoke tests (15 SQL errors + 15 caller-raised exceptions verify no leak). InplayBot now skips cycle with one log line on `PoolError`. Migration 069 made idempotent (drop-then-create). Recovery script `scripts/recover_today.py` triggers all missed jobs locally — usage: `venv/bin/python scripts/recover_today.py`. |
| POOL-WAIT | Saturation (not leak) was still crashing InplayBot mid-cycle (`PoolError: connection pool exhausted`) when many APScheduler jobs hit DB simultaneously. Added `_acquire_conn()` wrapper that polls with backoff up to `DB_POOL_WAIT_TIMEOUT` (default 60s) before raising. Pool size bumped 10→20 for headroom. Diagnostic value preserved — genuinely deadlocked pool still surfaces loudly after timeout, just gives transient saturation room to drain. | 30m | ✅ Done 2026-05-09 | ✅ Ready | `db.py:_acquire_conn()` + new POOL-WAIT smoke test (isolated 1-2 conn pool, verifies 1s timeout is respected and slot release unblocks waiter). Supersedes the "keep maxconn=10 for diagnostics" note from POOL-LEAK-FIX — POOL-LEAK-FIX already eliminated leaks, so saturation is now legitimate concurrency, not a hidden bug. |
| POOL-FANOUT | Root-cause follow-up to POOL-WAIT. The wait was a band-aid; actual sources of conn fan-out: (a) `fetch_post_match_enrichment` ran 4 threads × 3 conns/thread = up to 12 simultaneous conns; (b) APScheduler default 10-thread executor fired many missed jobs at once on Railway redeploy catch-up; (c) `store_match_events_batch` per-row INSERT loop holding a conn for ~30 round-trips/match × ~30 matches/cycle. Fixes: (1) cap settlement enrichment to 2 workers; (2) APScheduler executor capped at 4 threads; (3) bulk `execute_values` with per-row fallback for batch failures; (4) `DB_POOL_WAIT_TIMEOUT` default 60s→15s so saturation surfaces fast. | 30m | ✅ Done 2026-05-09 | ✅ Ready | 4 new POOL-FANOUT/POOL-WAIT smoke tests (source-inspection, no network). Bulk insert preserves the per-row fallback so a single bad event row can't poison the whole batch. |
| INPLAY-UUID-FIX | InplayBot was placing 0 live bets despite 89 candidates/cycle because `mid = cand["match_id"]` is a `uuid.UUID` object (psycopg2 default), but `_get_prematch_data` keys its return dict on `str(match_id)` — `prematch.get(uuid)` always returned None, every candidate hit `if not pm: continue` before any strategy ran. Same mismatch broke `red_card_matches` and `existing_bets` lookups. | 30m | ✅ Done 2026-05-08 | ✅ Ready | One-line fix: `mid = str(cand["match_id"])` at top of loop. 2 new source-inspection smoke tests (INPLAY-UUID-FIX). Also moved smoke suite from pre-push hook (145s blocking) to GH Actions on push to main. |
| EXCEPTION-BOUNDARIES | Wrap every APScheduler job and every `live_poller._run_cycle` iteration in top-level `try/except Exception` so a single bug can never kill the loop silently. Log to Sentry, write to `pipeline_runs` with status. ✅ All 4 reviewers flagged blast-radius isolation as the architectural fix that obviates `WORKER-SPLIT`. | 1h | ✅ Done 2026-05-08 | ✅ Ready | Scheduler already had `_run_job()` wrapper — fixed `job_budget_sync()` which was bypassing it. LivePoller `run_forever()` already had try/except — added `traceback.print_exc()` so exceptions aren't silently swallowed. Fixed `return None` → `return False` in budget-exhausted path. |
| JOB-COALESCE | `coalesce=True, max_instances=1` on every APScheduler job. ✅ Strong consensus. | 30m | ✅ Done 2026-05-08 | ✅ Ready | Applied via `BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1})` — one line covers all current + future jobs. |
| DB-STMT-TIMEOUT | Set `statement_timeout=60s` and `idle_in_transaction_session_timeout=30s` via DSN options on conn open. R4 caveat: 15s would kill nightly settlement (legitimate joins push 20-30s). 60s is the right global default. | 30m | ✅ Done 2026-05-08 | ✅ Ready | Migration 070. Set at database level (`ALTER DATABASE postgres`) — Supavisor strips per-connection options= so it must be database-level. |
| OBS-HEARTBEAT | External healthchecks.io ping on `/health` every 5min. Alert when `ops_snapshot` >2h old, pool >80%, or `/health` 5xx for 2 consecutive checks. ✅ All 4 reviewers — would have caught today's outage in 5 min vs 11h. | 1h | ✅ Done 2026-05-08 | ✅ Ready | `job_healthcheck_ping()` in scheduler pings `HEALTHCHECKS_IO_PING_URL` every 5 min. healthchecks.io account created, check live (last ping 20s ago confirmed). Period 5min / Grace 10min set. |
| OBS-SENTRY-BACKEND | Wire `sentry_sdk` into `workers/scheduler.py` + `workers/live_poller.py`. Frontend already has it (`SENTRY` ✅). Add `before_send` filter to drop `psycopg2.pool.PoolError` and `OperationalError` to avoid free-tier flood. | 1.5h | ⬜ | ✅ Ready | R4 trap: Sentry will flood without filtering — APScheduler scheduling exceptions and httpx retries are noisy. Budget 1h for tuning, not 0. |

### P1 — Pre-Paid-Launch Money / Security (~14h, before Stripe goes live)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| STRIPE-WEBHOOK-SIG | Verify `Stripe-Signature` header against `STRIPE_WEBHOOK_SECRET` using `stripe.Webhook.construct_event`. Reject any unsigned/bad-signature payload. ✅ R1 + R4 both flagged: without this, anyone can POST fake `checkout.session.completed` and grant themselves Elite. | 1h | ✅ Done 2026-05-08 | ✅ Ready | Already implemented — `constructEvent()` with body+sig+secret was already in the handler. Verified 2026-05-08. |
| MONEY-STRIPE-IDEMPOTENT | `processed_events` table keyed by `event.id` from **JSON payload (NOT header)** — R4 trap: header `Stripe-Signature` is per-attempt and won't dedupe retries. Wrap handler logic + DB write in a single transaction; on commit failure, mark event unprocessed for retry. | 3h | ✅ Done 2026-05-08 | ✅ Ready | Migration 071 (`processed_events` table, UNIQUE on event_id). Webhook handler now inserts event.id before processing — on 23505 (duplicate) returns 200 immediately without re-applying side effects. On unexpected DB error returns 500 so Stripe retries later. |
| MONEY-WEBHOOK-TEST | Script 50+ webhook scenarios via Stripe CLI: `success`, `dupe`, `out-of-order`, `network-fail-after-process`, `bad-signature`, `unknown-event-type`. Verify no double-grants, no ghost tiers, no missed grants. R2 add. | 1h | ✅ Done 2026-05-08 | ✅ Ready | `scripts/test_stripe_webhook.sh` — automates bad-sig and no-sig checks, provides manual checklist + exact `stripe trigger` commands for remaining scenarios. |
| STRIPE-RECONCILE | Daily script: `stripe.events.list(created.gte=yesterday)` → diff vs `processed_events` table → alert on drift. R4 add: bigger Stripe risk isn't double-grant, it's **never-grant when webhook silently fails**. | 1h | ✅ Done 2026-05-08 | ✅ Ready | `scripts/stripe_reconcile.py` + `job_stripe_reconcile()` in scheduler at 09:00 UTC. Emails `ADMIN_ALERT_EMAIL` with missed event IDs + resend instructions if drift found. |
| MONEY-RLS-AUDIT | Walk every table; confirm RLS policy + that service-role key is server-only (never in NEXT_PUBLIC_ env). R4: 30 min not 2h — checklist walkthrough since schema is known. | 30m | ✅ Done 2026-05-08 | ✅ Ready | All tables have RLS. Service key only in server-side API routes, never in NEXT_PUBLIC_. One gap fixed: migration 072 adds RLS to `processed_events` (no public SELECT). |
| MONEY-SETTLE-RECON | Daily reconciliation: count of bets settled vs count of finished matches. Alert on drift >2. ✅ R1 + R2 + R4. | 2h | ✅ Done 2026-05-08 | ✅ Ready | `scripts/settle_reconcile.py` — queries finished matches with pending bets, alerts via Resend if >2 stuck. Wired into scheduler at 21:30 UTC alongside settlement health check. |
| BACKUP-RESTORE-DRILL | Actually restore Supabase PITR to a scratch project. Time it. Document the procedure. R4 add: untested backups = no backups. | 1h | ⬜ | ✅ Ready | You upgraded to Pro for this. Verify it works end-to-end before you need it at 02:00 UTC during an incident. |
| RATE-LIMIT-API | Upstash rate limit on `/api/bet-explain` (Gemini cost), `/api/live-odds` (DB load), `/api/stripe/upgrade`. ✅ R1 + R4. | 2h | ✅ Done 2026-05-08 | ✅ Ready | No Upstash needed — in-memory sliding window (`src/lib/rate-limit.ts`). `bet-explain`: 10/hour/user, `live-odds`: 120/hour/user (30s chart refresh), `stripe-upgrade`: 5/hour/user. Resets on redeploy — sufficient for abuse prevention. |
| ABUSE-DETECT-PRELAUNCH | One-shot scan: SQL injection on user-input forms, password policy, session timeout, anonymous endpoint enumeration, CSRF on state-changing routes. R4 add. | 2h | ✅ Done 2026-05-08 | ✅ Ready | Audit complete: (1) SQL injection — safe, Supabase SDK uses parameterized queries throughout; (2) CSRF — safe, all state-changing routes require Supabase auth cookie verified server-side; (3) Input validation — UUID regex added to `matchId` (live-odds) and `betId` (bet-explain) params; (4) No sensitive data exposed to anon users — all data routes require auth; (5) Stripe webhook — signature verified. Rate limits added (RATE-LIMIT-API). No critical vulnerabilities found. |
| DEPLOY-ROLLBACK-RUNBOOK | One-page doc: exact Railway redeploy-from-SHA + Vercel redeploy-from-deployment commands. Test it: deploy a no-op commit, then roll back. R4 add. | 30m | ✅ Done 2026-05-08 | ✅ Ready | `docs/ROLLBACK_RUNBOOK.md` — Railway (redeploy via dashboard or git revert), Vercel (CLI `vercel rollback`, dashboard promotion, or revert push), DB migration reversal procedure, post-rollback checklist. |

### Critical bugs found 2026-05-09

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| OU-PARSE-BUG | `parse_fixture_odds` (api_football.py:445) used `"Over/Under" in bet_name` substring match — that swallowed `Goals Over/Under First Half`, `Goals Over/Under Second Half`, `Home Team Goals Over/Under`, `Away Team Goals Over/Under`, and similar markets, then bucketed all of them into the same `over_under_05/15/25/35/45` keys as full-match goals. Best-price selection downstream picks `MAX(odds)` across all rows in the bucket, so a 1H Over 2.5 priced at 6.6 beat the FT Over 2.5 at 1.81 and produced a fake +400% edge. Same bug in `parse_live_odds` (line 978). Surfaced when user noticed Widzew Łódź vs Lechia Gdańsk Over 2.5 displayed at 7.0 odds with massive "edge" — DB query confirmed three different odds values for the same bookmaker / market / selection / timestamp. **Fixed:** strict exact match `bet_name == "Goals Over/Under"` in pre-match parser, `market_name in ("Goals Over/Under", "Over/Under", "Over/Under Line")` in live parser. 3 new smoke tests (FT row preserved, 1H/2H/team variants dropped, source guard against substring revert). **DB cleanup completed:** 755,092 pre-fix corrupt OU rows deleted for today's matches; 22 pending O/U bets voided; betting refresh kicked off to regenerate clean bets. | 1h | ✅ Done 2026-05-09 | ✅ Ready | Side-finding during cleanup: `get_odds_by_date` paginates with the same fixture appearing across multiple AF pages, and AF returns the same bookmaker on each page with slightly different odds (e.g. Bet365 Under 0.5 = 8.50 on page 21, 10.00 on page 22). Spread is <1.5× — separate, lower-impact issue worth a future task (`AF-PAGE-DUP`). The original bug was 3-4× spreads from market-collapse; this is just AF-feed-time noise within the same market. |
| BULK-STORE-PREDICTIONS | `fetch_predictions.fetch_af_predictions`, `daily_pipeline_v2._fetch_af_predictions`, and the main `run_morning` per-match prediction loop all wrote predictions one row at a time via `store_prediction()` → 4–17 round-trips per match × 500 matches × ~150ms EU pooler RTT = ~21 min of pure DB wait time. Surfaced today: a manually-kicked betting refresh sat at PID 99800 for 22+ minutes, 0.4% CPU, sleeping on Supabase pooler connections. **Fix:** added `bulk_store_predictions(rows)` and `bulk_update_match_af_predictions(rows)` to `supabase_client.py` (single `execute_values` call with `ON CONFLICT (match_id, market, source) DO UPDATE`). Refactored all three call sites to buffer rows in memory and flush once. Same pattern as `BULK-STORE-MATCHES`/`BULK-STORE-ODDS`/`INJURIES-BY-DATE`. Expected wall-time: `run_morning` ~21min → ~1s for the prediction-write phase; `fetch_predictions` ~5min → ~1s. 3 new smoke tests — bulk helper exists + uses execute_values + correct ON CONFLICT key, both call sites use bulk helper, `daily_pipeline_v2.py` has zero remaining bare `store_prediction(match_id,` per-row calls. | 1.5h | ✅ Done 2026-05-09 | ✅ Ready | The standalone `store_prediction()` is preserved in `supabase_client.py` for any one-off ad-hoc writers, but no production code path should still use it — the smoke test enforces this in `daily_pipeline_v2.py`. |
| BULK-STORE-AUDIT | After the BULK-STORE-PREDICTIONS / BULK-STORE-MATCHES / BULK-STORE-ODDS / INJURIES-BY-DATE wins, audit the rest of the codebase for the same per-row-DB-write anti-pattern. Method: grep for `for ... in ...:` loops that contain `execute_write(`, `INSERT INTO`, `UPDATE`, or `store_*` helper calls inside the body — every match is a candidate for bulking. Specifically check: (a) `workers/jobs/fetch_enrichment.py` — does it write standings / H2H / team-stats per row?; (b) `workers/jobs/settlement.py` — `_settle_pending_bets` updates bots one by one; (c) `workers/api_clients/supabase_client.py` — `store_match_signal`, `store_match_injuries`, `store_team_season_stats` per-row callers; (d) `workers/jobs/news_checker.py` — does Gemini result store per match?; (e) `workers/jobs/inplay_bot.py` — bet placement loop; (f) `workers/scrapers/espn_results.py` — match result writes; (g) backfills in `workers/backfill_*.py`. For each, measure baseline (count of round-trips × pooler RTT estimate) vs bulk version and document in a rollup `dev/active/bulk-store-audit.md`. Each confirmed hot-spot becomes its own task `BULK-STORE-<area>`. **Method note:** the giveaway pattern is "wall time >> CPU time + AF API time" on Railway logs — that's pure DB-wait. | 2-3h audit + per-area implementation | ⬜ | ✅ Ready | Don't bulk-convert speculatively — only when there's a measurable hot path. Some loops are intentionally serial (e.g. settlement bankroll updates that depend on previous result). Read first, refactor only where the loop body has no inter-iteration dependency. |
| TRANSFERS-DUPE-KEY | `store_team_transfers` bulk-upserts on conflict key `(team_api_id, player_id, transfer_date)`, but the AF transfers endpoint returns multi-leg moves (e.g. loan-out + loan-back) on the same calendar date for the same player. Postgres rejects the whole batch with `ON CONFLICT DO UPDATE command cannot affect row a second time` — surfaced loudly during the 08:42 UTC `backfill_transfers` run, dropping every team's transfers silently to 0 rows stored. **Fix:** dedupe `valid` rows by the conflict tuple before `execute_values` — last leg wins (insertion order from AF). Smoke test extended to assert the dedupe is in place. | 15m | ✅ Done 2026-05-09 | ✅ Ready | Conflict-key dedupe is the right call vs. composite-key extension — the leg distinction (transfer_type) isn't a betting signal; we just need the latest known team for the player on that date. |
| SETTLE-MARKET-GAPS | `settle_bet_result` only handled `1x2` and `over_under_*` — every BTTS bet (yes + no) settled as `lost` regardless of score, and inplay O/U bets where the line lives in `selection` (e.g. `market='O/U'`, `selection='over 1.5'`) silently used the default 2.5 line. Surfaced when user noticed `bot_btts_all` was 9/9 lost despite several matches ending with both teams scoring; same shape on `bot_btts_conservative` (3/3). Audit across 349 settled `simulated_bets` rows found 17 mis-settled bets across 4 bots: `bot_btts_all` (6), `bot_ou15_defensive` (7), `bot_ou35_attacking` (2 — incl. one `won`→`lost` on `over 3.5` 2-1), `bot_btts_conservative` (2). **Fix:** added BTTS branch (yes ↔ both ≥1, no ↔ either 0); new `_parse_ou_line()` walks both market and selection tokens; `25` (no-dot legacy) still maps to 2.5; falls through to `lost` when no parseable line. **Re-settle:** `scripts/resettle_after_btts_fix.py` updates result/pnl on the 17 affected rows and rewrites `bankroll_after` running totals + `bots.current_bankroll` for the 4 affected bots (idempotent — safe to re-run). 2 new smoke tests cover BTTS yes/no truth table and inplay-format O/U line parsing. | 45m | ✅ Done 2026-05-10 | ✅ Ready | Bankroll deltas after re-settle: `bot_btts_all` 940.97→1031.19, `bot_ou15_defensive` 1077.92→1185.54, `bot_btts_conservative` 976.99→1020.21, `bot_ou35_attacking` 1059.77→1027.11. The `bot_ou35_attacking` decrease is correct — the fix surfaced one false-win (3.5 line treated as 2.5) and net effect was negative for that bot. |
| EMAIL-DIGEST-EDGE-UNITS | Smart-slot digest qualification (`EMAIL-DIGEST-SMART`, 2026-05-09) shipped with `sb.edge_percent >= 3` in three places (`compute_signal_strength`, `fetch_value_bets_summary`, `fetch_new_value_bets`). But `edge_percent` is stored as a decimal (0.05 = 5%), so the filter required ≥300% edge — impossible. Result: signal_strength=0 on every slot, no digest emails sent 2026-05-10/11/12 despite all 4 daily slots running to completion. Same filter broke value-bet alerts (also 0). Surfaced by user noticing admin dashboard "Digests sent today: 0" / "Value bet alerts: 0" at 23:37 local. **Root cause confirmed:** today's 36 pending bets had edge 0.04–0.34 (max ≈ 34%); zero pass `>= 3`. Author also wrote the signal_strength formula (`edge_pct × prestige × kelly`) assuming `edge_pct` is in percentage-point units (5 = 5%), so threshold default `EMAIL_DIGEST_MIN_SIGNAL=5.0` only works when edge ≈ percentage-points — mismatched with decimal storage. **Fix:** (a) all three `>= 3` → `>= 0.03` (3% as decimal); (b) `compute_signal_strength` formula scales `sb.edge_percent * 100` so threshold default keeps its documented meaning (~20 quality picks ≈ score 5). Verified post-fix on today's data: signal=21.7 ≥ 5.0, qualifies; 30 of 36 pending bets are in prestige-weighted leagues. Module docstring updated to call out the edge-percent-is-decimal trap. 1 new smoke test `EMAIL-DIGEST-EDGE-UNITS` blocks any future `edge_percent >= 3` regression and asserts the `*100` scaling stays in the formula. | 30m | ✅ Done 2026-05-12 | ✅ Ready | Backfill consideration: today's 16 subscribed users still won't get an email today — the next slot fires tomorrow 10:00 UTC. `python -m workers.jobs.email_digest --force` would send now if desired. |
| EMAIL-DIGEST-SMART | Replace single 07:30 UTC digest cron with 4 qualification slots (10:00, 12:00, 14:00, 16:00 UTC). First slot whose pending-bet signal-strength score clears `EMAIL_DIGEST_MIN_SIGNAL` (default 5.0) sends; later slots see the existing per-user `email_digest_log` lock and skip. Score = Σ(edge_pct × prestige_weight × kelly_fraction) where prestige weight comes from `workers/utils/league_prestige.py` — Big-5 European tops + UEFA = 1.0; Eredivisie/Championship/MLS/Brazil/etc. = 0.7; Switzerland/Austria/Russia/Ukraine/etc. = 0.4; youth/women/lower divisions = 0 (excluded entirely). Email content (`fetch_todays_previews` + `fetch_value_bets_summary`) also filters to weight > 0, so users no longer see "Brescia U19 vs Sudtirol U19" in the previews block. Replaces the broken pattern where 07:30 routinely sent "0 value bets today" emails — most evening markets aren't priced until 09:00–11:00 UTC. 6 new smoke tests covering tier weights for Big-5/youth/T2/T3, the `qualifies_today`/`compute_signal_strength` helpers, scheduler slot loop, and `run_email_digest` qualification gate. | 1h | ✅ Done 2026-05-09 | ✅ Ready | `EMAIL_DIGEST_MIN_SIGNAL` is configurable per-environment so we can tune the threshold from real Mon–Thu vs weekend signal data without redeploys. The existing `--force` CLI flag (added) bypasses qualification for ad-hoc sends. |
| BOT-AGGREGATES-CAP | `getAllBets()` in odds-intel-web `src/lib/engine-data.ts` had a silent `.limit(500)` ordered by `pick_time DESC`. Once total `simulated_bets` exceeded 500, the oldest bets fell off the window — the `/admin/bots` Per-Bot Performance table (which aggregates client-side from `getAllBets()`) under-reported settled/won/lost/P&L for the highest-volume bots while the public `/performance` Bot Leaderboard (which reads pre-aggregated `dashboard_cache.bot_breakdown`) stayed correct. Surfaced when user compared the two views: bot_aggressive showed 195 settled / +€99.29 in Leaderboard but 173 settled / +€30.23 in Per-Bot, and bot_ou35_attacking's P&L even flipped sign (+€27.11 vs −€3.07). Bankroll matched across both views because `bots.current_bankroll` is its own column. **Fix:** replaced `.limit(500)` with `.range(0, ALL_BETS_CEILING - 1)` (ceiling = 20000, ~4 years runway at current rate); `.range()` bypasses Supabase's default 1000-row `db-max-rows`. Added `console.warn` if the ceiling is hit. 1 new smoke test (`BOT-AGGREGATES-NO-SILENT-CAP`) source-inspects `engine-data.ts` and rejects any `.limit(N<10000)` reappearing in `getAllBets()`. | 30m | ✅ Done 2026-05-10 | ✅ Ready | Tactical fix — restores correctness immediately. The deeper architectural fix is `BOT-AGGREGATES-SSOT` below: make admin Per-Bot Performance read aggregates from the same `dashboard_cache.bot_breakdown` source the public leaderboard uses, so the two views can't diverge by construction. |
| ODDS-QUALITY-CLEANUP | Best-price OU aggregation in `_load_today_from_db` (`daily_pipeline_v2.py:1086-1102`) and ingestion in `store_match_odds` (`supabase_client.py:580+`) accepted garbage rows from three sources, inflating Over/Under odds 2–3× over true market. Audit found: `bookmaker='api-football'` 100% of OU pairs invalid (avg implied-sum 0.63 across all OU lines) — synthetic AF source emits non-market data; `William Hill` 88% of OU 1.5 pairs Under-favored (line labels swapped/shifted, 100% Under-favored on 2.5/3.5/4.5); `api-football-live` in-play odds (max 21.0) leaking into pre-match best-price. Surfaced when user reported `bot_ou15_defensive` taking Over 1.5 at 3.34 odds while Pinnacle had 1.45. Bot won 76% of these phantom-edge bets because Over 1.5 actually hits — but at fake prices that don't exist anywhere in the real market. 1X2 + BTTS verified clean (<0.05% invalid across all books). MFV/Platt/ELO/training also verified untouched (`build_match_feature_vectors` reads `market='1x2'` only). **Fix (Stage A):** blacklist (`api-football`, `api-football-live`, `William Hill`) for OU markets at both read and write paths, plus implied-sum sanity gate `1/over + 1/under >= 1.02` that auto-quarantines any future broken feed. **Cleanup (Stage B):** `scripts/cleanup_ou_odds_garbage.py` copies targeted rows to `odds_snapshots_quarantined` then DELETEs them; pair-validation sweep removes both sides of impossible markets. **Resettlement (Stage C):** `scripts/cleanup_ou_bets_after_quality_fix.py` voids settled OU bets whose `odds_at_pick` no longer matches any surviving snapshot (marker in `reasoning`, `pnl=0`), deletes pending; recomputes `bots.current_bankroll` and `simulated_bets.bankroll_after`. **Stage D:** dashboard cache rebuild + CLV recompute. `bot_ou15_defensive` + `bot_ou35_attacking` disabled during cleanup, re-enabled after Stage E. Plan + alignment with ML-PIPELINE-UNIFY in `dev/active/odds-quality-cleanup-plan.md` — confirmed MFV rebuild (Stage 0e) is **safe to run in parallel**, was not poisoned. **Shipped 2026-05-10:** 349,913 garbage OU rows deleted (262,741 blacklisted-source + 87,172 impossible-pair sides; quarantined to `odds_snapshots_quarantined`). 53 settled OU bets voided across 8 bots; ~$257 phantom PnL erased. Bankroll deltas: bot_ou15_defensive 1185.54→1092.96, bot_aggressive 1096.91→931.63, bot_ou35_attacking 1027.11→996.93, others ±1–7. Post-cleanup audit: 0% invalid pair rate across every remaining bookmaker. 5 new smoke tests prefixed `ODDS-QUALITY-CLEANUP — …`. New `bots.is_active=false` gate in `daily_pipeline_v2.run_morning` so future bot pauses are codeless. | 4-5h | ✅ Done 2026-05-10 | ✅ Ready | The 3-source blacklist is a pragmatic defensive choice; root cause inside the AF parser (why does AF emit `bookmaker='api-football'` rows that don't sum to a market?) deserves a follow-up `OU-LINE-DRIFT-INVESTIGATE` task. AF doesn't carry Nordic books (Paf, Coolbet, Veikkaus) — separate `NORDIC-BOOKS-INTEGRATION` task. |
| OU-LINE-DRIFT-INVESTIGATE | Follow-up to `ODDS-QUALITY-CLEANUP`. Side-by-side audit during cleanup (10 most recent `bot_ou15_defensive` picks vs every bookmaker) showed several books — Bet365, Betano, BetVictor, Betfair, 1xBet, Marathonbet, 10Bet — *occasionally* report Over-2.5-shaped prices in the `over_under_15` slot for specific matches (e.g. Pinnacle says Over 1.5 = 1.10, same match Bet365 stores Over 1.5 = 1.57 — looks like an Over 2.5 price). The implied-sum gate (`1/over + 1/under ≥ 1.02`) catches outright invalid pairs but doesn't catch shifted-but-still-valid pairs (a real Over 2.5 / Under 2.5 stored as Over 1.5 / Under 1.5 would sum to ~1.07 — passes the gate). Likely either AF feed-time anomaly or parser edge case in `parse_fixture_odds`. **How:** capture 1 day of raw `get_odds_by_date()` JSON to disk, diff per-bookmaker `over_under_15` rows against `over_under_25` rows for the same match — when prices match across labels, that's the bug. Then either fix the parser or extend the gate to require Pinnacle-relative sanity (Pinnacle Over 1.5 implied prob is the cleanest reference). | 4h | ⬜ | ✅ Ready | Lower priority than the source blacklist — historical impact small (these drifts didn't dominate max-aggregation the way `api-football` and `William Hill` did). Worth doing before any new OU bot ships, since Pinnacle-only fallback isn't viable for best-price aggregation. |
| NORDIC-BOOKS-INTEGRATION | AF doesn't carry Nordic-region books (Paf, Coolbet, Veikkaus, Svenska Spel, Norsk Tipping). Only Unibet (via Kindred) is Nordic-aligned in the AF feed. User asked about Nordic odds during `ODDS-QUALITY-CLEANUP` audit — would be useful for: (a) Estonian/Finnish/Swedish-market real-bettability sanity (Paf/Coolbet are the actual books a user could place bets at), (b) a second-source arbitrage check on AF prices for OU markets where the AF aggregate is most error-prone. **How:** likely separate scraper(s) — Paf/Coolbet expose JSON APIs through their public widgets; Veikkaus has a `tarjous` endpoint. Cache-first per-match snapshot writer to `odds_snapshots` with `bookmaker='Paf'` / `'Coolbet'` / etc. Same blacklist + sanity-gate machinery from ODDS-QUALITY-CLEANUP applies on entry. | 1-2 days | ⬜ | ⏳ When bookmaker count becomes a bottleneck OR Nordic real-bettability becomes a product requirement | Not blocking anything. File as P3 unless a Nordic-bettor-facing feature lands. |
| SETTLE-VOID-POSTPONED | When AF reports a fixture as `PST/CANC/SUSP/AWD/INT`, `_check_stale_matches` (`workers/jobs/settlement.py:491-498`) flipped `matches.status='postponed'` but did NOT touch `simulated_bets`. Pending bets on the cancelled fixture stayed `result='pending'` forever — surfaced by user noticing 6+ stuck bets across 3 bots dating back to May 3 (`Canberra Juventus`, `Bastia v Le Mans`, one earlier). The earlier "0 pending bets on finished matches" health query missed them because the stuck rows live on `status='postponed'` matches, not `finished`. **Fix:** same code path now runs `UPDATE simulated_bets SET result='void', pnl=0 WHERE match_id=%s AND result='pending'` immediately after the matches UPDATE; rowcount is logged for visibility. **Backfill:** voided 7 stuck bets (May 3 / May 8 / May 9) in one SQL pass. Bet IDs preserved in commit message for rollback. Smoke test `SETTLE-VOID-POSTPONED` source-inspects the postpone branch and asserts both UPDATEs are present and the void scope stays `result='pending'` only. | 30m | ✅ Done 2026-05-10 | ✅ Ready | Reversal: `UPDATE simulated_bets SET result='pending', pnl=NULL WHERE id IN (...)` — the 7 ids are in the commit message. The fix is symmetrical with the existing `result='void'` cleanup elsewhere in the codebase (e.g. `cleanup_match_dupes.py`, `cleanup_ou_bets_after_quality_fix.py`). |
| MATCH-DUPES-CLEANUP | Ops dashboard showed 3,136 "matches today" while `fetch_fixtures` had stored 1,211 — investigation found 1,425 duplicate-fixture groups in `matches` (3,177 extra rows). Root cause: `bulk_store_matches` (and the legacy `store_match`) dedup keys on `(home_team_id, away_team_id, date_prefix)`, so when a fixture is rescheduled across a UTC day boundary (e.g. AF moves a match from May 9 → May 10), the May-10 dedup window can't see the May-9 row and an INSERT fires. No DB-level unique constraint existed on `api_football_id` to prevent it. Bets/odds/signals scattered across the duplicate `matches.id` rows; `COUNT(*)` queries (matches_today, postponed) inflated 3×; `bot_ou15_defensive` showed ghost OU 1.5 entries from the cleanup-voided rows because the leaderboard table didn't filter `result='void'`. **Fix:** (1) `scripts/cleanup_match_dupes.py` — dry-run by default, `--apply` repoints all 24 `match_id`-bearing tables to the canonical (oldest-`created_at`) row per `api_football_id` (handles the 18 unique-constraint conflicts by deleting the dependent dupe row when the canonical already has a row at the same unique tuple), quarantines the orphan `matches` rows to `matches_dupe_quarantined`, then DELETEs them. (2) Migration 089 — partial unique index `matches_af_id_unique ON matches(api_football_id) WHERE api_football_id IS NOT NULL`. (3) `bulk_store_matches` + `store_match` dedup rewritten — first lookup by `api_football_id` when present (covers reschedules), home/away/date_prefix path kept as fallback for legacy rows without AF id. (4) `performance-leaderboard.tsx` table filters `result='void'` so cleanup-voided bets stop polluting bot history. (5) Ops dashboard subtitle copy fixed (the contradictory "1211 pulled, of those 3136 play today" line). 4 new smoke tests. **Shipped 2026-05-10:** dry-run vs apply matched exactly: 1,011,505 FK rows repointed to canonical, 13,251 dependent rows deleted on conflict (predictions 9,287 + match_feature_vectors 2,708 + match_player_stats 695 + match_events 332 + match_injuries 206 + others), 3,177 dupe `matches` rows quarantined and deleted. Post-cleanup snapshot: `matches_today` 4,136 → 1,227 (matches the actual fetch count); `matches_with_predictions` 2,959 → 1,154 (94% — the AF coverage rate, was inflated 240% by dupes). Migration 089 applied directly to DB after cleanup; quarantine table preserved for rollback. | 3h | ✅ Done 2026-05-10 | ✅ Ready | Destructive cleanup — quarantine table is the rollback path. The unique index makes recurrence impossible at the DB level even if the application-level dedup misses again. |
| BOT-AGGREGATES-SSOT | Follow-up to `BOT-AGGREGATES-CAP`. The two bot-stats views currently compute aggregates from different sources: `/performance` reads `dashboard_cache.bot_breakdown` (engine-computed in `write_dashboard_cache`, ground truth), while `/admin/bots` re-aggregates client-side from `getAllBets()`. They can drift any time someone changes one filter and not the other, even with the row cap fixed. **Refactor:** (1) admin Per-Bot Performance reads settled/won/lost/total_pnl/roi from `dashboard_cache.bot_breakdown` exactly like the public leaderboard; (2) extend `bot_breakdown` schema with `pending`, `total_staked` so admin can render full table from cache (extend `write_dashboard_cache` query); (3) for the click-to-expand modal (bankroll chart + bet history), add `getBetsForBot(botId)` that queries `simulated_bets` with `.eq('bot_id', botId)` — no global limit, fetches only one bot's bets lazily via a new API route or server action; (4) delete `getAllBets()` once both call sites migrate. Net effect: divergence becomes structurally impossible — both views read the same canonical aggregate from the same DB row. Bonus: `/admin/bots` page render time drops (no 500-1000+ joined bet rows fetched on initial load). | 2-3h | ⬜ | ✅ Ready | Don't ship until you also extend the smoke test to assert `dashboard_cache.bot_breakdown.total_pnl` for each bot reconciles to `bots.current_bankroll - bots.starting_bankroll` (within 1%) — that catches cache-side aggregation bugs the same way the current test catches frontend truncation. |
| OPS-COVERAGE-TIMEOUT | Ops dashboard showed `Have odds: 0`, `Bookmakers active: 0`, `Total rows today: 0` while `fetch_odds` was succeeding (540 rows, 66s) — every other metric on the page looked fine. Root cause: `write_ops_snapshot` odds_coverage section used a correlated `NOT EXISTS` subquery against the full `odds_snapshots` table to compute `without_pinnacle`. At 1.9M today-odds rows it timed out at Postgres `statement_timeout` (120s) — the exception was silently caught (the section's try/except just logs to `failed_sections`), and the snapshot still INSERTed with the 8 odds counters at their default 0 value. The 5 most recent `ops_snapshots` rows for today: 4 zeros, 1 with real values (646 matches with odds, 1,550,131 rows) — confirming intermittent timeout, not steady failure. **Fix:** rewrote the query with FILTER aggregates (`FILTER (WHERE o.bookmaker = 'Pinnacle')`) and derived `matches_without_pinnacle = matches_with_odds - matches_with_pinnacle` in Python. Empirical timing: 122s+ (timeout) → 11.8s. Also promoted the `failed_sections` mechanism to mark the entire `pipeline_runs` row as failed when any of `{fixtures_count, odds_coverage, predictions_count, signals_count}` errors — without that, the dashboard's Pipeline Runs widget kept showing "ok" while individual rows had silent zeros. Verified post-fix: latest snapshot now reports 666 matches with odds, 1,918,008 rows, 15 bookmakers, 584 with Pinnacle. 1 new smoke test (`OPS-COVERAGE-TIMEOUT`) source-inspects `write_ops_snapshot` for the FILTER form, the without_pinnacle Python derivation, the absence of the NOT EXISTS subquery, and the CRITICAL_SECTIONS guard. | 1h | ✅ Done 2026-05-10 | ✅ Ready | Reproduces the same silent-failure trap as `INPLAY-UUID-FIX` (May 8): a section runs to completion with no exception bubble-up, the surrounding pipeline reports "ok", but the data is silently zeroed. The CRITICAL_SECTIONS list is the structural fix — any future query inside `write_ops_snapshot` that errors will surface on the dashboard's pipeline-runs panel. |
| SETTLEMENT-CATCHUP | Frequent Railway scheduler restarts (every git push triggers a redeploy = full process restart) wiped the daily `settlement` job. Last night's 21:00 / 23:30 / 01:00 redundant runs all got `killed — scheduler restarted`. Without a startup catch-up, the next scheduled run wouldn't fire until the following 21:00, leaving finished matches sitting unsettled all day. APScheduler's `misfire_grace_time=300` only catches near-firings; full-day misses fall through. **Fix:** `_maybe_catchup_missed_settlement()` in `workers/scheduler.py` runs on startup, checks `MAX(completed_at) FROM pipeline_runs WHERE job_name = 'settlement' AND status = 'completed'`, and if >25h ago kicks `settlement_pipeline()` in a background thread (60s sleep first so the scheduler + health endpoint settle). Idempotent — settlement_pipeline already handles already-settled bets safely. Also realigned the dashboard `getStalePendingBets` 2h alarm threshold to 2.5h to match the `fix_stale_live_matches` 130-min cleanup window — the dashboard was alarming on bets the cleanup hadn't even become eligible to fix yet. 1 new smoke test (`SETTLEMENT-CATCHUP`) source-inspects scheduler.py for the function, the 25h threshold, and the settlement_pipeline call. | 1h | ✅ Done 2026-05-10 | ✅ Ready | Doesn't paper over the underlying Railway-restart-on-every-push pattern (that's expected during active dev), it just makes a single missed daily settlement self-healing. The cron triple (21:00/23:30/01:00) plus catch-up gives 4 chances per 24h. |
| OU-PIN-REQUIRED | User reported (10th time) the OU bot was *still* showing wrong-looking odds (3.05, 3.25, 3.42 on Over 1.5). Audit of `bot_ou15_defensive`'s 38-bet history: 19 voids = 50%. Of those, 12 had **no Pinnacle reference at all** for OU 1.5 over (Belarus Premier, Ireland Premier Div, Ecuador Serie B, Latvia Virsliga — leagues Pinnacle doesn't price OU 1.5 on); the remaining 7 exceeded the 2× Pinnacle cap from `OU-PINNACLE-CAP`. The cap can only fire when Pinnacle has a row to compare against — without one, a single mislabelled non-Pinnacle book row gets promoted by MAX-across-books and the bot bets at fake prices. **Fix:** extend the OU aggregation in `daily_pipeline_v2._load_today_from_db` so that when Pinnacle has no price for an `(match, market, selection)` OU triple, the entire row is skipped (not just non-Pinnacle ones). Result: bot places fewer bets but every one is validated against the sharpest book at placement time. Coverage check on next 2 days: Pinnacle prices ~58% of OU 1.5 / ~85% of OU 2.5 matches — bots stay productive on majors, skip the small-league mislabels that drove every void. Smoke test renamed `OU-PINNACLE-CAP` → `OU-PIN-REQUIRED`, asserts both the new Pinnacle-required check and the existing 2× cap. Frontend (`odds-intel-web/bot-dashboard-client.tsx`): voided bets now hidden by default in the per-bot modal with a "Show N voided" toggle, and the page header shows `N bets loaded · M void hidden` so the dashboard reflects the clean number. | 1h | ✅ Done 2026-05-10 | ✅ Ready | This is the placement-time guard the prior cap-only fix was missing. Historical voided bets are kept (audit trail), just hidden by default — the cleanup scripts (`cleanup_ou_*`) remain available if user decides later to wipe history entirely. The deeper investigation `OU-LINE-DRIFT-INVESTIGATE` is still open but lower priority now that placement requires Pinnacle confirmation. |
| OU-PINNACLE-CAP | Follow-up to `ODDS-QUALITY-CLEANUP`'s blacklist + implied-sum gate. User pointed out that `bot_ou15_defensive` was *still* showing inflated odds in the modal (e.g. Belarus Premier OU 1.5 OVER at 3.42 vs Pinnacle's 1.45 — implies 29% prob of 2+ goals, which is implausible). The earlier blacklist (`api-football`, `api-football-live`, `William Hill`) and the 1.02 implied-sum gate both passed this row through because: (a) the bookmaker isn't on the blacklist; (b) the *under* side was legitimate so 1/3.42 + 1/1.13 ≈ 1.18 > 1.02. The MAX-across-books aggregator at `daily_pipeline_v2._load_today_from_db` then promoted the inflated single-side price. Bookmaker audit across 80k+ rows: SBO avg OU 1.5 OVER = 1.97 (Asian-total feed), api-football-live = 1.96, vs Pinnacle 1.48 / Bet365 1.33. **Fix:** when Pinnacle has a price for the same `(match, market, selection)`, drop any other book's row priced more than 2.0× Pinnacle. Pinnacle is the sharpest reference and the 2× tolerance is wide enough for legitimate soft-book overlays but tight enough to drop label/line errors. Historical impact across all bot bets: bot_ou15_defensive 11/38 dropped, all-bots OU 1.5 16/70, OU 2.5 27/550, OU 3.5 1/14. Built into the same odds-aggregation loop as the existing blacklist — no extra DB round-trip. 1 new smoke test (`OU-PINNACLE-CAP`) source-inspects the cap multiplier and the non-Pinnacle gate. **Cleanup applied 2026-05-10:** `scripts/cleanup_ou_pinnacle_cap.py` mirrors the prior `cleanup_ou_bets_after_quality_fix.py` pattern — voids settled bets where odds_at_pick > 2× same-match Pinnacle price, hard-deletes pendings, recomputes `bankroll_after` running totals + `bots.current_bankroll`. Scope: 24 bets across 4 bots, 17 already voided by ODDS-QUALITY-CLEANUP, 7 newly voided (6 wins + 1 loss = $98.34 fake PnL erased). Bankroll deltas: bot_ou15_defensive 1092.96 → 1055.24, bot_v10_all 1088.63 → 1062.87, bot_aggressive ~965 → 930.38, bot_ou35_attacking unchanged (1 bet already void). | 1h | ✅ Done 2026-05-10 | ✅ Ready | Couldn't fully reconstruct the 3.42 source for the Belarus match because `prune_odds_snapshots` had since deleted intermediate snapshots (it keeps first + last + closing per match/bookmaker/market/selection on finished matches). Pattern is clear from the per-bookmaker aggregate stats though. The deeper investigation (`OU-LINE-DRIFT-INVESTIGATE`) — capturing raw AF JSON to diff `over_under_15` vs `over_under_25` rows per match — is still warranted but lower priority now that the cap defends against the worst case. |

### Critical bugs found 2026-05-13

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| RECOVER-PHASE2 | ✅ Done 2026-05-13 — **`scripts/recover_today.py` produced bets with `recommended_bookmaker = NULL`** (75/75 today). Root cause: `STEPS[4]` invoked `run_morning()` with no args → defaults to `skip_fetch=False` → Phase 1 path that re-fetches everything from API-Football and never populates `best_bookmaker` (initialized empty at `daily_pipeline_v2.py:1491`). The Railway scheduler is unaffected — it goes through `betting_pipeline.run_betting` → `run_morning(skip_fetch=True)` → Phase 2 path that reads from DB and fills the bookmaker. **Fix:** STEPS tuples extended to include a per-step kwargs dict; step 5 now passes `{"skip_fetch": True}`. Bonus: kills ~10 min of duplicate AF fetches (predictions + enrichment + odds) that Phase 1 was doing on top of steps 2-4. Smoke test `RECOVER-PHASE2` source-inspects the kwargs literal. | 20m | ✅ Done 2026-05-13 | ✅ Ready | Daily_picks.py becomes usable for manual Coolbet placement once today's bets re-run (or tomorrow morning naturally). |
| BET-TIMING-MONITOR | ✅ Done 2026-05-13 (Phase 1 infra) — **Shadow-bet pipeline shipped: every bot evaluated at every refresh window for clean per-bot timing data.** Motivation: Phase A audit showed the cohort A/B is confounded — different bots in different cohorts means we cannot tell if ROI gaps are from strategy or timing. Same-bot direct evidence (`bot_ou15_defensive`: morning −2.1%, midday +34.1% on OU n=33) confirms timing matters for at least one cohort. **Implementation:** migration 101 creates `shadow_bets` (mirrors `simulated_bets` minus bankroll, + `shadow_run_id`/`shadow_cohort`). `run_morning(shadow_mode=True, shadow_cohort=...)` ignores cohort filter, runs ALL bots, writes to `shadow_bets`, skips bankroll mutation + exposure cap, accumulates via `bulk_store_shadow_bets`. Railway scheduler: 3 new jobs at **06:30 / 11:30 / 15:30 UTC** (`job_shadow_run_morning/midday/pre_ko`). Settlement extended via `_settle_pending_shadow_bets()` — wrapped in own try/except so it never blocks real-bet settlement. Ops snapshot adds **`shadow_runs_today`** (should = 3 by 16:00) and **`shadow_bets_today`** counters so the dashboard surfaces daily health. Two-dimensional analysis comes free: each shadow row records `pick_time` + joins `matches.date`, so `hours_before_ko` is derivable for cuts by both absolute time (shadow_cohort) and match-relative time. 5 new smoke tests (SHADOW-BETS-TABLE, SHADOW-MODE-WIRED, SHADOW-NO-BANKROLL, SHADOW-SETTLE-WIRED, SHADOW-SCHEDULER). Compute cost: ~3× refresh-run time, zero AF calls. Phase 2: collect data through 2026-06-12. Phase 3: `scripts/bot_timing_recommendation.py` (build then). See `dev/active/bet-timing-monitor-plan.md`. | 6h | ✅ Done 2026-05-13 (Phase 1) | ✅ Ready (Phase 3 deferred to ~2026-06-15) | Replaces and supersedes `ODDS-TIMING-VALIDATE` and `ODDS-TIMING-OPT` for the per-bot factorial question. Existing CLV-based timing analysis (`scripts/odds_timing_analysis.py`) stays valid and complementary. |
| BOT-TIMING-OU-MIDDAY | ✅ Done 2026-05-13 — **Moved `bot_ou25_global` and `bot_opt_ou_british` from morning → midday cohort.** Phase A timing audit (this session) found: across 14d resolved bets, OU market ROI was **-3.6% morning (n=114)** vs **+26.8% midday (n=31)**. Same-bot A/B on `bot_ou15_defensive` (only bot that historically ran in both cohorts): **morning -2.1% (n=9), midday +34.1% (n=24)** — direction is consistent. Hypothesis: injury news landing at ~11:00 UTC is the deciding signal for totals markets. New cohort split: morning 7 / midday 9 / pre_ko 7. Smoke test `BOT-TIMING-OU-MIDDAY` asserts the 4 OU specialists are all midday so this can't silently revert. **Caveat:** `bot_aggressive` still places OU in morning (81 bets at -8.2% ROI) — can't move it because its 1X2 leg is +7.6% in morning. Needs the shadow-bet infrastructure (BET-TIMING-MONITOR below) to split. | 15m | ✅ Done 2026-05-13 | ✅ Ready | |
| OPS-SNAPSHOT-RETIRED | ✅ Done 2026-05-13 — **`ops_snapshot` total_bots count included retired bots, inflating `silent_bots` by 3.** Investigating "4-5 inplay bets across 100s of live matches" today surfaced 3 active-flagged inplay bots (`inplay_a2`, `inplay_c_home`, `inplay_f`) with zero tries — all retired on 2026-05-09 (`retired_at` set, `is_active` left true per the convention from `INPLAY-BOT-RETIREMENT`). Engine correctly skips them (intentional `# not dispatched` branches in `_check_strategy`), but `supabase_client.py:5159` counted `WHERE is_active = true` only → total_bots = 39 instead of 36 → `silent_bots = total − active` overstated by 3. Same drift hit `scripts/morning_update.py:247`. **Fix:** both queries now filter `is_active=true AND retired_at IS NULL` to match the existing convention used in `settlement.py:write_dashboard_cache`, `daily_pipeline_v2.py:1503`, and `bot_perf_report.py:144`. Inplay funnel itself is healthy — 13 active inplay bots logged 8,300+ tries today (more than yesterday's 3,867); only 7 fires is just strict edge/value filters, not a wiring bug. Smoke test `OPS-SNAPSHOT-RETIRED` source-inspects the query. | 20m | ✅ Done 2026-05-13 | ✅ Ready | The `retired_at` convention (`INPLAY-BOT-RETIREMENT`, 2026-05-10) is the authoritative marker. Any new caller that filters bots must check both flags; the smoke test prevents the ops_snapshot regression specifically. |

---

### Critical bugs found 2026-05-12

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| BEST-BOOKMAKER-RETURN | **0 bets placed all day 2026-05-12 — `best_bookmaker` never returned from `_load_today_from_db`.** Root cause: `best_bookmaker` was populated inside `_load_today_from_db` (line ~1336) but never included in the return tuple — the function returned a 2-tuple `(odds_matches, af_preds)` while `run_morning` referenced `best_bookmaker.get(...)` at `store_bet()` time, raising `NameError: name 'best_bookmaker' is not defined`. The exception was silently caught by `except Exception as e: console.print(...)`, so every bet stored as "Error storing bet" with 0 bets written and nothing looking wrong in the pipeline summary. **Fix:** return `best_bookmaker` as 4th element; two early returns in `_load_today_from_db` also corrected from `return [], {}` to `return [], [], {}, {}`; `run_morning` initializes `best_bookmaker = {}` before the `skip_fetch` branch and unpacks 4 values. Secondary finding: `load_shrinkage_alphas()` in `improvements.py` has a `%` in its SQL `LIKE 'shrinkage_alpha_%'` clause that psycopg2 treats as a parameter placeholder → IndexError → always falls back to hardcoded alphas (0.20 T1, 0.30 T2) instead of learned values (0.0008 / 0.0000). Not fixed this session — tracked as `SHRINKAGE-ALPHA-SQL-BUG` below. Smoke test `BEST-BOOKMAKER-RETURN` source-inspects the 4-tuple return and unpack. After fix: morning cohort 24 bets + midday cohort 2 bets placed. | 30m | ✅ Done 2026-05-12 | ✅ Ready | ACCESSIBLE-BM (2026-05-11) introduced `best_bookmaker` as a local variable but did not update the return signature. The `except Exception` swallow in the bet-store loop is the silent-failure trap (same pattern as INPLAY-UUID-FIX). |
| SHRINKAGE-ALPHA-SQL-BUG | ✅ Done 2026-05-12 — `load_shrinkage_alphas()` in `workers/model/improvements.py` uses `WHERE market LIKE 'shrinkage_alpha_%'` — the trailing `%` is treated as a positional parameter placeholder by psycopg2 (expects `params` tuple, gets `[]`) → `IndexError` → function always returns `{}` silently → hardcoded `CALIBRATION_ALPHA = {1: 0.20, 2: 0.30, 3: 0.50, 4: 0.65}` used instead of learned DB values (DB has `shrinkage_alpha_t1_1x2: 0.0008`, `shrinkage_alpha_t2_1x2: 0.0000`). **Fix:** `%%` in the LIKE clause. Impact: T1 alpha 0.20 → 0.0008 (near-zero Pinnacle shrinkage for T1 1X2), T2 0.30 → 0.0000. Smoke test `SHRINKAGE-ALPHA-SQL-BUG` source-inspects the `%%` form. | 15m | ✅ Done 2026-05-12 | ✅ Ready | |

---

### Unified ML Pipeline — Started 2026-05-10 (see `dev/active/unified-ml-pipeline-plan.md`)

> Origin: post-backfill audit revealed three disconnected pieces — production XGBoost (Kaggle-trained, frozen), `workers/model/train.py` (writes to paths nothing reads), and `match_feature_vectors` (only grows for live-pipeline matches). Plan: unify into one closed loop — DB → derived backfills → MFV → versioned train → loaded model → tagged predictions → comparison harness → next train.

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ML-PIPELINE-UNIFY | Master task — see `dev/active/unified-ml-pipeline-plan.md`. All stages complete as of 2026-05-12. v14 is production (MFV schema + Pinnacle + OU market features). Weekly retrain cron runs Sunday 03:00 UTC and trains `v{YYYYMMDD}`; compare_models auto-runs after; promotion stays manual. Stages 0e + 4a done on 47,292 MFV rows. ML-INFERENCE-MFV-WIRE done (v10+ dispatches to MFV path). MFV-LIVE-BUILD done (pre-KO rows written before predictions run). v10→v11→v12→v13→v14 progression completed 2026-05-11. | ~2 days agent + 14 days shadow | ✅ Done 2026-05-12 | ✅ Done | Weekly retrain + manual promotion is the ongoing loop. Next model improvement: B-ML3 meta-model or new features for next weekly bundle. |
| ML-INFERENCE-MFV-WIRE | **Shipped 2026-05-10.** `get_xgboost_prediction` now dispatches by schema: `_is_mfv_schema(feature_cols)` returns True for v10+ bundles (presence of `elo_home`, absence of `home_elo`) → fetches the row from `match_feature_vectors` by `match_id` via `_build_row_from_mfv`. v9* bundles still use the legacy `_build_row_from_legacy_cache` keyed by team name. Both paths feed the same downstream prediction logic. Stage-2a `_missing` indicators are recomputed at inference from the raw MFV row so they match training. Daily pipeline `daily_pipeline_v2.py` now passes `match_id` into `get_xgboost_prediction`. Smoke test `ML-INFERENCE-MFV-WIRE — v10 schema routes to MFV inference path` guards both helpers and the call-site wire. **Side benefit:** drops the legacy two-fetch (team_form_features + ELO daily) for v10+ down to a single MFV read. **Live shadow deploy is now technically unblocked** — Stage 4c can run as soon as ops sets `MODEL_VERSION_SHADOW=v10_pre_shadow` on Railway. | 4-6h | ✅ Done 2026-05-10 | ✅ Ready | One follow-up: build_match_feature_vectors only runs at nightly settlement for yesterday. For today's pre-KO matches, MFV row may not exist yet; `_build_row_from_mfv` returns None and the pipeline falls back to Poisson-only. To reach full v10 coverage live we'd need a build-today-MFV step before predictions run — separate `MFV-LIVE-BUILD` follow-up. |
| MFV-LIVE-BUILD | **Shipped 2026-05-10.** Pre-KO MFV rows now written for every match scheduled today, so v10+ inference (`_build_row_from_mfv`) finds a row instead of returning None and falling back to Poisson. New `build_match_feature_vectors_live(client, date_str)` in `supabase_client.py` is the twin of the nightly `build_match_feature_vectors` — same `_build_mfv_rows_for_matches` helper (extracted to share batched-load + per-match build + bulk-upsert path) but selects `WHERE status != 'finished'`. Wired into `run_morning` between the morning signals batch and the prediction loop, so opening_implied_* / odds_drift_home / ELO / form / signals are all populated when MFV is built. Re-runs on every betting_refresh because opening odds drift between cron passes. **Critical safety:** v10's FEATURE_COLS contains zero prediction-source columns (poisson_prob_home, ensemble_prob_home, etc.) — confirmed via `train.py:FEATURE_COLS`. So MFV can be built before Poisson predictions are computed in the loop without losing any inference signal. **Verified end-to-end:** 3,575 pre-KO MFV rows written for 2026-05-10 (96% with ELO, 17% with opening odds — opening_implied coverage rises across the day as more bookmakers price the late games). 3 new smoke tests prefixed `MFV-LIVE-BUILD — …`: live builder runs + returns int (real DB call), status-filter source guard (asserts `status != 'finished'` and that the `_build_mfv_rows_for_matches` shared helper exists), wire-through guard (asserts run_morning calls live builder AFTER signals batch and BEFORE the get_xgboost_prediction loop). | 2-3h | ✅ Done 2026-05-10 | ✅ Ready | v10 is still NOT loaded as the production bundle — the Railway env var swap is a separate step. Today's pipeline keeps using v9 (legacy path), but the moment ops sets `MODEL_VERSION` to a v10+ bundle, every pre-KO match it touches has a fresh feature row already waiting. Closes the last hidden critical-path blocker on ML-PIPELINE-UNIFY Stage 4c shadow deploy. |
| ML-BUNDLE-STORAGE | **Shipped 2026-05-10.** Solves Railway's ephemeral-filesystem problem for weekly retrains: every deploy was destroying any newly-trained bundle (latent bug — currently masked because production never switched off the git-tracked v9). New `model_versions` table (migration 090) is the registry; new `models` bucket in Supabase Storage holds the binaries; new `workers/model/storage.py` wires upload/download/list. `train.py:train_all()` now auto-uploads + auto-registers every bundle on success. `xgboost_ensemble._load_models()` now lazy-downloads from Storage when the bundle dir is missing locally — first prediction after deploy adds ~1-3s, all subsequent predictions hit local cache. New `scripts/list_models.py` is the operator CLI; new `scripts/bootstrap_model_storage.py` is the one-shot uploader (already run for all 16 existing bundles). End-to-end verified: forced cold-start by moving v12_post0e dir aside → loader auto-downloaded all 6 files from Storage → inference loaded successfully. 4 new smoke tests prefixed `ML-BUNDLE-STORAGE — …`. **Operator workflow now**: (1) train: `python3 workers/model/train.py --version v_YYYYMMDD` (auto-uploads + registers). (2) inspect: `python3 scripts/list_models.py`. (3) compare: `python3 scripts/offline_eval.py vA vB ...`. (4) promote: SQL update + set `MODEL_VERSION` on Railway + redeploy. (5) rollback: change MODEL_VERSION back; old bundle still in Storage. Costs: ~$0.05/mo for 5 years of weekly bundles. Full reusable design in `docs/ML_MODEL_REGISTRY.md` (~7,000 words covering architecture, costs, gotchas, port-to-other-projects guide). | 4-5h | ✅ Done 2026-05-10 | ✅ Ready | **Railway needs `SUPABASE_SECRET_KEY` env var set** — that's the service-role key that can write to Storage. Without it, training works but upload fails (loud warning). Queue follow-up: thread per-market CV metrics from train_*_model() through to register_version()'s `cv_metrics` JSONB field — currently NULL. |
| ML-MODEL-COMPARISON | **Shipped 2026-05-10.** New `scripts/offline_eval.py` runs N model bundles head-to-head on the same held-out MFV slice — answers "did retraining actually improve the model" without waiting 14 days for shadow data. Final 5-way comparison (v9 / v10 / v11 / v12 / v13) on 6,544 finished matches across 2026-04-26 → 2026-05-09 saved to `dev/active/model-comparison-2026-05-10-final.md`. **Headline (1x2_home log_loss): v9=0.760, v10=0.343, v11=0.359, v12=0.279, v13=0.345.** v12 (post-0e MFV refresh, no Pinnacle) wins all 1X2 markets. v13 (Pinnacle features, 5% coverage) wins on over_25 + btts_yes. v11 still has the strongest *complete* bundle (1X2 + OU + BTTS all calibrated). v10 has broken BTTS (uncalibrated, ECE 0.25). v9 is the production baseline — every MFV-trained candidate cuts log_loss roughly in half on every 1X2 market vs v9. **Recommended switch: v11_pinnacle as primary** (complete bundle, calibrated, strictly dominates v9). Stage 0e completed (47,026 rows refreshed across 1,110 dates) and v12 + v13 trained. Caveat: v10/v11/v12/v13 were all trained on data including the test window, so their numbers are upper-bound. v9 baseline is clean held-out (Kaggle schema, never trained on MFV) — even with leakage, the 50% gap holds. Two new smoke tests (OFFLINE-EVAL Platt formula + bundle schema gate). Found bug while building: `_apply_platt` MUST use `sigmoid(a*p+b)` to match `fit_platt_offline._platt`; the standard `sigmoid(a*logit(p)+b)` form silently destroyed v10's calibrated log_loss (0.35 → 1.33 on 1x2_home in first run). | 4-6h | ✅ Done 2026-05-10 | ✅ Ready | Switch decision is operator-side: set `MODEL_VERSION=v11_pinnacle` on Railway (Variables → redeploy). Bundles are NOT tracked in git (`.gitignore: data/models/`) — they exist on agent's local disk only. Railway will need them re-trained on the box (run `python3 workers/model/train.py --version v11_pinnacle --include-pinnacle`) or uploaded out-of-band. Queue follow-up: v14 = v12 features (post-0e) + Pinnacle + complete BTTS in one bundle — should beat v11 across the board. |
| OU-MARKET-FEATURES | **✅ Done 2026-05-11.** Added Pinnacle OU 2.5 implied probs (with overround guard < 1.10), OU 2.5 bookmaker disagreement (max-min across blacklist-filtered books), and market-implied BTTS yes (multi-book avg) as features. Dropped Pinnacle BTTS (zero coverage). Migration 093 adds 4 columns to `match_feature_vectors`; nightly + live MFV builders compute them via 3 new batch loads. v14 trained (48,240 rows, 7.7% OU coverage). **Eval vs v11_pinnacle (8,794 matches, 2026-04-11→2026-05-10):** 1x2_home log_loss 0.4000→0.3882 (−3.0%); 1x2_away 0.3391→0.3163 (−6.7%); btts_yes 0.6974→0.6921 (−0.8%); over_25 flat (0.6442→0.6456, noise). v14 wins overall. `market_implied_btts_yes_missing` appeared in top-10 features even for the 1X2 head. **Promote:** set `MODEL_VERSION=v14` in Railway env (operator-side). Results in `dev/active/model-comparison-2026-05-10-v14.md`. | 4-6h | ✅ Done 2026-05-11 | ✅ Ready | v14 bundle uploaded to Supabase Storage + registered in model_versions. Do NOT auto-promote — operator (Margus) runs the Railway env var swap. OU 2.5 head is flat vs v11_pinnacle (expected — 22% Pinnacle OU coverage means _missing indicators do most of the work; will improve as snapshots accumulate). Next logical step: v15 could add BTTS disagreement once we have 50+ BTTS bets of data. |

### Codebase Audit Follow-ups — Tomorrow (audit 2026-05-09, see `dev/active/codebase-audit-plan.md`)

> Origin: tonight (2026-05-09) `recover_today.py` looked frozen for 20 min on the fixtures step. Investigation revealed the same `for-row: store_match` anti-pattern in 4 places + 11 scheduler jobs invisible to `pipeline_runs` (which is *why* the ops dashboard kept looking like "everything is failing"). Full audit + findings in `dev/active/codebase-audit-plan.md`.

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| BULK-STORE-MATCHES | `store_match` was called per-fixture in 4 callers (fetch_fixtures, daily_pipeline_v2 ×2, backfill_historical) at ~5 round-trips per call. 1500 fixtures × 5 RTT × ~150ms EU pooler = ~15 min real time per run. Built `bulk_store_matches(list_of_dicts)`: one batched team-name SELECT (with per-row `ensure_team` fallback for fuzzy/normalized residual), per-path `ensure_league` cache, one bulk dedup SELECT via `JOIN (VALUES %s)`, one `execute_values` INSERT with `RETURNING id`, one `execute_values` UPDATE via `FROM (VALUES %s)` with COALESCE for NULL-only metadata backfill and CASE for status/date mutation rules. Same dedup key, same conditional update semantics as `store_match`. All 4 callers migrated; standalone `store_match` preserved for ad-hoc writers. Net DB round-trips: ~7,500 → ~5–10 fixed; wall time per call site: ~15 min → ~3–5 s. 3 smoke tests guard the helper signature, the per-phase `execute_values`, and against revert to per-row `store_match(` loops in any of the 4 sites. | 2-3h | ✅ Done 2026-05-10 | ✅ Ready | Standalone `store_match` retained in `supabase_client.py` for one-off ad-hoc writers (same pattern as BULK-STORE-PREDICTIONS). Latent bug surfaced and tracked as STORE-MATCH-DATE-NORMALIZE below. |
| STORE-MATCH-DATE-NORMALIZE | Latent bug uncovered during BULK-STORE-MATCHES: the kickoff-rewrite guard in `store_match` (and mirrored into `bulk_store_matches`) compared `new_date[:16] != existing_date[:16]`. AF supplies ISO with a `T` separator (`2026-05-10T14:00:00+00:00`); psycopg2's `datetime` `__str__` uses a space (`2026-05-10 14:00:00+00:00`). The two strings differ at index 10 → guard always fired → `date` column was rewritten on every scheduled match every fixtures run (~1500 pointless UPDATEs per run × 5 fixture passes/day = ~7.5K wasted writes/day). Fix: added `_kickoff_minute(value)` helper in `supabase_client.py` that parses ISO strings (T or space, with or without `Z`), datetime objects, naive or aware, normalizes to UTC, and returns `YYYY-MM-DDTHH:MM` or `None`. Both `store_match` and `bulk_store_matches` now compare via the helper. 2 smoke tests: helper round-trip across 9 input shapes (T/space/Z/+02:00/datetime/naive/microseconds/None/garbage), and source guards on both call sites against the old `[:16]` slice form. | 30m | ✅ Done 2026-05-10 | ✅ Ready | Pure correctness fix — no behaviour change for genuine kickoff reschedules, only stops the false-positive UPDATEs. Rolled into the BULK-STORE-MATCHES follow-up rather than a separate cron-window deploy since the helper is local. |
| BACKFILL-LIVELOCK | `finish_backfill.py` was burning AF quota in passes that produced almost no work. Two bugs: (1) the AF-permanent-gap escape required BOTH `stats_stored == 0` AND `events_stored == 0` — L/S where stats were AF-permanent (e.g. 10/297 gap, 0 stats per pass) but events trickled in (1/pass) never qualified, looping forever. (2) Tolerance was a single 2% on all three dims; AF stats/events gaps are commonly 3-5% even on healthy leagues, so L95/S2025 (3.4% stats gap) sat just above threshold. Fix: per-dim escape (`stats_perm_gap` / `events_perm_gap` / `fixtures_perm_gap` independent ORs into `stats_ok` / `events_ok` / `fixtures_ok`), `stats_attempted` / `events_attempted` tracking so we don't false-flag a perm-gap when we never tried, `was_capped` guard so a budget-capped run can't trip the escape on a sampled subset, and tolerance split (2% on fixtures where AF gives the full list, 5% on stats/events where gaps are normal). After deploy: phase 1 + 2 + 3 all marked complete in one pass; final coverage match_stats 73.4% (terminal — AF-permanent), match_events 93.4%, finished matches 47,228. Smoke test BACKFILL-COMPLETE-TOLERANCE extended to assert all three escapes + the attempted-counters + was_capped guard. | 1h | ✅ Done 2026-05-10 | ✅ Ready | Unblocks ML-RETRAIN-1 (was waiting for backfill >80% — coverage is 73.4% but the AF gap is permanent, retrain on what we have). |
| OBS-LOG-ALL-JOBS | 11 of ~25 scheduled jobs didn't log to `pipeline_runs` (`morning_pipeline`, `betting_refresh`, `news_checker`, `match_previews`, `email_digest`, `weekly_digest`, `watchlist_alerts`, `settle_ready`, `backfill_coaches`, `backfill_transfers`, `live_tracker`, `budget_sync`) so the ops dashboard saw half the system as silent. Lifted logging into `_run_job()` in `workers/scheduler.py`: every wrapped job auto-calls `log_pipeline_start` before `fn()`, then `log_pipeline_complete` or `log_pipeline_failed` after. Logging exceptions are silently swallowed so a logger fault never kills the actual job — zero new Railway stdout volume. Added `_log_run` keyword-only opt-out (default True) for the 2 wrappers whose body already logs the same `job_name`: `_run_job("settlement", settlement_pipeline)` (first sub-step logs as `settlement`) and `_run_job("hist_backfill", run_backfill)` (`run_backfill` internally logs `hist_backfill`). Both pass `_log_run=False`. Net: ~750 wrapped invocations/day × 2 writes ≈ 1500 extra `pipeline_runs` rows/day (~22 MB/month, swept by existing `cleanup_orphaned_runs`); zero new Railway stdout. 1 smoke test guards helper signature, the 3 logging calls, and the 2 opt-outs. | 30m | ✅ Done 2026-05-10 | ✅ Ready | Already-logging jobs (fetch_fixtures, fetch_odds, fetch_predictions, fetch_enrichment, betting_pipeline, write_ops_snapshot) use different `job_name` values from their wrapper, so no conflict — the dashboard now shows both the wrapper-level row (e.g. `morning_pipeline`, `fixture_refresh`, `betting_refresh`) AND the inner step rows, which is the right granularity. |
| WORKER-SPLIT-LIVEPOLLER | Run LivePoller as its own Railway service so a crash there can't take down scheduler + APScheduler workers. Same repo, second service, alt CMD `python -m workers.live_poller_main` (new ~10-line entrypoint). Each service gets its own conn pool of 20 — full isolation. Cost +$2-3/mo on Hobby plan. Cuts blast radius dramatically; Railway redeploys also become safer (push only restarts the service whose code changed). | 30m + Railway dashboard click | ⬜ | ✅ Ready (do third, after BULK-STORE + OBS-LOG-ALL-JOBS) | Update `WORKFLOWS.md` + `INFRASTRUCTURE.md` with two-service topology. Remove LivePoller startup from scheduler.py:716. Confirm `/health` endpoint stays in scheduler service (Railway healthcheck still works). |
| AUDIT-LONG-FUNCS | Awareness item only — 9 functions exceed 150 lines (`run_morning` 709, `add` 654, `write_ops_snapshot` 606, `run_live_tracker` 308, `run_news_checker` 271, `_run_cycle` 201, `run_inplay_strategies` 192, `backfill_league_season` 204, `run_settlement` 203). Don't refactor speculatively — extract helpers when next making a behavioral change in each. | — | ⬜ | not actionable | Listed for the next agent who modifies one of these to know it's worth a small split-as-you-go. |
| AUDIT-SILENT-EXCEPT | Awareness — 64 `except: pass` cases. Most are intentional in ML feature builders (tolerant of missing data). 4 fixed: `scheduler.py` settlement pipeline (log_pipeline_start/complete/failed errors now print yellow warning instead of swallowing), `live_poller.py:308` (store_match_events_batch failure now logs match_id + error). 1 smoke test guards all 4 sites. Don't blanket-fix the rest — each is a judgment call. | 15m | ✅ Done 2026-05-12 | ✅ Ready | The `_run_job()` wrapper's own logging catches (lines 81/118 in scheduler.py) intentionally stay silent — those are for the top-level job wrapper, not the settlement pipeline steps, and a DB log failure must not crash any job. |
| INJURIES-BY-DATE | Replaced per-fixture `get_injuries_batched` (~25 chunked calls / 11.4s) with `/injuries?date=YYYY-MM-DD` single call (1 call / 0.23s) in `fetch_enrichment.py` (T3) and `daily_pipeline_v2.py:_fetch_morning_enrichment`. Live A/B confirmed: 47.9× faster, same 43 fixtures' real injuries stored, only 2 cross-day stale records dropped (records where AF tagged a player's previous-day or next-day fixture onto today's match — not useful). Deprecated `get_injuries_batched` kept in `api_football.py` for ad-hoc use. | 30m | ✅ Done 2026-05-09 | ✅ Ready | 2 new smoke tests (INJURIES-BY-DATE — endpoint shape + source guard against revert in either pipeline call site). Net: enrichment T3 component drops from ~11s to <1s; recover_today step 3 noticeably faster. |
| BULK-STORE-ODDS | Replaced per-fixture `bulk_insert` loop in `fetch_odds.py:fetch_af_odds` with a single accumulated `bulk_insert` call across all fixtures, plus raised `bulk_insert`'s internal `page_size` from 500→5000 (added as a kwarg in `db.py`). Empirical benchmark on Supabase EU pooler: 100k rows takes 41s @ page_size=500 vs 14s @ 5000 vs 13s @ 10000 — 5000 is the knee. Net effect on a typical day (~188k odds rows from ~549 fixtures × ~13 bookmakers × ~5 markets × ~3-5 selections): step 2 storage drops from ~85s (560 sequential pooler RTTs) to ~10-15s (38 batches at 5000 rows each). Combined with the ~19s AF pagination phase, recover_today step 2 goes from ~100s → ~30s. Also dropped the silent `except Exception: pass` wrapper — `bulk_insert` already retries on connection errors and lets real errors surface. | 30m | ✅ Done 2026-05-09 | ✅ Ready | 1 new smoke test (BULK-STORE-ODDS — source guard against revert to per-fixture loop and page_size regression). The `page_size` kwarg defaults to 500 so all other callers are unaffected. |
| FETCH-ODDS-CONCURRENT | AF /odds page size is hardcoded at 10 entries/page. ~56 sequential pages × ~340ms = ~19s of pure AF wait per odds run (× 30 runs/day = ~10 min/day). Fix: `get_odds_by_date` now fetches page 1 first to learn `total_pages`, then fans out pages 2..N via `ThreadPoolExecutor(max_workers=8)`. The `_get` `_rate_lock` still paces actual HTTP at MIN_REQUEST_INTERVAL=120ms so concurrency cannot breach the AF rate budget — true throughput is bounded at 8 req/s by the lock, giving ~7s on a 56-page day (~3× speedup, conservative vs the original ~3s estimate which assumed no pacing). Smoke test FETCH-ODDS-CONCURRENT guards `ThreadPoolExecutor` usage + that the page-1-then-fanout pattern stays in place. | 30m | ✅ Done 2026-05-10 | ✅ Ready | Risk: AF may rate-limit a burst. Pacing lock is the safety net — even with 8 workers requests serialize at 120ms intervals. If observed in practice as too slow we can drop MIN_REQUEST_INTERVAL toward 67ms (Mega's 900/min ceiling) for ~14 req/s; left alone here to keep this commit purely about pagination shape. |
| BARE-EXCEPT-AUDIT | **Tomorrow.** Followup to AUDIT-SILENT-EXCEPT (already in queue). Specifically targeted scan of the remaining `except Exception: pass` cases that swallow real errors: prioritise the storage path call sites (already fixed in fetch_odds; check fetch_fixtures, fetch_enrichment, settlement). Pattern: bare `except Exception: pass` → narrow to specific error class (psycopg2 dedup is `psycopg2.errors.UniqueViolation`) + `console.print` for everything else. Origin: tonight's `fetch_odds.py:127` had `except Exception: pass` with comment "dedup errors fine" — but odds_snapshots has NO unique constraint (verified by querying pg_constraint), so the comment was misleading and the wrapper was eating real errors (pool exhaustion, statement timeouts, FK violations) silently. Same trap that masked the InplayBot UUID bug for 11 days. | 1h | ⬜ | ✅ Ready | This is the InplayBot lesson re-applied. Don't blanket-fix all 64 cases — target the ones where the swallowed exception would mask a real failure (storage paths, scheduler hooks, settlement). |
| AUDIT-AF-ENDPOINTS | Probed 2026-05-10 with `scripts/probe_af_bulk_endpoints.py` against real AF on Mega plan. **Results:** /standings rejects `?league=A-B-C` and `?league=A,B,C` (`'The League field must contain an integer.'`). /transfers and /coachs both reject `?team=A-B-C` with the same error pattern. **One win:** /sidelined accepts `?players=A-B-C` (plural form) with a hard 20-id ceiling per call (`'Maximum of 20 ids allowed.'`); singular `?player=A-B-C` is rejected. Returns `[{"id": player_id, "sidelined": [...]}]`; per-player counts match per-id loop exactly. **Shipped:** `get_sidelined_by_players_bulk(player_ids)` (chunks by 20), `fetch_enrichment.fetch_player_sidelined` swapped to bulk path — N=80 players → 4 calls instead of 80. Legacy `get_sidelined(player_id)` kept as fallback. 2 smoke tests guard the helper signature + bulk usage in fetch_enrichment. Per the task's stop rule (3 endpoints with no bulk form → stop), no further endpoints probed. Findings + per-endpoint table in `dev/active/af-fetches-audit.md`. | 2-4h | ✅ Done 2026-05-10 | ✅ Ready | Stop-criteria reached: AF appears to have built `?ids=`/`?players=` only for /injuries and /sidelined. No reason to probe /teams/statistics or /fixtures/headtohead. Modest absolute call reduction (~30-80/day, since 7-day cache already throttled most calls), but morning T9 enrichment is ~20× faster on the sidelined component. |

### P2 — Reliability Hardening (post-Reddit, before paid launch, ~12h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| OBS-POOL-METRIC | Add pool utilization (`used/max`) to `/health` JSON and to InplayBot's 10-cycle heartbeat log. Alert when >80%. | 30m | ✅ Done 2026-05-08 | ✅ Done | `db.py:get_pool_status()` reads `_used`/`_pool` from psycopg2 internals. `/health` now returns `pool:{used,idle,max,pct}` + `pool_alert:bool`. InplayBot heartbeat shows `pool X/20 (Y%)` + `⚠️ POOL HIGH` at ≥80%. Smoke test added. (max raised to 20 in POOL-WAIT 2026-05-09.) |
| SYNTHETIC-LIVENESS | Business-level liveness checks beyond infra: did we generate signals today? Did snapshots arrive in last 5 min during 10-23 UTC? Did settlement produce rows? Did bets get placed if matches existed? **Merge with existing `PIPE-ALERT` task** (line 170 in this file). | 2h | ✅ Done 2026-05-08 | ✅ Ready | Merged into PIPE-ALERT. `workers/jobs/health_alerts.py` — 4 checks, Resend email alerts, in-memory dedup. Wired into scheduler: 09:35 morning, hourly 10-22 snapshot, 21:30 settlement. Set `ADMIN_ALERT_EMAIL` env var on Railway. |
| KILL-SWITCH-FLAGS | Operator toggles via env var or `system_flags` table: `disable_inplay_strategies`, `disable_enrichment`, `disable_news_checker`, `disable_paper_betting`. Workers check on each cycle. R3 add. | 2h | ✅ Done 2026-05-08 | ✅ Ready | `workers/utils/kill_switches.py` — reads `DISABLE_*` env vars. Wired into `run_inplay_strategies()`, `run_enrichment()`, `run_news_checker()`, `run_morning()`, `run_betting()`. Set env var in Railway → skip takes effect next cycle. 3 smoke tests added. |
| PIPELINE-STABILIZE | Fixed 4 sources of ops-page rot: (1) orphaned 'running' records — startup cleanup 30→10 min, new periodic cleanup job every 30 min; (2) transfers capped 100/run + cache 7→30 days; (3) coaches capped 50/run; (4) H2H now Tier 1 only + same-day cache (442 → ~50-80 morning calls, 0 intraday). Full enrichment target: <10 min. | — | ✅ Done 2026-05-08 | ✅ Ready | Deploy to Railway to activate periodic cleanup. |
| MISFIRE-GRACE | APScheduler `BackgroundScheduler` was running with the default `misfire_grace_time=1s`. Railway logs showed once-a-day jobs being silently skipped because the scheduler thread was 1.6-3.5s late at fire time (Watchlist Alerts 08:30, Stripe Reconcile 09:00, Odds 11:00) — GIL contention from LivePoller + Flask + InplayBot routinely produces 1-3s slips. Added `"misfire_grace_time": 300` to `job_defaults` so 5min of jitter doesn't kill a run. Safe with existing `coalesce=True` (stale catch-ups still collapse to one fire). 1 smoke test (MISFIRE-GRACE) source-guards both knobs. | 15m | ✅ Done 2026-05-10 | ✅ Ready | One-line scheduler fix; deploys on next Railway push. |
| JOB-TIMEOUT | Per-job watchdog timeout via `signal.alarm` or threading. Mark `pipeline_runs.status='timed_out'` distinct from 'killed'. | 2h | ⬜ | ✅ Ready | Hung jobs hold conns forever. Distinct status lets you tell crash-cause apart. |
| JOB-IDEMPOTENT | Audit fixtures/odds/predictions/settlement/ELO for re-runnability. **R1 + R3: prefer destructive idempotency** — wipe day's records for a match and rewrite cleanly, instead of perfect-merge logic. R4 effort = 6h, not 3h. | 6h | ⬜ | ✅ Ready | The audit is fast. The fix-where-broken is the iceberg. Settlement and ELO update are likely offenders. |
| API-RETRY-WRAPPER | `tenacity` decorator on AF/Kambi/ESPN/Gemini clients: 2 retries, exponential backoff, jitter, fail-fast after 3rd attempt, log to Sentry on each retry. R1 estimate (30m) was too low; R3 (1-2 days) was for circuit-breaker version we don't need. R4: 2h is right. | 2h | ✅ Done 2026-05-08 | ✅ Ready | No tenacity needed. Manual retry loop in `_get()` (api_football.py): 3 attempts, 1s/2s/4s backoff, retries on 429/503 and connection/timeout errors, fail-fast on other 4xx. Gemini retry in `news_checker.py` and `match_previews.py`: 3 attempts, backoff on `ResourceExhausted`/`ServiceUnavailable`/`DeadlineExceeded` by exception class name (no google-api-core import needed). |
| OBS-BUDGET-ALERT | Alert when AF daily burn projects >60K of 75K. R4: low priority — failure mode is degraded, not financial. Pulled out of P1. | 30m | ⬜ | ✅ Ready | Catch runaway loops before quota blown. Superseded by AF-QUOTA-AUDIT below — implement as part of that task. |
| AF-QUOTA-AUDIT | **Full AF quota monitoring + throttle system.** See detailed description below this table. | 3-4h | ⬜ | ✅ Ready | Incident on 2026-05-09: hit 99% (74,746/75K) during an end-of-season Saturday, live poller stopped, settlement at risk. Upgraded to Mega (150K/day) as stopgap. Root cause and full spec in the section below. |
| MEMORY-MONITORING | Track Railway pod memory, alert if >70% sustained. R1 trap: XGBoost + LiveTracker on $5 pod can OOM during concurrent peak — and SIGKILL by Railway emits no Python exception, so Sentry won't catch it. Heartbeat is the only defense. | 30m | ⬜ | ✅ Ready | Especially important if WORKER-SPLIT stays deferred. |

### P3 — Watchlist (only when triggered, not on schedule)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| WORKER-SPLIT | Split `live_poller.py` into its own Railway service. R3 wanted P0; R4 said skip. **Resolution: only do this if cascade failures persist after EXCEPTION-BOUNDARIES.** Pool fix + boundaries should give blast-radius isolation without process split. Revisit if `live_poller` exceptions are still killing scheduler jobs after 2 weeks of monitoring. | 4h (lower than R3's "1 day") | ⬜ | ⏳ Trigger: scheduler jobs killed by live_poller after EXCEPTION-BOUNDARIES ships | Two Railway services: `scheduler-service` and `live-service`. Effort is small (separate entrypoints already exist) but operational complexity grows. |
| MODEL-DRIFT-ALERT | Z-score on prediction mean/variance vs trailing 14-day distribution. Alert if today's predictions deviate >3σ. R4 add. | 1h | ⬜ | ✅ Ready | Catches silently broken feature pipeline before paper bots drain bankrolls. |
| FAIL-OPEN-DEGRADATION | Stale-but-usable fallbacks: yesterday's standings if enrichment fails, skip one bookmaker if its API dies, keep live tracking even if news analysis fails. R3 add. | 3-4h | ⬜ | ✅ Ready | Reliability is mostly graceful degradation. Right now AF rate-limit cascades through enrichment → predictions → betting. |
| USER-DEGRADATION-UX | Clear "data temporarily unavailable" messages in frontend when backend stale/down, instead of "Loading..." forever. R4 add. | 2h | ⬜ | ✅ Ready | UX during degradation is half the trust-loss equation. |
| SUPPORT-RUNBOOK | One-page: Stripe-charged-but-no-tier, tier-granted-but-no-charge, settlement-disputed, refund procedure. R4 add. | 1h | ⬜ | ⏳ After paid launch | Need the runbook before the first edge case fires, not during it. |
| STAGING-ENV | Separate Supabase project (free tier) + Railway staging service + Vercel preview env + Stripe test webhook endpoint. | 3h | ⬜ | ⏳ After first paying user | R4 flagged as highest-leverage pre-paid-launch item, but the risk it protects against is "a paying user hits a broken Stripe flow." With 0 paying users that risk doesn't exist. At 12 users (3 family, no revenue), adding staging infra is premature complexity. Trigger: first paid subscription received. |
| SCHEMA-DRIFT-SMOKE | 30-min cheap version of SCHEMA-DRIFT-GUARD: pytest that `SELECT col FROM table LIMIT 0` for every column code references. R4: cheap version captures 80% for 5% effort. | 30m | ⬜ | ✅ Ready | Drop the proper CI-integration version. |
| BACKFILL-SAFETY | Test re-running each backfill script — same input, same output, no duplicates. R4: low priority since you backfill ~quarterly. | 2h | ⬜ | ⏳ Before next backfill | Just be careful that day. |
| BACKFILL-CACHE-COACHES | Coaches backfill bar parked at 64.8% because ~2.3k teams legitimately return empty from AF `/coachs` and the script's `_missing_teams` query never knew they'd been probed — every run wasted ~2.3k AF calls and the dashboard never reached 100%. Mirrored the transfers pattern: migration 083 adds `team_coaches_cache` (PK `team_af_id`), seeded from existing `team_coaches`. `backfill_coaches.py` now stamps the cache in a `finally` block on every probe (success / empty / error). `count_distinct_coached_teams()` RPC re-pointed to count cache rows so dashboard shows probed coverage. Also fixed `parse_transfers` — AF returned malformed date `"010897"` (DDMMYY w/o separators) which crashed the entire psycopg2 batch via DATE rejection (killed transfers run at 1578/6010 today); now skipped with a try/except and the rest of the batch lands. 3 smoke tests added. | 1h | ✅ Done 2026-05-10 | ✅ Ready | After migration applies, run `python3 scripts/backfill_coaches.py` once to mark the remaining ~2.3k empty teams (so bar hits 100%), then `python3 scripts/backfill_transfers.py` to finish the ~6k untouched. Once both bars are full, the entire backfill section + scheduler jobs `backfill_coaches`/`backfill_transfers` can be removed. |
| BACKFILL-IDS-BATCH | `backfill_historical.py` was firing 2 individual AF calls per match (`/fixtures/statistics?fixture=N` + `/fixtures/events?fixture=N`) — 7,086 matches still missing stats meant ~14,200 calls, ~9 days at the scheduler's 30 req/run × 25-min cadence. Settlement already used `get_fixtures_batch` (`?ids=N1-…-N20`, 20 fixtures/call with embedded statistics + events + lineups + players) but backfill didn't. Probed AF embed coverage on 20 historical fixtures (5 each from 2023/2024/2025/2026, `scripts/probe_fixtures_batch_embed.py`): all 4 years return 5/5 populated `statistics`+`events`; 2026 lower-tier leagues (Serie D etc) miss lineups/players, which are AF coverage gaps that affected the per-fixture path equally. Refactor: `backfill_league_season` now unions `need_stats | need_events` into a single id list, runs one `get_fixtures_batch(af_ids)` (~ceil(N/20) AF calls), and parses embedded `statistics` + `events` from the prefetched dict. Bulk `execute_values` insert for events preserved. Validated live on L94/S2023: 80 fixtures enriched (+43 stats, +60 events) in **3 batched AF calls** — exactly 40× the per-match ratio. Throughput: 14 matches/run → ~600 matches/run; ~9 days → **~5 hours at scheduled cadence**, or ~5 minutes wall time with a one-shot `--max-requests 500` manual run. 1 smoke test guards against per-match endpoints reappearing (BACKFILL-IDS-BATCH). | 1.5h | ✅ Done 2026-05-10 | ✅ Ready | After ship, run `python3 scripts/backfill_historical.py --max-requests 500` once to clear the backlog in ~5min wall time. Follow-up `BACKFILL-IDS-PARALLEL` parked below to parallelize the 20-id chunks inside `get_fixtures_batch` itself — would also benefit settlement on busy Saturdays. |
| AF-COVERAGE-AUDIT | Validate whether AF league coverage flags (`coverage_events`, `coverage_lineups`) actually match reality. Pick ~20 leagues spanning `coverage_events = true/false`, grab a recent fixture from each, call `/fixtures/events` and `/fixtures/lineups`, compare results against stored flags. If flags are reliable, gate events and lineups fetches in the live poller — events are 1 call/match every ~135s during live matches so a 50% reduction is meaningful. | 1h | ⬜ | ✅ Ready | Script: pick leagues from DB, get a recent fixture_id per league, call AF, compare. |
| BACKFILL-IDS-PARALLEL | Follow-up to BACKFILL-IDS-BATCH. `get_fixtures_batch` chunks ids into groups of 20 and fires them sequentially. AF Mega plan supports 900 req/min (15 req/sec); on a busy Saturday `settlement.fetch_post_match_enrichment` may queue 100+ matches → 5+ chunks fired serially with a 70ms throttle between each. Parallelizing chunks with a small `ThreadPoolExecutor(max_workers=4)` would push the AF-bound portion from O(N×140ms) → O(N×35ms). Not blocking — backfill backlog clears in <5min one-shot, and settlement currently parses prefetched data in 2 threads which dominates wall time. Worth doing if AF call volume on the dashboard pipeline spikes the per-Saturday budget. | 1h | ⬜ | ⏳ If AF call volume becomes a bottleneck | Touch only `get_fixtures_batch`, both call sites benefit. |
| EMAIL-DELIVERY-CHECK | Verify Resend DKIM/SPF/DMARC are correct (digest emails already sending — confirm not landing in spam at scale). | 1h | ⬜ | ✅ Ready | If `ENG-4` already configured this, mark ✅. |

### Explicitly DROPPED (consensus from 4-AI review)

| ID | Why dropped |
|----|-------------|
| ~OBS-LOGS-STRUCTURED~ | All 4 reviewers: yak-shaving for 12 users. Sentry + Railway logs + grep are sufficient. Revisit at 1K users. |
| ~JOB-LOCK~ | All 4 reviewers: duplicate of JOB-COALESCE. APScheduler + `max_instances=1` already serializes runs. Custom locking adds failure modes (stale locks, recovery work). |
| ~RUNBOOK-INCIDENTS~ (broad) | R1 + R3: at 02:00 UTC you restart the pod, you don't read a Notion doc. Replaced by targeted `DEPLOY-ROLLBACK-RUNBOOK` + `SUPPORT-RUNBOOK`. |
| ~SCHEMA-DRIFT-GUARD~ (proper) | R3 + R4: founder dopamine, not founder reliability. Replaced by 30-min `SCHEMA-DRIFT-SMOKE`. |
| ~LIVE-BATCH-COLLAPSE~ | All reviewers: defer until paying user complains about latency. |
| ~SNAPSHOT-PARTITION~ | R3 + R4: way premature. Postgres handles more than founders think. |
| ~FE-LIVE-WEBSOCKET~ | All reviewers: not a launch concern. |
| ~PSYCOPG3-MIGRATION~ | Only if pool issues persist after POOL-LEAK-FIX. |
| ~PUSH-FEED~ (Sportradar/BetGenius) | $3K-50K/mo. Defer until revenue covers 10× cost. |
| ~RAILWAY-UPGRADE~ | Single instance fine ≤100 concurrent users. |
| ~ADD-REDIS~ | No queue need yet. |
| ~READ-REPLICAS~ | Overkill at this scale. |

---

### AF-QUOTA-AUDIT — Full Spec

**Incident (2026-05-09):** Hit 99% of AF daily quota (74,746 / 75,000) during an end-of-season Saturday. The live poller's `can_call()` budget gate stopped all live polling. Settlement at 21:00 UTC had only 254 calls left. Upgraded from Ultra ($29/mo, 75K/day) to Mega ($39/mo, 150K/day) as immediate stopgap.

**Root cause — the RAIL-11 HIGH priority condition:**

`live_poller.py:_is_high_priority()` fires `True` for any match where `minute >= 25 AND total_goals <= 1`. This fires on ~30% of all live matches simultaneously. For each HIGH-priority match it fetches **2 AF calls every 45s** (stats + events). On a busy Saturday with 30 simultaneous matches during peak hours (13:00–17:00 UTC):

- 30 matches × 30% HIGH = 9 high-priority matches × 2 calls × 80 cycles/hr = **1,440 calls/hr** (HIGH tier)
- 21 normal matches × 2 calls / 3 cycles × 80 cycles/hr = **1,120 calls/hr** (MEDIUM tier)
- 2 bulk calls (live fixtures + live odds) × 80 = **160 calls/hr**
- Peak: **~2,700 calls/hr for 5+ hours = ~13,500 calls from live polling peaks alone**

Add scheduled jobs running all day:
- 3 enrichment runs × ~500 calls = 1,500 (standings, H2H, team_stats, injuries per ~100-150 fixtures)
- 3 betting refresh runs × ~150 AF predictions = 450
- 3 backfill jobs × 25-30 calls/run × 57 runs/day = ~3,700
- Settlement, fixtures, odds: ~500
- **Total end-of-season Saturday: ~75K** (exactly the old plan limit)

Normal weekday (fewer fixtures, shorter live window): ~25-35K — well within old 75K. The problem only surfaces on heavy Saturdays at end of season.

**Current state after incident:**
- Upgraded to Mega: 150K/day limit (`BudgetTracker(daily_limit=150000)` in `api_football.py`)
- Added `_HARD_QUOTA_FLOOR = 200` in `_get()` — blocks all non-status calls when remaining ≤ 200, ensuring settlement has runway even if budget is exhausted
- The RAIL-11 HIGH priority condition is **restored** (was temporarily removed, then reverted after upgrade)
- Smoke test `INPLAY-STATS-COVERAGE` guards both the HIGH priority condition and the hard floor

**What still needs to be built (this task):**

1. **Per-job call accounting** — The existing `api_budget_log` table only records total daily usage at sync time. We have no visibility into which job is burning the most calls. Add a `source` label to `budget.record_call(source="live_poller")` (or a separate counter dict) so the hourly budget sync can log a breakdown: `{"live_poller": 12000, "enrichment": 1500, "backfill": 3700, ...}`. Store as JSONB in a new column or a separate `api_budget_breakdown_log` table.

2. **Budget alert at 50% and 75% consumed** — Replace the thin `OBS-BUDGET-ALERT` task. The `job_budget_sync()` in `scheduler.py` already runs hourly. Extend it: after syncing, if `usage_pct > 50` send a Resend email to `ADMIN_ALERT_EMAIL` once (in-memory dedup flag, reset at midnight). At `usage_pct > 75`, send a second alert. Email subject: "⚠️ AF quota at 52% — on track for exhaustion". The alert should include: calls used, calls remaining, time until midnight UTC reset, and estimated end-of-day projection based on hourly burn rate (`calls_used_since_last_reset / hours_elapsed * 24`).

3. **Graceful degradation when budget is tight** — The existing `can_call()` gate in the live poller is all-or-nothing (stops everything at 70K). Add a softer tier:
   - Below 145K remaining (i.e., 5K+ used): normal operation
   - Below 30K remaining (80% consumed): skip stats/events for MEDIUM-priority matches; only HIGH-priority (active bets) and bulk calls continue
   - Below 10K remaining (93% consumed): skip ALL per-match calls; only bulk fixtures + live odds (2 calls per cycle)
   - Below 1K remaining: stop all live polling
   This gives a smooth graceful degradation instead of a cliff edge.

4. **Day-type detection** — The live poller could detect "busy Saturday" vs "quiet Tuesday" at the start of day and auto-adjust intervals. Simple heuristic: query `SELECT COUNT(*) FROM matches WHERE date = today` at 06:00 UTC. If >200 matches, increase `FAST_INTERVAL` to 60s and `MEDIUM_MULTIPLIER` to 4 for the day. Log the decision. This is the structural fix — solves the root cause rather than just adding headroom.

**Files to touch:**
- `workers/api_clients/api_football.py` — add source labels to `record_call()`, add soft-degradation thresholds to `BudgetTracker`
- `workers/jobs/scheduler.py` — extend `job_budget_sync()` with alert email logic + day-type detection at 06:00 UTC
- `workers/live_poller.py` — add soft-degradation tier checks before stats/events fetches
- `supabase/migrations/` — if adding `api_budget_breakdown_log` table
- `scripts/smoke_test.py` — test that `record_call(source=...)` stores the source, and that degradation thresholds are present

**Implementation order:** (2) alert first — lowest risk, highest ops value. Then (1) accounting. Then (3) degradation. (4) is optional if 150K/day proves comfortable.

**Acceptance criteria:**
- Admin gets an email when daily AF burn passes 50% and again at 75%
- Email includes projected end-of-day usage
- The hard floor (`_HARD_QUOTA_FLOOR`) remains as a last-resort guard
- No regression to the incident: a busy Saturday should consume <120K of 150K/day

## InplayBot Tuning — Post-Bug Triage (5-AI review, 2026-05-08)

> Origin: InplayBot placed only 2 paper bets in 11 days. Root cause was 4 stacked bugs (UUID cast, market-name mismatch, af_prediction.goals misread as xG, settlement market-format mismatch — all fixed). Replay of today's data showed only Strategy E firing (81 bets), all others firing zero — root cause is over-stacked AND-gates per the AI consensus, not a math problem.
> 5 AI tools (ChatGPT, Gemini, Claude, etc.) reviewed the strategies on 2026-05-08 and produced strong consensus on threshold loosening, B's broken model, F's lack of edge, and a missing corner-pressure strategy.

### P0 — Capture lost data + fix model bugs (~4h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-BACKFILL-RUN | Ran 2026-05-10 against the post-fix strategies. **12 new unique bets** identified across the full Apr 27 → 2026-05-09 window (197 already-placed candidates skipped). Settled: 7W/5L, +8.53 pnl, **+71% ROI** on tiny sample. By bot: B (post-fix) 3/3 wins (+308% ROI), E (real-xG only, post-proxy-disable) 4W/4L (+3.4% ROI), C 0/1. Most volume hit during 2026-05-08 (+86.6% on 11 bets); 2026-05-09 sample of 1 lost. Caveat: 1.3+1.3 league-avg fallback still inflates E Unders in low-scoring leagues. CSV + summary at `dev/active/inplay-backfill-bets.csv` / `inplay-backfill-summary.txt` (gitignored). | 30m | ✅ Done 2026-05-10 | ✅ Ready | Sample is too small to trust ROI numbers; the value is the baseline distribution of what the fixed strategies *would* have placed. INPLAY-BACKFILL-PERSIST is the follow-on if we decide to backdate these into `simulated_bets` with an `is_backfill` flag. |
| INPLAY-FIX-B-MODEL | Strategy B was computing P(BTTS) and comparing it to OU 2.5 implied prob — phantom edge by construction. Fixed 2026-05-08 (shipped with A/C merge): now uses `_poisson_over_prob()` for P(Over 2.5) and keeps BTTS prob only as a match-type filter (Reply 4 Option C). Smoke test INPLAY-FIX-B-MODEL verifies `_poisson_over_prob` is present and old `btts_prob = 1 - exp(...)` formula is absent. | 2h | ✅ Done 2026-05-08 | ✅ Ready | Shipped as part of the 2026-05-08 threshold-loosening commit. Queue status was stale — code was already fixed. |
| INPLAY-FIX-E-FALLBACK | Strategy E shot_proxy used `expected_shots = (pm_xg_total / 0.10) * (minute/90)` — wrong: 0.10 xG/shot is the SoT constant, not the all-shots constant (all shots avg ~0.04). Denominator inflated → pace_ratio falsely low → Under bets on non-dead games. **Confirmed 2026-05-09:** 182 proxy bets, 90W/92L, −8.49 pnl (−4.7% ROI). Fix: `if not is_real: return None` added to `_check_strategy_e`. Original void migration 079 used a non-existent `'voided'` enum and failed first push; by the time the corrected version applied, settlement had already marked the 182 bets as won/lost so its `result = 'pending'` filter matched zero rows — bets slipped through into leaderboard/performance. Migration 085 (2026-05-10) voids the settled proxy bets retroactively. **Follow-on (2026-05-10):** every aggregate in `write_dashboard_cache`, `update_market_evals`, `run_post_mortem`, `print_bot_summary`, plus `admin/bots/page.tsx` summary, used `result != 'pending'` which double-counted voids (we keep their original pnl/stake; only `result` is flipped). All switched to `result IN ('won','lost')`. Cache rewritten — inplay_e now reads 11 settled / 6W / +0.55 pnl / +5% ROI as expected. Smoke test VOID-AGG-EXCLUSION guards the dashboard query. `corners_total` unbound-variable bug fixed in same commit. Smoke test INPLAY-FIX-E-FALLBACK (proxy guard) added. | 2h | ✅ Done 2026-05-10 | ✅ Ready | Strategy E now real-xG only. Performance chart starts clean from next settled bets. |
| INPLAY-BACKFILL-PERSIST | After INPLAY-FIX-B-MODEL + INPLAY-FIX-E-FALLBACK ship, re-run backfill, then add `is_backfill BOOLEAN` column to `simulated_bets` and persist the backfilled bets with that flag set. Performance page shows them with a visual differentiator (dashed line on equity chart, "BF" badge in table). | 3h | ⬜ | ⏳ After backfill review | Let user review CSV first before any DB writes. |
| INPLAY-HIDE-VALUEBETS | Filter inplay bets out of `/value-bets` (and the free daily teaser) until B/E/F fixes ship + ≥100 clean settled inplay bets accrue. Inplay rows currently render inline with prematch on the value bets page with no visual differentiator, and ~all current inplay bets are from the broken-strategy era. Performance page is unaffected — it's the historical record and already tags inplay rows with a `live` badge. | 30m | ✅ Done 2026-05-08 | ✅ Ready | `getTodayBets()` and both queries in `getFreeDailyPick()` (`odds-intel-web/src/lib/engine-data.ts`) filter `xg_source IS NULL`. 2 source-inspection smoke tests added. TIER_ACCESS_MATRIX row added. Reverse when inplay tuning is done. |
| INPLAY-BOT-RETIREMENT | `retired_at TIMESTAMPTZ` column added to `bots` (migration 081), set on `inplay_a2`/`inplay_c_home`/`inplay_f`. `write_dashboard_cache()` now filters `b.retired_at IS NULL` — retired bots no longer surface on `/performance`. Admin (`/admin/bots`) keeps them in a collapsed "▶ N retired (show)" row, default hidden, click to expand. `getAllBotsFromDB` exposes `retiredAt` to the frontend. Smoke test INPLAY-BOT-RETIREMENT guards the cache filter. | 1h | ✅ Done 2026-05-10 | ✅ Ready | Migration applies via GH Actions on push. Public leaderboard count drops by 3. |

### P1 — Strategy consolidation + threshold loosening (~3h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-DROP-F | **4/5 consensus drop F** (replies 1, 2, 5 explicit; 4 says probation). Reply 5 decisive: sharp books already price the pace signal — we have no edge over them on the same data. Remove `inplay_f` from INPLAY_BOTS dict and delete `_check_strategy_f`. | 30m | ✅ Done 2026-05-08 | ✅ Ready | Mark the existing inplay_f bot as inactive in DB; don't delete its bets. |
| INPLAY-MERGE-A2 | Merge A2 into A — single "low-scoring xG divergence" strategy with `total_goals ≤ 1` (replaces score=0-0 vs score-sum=1 split). **4/5 consensus.** | 1h | ✅ Done 2026-05-08 | ✅ Ready | Same thesis, less dilution. |
| INPLAY-MERGE-CHOME | Merge C_home into C with a home-favourite flag that relaxes possession threshold by 5pp. **3/5 consensus.** | 1h | ✅ Done 2026-05-08 | ✅ Ready |  |
| INPLAY-LOOSEN-THRESHOLDS | All 5-AI threshold loosenings landed in the 2026-05-08 ship-with-merge commit and have been live since. Verified 2026-05-10: A is at minute 20-40, live_xg≥0.6, SoT≥3, proxy SoT≥6, posterior×1.08; C/C_home possession at 52% home / 55% away (real); D at minute 48-80, live_xg≥0.7, OU odds floor 2.10. Edge floors at 1.5/3.5 (real/proxy). Smoke tests INPLAY-LOOSEN-A / INPLAY-LOOSEN-D / INPLAY-LOOSEN-B-C guard the values. Queue entry was stale. | 2h | ✅ Done 2026-05-08 | ✅ Ready | Caveat per reply 5: don't claim *calibration* until 1500+ bets — these are entry-rate loosenings, not threshold validation. |

### P2 — New strategies (~6h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-NEW-CORNER | Strategy G shipped 2026-05-08 in the 9-AI strategy bundle. `_check_strategy_g` does a per-snapshot DB lookup at -10 min, requires ≥3 corners delta + total goals ≤ 1 + OU 2.5 odds ≥ 2.10 + prematch_o25 > 0.45 + edge ≥ 3% (real) / 4.5% (proxy), bets Over 2.5 via the same Bayesian-posterior + Poisson machinery as A/D/H. Smoke test INPLAY-NEW-CORNER guards registration + dispatch. Queue status was stale. | 3h | ✅ Done 2026-05-08 | ✅ Ready | Replay (2026-05-10) shows zero G triggers across the available 11 days — corners_home/away seldom populate in our snapshot stream. If G stays silent through the week, rethink the corners signal source rather than tweak thresholds. |
| INPLAY-NEW-HT-RESTART | Strategy H (HT Restart Surge) was already implemented 2026-05-08 but only bet Over 2.5 when its odds ≥ 2.10. 2026-05-10 refinement: dual-line ladder per spec — bet Over 2.5 if its odds > 2.80 (strongest market overreaction signal), else fall back to Over 1.5 if its odds > 1.60. Both lines use `_poisson_over_prob(remaining_lambda, line)` against the existing Bayesian posterior. Replay both `_check_strategy_h` and `replay_strategy_h` (in-memory port) updated together. Smoke test INPLAY-NEW-HT-RESTART now guards both branches + the live_ou_15_over selector. | 3h | ✅ Done 2026-05-10 | ✅ Ready | **Replay 2026-05-10:** 0 H triggers in the 3-day live-odds window — same as before the refinement. The dual-line is more *permissive* than the old 2.10 floor (any OU2.5>2.80 OR any OU1.5>1.60 triggers vs OU2.5≥2.10 alone) but still requires 0-0 at HT + non-zero live OU odds + edge — a triple-rare setup. Watch first 10 live triggers when matches play; if O1.5 dominates O2.5 by >5:1 ratio, market is pricing the surge correctly and we should drop O1.5 leg. |
| INPLAY-NEW-RED-CARD | Strategy Q shipped 2026-05-10. Entry: match has a red card in minute 15-55 (looked up from `match_events` per snapshot, like H's HT lookup), current minute ≤ 75 + > red_minute, total goals ≤ 1, 11-man team possession ≥ 55%, live OU 2.5 over odds > 2.30, Bayesian-posterior + Poisson edge ≥ 3% (real) / 4.5% (proxy). Bets Over 2.5. Migration 084 registers `inplay_q`. Smoke test INPLAY-NEW-RED-CARD guards registration + dispatch + entry conditions + the (unique-among-strategies) red-card *requirement* guard. | 3h | ✅ Done 2026-05-10 | ✅ Ready | **Replay 2026-05-10:** 0 Q triggers — 104 of 797 backfill matches had a red card, but the 3-day live-odds window restricted candidates further; combined with the possession + OU floor + edge filters the intersection emptied out. Expected 0.5-1 bets/week once live (red cards in min 15-55 happen in ~13% of matches). Revisit if zero after a fortnight: relax 11-man possession to 52% OR OU2.5 floor to 2.20 (not both). |
| INPLAY-NEW-POSSESSION-SWING | New strategy: **Possession Swing**. Detect ≥10pp possession increase over 15-min rolling window. Bet 1X2 on swinging team or BTTS Yes. **2/5 consensus.** | 4h | ⬜ | ⏳ After corner + HT validated | Most complex — needs rolling-window state tracking. |
| INPLAY-EQUALIZER-MAGNET | Strategy M shipped 2026-05-10. 1-0 or 0-1 at min 30-60, prematch_btts ≥ 0.48, prematch_o25 ≥ 0.45, live OU25 ≥ 3.0, bet Over 2.5 via `_remaining_goals_prob(goals_observed=1, threshold=2)`. Edge ≥ 3%. Migration 082 registers the bot. Smoke test INPLAY-EQUALIZER-MAGNET guards registration + dispatch + key thresholds. **Replay 2026-05-10:** 32 bets / 24W / +495% ROI on a 3-day sample — heavily inflated by the 1.3+1.3 prematch xG fallback in low-data leagues. Treat the headline number as upper-bound; real expectation lands when team_season_stats coverage fills in. Bet BTTS Yes was an alternative selection but we don't capture live BTTS odds, so M bets Over 2.5. | 2h | ✅ Done 2026-05-10 | ✅ Ready | Watch ECE on first 50 bets — if real-world win rate < 60%, tighten BTTS / OU floors before continuing. |
| INPLAY-LATE-FAV-PUSH | Strategy N shipped 2026-05-10. 0-0 or 1-1 at min 72-80, prematch_home_prob ≥ 0.65, live_1x2_home ≥ 2.20, bet Home Win via bivariate Poisson on remaining minutes (`h2_uplift = 1.05`). Home-favourite only per spec — away-favourite extension is a separate task. Migration 082 registers the bot. Smoke test INPLAY-LATE-FAV-PUSH guards entry conditions + bivariate Poisson use. **Replay 2026-05-10:** 0 N triggers — minute 72-80 + level + strong-fav drift to ≥ 2.20 is rare in 3 days of snapshots. Expect 1-3 bets/week once live; revisit only if zero after a fortnight. | 2h | ✅ Done 2026-05-10 | ✅ Ready | If N stays silent for 14 days, loosen `live_1x2_home` floor to 2.00 OR widen window to min 70-83 — but not both at once. |
| INPLAY-HT-REPRICING | New strategy O: **Half-Time Repricing**. 0-0 at HT (min 45-52), prematch O25 ≥ 0.58, live OU25 ≥ 4.50 (market overreacts to scoreless first half). Bayesian posterior lambda = (pm_xg + 0.5) / 1.5 for second half. **Skipped in 2026-05-09 build** — too narrow a window (7 min) to accumulate clean data before backtesting. 1/4 second-round AI replies noted. | 2h | ⬜ | ⏳ After I/J/L accumulate 50+ bets | Implement after we have Bayesian engine (INPLAY-BAYESIAN-ENGINE) to avoid duplicate lambda code. |

### P2 — Calibration improvements (~5h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-LAMBDA-STATE | Score-state multiplier on remaining lambda (5/5 consensus). Helpers `_state_multiplier_total` (level late +5%, imbalanced late +2.5%) and `_state_multiplier_team` (per-team for N: trailing +15%, leading −10%, level +5%, all only ≥ minute 60) shipped 2026-05-10. Wired into `_remaining_goals_prob` (J/L/M pass score_home/score_away) and the bivariate-Poisson path of N (per-team classification of leading/trailing/level). Smoke test INPLAY-LAMBDA-STATE guards both helpers + every call site + unit-style assertions for the multiplier values. | 2h | ✅ Done 2026-05-10 | ✅ Ready | Total-goal multiplier is intentionally smaller than per-team (the +15% / −10% per-side average to ~+2.5% net for total when one side is trailing). Validation = replay diff vs baseline (`dev/active/inplay-backfill-summary-BASELINE.txt` — 46 bets, 32W-14L, +378% ROI). |
| INPLAY-TIME-DECAY-PRIOR | Bayesian blend rewritten 2026-05-10: `w_live = 1 - exp(-minute/30)` replaces the flat `(pm + live)/(1 + minute/90)`. At min 30 → 63% live / 37% prematch; at min 60 → 86/14. Same weight applied inside `_remaining_goals_prob` (where evidence is goal count rather than live xG). Both blends now operate in rate-space (live signal normalized to per-90 via `live_xg_total × 90 / minute`). Smoke test INPLAY-TIME-DECAY-PRIOR guards the formula + asserts unit values at min 30/60/0. | 2h | ✅ Done 2026-05-10 | ✅ Ready | Affects every Bayesian-posterior caller — A/B/C/D/E/G/H/I/Q via `_bayesian_posterior`, J/L/M via `_remaining_goals_prob`. Validation = replay diff vs baseline. |
| INPLAY-PERIOD-RATES | `_period_multiplier(minute)` (0.85× ≤15, 1.20× ≥76, 1.0× elsewhere) shipped 2026-05-10 inside the new `_scaled_remaining_lam` helper. All seven strategies that compute their own remaining lambda (A/C/D/E/G/H/Q) plus the J/L/M Bayesian helper now go through `_scaled_remaining_lam` so the calibration stack (h2_uplift × period × state) lands once. Strategy N's bivariate Poisson lambdas also receive the period multiplier. Smoke tests INPLAY-PERIOD-RATES + INPLAY-CALIBRATION-STACK guard the multiplier values + ensure no raw `posterior * remaining / 90.0` callsite leaks past the helper. | 1h | ✅ Done 2026-05-10 | ✅ Ready | Marginal lift composed with state multiplier; biggest practical effect on Q/N (entry windows squarely inside the late-period 1.20× zone). |
| INPLAY-EMA-LIVE-XG | `_attach_ema_live_xg` shipped 2026-05-10. Live mode does one bulk SQL fetch of the last 10 minutes of snapshots per match, runs a time-aware EMA (alpha = 1 − exp(−delta_minutes / half_life)) with 5-min half-life, and overwrites `cand["xg_home/away"]` in-place before strategies run — every Bayesian-posterior caller automatically sees smoothed values, no per-strategy edits needed. Replay has an in-memory port (`apply_ema_live_xg_replay`) called once before `run_replay`. Real-xG candidates only; proxy-xG matches pass through untouched. Smoke test INPLAY-EMA-LIVE-XG guards both paths + the time-aware alpha + the in-place mutation. | 1h | ✅ Done 2026-05-10 | ✅ Ready | A single big-chance snapshot can no longer trigger a bet on its own — the EMA dampens it across the prior window. Validation = replay diff vs baseline. |
| INPLAY-DIXON-COLES | Apply Dixon-Coles low-score correction (replies 3, 4). Matters most for E (Under bets on low-scoring) where exact P(0-0)/P(1-0)/P(0-1) determines edge. | 4h | ⬜ | ⏳ After 1500+ bets | Don't tune this until we have enough data to validate the correction parameter. |
| INPLAY-CALIBRATION-IJL | Wait for 50+ settled bets per new bot (I/J/L) then run ECE (Expected Calibration Error) check. ECE < 5% is the gate before real-money use. Compare model edge at entry vs actual P&L per strategy. Tune entry thresholds (edge floor, odds floor) per strategy based on observed calibration. | 1h | ⬜ | ⏳ ~June (need 50+ settled bets per bot) | Bots I/J/L went live 2026-05-09. At ~5 bets/bot/day, 50 takes ~10 days of live matches. Use `scripts/check_calibration.py` extended for inplay markets. |
| INPLAY-ZERO-FIRE-AUDIT | ✅ Done 2026-05-11 — Diagnosed all 13 strategies. Bot IS running (heartbeat confirmed, 8,855 tries/strategy today). 0 fires are legitimate: **E** blocked by corner check (today's qualifying matches have 5+ corners at min 45, threshold=4.0); **H** window is min 46-55, matches at halftime — will fire next cycle; **I** no strong favourites (≥0.62) at 0-0 today; **J** all 0-0 matches have pm_o25 < 0.62; **G/N/Q** rare-event by design. **Critical finding: Strategy M is structurally non-functional.** `live_ou_25_over ≥ 3.0` is never met in real markets for 1-goal games (current values: 1.6–2.0). Replay ROI was inflated by the 1.3+1.3 xG fallback. Fix: lower M's OU floor to 2.40 (or remove it — let Bayesian edge gate the bet). Filed as `INPLAY-M-THRESHOLD-FIX`. | 2h | ✅ Done 2026-05-11 | ✅ Ready | Full diagnostic in git commit. |

### P3 — Speculative / infra-dependent

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-M-THRESHOLD-FIX | ✅ Done 2026-05-11 — Lowered Strategy M's `live_ou_25_over` floor from `3.0` → `2.40`. Changed `min_val=3.0` → `min_val=2.40` and `if ou25 < 3.0` → `if ou25 < 2.40` in `_check_strategy_m`. Updated INPLAY-EQUALIZER-MAGNET smoke test to assert `"2.40"`. Strategy M will now evaluate qualifying 1-goal matches (real OU2.5 odds sit at 1.6–2.0; threshold 2.40 is reachable). Monitor first 20 live bets — if win rate <50%, tighten BTTS floor (0.50) rather than raising OU floor again. | 30m | ✅ Done 2026-05-11 | ✅ Ready | |
| INPLAY-SOFT-GATES | Reply 1's biggest recommendation: replace hard threshold gates with composite weighted scoring (assign points to SoT pace, xG pace, possession, corners, market drift, prematch strength, score state — trigger above a single composite threshold). High-impact but architectural. | 8h | ⬜ | ⏳ After P0/P1 land | Will likely supersede many of the threshold tweaks above. |
| INPLAY-TWO-BOOK-ARB | Reply 5: bet when primary book's OU 2.5 differs from a second feed by ≥4pp. Requires a second odds source. **Most reliable edge** if infra exists. | varies | ⬜ | ⏳ Need 2nd odds feed | Out of scope without Pinnacle/sharp feed. |
| INPLAY-FUNNEL-LOGGING | `_funnel` counters now incremented at all seven skip points (`no_prematch`, `league_xg_gate`, `existing_bet`, `no_strategy_trigger`, `odds_stale`, `score_changed`, `store_bet_error`). Heartbeat (every 10 cycles ≈ 5 min) appends `funnel since-last: [k=v, ...]` and resets the counters, so any silent collapse becomes obvious in the next heartbeat. Smoke test INPLAY-FUNNEL-LOGGING guards every increment + the heartbeat line. | 1h | ✅ Done 2026-05-10 | ✅ Ready | Was hiding a real outage class — see InplayBot UUID bug (11 days lost). Now visible within ~5 min. |
| INPLAY-NEXT-10-MIN-MARKET | Captured: `parse_live_odds()` now matches `bet.id == 65` (or names "Next 10 Minutes Total" / "Next 10 Minutes") and emits `market="next10"`. `build_snapshot()` (both copies in live_tracker.py) maps to `live_next10_over` / `live_next10_under`. Migration 080 adds the columns; both `db.py` and `supabase_client.py` writer column-lists include them. `_get_live_candidates()` selects them so future strategies can read them. **No strategy uses them yet** — capture-only by design. | 1h | ✅ Done 2026-05-10 | ✅ Ready | Validate with a live probe once a match runs: `SELECT match_id, minute, live_next10_over, live_next10_under FROM live_match_snapshots WHERE live_next10_over IS NOT NULL ORDER BY captured_at DESC LIMIT 10;`. If still empty after a few hours of live football, market name needs adjusting. |
| INPLAY-BAYESIAN-ENGINE | `_remaining_goals_prob(pm_xg_total, minute, goals_observed, threshold)` extracted; returns `(model_prob, posterior_lam, remaining_lam)`. Strategies J (0-0 → Over 1.5) and L (1 goal → Over 2.5 FT) now share one code path. h2_uplift (1.05× post-min-45) baked into the helper with the Dixon & Robinson rationale in the docstring. Future strategies M/N/O can drop in with 2-3 lines. Smoke test INPLAY-BAYESIAN-ENGINE guards the helper + both call sites. | 1h | ✅ Done 2026-05-10 | ✅ Ready | Done early (vs the original "wait for M/N" plan) — the duplication was already paying interest, and M/N/O will land cleaner with the helper in place. |
| INPLAY-LAYER-ARCH | Decouple candidate state detection from execution — run a 100%-coverage detection pass (all live matches with score+minute+prematch) every cycle, then a second execution pass that only fires on candidates where live odds confirm the edge. Proposed by all 4 second-round AI replies as the structural fix that makes adding new strategies trivial. Current code mixes detection and execution in `_check_strategy_*`. | 4h | ⬜ | ⏳ After P0/P1 strategies stable (≥4 strategies firing) | Two classes: `CandidateEngine` (DB only, runs every cycle) and `ExecutionEngine` (requires live odds, fires on candidates). Enables delayed execution (detect at min 35, wait for entry at min 40 if odds improve) and better funnel logging. High architectural value but disruptive — wait until strategy count justifies the refactor. |

### AF Quota Optimization (audit, 2026-05-08)

> Origin: a comprehensive audit of every AF endpoint we call (logged in agent transcript). Bot currently uses ~31K of 75K daily quota. Live polling alone is only ~6.3% of accounted budget; the bottleneck for in-play strategies is **stats coverage** (xG/SoT/corners populated on only ~9% of snapshots), not quota. Several non-critical fetches refetch the same data 3-5× per day and could be cached, freeing budget for stats polling.

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-STATS-COVERAGE | Lift matches with `goals ≤ 1 AND minute ≥ 25` to HIGH priority in LivePoller so they get stats every cycle (45s) instead of every 3rd (135s). Targets exactly the matches that strategies A/D/G/H care about. ~30% of live matches will be HIGH at any given moment; stats calls roughly double during peak hours but absolute cost is small (~430 extra calls/day on a 720 baseline). Best-ROI single change before any further strategy work. | 30m | ✅ Done 2026-05-08 | ✅ Ready | `live_poller.py:_is_high_priority()` already fires `True` for `minute >= 25 AND total_goals <= 1`. Smoke test INPLAY-STATS-COVERAGE guards both the HIGH condition and the hard quota floor. |
| AF-CACHE-H2H | ✅ Done 2026-05-12 — Added 7-day cross-match H2H cache in `fetch_h2h`. After building `to_fetch`, queries `matches` for any row with same `(home_team_api_id, away_team_api_id)` pair + `h2h_raw IS NOT NULL` within last 7 days. If found, calls `store_match_h2h` with cached data and skips the AF call. Also already had a same-day match-level cache (`h2h_raw IS NOT NULL` on the current match). Smoke test AF-CACHE-H2H guards `h2h_week_cache` build and `pair_key` check. | 1h | ✅ Done 2026-05-12 | ✅ Ready | Save ~360 AF calls/day. |
| AF-CACHE-TEAM-STATS | ✅ Done 2026-05-12 — Added same-day DB cache in `fetch_team_stats`. Queries `team_season_stats WHERE fetched_date = today` before the loop, builds `cached_stat_keys` set. Per-team loop skips keys already in `cached_stat_keys`. Smoke test AF-CACHE-TEAM-STATS guards the query and check. | 1h | ✅ Done 2026-05-12 | ✅ Ready | Save ~150 calls/day. Also fixes the freshness layer for E's xG fallback. |
| AF-STANDINGS-DAILY | ✅ Done 2026-05-12 — Standings moved from intraday (10:30/13:00/16:00) to nightly-only. `job_enrichment_refresh` now uses `components={"injuries"}` only. `job_enrichment_full` now explicit `components={"injuries", "h2h", "team_stats"}`. New `job_standings_nightly` function runs standings at 23:30 UTC. Smoke test AF-STANDINGS-DAILY guards all three. | 30m | ✅ Done 2026-05-12 | ✅ Ready | Save ~40 calls/day. |
| AF-PREDICTIONS-FREQ → **P-PRED-1** | AF `/predictions` was running 6× daily (morning + 5 betting_refresh slots). Probed 2026-05-10: endpoint accepts ONLY `?fixture=ID` — no `ids`, `fixtures`, `date`, or `league` bulk form (every variant returned explicit field-not-exist errors). AF docs page 79-81 confirm. Per-fixture × ~3,000 fixtures × 5 extra slots = up to ~15K wasted calls/day for data identical to what's already on `matches.af_prediction`. **Shipped (more aggressive than original 5×→2× spec):** dropped predictions refetch from `job_betting_refresh` entirely. Predictions remain morning-only at 05:30 UTC; betting refreshes read the cached JSONB. Smoke test `P-PRED-1` regex-asserts no `run_predictions(` call or import sneaks back into the function body. WORKFLOWS.md ④ Predictions section updated. | 30m | ✅ Done 2026-05-10 | ✅ Ready | Save 2.5K–10K calls/day depending on fixture count and how many of the 5 slots saw fresh fixtures. Verifiable end-of-day in `api_budget_log.endpoint_breakdown_today` — predictions share should drop to ~3,115 (one burst) instead of climbing through the day. Also see `dev/active/morning-pipeline-af-audit.md` for the full audit. |
| P-ENR-1 — drop duplicate /fixtures in _build_fixture_meta | `fetch_enrichment._build_fixture_meta` was calling `get_fixtures_by_date(target_date)` to recover `home_team_api_id`, `away_team_api_id`, `venue_af_id`, and `season` — but step ① fixtures already extracts and writes all four via `fixture_to_match_dict` (`api_football.py:1547-1571`). Pure duplicate. **Shipped:** SQL select extended to read those four columns directly from `matches`; AF call removed. Verified: `season` is 100% populated; `home_team_api_id`/`away_team_api_id` ~95% (graceful skip downstream); `venue_af_id` 8-36% (unchanged — AF only emits venue.id for higher-tier fixtures). 3 enrichment runs/day × 1 call = 3 calls/day saved + ~50ms latency reduction per run. Smoke test `P-ENR-1` source-asserts both the SQL has the four fields AND no `get_fixtures_by_date(target_date)` call inside the function. | 20m | ✅ Done 2026-05-10 | ✅ Ready | Risk note from audit (verified): step ① runs 4× per day so any newly-discovered fixture goes through the same `bulk_store_matches` path that writes the four fields. Edge case: a fixture inserted between step ① runs would have nulls — but the consumers all use `.get()` with None checks, so the fixture just skips that round of enrichment until the next step ① run picks it up (max 6h). |
| AF-INJURIES-LATE | Injuries refetched 2-3× per match-day. Move to once at 08:00 UTC + a news-event-triggered refresh path. | 1h | ⬜ | ✅ Ready | Save ~30 calls/day; bigger win is freshness when news drops. |
| BT-SEED-FIX | ✅ Done 2026-05-11 — Added `_seed_from_db()` to `BudgetTracker` in `workers/api_clients/api_football.py`. Called from `__init__` at startup; queries `api_budget_log` for today's latest `endpoint_breakdown_today` JSONB and updates `_endpoint_counts_today`. Silently skips if DB unavailable or row is from a prior day. New BT-SEED-FIX smoke test asserts method exists, `__init__` calls it, and it populates `_endpoint_counts_today`. After next Railway redeploy, wait 1 full day then re-run `af_call_breakdown.py --days 1` to confirm attribution is now clean. | 30m | ✅ Done 2026-05-11 | ✅ Ready | |
| AF-BREAKDOWN-REVIEW | ✅ Done 2026-05-11 — Ran `af_call_breakdown.py --days 2`. Daily totals: Sat 103K, Sun-10 120K, Sun-11 129K. **Critical finding: seeding bug blocks clean attribution.** `_endpoint_counts_today` resets on every Railway redeploy; today's pod restart (INPLAY-LIVE-DEBUG push) zeroed the counter, leaving 129K calls unattributed. From the partial post-restart hourly window: `fixtures/statistics` + `fixtures/events` dominate at ~70% of attributed calls; extrapolated ~90K/day → far exceeds the 40K rule (a) threshold. **Decision: AF-COVERAGE-AUDIT is the next task.** Prerequisite: fix `BudgetTracker.__init__` to seed `_endpoint_counts_today` from `api_budget_log.endpoint_breakdown_today` on startup (`BT-SEED-FIX`, 30m). Full data in `dev/active/af-fetches-audit.md`. | 30m | ✅ Done 2026-05-11 | ✅ Ready | |
| AF-FETCHES-AUDIT | Pre-this-task `BudgetTracker.record_call()` only incremented a global counter — no on-disk per-endpoint detail, so the 26K gap was undiagnosable. **Shipped:** `BudgetTracker` now keeps `_endpoint_counts` (per-interval, drained on each hourly sync) + `_endpoint_counts_today` (cumulative since UTC midnight, reset on day rollover). `_get(endpoint, ...)` calls `record_call(endpoint)` with the literal AF path; `sync_with_server` writes both maps as JSONB on `api_budget_log`. Migration 086 adds `endpoint_breakdown` + `endpoint_breakdown_today` columns. New `scripts/af_call_breakdown.py` reads the JSONB and prints daily totals + sorted endpoint share + an hour-by-hour endpoint matrix. 3 smoke tests (counter behavior, source guards on _get + sync_with_server, migration column types). Once 24h of post-deploy traffic accumulates, run the script to see where the 26K actually goes — top deviators from the a-priori expected mix become follow-up tasks. Findings + expected-share table in `dev/active/af-fetches-audit.md`. | 2h | ✅ Done 2026-05-10 | ✅ Ready | **Reframing per user:** this is *not* a budget-rescue exercise (Mega 150K/day is plenty, bigger plans are buyable). The point is reclaiming headroom for **more aggressive live polling** — every saved call on a once-daily enrichment is a call we can re-spend on a 30s-cadence live job. Top expected sources of the 26K: live_poller's `fixtures/statistics` + `fixtures/events` per-cycle calls during peak Saturday hours. Verify after first full day of traffic. |
| LIVE-SNAPSHOTS-PRUNE | `live_match_snapshots` is currently NEVER pruned. At ~50K rows/day it'll hit 1M+ in a month. Add a nightly job that, for matches finished 48h+ ago, keeps only snapshots at minute 0/45/90 + ±30s of every match_event row, and deletes the rest. Same model as `prune_odds_snapshots.py`. | 2h | ⬜ | ✅ Ready | ~90% reduction in stored snapshot rows; no API impact, just storage + query speed. Match the user's "use the data 101% then prune" intent. |
| LIVE-ODDS-PRUNE | `live_odds` (per-snapshot odds rows) also unbounded. Same 48h-post-match pruning rule. | 1h | ⬜ | ✅ Ready | Pair with LIVE-SNAPSHOTS-PRUNE in one cleanup commit. |

### Suggested commit grouping

1. **Commit 1 (done):** POOL-LEAK-FIX + EXCEPTION-BOUNDARIES + JOB-COALESCE + DB-STMT-TIMEOUT — fixes today's outage class.
2. **Commit 2 (next):** OBS-HEARTBEAT + OBS-SENTRY-BACKEND — visibility before Reddit goes live.
3. **Commit 3 (pre-paid-launch):** STRIPE-WEBHOOK-SIG + MONEY-STRIPE-IDEMPOTENT + MONEY-WEBHOOK-TEST + STRIPE-RECONCILE — one Stripe-integrity commit.
4. **Commit 4:** MONEY-RLS-AUDIT + MONEY-SETTLE-RECON + BACKUP-RESTORE-DRILL + RATE-LIMIT-API + ABUSE-DETECT-PRELAUNCH + DEPLOY-ROLLBACK-RUNBOOK — pre-paid-launch security/recoverability.
5. **Commit 5+:** P2 tasks individually as time allows.

---

## Gemini AI Cost Tracker

> Billing enabled 2026-05-05. Prices: `gemini-2.5-flash` $0.15/1M input + $0.60/1M output · `gemini-2.5-flash-lite` $0.075/1M input + $0.30/1M output.

### Running now

| Job | Model | Calls/day | Tokens/call | $/mo now | Scales with |
|-----|-------|-----------|-------------|----------|-------------|
| `news_checker` (4×/day per active bet) | flash | ~64 (4 × 16 bets) | ~800 | **~$0.38** | Active bets — 50 bets = ~$1.20/mo |
| `match_previews` (ENG-3, 1×/day) | flash | ~10 | ~1,200 | **~$0.11** | Fixed (top 10 matches) |
| `settlement post-mortem` (1×/day) | flash | 1 batch | ~2,000 | **~$0.04** | Fixed |
| `bet-explain` (BET-EXPLAIN, user-triggered) | flash-lite | ~5 new/day | ~800 | **~$0.02** | New bets only — cached after 1st call |
| **Total running** | | | | **~$0.55/mo** | |

### Planned (not yet built)

| ID | Feature | Model | Calls/day | $/mo at launch (10 users) | $/user/mo |
|----|---------|-------|-----------|--------------------------|-----------|
| MTI | Managerial tactical intent (press conf.) | flash | ~10 (5 matches × 2 teams) | **~$0.22** flat | negligible per user |
| RSS-NEWS | RSS news extraction pipeline | flash | ~20 articles | **~$0.30** Gemini only | negligible — data service ($30-90/mo) is the real cost |

**Current total AI cost: ~$0.55/mo running + $0/mo planned = ~$0.55/mo**

---

## Tier 0 — Foundation (all done)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| B-ML1 | Pseudo-CLV for all ~280 daily matches | 2-3h | ✅ | ✅ Done | `(1/open) / (1/close) - 1` per finished match. 280 rows/day |
| B-ML2 | `match_feature_vectors` nightly ETL | 1 day | ✅ | ✅ Done | Pivots signals + predictions + ELO/form → wide row per match |
| CAL-1 | Calibration validation script | 2h | ✅ | ✅ Done | `scripts/check_calibration.py` |
| S1+S2 | Migration 010: `source` on predictions + `match_signals` table | 2-3h | ✅ | ✅ Done | Unique constraint on (match_id, market, source) |
| CAL-2 | Flip calibration α: T1→0.20, T2→0.30, T3→0.50, T4→0.65 | 30 min | ✅ | ✅ Done | Was T1=0.55 (model-heavy). Now market-heavy in efficient markets |
| RISK-1 | Reduce Kelly fraction to 0.15×, cap to 1% bankroll per bet | 15 min | ✅ | ✅ Done | KELLY_FRACTION 0.25→0.15, MAX_STAKE_PCT 0.015→0.010 |
| LLM-RESOLVE | Run `scripts/resolve_team_names.py --apply` | 30 min | ✅ | ✅ Done | 143 total mappings. 204 unmatched names accounted for. **AI: $0 ongoing (one-time batch)** |

---

## Tier 1 — Next 1-2 Weeks

### Done

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| OPS-DASHBOARD-FIX | Ops dashboard blank — `write_ops_snapshot` swallowed all errors silently, no row written → every metric showed `—` | ✅ Done 2026-05-08 | `workers/api_clients/supabase_client.py`: per-section try/except so a single bad query doesn't kill the snapshot; logs to `pipeline_runs` so failures are visible on the dashboard; re-raises only on INSERT failure. `workers/scheduler.py`: hourly `job_ops_snapshot` now wrapped in `_run_job` so failures hit `/health` and `_recent_errors`. 2 smoke tests added. Backfilled today's row. |
| INPLAY-EDGE-BUG | Inplay bot edge_percent stored as percent not decimal | ✅ Done 2026-05-07 | `inplay_bot.py` stored `edge = (prob-implied)*100` but `store_bet` expects decimal. Fixed: divide by 100 at storage. Patched 1 bad DB record. Smoke test added. |
| SIGNALS-RLS | `match_signals` RLS enabled but no SELECT policy — anon key returned [] for everyone | ✅ Done 2026-05-07 | Migration 069. `getMatchSignals()` was silently returning empty, hiding accordion + summary on ALL matches. |
| SIGNALS-UI | Wire all missing signals to accordion + summary (~20 signals in DB but invisible) | ✅ Done 2026-05-07 | `signal-labels.ts`: 15 new label functions (Pinnacle, manager change, turf, H2H depth, goals avg, relegation, referee O/U, AH, BTTS). `signal-accordion.tsx`: new Specialist Markets group + all signals added. `match-signal-summary.tsx`: manager change, relegation pressure, Pinnacle line moves added to top-5 priority list. |
| S3/S4/S5/S3b-f | All signals wired (ELO, form, H2H, referee, BDM, OLM, venue, rest, standings) | ✅ | Full signal set in match_signals |
| SIG-7/8/9/10/11 | Importance asymmetry, venue splits, form slope, odds vol, league meta | ✅ | |
| META-2 | Meta-model feature design (8 market-structure features) | ✅ | |
| PIPE-1 | Clean 9-job pipeline replacing monolith | ✅ | |
| STRIPE / F8 | Stripe test mode: checkout, webhook, portal, founding cap, annual billing | ✅ | |
| B3 | Server-side tier gating in Next.js | ✅ | |
| SUPABASE-PRO | Supabase upgraded to Pro ($25/mo) | ✅ | PITR + backups |
| LEAGUE-DEDUP | Kambi/AF dedup, priority sort, ~1100 orphan leagues pruned | ✅ | |
| SENTRY | Error monitoring wired in frontend | ✅ | |

### Done (continued)

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| PERF-FE-1 | A1: daily_unlocks check parallelised inside auth IIFE | ✅ Done 2026-05-06 | Was sequential after main Promise.all (one extra round-trip for logged-in users). Moved inside authResult async block, runs in parallel with getUserTier. `src/app/(app)/matches/page.tsx` |
| PERF-FE-2 | C3: getTodayOdds — replace SELECT * with get_latest_match_odds RPC | ✅ Done 2026-05-06 | Was fetching all historical snapshots (~18k rows for 160 matches). Now DISTINCT ON (match, bookmaker, market, selection) returns only the latest snapshot per combo. Migration 053. `src/lib/engine-data.ts` |
| PERF-FE-3 | D1: getTrackRecordStats — replace 2500-row fetch with get_coverage_counts RPC | ✅ Done 2026-05-06 | Was fetching 500 odds_snapshots + 2000 matches to count distinct bookmakers/leagues in JS. Now COUNT(DISTINCT) in DB returns two integers. Migration 053. `src/lib/engine-data.ts` |
| PERF-FE-4 | C1: getPublicMatchBookmakerCount — replace row fetch with get_bookmaker_count_for_match RPC | ✅ Done 2026-05-06 | Was fetching all 1x2 rows per match and counting in JS. Now single COUNT(DISTINCT bookmaker) in DB. Migration 053. `src/lib/engine-data.ts` |
| PERF-FE-5 | C2: getOddsMovement — replace JS bucketing with get_odds_movement_bucketed RPC | ✅ Done 2026-05-06 | Was fetching 100-1000 raw snapshots and bucketing by hour in JS. Now DATE_TRUNC('hour') + MAX GROUP BY in DB returns ~20-50 rows. Migration 053. `src/lib/engine-data.ts` |
| PERF-PY-1 | B1: compute_market_implied_strength — fix N+1 (was 2+N queries) | ✅ Done 2026-05-06 | Was 1 query per match in two loops (up to 12 queries). Replaced with one batched DISTINCT ON query for all match IDs. 2+N → 3 queries total. `workers/api_clients/supabase_client.py` |

### Open

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| CAL-DIAG-1 | SQL diagnostic on 77 settled home bets: avg Poisson vs XGB prob, sharp_consensus direction, pre-Platt vs post-Platt comparison | 1h | ✅ Done 2026-05-06 | ✅ Ready | Results: n=31 bets, model=38.2%, calibrated=42.0% (Platt inflated +3.87pp), market_implied=29.0%, actual=25.8%. Pinnacle=30.2% — closer to actual than model. Sharp consensus avg=−0.0034. Gate coverage: 1/23 losses caught, 7 missing signal. `scripts/run_cal_diag.py` |
| CAL-PIN-SHRINK | Switch shrinkage anchor from market avg → Pinnacle (with soft-book fallback when Pinnacle unavailable) | 30min | ✅ Done 2026-05-06 | ✅ Ready | `calibrate_prob()` now accepts `anchor_implied`; Pinnacle-implied used when available for 1X2 Home. Batch-loaded from match_signals in daily_pipeline_v2. Soft-book fallback preserved when Pinnacle unavailable. `workers/model/improvements.py` |
| CAL-ALPHA-ODDS | Odds-conditional α reduction: `if odds > 3.0: alpha = max(alpha - 0.20, 0.10)` | 30min | ✅ Done 2026-05-06 | ✅ Ready | Note: alpha = model weight in this codebase (opposite of AI consultant convention — they used α = market weight). Reducing alpha pulls harder toward anchor for longshots. Targets 0.30-0.40 bin (23 bets, 13% actual vs 35.5% predicted). `workers/model/improvements.py` |
| CAL-SHARP-GATE | Skip 1X2 Home bets when `sharp_consensus_home < −0.02` | 1h | ✅ Done 2026-05-06 | ✅ Ready | Batch-loads `sharp_consensus_home` from match_signals alongside Pinnacle. Gate fires in betting loop after PIN-VETO check. Coverage currently low (1/23 losses, 7 missing signal) — will improve as more bets settle with signal data. `workers/jobs/daily_pipeline_v2.py` |
| CAL-DRAW-INFLATE | Add draw inflation factor to Poisson convolution: `adjusted_draw = raw_draw_prob × 1.08`, renormalize home/away | 1h | ✅ Done 2026-05-06 | ✅ Ready | Applied after DC correction in `_poisson_probs()`. DRAW_INFLATE=1.08 constant; excess probability redistributed proportionally to home/away. Unlocks draw market betting. `workers/jobs/daily_pipeline_v2.py`. |
| TZ-TOMORROW | Tomorrow's matches tab on matches page | 2-3h | ✅ Done 2026-05-06 | ✅ Ready | `getPublicMatches(dayOffset)` accepts 0=today, 1=tomorrow. URL param `?tab=tomorrow`. Yesterday overhang skipped on tomorrow tab. WhatChangedToday hidden on tomorrow tab. Also shipped: parallel odds RPC batches (was sequential) + replaced 60k-row signal count query with `get_signal_counts` RPC (migration 051). |
| RAIL-POLL-TUNE | Tune LivePoller intervals to reduce Railway cost ~25% | 30min | ✅ Done 2026-05-08 | ✅ Ready | `FAST_INTERVAL` 30→45s, `MEDIUM_MULTIPLIER` 2→3. AF calls ~8-12K/day (was 12-18K). |
| STAKE-RANK | Exposure cap should rank bets within a league by edge before applying declining stakes — currently processes in DB query order so the highest-edge bet in a league can end up with the smallest stake if evaluated last. Fix: collect all bet candidates per league first, sort descending by edge, then apply 50% halving in ranked order so best bet always gets most money. | 2h | ✅ Done 2026-05-08 | ✅ Ready | One-liner in `daily_pipeline_v2.py`: `bet_candidates.sort(key=lambda x: x[6], reverse=True)` inserted after the candidate collection loop and before the placement loop. Edge is index 6 of the 11-tuple. Highest-edge bet always gets full stake; any 3rd+ bet in the same league gets halved in that order. |
| B-ML3 | First meta-model: 8-feature logistic regression, target=pseudo_clv>0 | 1 day | ⬜ | ⏳ Waiting — target 2026-05-26 (~1,400 CLV-outcome rows). At 582 rows (60/day growth) coefficients are too noisy — logistic regression needs ~50 examples per feature for stability (8 features × 50 = 400 min; 800+ for confidence). By May 26 we have 2.4× more data and can batch with NEWS-LINEUP-VALIDATE + ODDS-TIMING-VALIDATE in the same session. | **Data quality cutoff:** `match_date >= '2026-05-06'` (pre-cutoff rows lack key signals). Use `opening_implied_home IS NOT NULL` filter (not `pinnacle_implied_home` — never added to MFV schema). Features: (1) `ensemble_prob_home - opening_implied_home` (likely strongest); (2) `odds_drift`; (3) `elo_diff`; (4) `form_ppg_diff`; (5) `sharp_consensus`; (6) `news_impact_score` (include only if NEWS-LINEUP-VALIDATE passes AUC > 0.52); (7) `time_to_kickoff`; (8) `bookmaker_disagreement`. After training: check coefficients — near-zero means drop the feature. |
| BOT-TIMING | Time-window bot cohorts: morning/midday/pre-KO A/B test | 2-3h | ✅ | ✅ Done 2026-05-01 | 16 bots → 5 morning / 6 midday / 5 pre_ko. `BOT_TIMING_COHORTS` dict + cohort param in run_morning(). Migration 032 adds timing_cohort to simulated_bets. Scheduler auto-selects cohort by UTC hour. |
| POSTGREST-CLEANUP | Migrate remaining PostgREST callers to psycopg2 | 3-4h | ✅ | ✅ Done 2026-05-03 | All workers + scripts fully migrated. Last batch: `fit_platt.py` (SQL JOIN replaces paginated PostgREST), `backfill_historical.py` (all progress tracking + bulk event INSERT), `live_tracker.py` (crash fix — undefined `client`). `get_client()` lives exclusively in `supabase_client.py` internals. Backfill moved to Railway 02:00 UTC daily. |
| PERF-1 | Batch morning signal writing — replace 25-40 per-match DB queries | 2-3h | ✅ | ✅ Done 2026-05-03 | `batch_write_morning_signals()` in supabase_client.py: 10 bulk queries (ANY(match_ids[])) + one execute_values INSERT replaces ~14K serial round-trips. Reduced 34-70 min bottleneck to ~15s. Added league_id to match_dict for SIG-11. |
| PERF-2 | Rewrite prune_odds_snapshots.py — single SQL DELETE | 1h | ✅ | ✅ Done 2026-05-03 | Replaced per-match PostgREST iteration with one DISTINCT ON subquery DELETE. Prunes all finished matches in a single statement. Migrated to psycopg2. |
| STRIPE-PROD | Swap Stripe to production keys | 1h | ✅ Done 2026-05-04 | ✅ Done | Live products created (Pro €4.99, Elite €14.99 + yearly + founding). All Vercel env vars updated. Live webhook `https://www.oddsintel.app/api/stripe/webhook`. Deployed. |
| GH-CLEANUP | Remove pipeline workflow files from GitHub Actions | 30min | ✅ Done 2026-05-05 | ✅ Done | Deleted fixtures/enrichment/odds/predictions/betting/live_tracker/news_checker/settlement .yml. Only migrate.yml + backfill.yml remain. |
| BOT-PROVEN | `bot_proven_leagues` — focused strategy targeting only the 5 cross-era backtest-confirmed leagues (Singapore/Scotland/Austria/Ireland/S.Korea) | 1h | ✅ Done 2026-05-05 | Added to BOTS_CONFIG + BOT_TIMING_COHORTS (midday). 17th bot. Clean performance track for strongest backtest signals. |
| RHO-DYN | Dynamic Dixon-Coles rho per league tier — fit rho from historical scoreline frequencies instead of global -0.13 | 2h | ✅ Done 2026-05-05 | `scripts/fit_league_rho.py` → `model_calibration` (market=`dc_rho_tier_{n}`). `_load_dc_rho_cache()` + `_poisson_probs(rho=)` in pipeline. Sunday refit step 6/6. Falls back to -0.13 if <200 matches/tier. |
| N4/N6/N9 | Settlement 01:00 UTC, watchlist 14h lookback, stagger 20:35 | 30min | ✅ Done 2026-05-05 | Settlement overnight run added (21:30+ KO extra time). ODDS_LOOKBACK_HOURS 6→14 covers overnight drift. Watchlist 20:30→20:35 avoids collision with betting refresh. Tested dry-run. |
| N5 | Afternoon + evening value bet alert emails for Pro/Elite | 2h | ✅ Done 2026-05-05 | `run_value_bet_alert(slot)` in email_digest.py. Afternoon (16:00, since 10:00 UTC) + Evening (20:45, since 17:00 UTC). Migration 046: `value_bet_alert_log` UNIQUE(user_id, alert_date, slot). No-op if no new bets. Pro gets count+CTA, Elite gets full table. |
| N7 | Full enrichment (all 4 components) at 13:00 UTC | 30min | ✅ Done 2026-05-05 | `job_enrichment_full()` added to scheduler at 13:00 UTC. `run_enrichment()` with no components filter = standings+H2H+team_stats+injuries. Ensures H2H+team_stats fresh for afternoon/evening betting refreshes. |
| SCHED-AUDIT | Full cron audit — 10 gaps fixed + 3 structural bugs | 2h | ✅ Done 2026-05-05 | 6 betting runs/day (was 4): added 09:30 + 20:30. Closing odds 20:00. Enrichment 12:00→10:30. News 14:30 added, 19:30→18:30. Previews 07:00→07:15. Platt+blend Wed+Sun. N1: LivePoller 24/7 (was 10-23 UTC), adaptive 30s live / 120s idle. N2: betting pipeline now filters `status='scheduled'` AND `kickoff > now` — no more bets on live matches. N3: `fixture_to_match_dict` passes `af_status_short`; `store_match` updates status→'postponed' + kickoff time for existing scheduled matches; fixture refresh job 4×/day (09:15, 10:45, 14:45, 18:45). |
| ADMIN-TIER-PREVIEW | Superadmin tier preview switcher — switch between free/pro/elite to QA any page | 2-3h | ✅ Done 2026-05-04 | ✅ Done | Cookie-based override. (1) `src/lib/get-user-tier.ts` shared utility — wraps profile fetch, checks `preview_tier` httpOnly cookie when `is_superadmin=true` and overrides tier; replace ~5 pages that inline `.select("tier, is_superadmin")` with this. (2) `/api/set-preview-tier` POST route — sets/clears cookie, superadmin-only server-validated. (3) Floating pill UI (`src/components/superadmin-tier-bar.tsx`) — fixed overlay, only renders for superadmins, shows current preview tier badge + free/pro/elite/"My Tier" buttons, added to app layout. Cookie is httpOnly+sameSite=lax. Works cleanly: all pages are dynamic (no ISR), so cookie flip = instant re-render with different tier data. Visual banner ensures you always know which tier you're previewing. |

---

## Signal UX — Phase 1 (all done)

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| SUX-1 | Match Intelligence Score: A/B/C/D grade on every match card | ✅ | Grade badge + signal count tooltip. All tiers |
| SUX-2 | Match Pulse composite indicator (⚡ / 🔥 / —) | ✅ | bdm>0.12 + OLM/vol threshold. ~15-20% scarcity |
| SUX-3 | Free-tier signal teasers on notable matches | ✅ | 1-2 italic hooks on 30-40% of matches |

---

## Tier 2 — 2-4 Weeks

### Done

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| MOD-1 | Dixon-Coles correction to Poisson model | ✅ | `DIXON_COLES_RHO=-0.13`. τ correction for low-score draws |
| PLATT | Platt scaling + weekly recalibration | ✅ | `scripts/fit_platt.py`. Weekly Sunday refit — now passes `MODEL_VERSION` env so refit is scoped to current production model. Run manually once v14 has 100+ settled predictions per market (currently ~40/market, threshold expected ~2026-05-15). |
| BDM-1 | Bookmaker disagreement signal | ✅ | compute_bookmaker_disagreement() → match_signals |
| FE-LIVE / ODDS-OU-CHART / ODDS-BTTS / ODDS-MARKETS | Live in-play chart, O/U 2.5 chart, BTTS/O/U 1.5/3.5 odds table | ✅ | Pro gated |
| MKT-STR | Market-implied team strength into XGBoost | ✅ | market_implied_home/draw/away in feature row |
| EXPOSURE-AUTO | Auto-reduce stakes on league concentration | ✅ | 3rd+ bet same league = 50% stake |
| LIVE-FIX | Populate xG/shots/possession/corners in snapshots | ✅ | Was empty. Now 1 extra AF call per live match |
| BOTS-EXPAND | 10→16 bots (BTTS, O/U 1.5/3.5, draw, O/U 2.5 global) | ✅ | ~30-40 bets/day |
| KAMBI-BTTS | O/U + BTTS from Kambi event endpoint | ✅ | ~40 matches with BTTS now |
| BET-MULTI | Betting pipeline 5x/day (06/10/13/16/19 UTC) | ✅ | Idempotent — unique constraint prevents duplicates |
| TR-REDESIGN | Track record redesign: CLV-led, tier-gated | ✅ | |
| LP-1/2/3 | Landing page fixes | ✅ | Pricing/urgency cleanup |
| P5.1 | Sharp/soft bookmaker classification + sharp_consensus signal | ✅ Done 2026-05-03 | `data/bookmaker_sharpness_rankings.csv` (13 bookmakers, 3 tiers). `sharp_consensus_home` signal in `batch_write_morning_signals`. |
| PIN-1 | Pinnacle anchor signal: `pinnacle_implied_home` stored per match | ✅ Done 2026-05-04 | `batch_write_morning_signals()` in supabase_client.py. |
| PIN-VETO | Pinnacle disagreement veto for 1X2 home bets (gap > 0.12 → skip) | ✅ Done 2026-05-06 | `PINNACLE_VETO_GAP = 0.12` in `daily_pipeline_v2.py`. Empirical: catches 22/34 losses, filters 6/40 wins. |
| ODDS-API | ~~Activate The Odds API for Pinnacle odds ($20/mo)~~ | ❌ Cancelled | AF already provides Pinnacle. |
| LEAGUE-ORDER | 6-tier league priority system | ✅ Done 2026-05-05 | Migration 044. |
| ALN-FIX | Alignment NONE class when active=0 | ✅ Done 2026-05-04 | `improvements.py:compute_alignment()`. |
| ALN-EXPAND | sharp_consensus + Pinnacle anchor as alignment dimensions 5+6 | ✅ Done 2026-05-04 | `improvements.py`. |
| PERF-CACHE | Pre-stored dashboard stats in DB via settlement | ✅ Done 2026-05-04 | Migration 035. `write_dashboard_cache()` in settlement.py. |
| PERF-CACHE-REFRESH | Periodic dashboard_cache refresh — `/performance` was lagging up to ~24h between settlement runs (showed 146 settled vs 213 actual). New `job_dashboard_cache_refresh` runs every 30 min at :15/:45 in `workers/scheduler.py`. Lightweight — pure SQL aggregations, no API calls. Source-inspect smoke test added. | ✅ Done 2026-05-09 | Now: ~50 cache rows/day vs ~5; aligns `/performance` with `/admin/bots` totals within 30 min. |
| FE-BOT-DASH | Bot P&L dashboard (superadmin-gated) | ✅ Done 2026-05-04 | `/admin/bots` page. |

### Open

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| CODE-SONAR-WEB | Fix SonarCloud findings on odds-intel-web. **After repo cleanup (stitch_output removed):** Security 16 vulns = all stitch HTML (gone from repo, will resolve on next scan). Reliability D (5 bugs): 2 stitch HTML (gone), 2 `value-bets-live.tsx` sort bugs (fixed — `localeCompare`), 1 `login-modal.tsx` a11y (fixed — keyboard handler + role). 27 critical code smells = all cognitive complexity (`S3776`). **Remaining after next scan:** ~0 vulns, ~0 bugs, 396 code smells (non-urgent). | 30min | ✅ Done 2026-05-06 | ✅ Done | Real bugs fixed. Stitch files gitignored + removed from tracking. Re-run SonarCloud scan to confirm A/A ratings. Remaining 396 code smells are all complexity — address as part of CODE-RADON if ever needed. |
| CODE-WEB-ESLINT | Fix 9 ESLint errors + 16 warnings in odds-intel-web. **Errors:** `signal-delta.tsx:84` setState sync in effect (cascading renders); `superadmin-tier-bar.tsx:28` JSX inside try/catch (errors won't be caught); `login-modal.tsx`, `match-notes.tsx`, `match-pick-button.tsx`, `cookie-banner.tsx`, `api/stripe/upgrade` (review each). **Warnings:** 63 complexity violations — worst offenders: `bet-explain GET` (59), engine-data functions (60, 64), `bankroll/page` (40), `my-picks` (27). 2 auto-fixable with `--fix`. Complexity rule added to `eslint.config.mjs` (threshold=10). | 2-3h | ✅ Done 2026-05-06 | ✅ Ready | Fixed all 9 errors: prefer-const (bet-explain, mock-data), no-html-link (bankroll), JSX-in-try-catch (superadmin-tier-bar), disabled `react-hooks/set-state-in-effect` (flags valid guard/reset patterns). 0 errors remain, 79 warnings (all complexity). Future protection: `next build` already runs lint and fails on errors → Vercel blocks bad deploys. |
| CODE-WEB-KNIP | Remove dead code found by Knip in odds-intel-web: **20 unused files** (components + lib files never imported), **24 unused exports**, **23 unused exported types**. Key files: `src/lib/mock-data.ts`, `src/lib/types.ts`, `src/lib/queries.ts`, `src/lib/supabase.ts` (old Supabase client?), `src/components/track-record-client.tsx`, `src/components/value-bets-client.tsx`, `src/components/match-detail-tabs.tsx`. Also: 6 unused engine-data.ts query functions (getTodayOdds, getAvailableLeagues, getDashboardCache etc). | 1-2h | ✅ Done 2026-05-06 | ✅ Ready | Deleted all 20 files. Removed dead functions: getAvailableLeagues, signalLabel, PULSE_SIGNALS, getCountryFromLeague. Removed export from internal-only: getTodayOdds, getDashboardCache. 5,319 lines deleted. lint warnings 79→72. |
| PIN-2 | Extend Pinnacle signals to all bet markets | 1h | ✅ Done 2026-05-06 | ✅ Ready | Added `pinnacle_implied_draw`, `_away`, `_over25`, `_under25` to `batch_write_morning_signals()` via dedicated bulk query block (3b). `workers/api_clients/supabase_client.py`. |
| PIN-3 | Extend disagreement veto to draw/away/O/U markets | 1-2h | ✅ Done 2026-05-06 | ✅ Ready | Veto gate in `daily_pipeline_v2.py` now uses a selection→dict map covering Home/Draw/Away/Over 2.5/Under 2.5. Threshold 0.12 for all markets (tune per market once 50+ settled bets). Pinnacle anchor also extended to all markets in `calibrate_prob()` call. |
| PIN-VETO-EXT | Extend disagreement veto to BTTS / double_chance / AH / O/U non-2.5 | 30min | ✅ Done 2026-05-12 | ✅ Ready | PIN-3 only covered markets with a stored Pinnacle signal. BTTS, DC, AH, O/U 1.5/3.5 had no Pinnacle anchor, so bets with cal_prob 20–23pp above implied odds were being placed (7 today, showing +40% EV on frontend). Fix: if no Pinnacle signal exists, fall back to `ip = 1/best_odds` as the veto anchor — same 0.12 gap threshold. Unified into a single `_veto_anchor` variable. Retroactively all 7 suspicious bets vetoed; 31 legitimate bets (gap < 12pp) survive. |
| PIN-4 | Pinnacle line movement signal | 1-2h | ✅ Done 2026-05-06 | ✅ Ready | `pinnacle_line_move_home/draw/away` added to `batch_write_morning_signals()`. Uses oldest vs most recent Pinnacle snapshot (requires 2+ snapshots). Positive = home shortened = sharp money backing. `workers/api_clients/supabase_client.py`. |
| PIN-5 | Pinnacle-anchored CLV | 2h | ✅ Done 2026-05-06 | ✅ Ready | `clv_pinnacle` column added via migration 050. New `get_pinnacle_closing_odds()` helper in `settlement.py`. Computed as `(odds_at_pick / pinnacle_closing_odds) - 1` and written alongside `clv` on every settlement. Falls back to latest Pinnacle snapshot when is_closing not flagged. |
| PIN-5-BACKFILL | Backfill clv_pinnacle on existing settled bets | 30min | ✅ Done 2026-05-06 | ✅ Ready | `scripts/backfill_clv_pinnacle.py` — updated 26/77 settled bets. Remaining 51 pre-date Pinnacle odds collection (PIN-1 started May 4). Run any time to catch newly settled bets. |
| CAL-PLATT-UPGRADE | Replace single-input Platt with 2-feature logistic: `X = [shrunk_prob, log(odds)]` | half day | ✅ Done 2026-05-12 | Code shipped. O/U fit pending data (73 settled bets, need 300). 1X2 fit also pending (114/300). | Code: `apply_platt()` uses 2-feature path when `platt_c` non-null in `model_calibration`. `fit_platt.py` auto-fits O/U when ≥300 settled bets present. Migration 100 adds `platt_c` column. Weekly Sunday refit will auto-populate O/U once threshold met. Actual settled O/U bets (won+lost, 2.5 only) = 73 as of 2026-05-12 audit — previous 353 count was from predictions table, not simulated_bets. |
| CAL-PLATT-UPGRADE-VALIDATE | Validate O/U 2-feature logistic after first successful fit | 30min | ⬜ | ⏳ After O/U threshold met (~4-6 weeks). Weekly Sunday refit auto-triggers. | When `platt_c` first appears in `model_calibration` for `over_under_25_over`/`under`, check: (1) ECE before/after in fit_platt.py output (should improve); (2) `SELECT AVG(clv_pinnacle), COUNT(*) FROM simulated_bets WHERE market='O/U' AND result IN ('won','lost') AND pick_time >= [fit_date]` — CLV should be positive. If ECE improves but CLV doesn't, the correction is miscalibrated at bet-placement time. |
| ALN-1 | Dynamic alignment thresholds | 2h | ✅ Done 2026-05-12 | ✅ Done | **Shipped 2026-05-12.** LOW-alignment bets now require edge ≥ base_threshold + 1%. `_ALN_BUMP = {"LOW": 0.01, "MEDIUM": 0.0, "HIGH": 0.0, "NONE": 0.0}` in `daily_pipeline_v2.py`. Retrospective impact: 63/347 quality bets filtered (18.2%), retained LOW ROI 17.9% vs filtered 13.0%. HIGH/MEDIUM thresholds unchanged (3 and 11 bets respectively — too small). Tune bumps once 100+ HIGH/MEDIUM bets accumulated. Analysis script: `scripts/aln1_analysis.py`. Smoke test: `ALN-1`. |
| NEWS-LINEUP-VALIDATE | Validate `news_impact_score` AUC and `lineup_confidence` accuracy before including in B-ML3 | 1h | ⬜ | ⏳ Waiting — target 2026-05-26, batch with B-ML3. At 415 samples the AUC estimate has a confidence interval of ~±0.08 (too wide to trust a 0.52 threshold decision). By May 26 we'll have ~830+ samples and a tighter estimate. (1) AUC of `news_impact_score` vs actual outcome divergence — gate: >0.52; (2) `lineup_confidence >= 0.9` accuracy check. |
| VAL-POST-MORTEM | Review 14 days of LLM post-mortem patterns | 30min | ⬜ | ⏳ May 13+ (have 2 rows, need 14) | `SELECT notes FROM model_evaluations WHERE market='post_mortem' ORDER BY date DESC LIMIT 14` |
| MD-POLISH | Match detail visual polish: sticky tab blur, tab badge counts (e.g. "Match 4"), signal severity colors by group (market=blue, form=green, injuries=red), signal timestamps ("detected 3h ago"), "Why this match?" auto-generated hook at top of Intel, bot consensus as visual icons. Bookmaker comparison table (Odds tab). | 2-3h | ✅ Done 2026-05-07 | ✅ Ready | Polish pass on the tabbed match detail layout. All data already available — purely frontend rendering. `src/components/match-detail-tabs.tsx`, `signal-accordion.tsx`, `bot-consensus.tsx`. |
| BOT-QUAL-FILTER-DUAL | ✅ Done 2026-05-13 — **"Quality bets only" toggle now on BOTH `/admin/bots` AND public `/performance`.** Originally scoped admin-only (BOT-QUAL-FILTER, queued 2026-04 with "wait for 100 settled bets" gate). When picked up today: data threshold was 3.85× over (385 settled bets post-2026-05-06 vs 100 needed) and `/performance` had become public-facing for paying users (`isPro` gate), so the same ~15% legacy-bet drag affected paying users more than superadmin. **Implementation:** new `src/lib/bot-aggregates.ts` holds `QUALITY_CUTOFF = "2026-05-06"`, `filterQuality()`, `buildBotStats`, `buildSummary`, `buildMarketStats`, `buildPublicBotStats`, `buildPerformanceStats`. `/admin/bots` refactored — server passes raw `bets + allBotsDB` only; client (`bot-dashboard-client.tsx`) computes everything via `useMemo` so the toggle updates botStats + summary + marketStats + per-bot detail modal at once. Default OFF (admin wants diagnostic transparency). `/performance` wrapped in new `PerformanceClient` that owns the toggle and feeds both `PerformanceHero` + `PerformanceLeaderboard`. Default ON (paying users see honest current-pipeline numbers; toggle off reveals legacy). Free users see cache-based stats with no toggle (they don't have bet-level access). 3 source-inspection smoke tests (BOT-QUAL-LIB, BOT-QUAL-ADMIN, BOT-QUAL-PERFORMANCE). | 1.5h actual | ✅ Done 2026-05-13 | ✅ Ready | All metrics — ROI, CLV, hit rate, P&L, per-bot rows, market splits, bankroll chart — recompute under the toggle via useMemo. Toggle element has `data-testid="quality-only-toggle"` for future E2E. |
| VIG-REMOVE | Fix Pinnacle implied probability calculation — currently using raw `1/odds` with no vig removal, which biases the calibration anchor ~1.5-2% high per outcome. **Confirmed in code**: `supabase_client.py:3050` `pin_implied = 1.0 / float(pinnacle_rows[0]["odds"])`. Fix: use multiplicative normalization across all 3 Pinnacle 1X2 prices: `fair = raw / sum(raws)`. Applies to all `pinnacle_implied_*` signals, the `calibrate_prob()` anchor, and the 0.12 veto threshold (which was calibrated against biased values). O/U: normalize across Over+Under pair. | 2h | ✅ Done 2026-05-07 | ✅ Ready | Block 3b in `batch_write_morning_signals()` refactored: single query loads all 3 Pinnacle 1X2 selections per match, normalizes home/draw/away together. Separate O/U query normalizes over+under pair. Line movements kept as raw diffs (direction matters, vig stable intraday). Tests added. `workers/api_clients/supabase_client.py`. |
| DRAW-PER-LEAGUE | Per-league draw inflation factor. Current fixed `DRAW_INFLATE = 1.08` applies globally — draw rates vary from ~22% (PL, high-scoring open leagues) to ~32% (defensive lower-division leagues). `league_draw_pct` is already collected as a signal. Replace constant with a per-match calculation: `draw_inflate = 1.0 + max(0, (league_draw_pct - 0.268) / 0.268 * 0.08)` clamped to [1.03, 1.15]. Where `league_draw_pct` unavailable, keep 1.08 as fallback. | 2h | ✅ Done 2026-05-07 | ✅ Ready | `_poisson_probs()` now accepts `league_draw_pct` param. `compute_prediction()` passes it through. `run_morning()` batch-loads `league_draw_pct` from `match_signals` alongside Pinnacle signals and passes per match. Fallback 1.08 preserved when signal absent. Tests added. `workers/jobs/daily_pipeline_v2.py`. |
| NEWS-IMPACT-DIR | Store directional news impact as separate signals. Gemini already returns `home_net_impact` and `away_net_impact` (−1.0 to +1.0) but they are never written to `match_signals` — only the combined bet-relative `news_impact_score` is stored. Fix: add `store_match_signal(match_id, "news_impact_home", home_net_impact, ...)` and `"news_impact_away"` in `news_checker.py` after Gemini parse. Zero extra Gemini cost. Enables match_feature_vectors and meta-model to distinguish "bad news for home team" from "bad news for away team". | 1h | ✅ Done 2026-05-07 | ✅ Ready | Two new `store_match_signal` calls added in `news_checker.py` after existing `news_impact_score` write. `news_impact_home` and `news_impact_away` now stored per match. Test added. `workers/jobs/news_checker.py:322-327`. |
| MGR-CHANGE | New manager signal. Add `manager_change_home_days` and `manager_change_away_days` to match_signals — number of days since either team's manager changed (NULL = no change in last 90 days). Source: AF `/coaches` endpoint. Known market inefficiency: post-sacking home bounce ~+8% win rate above expectation in first 3 games (both in industry literature and confirmed by 2 of 5 AI reviewers). Converse: away form collapse under caretaker. Add to enrichment job, cache coach history in a `team_coaches` table or similar. | 3-4h | ✅ Done 2026-05-07 | ✅ Ready | Migration 064 (`team_coaches` table). `get_coaches()`/`parse_coaches()` in `api_football.py` (AF endpoint is `/coachs`). `store_team_coaches()` in `supabase_client.py`. `fetch_coaches()` in `fetch_enrichment.py` — skips teams fetched within 48h. Signal block 3c in `batch_write_morning_signals()` loads current coach start date per team and writes `manager_change_home/away_days` when ≤ 90 days. `coaches` added to `ALL_COMPONENTS`. 4 smoke tests added. |
| PIPE-ALERT | **Merge target for `SYNTHETIC-LIVENESS` (P2 in Reliability Hardening section above).** Automated pipeline anomaly alerting. | 3-4h | ✅ Done 2026-05-08 | ✅ Ready | `workers/jobs/health_alerts.py`. 4 checks via Resend email to `ADMIN_ALERT_EMAIL`. Wired into scheduler: 09:35 morning, hourly 10-22 snapshot, 21:30 settlement. In-memory dedup prevents repeat alerts per day. |
| BM-FILTER | Bookmaker availability filter on value bets page. Users in different countries have access to different bookmakers — showing a Betano pick to a UK user who can't use Betano creates frustration and churn. Add `preferred_bookmakers` text[] column to `profiles` (migration NNN). Profile page: checkbox list of the 13 bookmakers. Value bets page respects filter: only shows picks where `bookmaker = ANY(preferred_bookmakers)`. Default = show all (no change for users who haven't set preferences). | 3-4h | ⬜ | ✅ Ready | Frontend: `src/app/(app)/value-bets/page.tsx` + `src/lib/engine-data.ts`. Backend: migration + profile update API. |
| BOT-PUBLIC-PERF | Public bot performance page. `bot_aggressive` is at +93 units (paper trading) and is the strongest conversion asset in the product — currently visible only at `/admin/bots` (superadmin). Build a public `/performance` page (free tier) showing paper trading results clearly labeled: daily bets settled, cumulative units chart, hit rate, CLV context. Include "paper trading — not real money" disclaimer. Replaces the need for social proof via Reddit posts alone. | half day | ✅ Done 2026-05-08 | ✅ Ready | Replaced `/track-record` with `/performance` (redirect from old URL). 4-tier gated: Free=hero stats+bot leaderboard(≥10 settled)+last 10 bets+CLV education; Pro=all 16 bots+W/L+P&L+bankroll chart modals+full 500-bet history with filters+CLV direction arrows; Elite=exact CLV %+stake sizes+closing odds+current bankroll per bot; Superadmin=all Elite. CLV is hero metric throughout. Sanitization server-side in page.tsx — client never receives gated data. Engine-data.ts: exported `getDashboardCache`, added `getRecentSettledBets`. Nav updated: "Track Record" → "Performance". TypeScript clean. |
| PERF-GRAPH-START | Both performance graphs prepended a synthetic origin point. **`/performance` bot bankroll modal** (`performance-leaderboard.tsx:buildChartData`): now starts with `{idx:0, bankroll:1000, date:'Start', result:'origin'}`, dot renderer adds a gray case for `result==='origin'`, and the tooltip's `chartData[Number(label) - 1]` lookup was replaced with a `find(x => x.idx === idx)` so origin tooltips show "Starting bankroll" instead of falling through to the wrong row. **Elite `/bankroll` chart** (`engine-data.ts:getUserBankrollData`): `cumulativeSeries.unshift({ date: <day-before-first>, units: 0 })` so the cumulative-units line visibly starts at 0u — without it the first dot was the first bet's delta with no reference. Pure frontend, no DB change. Smoke test PERF-GRAPH-START guards both prepends + that the broken index-1 tooltip lookup stays removed. | 30m | ✅ Done 2026-05-10 | ✅ Ready | The bot modal's Y-axis already had `Math.min(..., 1000)` / `Math.max(..., 1000)` fallbacks so 1000 was already in the domain — the only thing missing was an actual data point at 1000, which is what the origin provides. |
| ODDS-TIMING-OPT | For every match we bet on, pull the full `odds_snapshots` history and find the odds at each time-bucket before kickoff: 24h+, 10h, 5h, 3h, 1h, 30min. For each settled bet compare the odds we got vs the best odds available in each bucket. Answer: at what window before kickoff were odds most favorable on average? Script outputs a table: `hours_before_ko | avg_best_odds | avg_bet_odds | pct_captures_peak | sample_n` per market (1X2 home/draw/away, O/U 2.5). If there's a clear window (e.g. odds peak 3-5h before KO), shift the betting pipeline's primary run to that window. Data: join `simulated_bets` (has `odds_at_pick`, `created_at`, `match_id`) → `matches` (kickoff) → `odds_snapshots` (full odds history). Filter to bets with `created_at >= 2026-05-06` for clean pipeline era. | 2h | ⬜ | ✅ Ready | Output to `scripts/odds_timing_analysis.py` + CSV in `dev/active/`. Don't change pipeline schedule based on a single run — need 200+ bets per market before conclusions are actionable. |

---

## Engagement & Growth — Phase 1 (Launch Sprint — do this week)

> Full strategy in `docs/ENGAGEMENT_PLAYBOOK.md`. Reddit execution plan + post drafts in `docs/REDDIT_LAUNCH.md`. Launch phases + paid ads in `docs/LAUNCH_PLAN.md`. Phase 1 = ship with Reddit launch. Phase 2 = retention (weeks 3-6). Phase 3 = differentiation (months 2-3).

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-3 | Daily AI match previews (top 5-10, Gemini) | 1-2 days | ✅ Done 2026-05-01 | ✅ Ready | `workers/jobs/match_previews.py`. Scheduler 07:00 UTC. `match_previews` table (migration 033). Free sees teaser, Pro/Elite see full 200-word preview. Triple-duty: on-site + email + social. Fixed 2026-05-05: predictions pivot (source=ensemble), odds_snapshots pivot, match_injuries schema. **AI: ~$0.11/mo (10 calls/day, flash)** |
| ENG-4 | Daily email digest via Resend | 2-3 days | ✅ Done 2026-05-05 | ✅ Ready | `workers/jobs/email_digest.py`. Scheduler 07:30 UTC. `email_digest_log` (migration 034). Free: teasers + CTA. Pro: + bet count. Elite: + full picks table. Branded HTML: dark `#0a0a14` header, ODDS white + INTEL green logo, green CTAs/badges. Fixed 2026-05-05: migration 042 backfills `user_notification_settings` for all existing users + trigger wired for new signups (was empty → zero sends). Tested end-to-end. |
| ENG-1 | "X analyzing this match" live counter | 4-6h | ✅ Done 2026-05-04 | ✅ Done | `match_page_views` table (migration 038). `/api/track-page-view` POST route — upserts session_id+match_id, returns 30-min window count. `MatchViewingCounter` client component in match header metadata row. Hidden until 2+ people (no self-only display). |
| ENG-2 | Community vote split display | 4-6h | ✅ Done 2026-05-04 | ✅ Done | `community-vote.tsx` updated: percentages + fill bars always visible when any votes exist. Locks at kickoff (live/finished) with Lock icon + "Locked at kickoff" label. Voting disabled for locked matches. |
| ENG-6 | Bot consensus on match detail ("7/9 models agree: Over 2.5") | 3-4h | ✅ Done 2026-05-03 | ✅ Ready | Data in `simulated_bets`. Zero new data needed. Free: count. Pro: markets. Elite: full breakdown |
| ENG-7 | Public /methodology page | Half day | ✅ Done 2026-05-03 | ✅ Ready | Plain-English model explanation. Trust anchor. Nobody else publishes this |
| ENG-5 | Betting glossary (10-15 SEO pages at /learn/[term]) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | 12 terms at /learn/[term]: EV, CLV, Kelly, value betting, Poisson, xG, BTTS, O/U, odds movement, margin, ELO, bankroll. FAQ schema. /learn index. Glossary nav link. Sitemap updated. |

---

## Engagement & Growth — Phase 2 (Retention Engine, weeks 3-6)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-9 | Personal bet tracker + "Model vs You" dashboard | 3-4 days | ✅ Done 2026-05-05 | ✅ Done | my-picks: ROI%, units, W/L stats. Model vs You card after 5+ settled picks. Model prob + agree/disagree icon per row. Share button (native share API + clipboard fallback + OG image). |
| ENG-11 | "What Changed Today" widget on matches page | 1 day | ✅ Done 2026-05-05 | ✅ Done | `getWhatChangedToday()` in engine-data.ts: compares last 8h signals vs 20-32h ago, top 5 by abs delta. `what-changed-today.tsx` component: links to matches, free sees magnitude dot, Pro sees exact delta. |
| ENG-12 | Model vs Market vs Users triangulation | 4-6h | ✅ Done 2026-05-05 | ✅ Done | `getModelMarketUsers(matchId)` queries ensemble 1x2_home prediction + implied_prob + match_votes. `model-market-users.tsx`: 3 colored bars + tension text when model/market gap >5pp. On every match detail page. |
| ENG-13 | Shareable pick cards (branded image generation) | 1-2 days | ✅ Done 2026-05-05 | ✅ Done | `/api/og/pick` route: Next.js ImageResponse, accepts home/away/selection/odds/model_prob/result as query params. Share button on my-picks uses native Web Share API, falls back to clipboard. |
| ENG-14 | Auto-generated prediction pages for SEO (/predictions/[league]/[week]) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | `/predictions` index + `/predictions/[league]` pages. 8 featured leagues. Prob bars, model call badges, preview teasers, FAQ schema. "Predictions" nav link added. Sitemap updated. |
| ENG-8 | Watchlist signal alerts (email/push) | 3-4 days | ✅ Done 2026-05-05 | ✅ Done | `workers/jobs/watchlist_alerts.py`. Scheduler 08:30/14:30/20:30 UTC. Migration 045: `watchlist_alerts_enabled` + `watchlist_alert_log`. Free: kickoff reminder ≤2h before KO. Pro/Elite: odds movement ≥5% alert (6h lookback). Profile page toggle for all 3 notification types (daily digest, weekly report, watchlist alerts). |
| ENG-10 | Weekly performance email (Monday 08:00 UTC) | 1 day | ✅ Done 2026-05-05 | ✅ Done | `workers/jobs/weekly_digest.py`. Scheduler Monday 08:00 UTC. `weekly_digest_log` table (migration 043). Model W/L/units + user's picks + upcoming top matches. Uses `weekly_report` column (default true). Free: model stats + CTA. Pro/Elite: + personal pick stats + CLV. |

---

## Engagement & Growth — Phase 3 (Differentiation, months 2-3)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-15 | Market inefficiency index per league (rolling 30-day edge) | 1 day | ⬜ | ⏳ ~June (needs 30 days of data) | "Eredivisie: HIGH +4.8%. Premier League: LOW +1.2%." No competitor does this |
| ENG-17 | Season-end "Year in Review" (personal, shareable) | 2-3 days | ⬜ | ⏳ ~Aug+ (needs full season of user data) | Strava-style. "312 bets, best month October." Viral potential |

---

## Railway Migration — LIVE-INFRA (all done ✅)

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| LIVE-INFRA | Full migration: GH Actions → Railway scheduler + direct SQL + tiered live polling | ✅ | All 5 phases complete 2026-05-01 |
| RAIL-1 | `workers/scheduler.py` (APScheduler + health endpoint) | ✅ | 21 jobs. Health on :8080 |
| RAIL-2 | Extract `run_*()` from all job scripts | ✅ | main() kept as CLI wrapper |
| RAIL-3 | API budget tracker in `api_football.py` | ✅ | BudgetTracker class, thread-safe |
| RAIL-4 | Dockerfile + railway.toml + .dockerignore | ✅ | Python 3.12-slim, TZ=UTC |
| RAIL-5 | Deploy + validate (shadow mode) | ✅ | Superseded — went straight live |
| RAIL-6 | Disable GH Actions crons | ✅ | schedule: commented in 7 workflows. backfill.yml kept |
| RAIL-7 | `workers/api_clients/db.py` (psycopg2 pool) | ✅ | ThreadedConnectionPool 2-20, wait-on-saturation (DB_POOL_WAIT_TIMEOUT=60s), bulk_insert/upsert |
| RAIL-8 | Live tracker DB functions → direct SQL | ✅ | 6 functions in db.py. Batched writes, no 1K limit |
| RAIL-9 | `workers/live_poller.py` (tiered 30s/60s/5min) | ✅ | LivePoller class, budget-aware |
| RAIL-10 | Decompose `live_tracker.py` into sub-functions | ✅ | fetch_live_bulk/stats/events/build_snapshot |
| RAIL-11 | Smart polling: priority tiers + event-triggered snapshots | ✅ | HIGH priority (active bets) = 30s stats. Goal → extra odds snapshot |
| RAIL-12 | Full doc sweep aligned with Railway | ✅ | 8 .md files updated |
| RAIL-13 | Instant settlement on FT + score sync fix | ✅ | finish_match_sql on FT detection. UTC rollover fix. 23:30 safety net |

---

## Frontend UX — All Done ✅

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| LP-0 / A-1/2/3/4 | Landing page rewrite + profile page redesign | ✅ | |
| B-1/2/3/4/5/6 | Track record public, confidence filter, /how-it-works | ✅ | |
| C-1 to C-6 | Match page tooltips, odds header, value bets gate, login modal | ✅ | |
| F5 | Value bets page: Free=teaser, Pro=directional, Elite=full | ✅ | |
| BET-EXPLAIN | Natural language bet explanations (Gemini, Elite-gated) | ✅ | GET /api/bet-explain. **AI: ~$0.02/mo (flash-lite, cached after 1st call per bet)** |
| SUX-4/5/6/7/8/9/10 | Signal summary, accordion, labels, hooks, timeline, delta, post-match reveal | ✅ | |
| SUX-11/12 | "Why This Pick" Elite card + CLV tracker | ✅ | |
| ML-1/2/3/4/5/6/7/8 | Logos, live timer, form strip, match filter tabs, predicted score, odds arrows, BM badge, match star | ✅ | |
| FE-FAV-1/2/3 | My Leagues bug fix + league ordering + per-match star | ✅ | |
| FE-BUG-1/2 / FE-AUDIT | Pro CTA bug, select dropdown bug, full tier gating audit | ✅ | |
| PIPE-2 / XGB-FIX / POISSON-FIX / DRAW-FIX | Pipeline cleanup + model fixes | ✅ | XGBoost retrained on 95K rows, joblib loader |
| LAUNCH-BETA / LAUNCH-PICK | Beta label, daily pick visible without login | ✅ | |
| AF-EVAL | AF Ultra confirmed required — do NOT downgrade (live polling needs 18K-45K calls/day) | ✅ | |
| KAMBI-BUG-1 | Duplicate value bets when Kambi league name ≠ AF name — added Bulgaria PFL 1 mapping + improved frontend dedup to normalise club prefixes (FK/FC/etc) and key on kickoff date | ✅ Done 2026-05-06 | |
| KAMBI-DROP | Drop Kambi entirely — empirical analysis showed "ub"=Unibet (AF has it), "paf"/"kambi"=36 rows/30 days. Removed scraper from pipeline, cleaned 20 league/50 team/7 fixture dupes via migration 047. Full cleanup 2026-05-06: deleted `kambi_odds.py`, `kambi_odds_value.py`, `detect_duplicates.py`, removed `fetch_kambi_odds()` from fetch_odds.py, removed `KAMBI_TO_AF_LEAGUE` mapping, renamed team_names.py refs. Cleaned 37 more duplicates from 23h deploy gap. | ✅ Done 2026-05-06 | |
| SETTLE-FIX | Settlement `KeyError: 'odds'` — `bet["odds"]` → `bet["odds_at_pick"]` in settlement.py:1034. Was crashing settle_ready every 15 min, blocking 158 matches from settling. | ✅ Done 2026-05-06 | |
| LIVE-ODDS-PARSE | `parse_live_odds()` returned 0 fixtures — AF sends "Fulltime Result" not "Match Winner", and O/U uses `value="Over"` + `handicap="2.5"` (not `"Over 2.5"` combined). Inplay bot has never had real live odds data. Fixed both parsers. 2 regression tests added to smoke_test.py. | ✅ Done 2026-05-07 | `workers/api_clients/api_football.py:parse_live_odds` |
| SENTRY-CRON | Sentry cron monitors not registering — `grace_period_minutes` → `checkin_margin` (correct sentry-sdk 2.x key). | ✅ Done 2026-05-06 → Reverted: Sentry removed from engine 2026-05-06 (free tier budget exceeded, Railway logs sufficient) | |
| RAIL-AUTODEPLOY | Railway auto-deploy from GitHub — connected repo in Settings → Source, main branch, Wait for CI off. Previously required manual `railway up`. | ✅ Done 2026-05-06 | |

---

## ADMIN-OPS-DASH — Operational Health Dashboard ✅ Done 2026-05-07

> Full spec and implementation in git history. Task complete.

### Goal

A `/admin/ops` page (superadmin-only) that answers "Is today's pipeline healthy?" in 3 seconds. Opens instantly — all heavy counts are pre-computed and stored in `ops_snapshots`; page does a single SELECT. Live panels (pipeline job grid, stale bets, last snapshot age) do lightweight point queries on small tables.

---

### Architecture

**`ops_snapshots` table — append-only, one row per snapshot.**

Written by `write_ops_snapshot()` which is called:
1. At the **end of each major job**: `run_fixtures`, `run_odds`, `run_betting`, `run_morning`, `run_settlement`, `run_enrichment` — numbers refresh as work happens
2. **Fallback cron every 60 min** — covers idle hours / weekends

Each write is a **full recompute of all counters for today (UTC date)**. Not a delta. Dashboard reads `WHERE snapshot_date = today ORDER BY created_at DESC LIMIT 1`.

Append-only avoids write races when jobs overlap. Also gives 7-day history for sparklines: `DISTINCT ON (snapshot_date) ORDER BY snapshot_date, created_at DESC`.

**Three panels use live queries (not pre-computed)** — they're cheap and must be real-time:
- Pipeline job grid → `DISTINCT ON (job_name) FROM pipeline_runs WHERE started_at > now() - interval '26 hours'`
- Stale pending bets → `JOIN simulated_bets + matches WHERE result='pending' AND status='finished'`
- LivePoller last snapshot age → `MAX(created_at) FROM live_match_snapshots`

---

### `ops_snapshots` schema (42 columns)

```sql
CREATE TABLE ops_snapshots (
  id            SERIAL PRIMARY KEY,
  snapshot_date DATE NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT now(),

  -- ① Fixtures & coverage (funnel top)
  matches_today            INT,  -- matches with kickoff on snapshot_date
  matches_with_odds        INT,  -- matches with ≥1 odds snapshot today
  matches_with_pinnacle    INT,  -- matches with Pinnacle odds today
  matches_with_predictions INT,  -- matches with source='af' prediction today
  matches_with_signals     INT,  -- matches with ≥1 signal today
  matches_with_fvectors    INT,  -- matches in match_feature_vectors today
  matches_missing_grade    INT,  -- matches where grade IS NULL and status != 'postponed'
  matches_postponed_today  INT,  -- informational

  -- ② Odds pipeline
  odds_snapshots_today  INT,  -- total rows in odds_snapshots today
  distinct_bookmakers   INT,  -- should be 13; drop = odds job half-dead
  matches_without_pinnacle INT, -- has odds but no Pinnacle specifically

  -- ③ Betting & bots
  bets_placed_today   INT,          -- simulated_bets created today (all bots)
  bets_pending        INT,          -- result='pending' right now (all time)
  bets_settled_today  INT,          -- settled today
  pnl_today           NUMERIC(8,2), -- sum pnl on bets settled today
  bets_inplay_today   INT,          -- from bot_id LIKE 'bot_inplay%'
  active_bots         INT,          -- distinct bot_id with ≥1 bet today
  silent_bots         INT,          -- bots with 0 bets today (out of 17 expected)
  duplicate_bets      INT,          -- (bot_id, match_id, market, selection) with count >1

  -- ④ Live / in-play
  live_snapshots_today     INT,  -- live_match_snapshots rows today
  snapshots_with_xg        INT,  -- home_xg IS NOT NULL
  snapshots_with_live_odds INT,  -- ou_over_25_odds IS NOT NULL (fixed 2026-05-07)

  -- ⑤ Post-match / settlement
  matches_finished_today INT,
  bets_settled_today_v2  INT,   -- alias — use bets_settled_today above
  post_mortem_ran_today  BOOL,  -- model_evaluations market='post_mortem' for today
  feature_vectors_today  INT,   -- match_feature_vectors rows built today (captured_at)
  elo_updates_today      INT,   -- team_elo_daily rows updated today

  -- ⑥ Enrichment quality
  matches_with_h2h      INT,  -- distinct match_id in match_h2h for today's matches
  matches_with_injuries INT,  -- distinct match_id in match_injuries today
  matches_with_lineups  INT,  -- via JOIN on matches.kickoff_time::date (not lineups.created_at)

  -- ⑦ Email & alerts
  digests_sent_today        INT,
  value_bet_alerts_today    INT,  -- from value_bet_alert_log today
  previews_generated_today  INT,  -- from match_previews today
  news_checker_errors_today INT,  -- pipeline_runs WHERE job_name='news_checker' AND status='error'
  watchlist_alerts_today    INT,

  -- ⑧ Backfill
  backfill_total_done INT,       -- COUNT(DISTINCT match_id) FROM match_stats (all time)
  backfill_last_run   TIMESTAMPTZ, -- MAX(started_at) FROM pipeline_runs WHERE job_name='hist_backfill'

  -- ⑨ API budget (NULL until Phase 3 persists BudgetTracker)
  af_calls_today      INT,  -- NULL until Phase 3 — BudgetTracker is in-memory only
  af_budget_remaining INT,  -- 75000 - af_calls_today, NULL until Phase 3

  -- ⑩ Users
  total_users      INT,
  pro_users        INT,
  elite_users      INT,
  new_signups_today INT
);
```

---

### Dashboard layout (`/admin/ops`)

**Top strip — always visible, aggressively colored:**

| KPI | Green | Yellow | Red |
|-----|-------|--------|-----|
| Matches Today | ≥ 50 | 10–49 | < 10 |
| Prediction Coverage % | ≥ 90% | 70–89% | < 70% |
| Bookmakers | 13 | 5–12 | < 5 |
| Bets Today | ≥ 20 | 5–19 | 0 |
| Silent Bots | 0 | 1–2 | ≥ 3 |
| Stale Pending | 0 | 1–3 | > 3 |
| Last Snapshot | < 10 min | 10–20 min | > 20 min |
| AF Budget Used | < 70% | 70–90% | > 90% |

**"Currently Broken" feed** (auto-generated, appears only when non-empty): Scans all alert conditions and lists plain-English failures — "17 matches missing odds", "3 bots silent", "LivePoller stale 8 min", "Settlement lag 212 min". This is the operational inbox.

**9 panels below the strip:**

**1. Pipeline Job Health** (live query — `pipeline_runs`)
Table: job_name | last run | status (green/yellow/red) | duration | rows_affected | error (truncated 80 chars). One row per job (DISTINCT ON). Jobs: fixtures, enrichment (×2), odds, betting (×6/day), settlement, news_checker, match_previews, email_digest, live_poller (derived from snapshot age, not a pipeline_runs row).

Alert: Red if `status='error'`. Yellow if `rows_affected=0` on jobs expected to produce output. Red if `finished_at IS NULL` and started > 30 min ago (stuck).

**2. Data Funnel** (from ops_snapshots)
Horizontal waterfall: `matches_today → matches_with_odds → matches_with_predictions → matches_with_signals → matches_with_fvectors → bets_placed_today`. Each bar shows count + % of step above. The step with the biggest drop is highlighted.

Alert thresholds:
- `matches_with_odds / matches_today` < 80% = yellow, < 50% = red
- `distinct_bookmakers` < 8 = yellow, < 5 = red (should be 13)
- `bets_placed_today` = 0 when matches_today ≥ 10 = red

**3. Bot Health** (live query — `simulated_bets`)
Top numbers: bets_placed_today (large), active_bots / 17, bets_inplay_today.
Table: one row per bot — bot_id | bets today | pending | won | lost | avg_stake.
Red row if bot has 0 bets and matches_today ≥ 10. `duplicate_bets > 0` = always red.

**4. Live Tracker Health** (live + ops_snapshots)
Large: "Last snapshot: X min ago" (live from MAX(live_match_snapshots.created_at)).
Sparkline: snapshots/hour over last 12 hours.
Cards: live_matches_now | snapshots_with_xg % | snapshots_with_live_odds % (post 2026-05-07 fix).

Alert: Last snapshot > 20 min AND live_matches > 0 = red.

**5. Settlement Health** (live + ops_snapshots)
Large red badge if stale_pending > 0: "X bets stuck — match finished but not settled". Settlement run times today (from pipeline_runs). P&L today: won/lost/pending breakdown. ELO update today: green tick / red X.

Alert: stale_pending > 5 = red. Settlement never ran today after 22:00 UTC = red.

**6. Data Quality** (from ops_snapshots)
Quality scorecard — each row is a check with a count. Zero = healthy, any non-zero highlighted:

| Check | Yellow | Red |
|-------|--------|-----|
| matches_missing_grade | > 5 | > 20 |
| matches_with_0_signals | > 10 | > 30 |
| matches_without_pinnacle | > 20% of odds matches | > 50% |
| duplicate_bets | — | ≥ 1 (always red) |
| news_checker_errors_today | ≥ 1 | ≥ 3 |

**7. API Budget** (from ops_snapshots)
Progress bar 0 → 75K, green/yellow/red zones. af_calls_today | af_budget_remaining | estimated Gemini cost today ($).
**Note: shows NULL until Phase 3** — BudgetTracker is in-memory, resets on Railway restart.

**8. Email & Alerts** (from ops_snapshots)
Cards: digests_sent_today | value_bet_alerts_today | previews_generated_today | watchlist_alerts_today | news_checker_errors_today. Zero digests after 09:00 UTC with users > 0 = red.

**9. 7-Day Sparklines** (from ops_snapshots WHERE snapshot_date >= today - 7)
8 mini Recharts LineCharts (no axes, just line + today value large): matches_today | distinct_bookmakers | bets_placed_today | matches_with_signals/matches_today ratio | live_snapshots_today | bets_settled_today | af_calls_today | new_signups_today.
Yellow warning if today's value < 7-day average × 0.60.

---

### The 9 numbers that catch 80% of bugs

| # | Number | Red threshold | What it catches |
|---|--------|---------------|-----------------|
| 1 | Last betting job rows_affected | 0 on a day with ≥10 matches | Silent betting failure |
| 2 | distinct_bookmakers | < 5 | Odds pipeline dead |
| 3 | active_bots | < 10 on busy day | Gate logic misfiring |
| 4 | Last snapshot age | > 20 min | LivePoller dead |
| 5 | stale_pending | > 0 | Settlement broken |
| 6 | matches_missing_grade | > 20 | Signal pipeline broken |
| 7 | af_budget_remaining | < 5,000 | Quota breach today |
| 8 | digests_sent_today | 0 after 09:00 UTC | Resend/scheduler failure |
| 9 | bets_placed_today 7d sparkline | < avg × 0.60 | Model edge eroding |

---

### Implementation phases

**Phase 1 — Schema + writer (engine, ~4h)**
- Migration NNN: `CREATE TABLE ops_snapshots` (schema above, skip af_calls_today/af_budget_remaining — leave NULL)
- `write_ops_snapshot()` in `supabase_client.py` — runs all count queries, writes one row
- Call at end of: `run_fixtures`, `run_odds`, `run_betting`, `run_morning`, `run_settlement`, `run_enrichment`
- Scheduler: fallback cron every 60 min

**Phase 2 — Dashboard (frontend, ~4h)**
- `getOpsSnapshot()` in `engine-data.ts` — single SELECT latest row
- `getOpsSnapshotHistory()` — 7-day history for sparklines
- `getPipelineJobsToday()` — live DISTINCT ON from pipeline_runs (no pre-compute needed)
- `getStalepeningBets()` — live join simulated_bets + matches
- `getLastSnapshotAge()` — live MAX from live_match_snapshots
- `/admin/ops/page.tsx` — server component, superadmin-gated

**Phase 3 — AF budget persistence (~2h, independent)**
- `BudgetTracker` currently in-memory only → resets on Railway restart
- Add `write_budget_log(calls_made)` after each AF job → persists to `api_budget_log (date, job_name, calls, created_at)` or directly into `ops_snapshots.af_calls_today`
- Until Phase 3: column shows NULL with a tooltip "Requires Phase 3"

---

### Implementation notes

1. **BudgetTracker is memory-only** — af_calls_today will be NULL after every restart until Phase 3. Do not show misleading zeros; show NULL / "—".
2. **Lineups date filter** — `matches_with_lineups` must JOIN via `matches.kickoff_time::date`, not `lineups.created_at` (lineups fetched pre-kickoff may land on prior UTC date for early fixtures).
3. **LivePoller has no pipeline_runs rows** — it's a daemon. Derive "last snapshot age" from `MAX(live_match_snapshots.created_at)` directly. No new heartbeat writes needed.
4. **Fallback cron frequency** — 60 min. 30 min gives ~1,000 rows/year vs ~500 for marginal benefit.
5. **bets_settled_today_v2 column** — remove the duplicate before migration; keep only `bets_settled_today`.

---

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ADMIN-OPS-DASH | `ops_snapshots` table + `write_ops_snapshot()` writer + `/admin/ops` dashboard | 1.5 days | ✅ Done 2026-05-07 | ✅ Ready | All 3 phases complete. Engine hooks, migration 059+060, /admin/ops frontend with 10 panels. |

---

## Signal Improvements — 4-AI External Review (2026-05-07)

> Sourced from 4-model AI review of MODEL_WHITEPAPER.md + SIGNALS.md. Ordered by effort and when unblocked.
> Full synthesis in conversation history (2026-05-07). Items below are all net-new tasks; items already in other sections are cross-referenced.

### Group 1 — Quick wins (do now, data already exists, no new API calls)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| DOUBTFUL-SIGNAL | Wire `players_doubtful_home/away` from `match_injuries`. | 30 min | ✅ Done 2026-05-07 | ✅ Done | Block 5. Captures "Doubtful" + "Questionable" statuses. Rendered in `signal-accordion.tsx`. |
| SHARP-DRAW-AWAY | Add `sharp_consensus_draw` and `sharp_consensus_away`. | 1h | ✅ Done 2026-05-07 | ✅ Done | New block 3a — DISTINCT ON per selection. All 3 selections rendered in accordion. |
| LEAGUE-GOALS-DIST | Add `league_over25_pct` and `league_btts_pct`. | 1h | ✅ Done 2026-05-07 | ✅ Done | Added to block 11 from same 200-match window. Rendered in accordion. |
| H2H-GATE | Apply `LEAST(n/10, 1.0)` gate to h2h_win_pct, h2h_avg_goal_diff, h2h_recency_premium. | 30 min | ✅ Done 2026-05-07 | ✅ Done | Blocks 2 + 2b. Unit test in smoke_test.py. |
| INJURY-UNCERTAINTY | Add `injury_uncertainty_home/away` = doubtful player count. | 30 min | ✅ Done 2026-05-07 | ✅ Done | Block 5 alongside DOUBTFUL-SIGNAL. Rendered in accordion. |
| ODDS-VOL-AUDIT | Audit `odds_volatility` for lookahead leakage. | 30 min | ✅ Done 2026-05-07 | ✅ Done | **Audit result: CLEAN.** `is_live=false` filter prevents post-kickoff contamination. `cutoff_24h=now−24h` is always past-pointing. Smoke test guards the filter. |

### Group 2 — Signal refinements (this week, computation changes to existing signals)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| TURF-FAMILIARITY | Add `away_team_turf_games_ytd` companion to `venue_surface_artificial`. The turf edge is a visitor unfamiliarity effect, not a "game is on turf" effect. Two Finnish teams on turf = no edge. An English team visiting a Swedish team on turf in April = real edge. This companion signal (count of away games on artificial turf this season for the away team) transforms the signal from context to actual edge quantification. | 2h | ✅ Done 2026-05-07 | ✅ Ready | `away_team_turf_games_ytd` written in new block 11c. UI label: `turfFamiliarityLabel`. |
| IMPORTANCE-GAMES-REM | Normalize `fixture_importance` by games remaining. Current formula compresses urgency mid-season: 6 points from relegation with 20 games left = background noise; same gap with 5 games = crisis. Fix: `urgency = points_gap / (games_remaining * 3)` — values >1.0 = mathematically dire, 0.7-1.0 = high urgency. Games remaining available from fixtures metadata (round number + total rounds per league). | 1h | ✅ Done 2026-05-07 | ✅ Ready | `fixture_urgency_home/away` + `games_remaining_home/away` added using `played` from `league_standings`. UI label: `fixtureUrgencyLabel`. |
| REST-NONLINEAR | Log-transform or bucket `rest_days_home/away`. The effect is non-linear: 2→3 days rest is massive, 10→11 days is zero. Current linear storage doesn't encode this. Either transform to `log(rest_days + 1)` or use 3 buckets: short (≤3d), normal (4-7d), long (8d+). | 30 min | ✅ Done 2026-05-07 | ✅ Ready | `rest_days_norm_home/away` = `log(rest_days+1)` added alongside raw. UI label: `restDaysNormLabel`. |
| FORM-ELO-RESIDUAL | Add `form_vs_elo_expectation_home/away` residual signal. Instead of raw `form_ppg`, compute how much the team is over/underperforming what their ELO rating predicts. Strips out baseline quality already priced by the market. A bad team on a hot streak and a good team playing normally are conflated by raw form_ppg; the residual separates them. 3/4 AIs recommended this. | 2h | ✅ Done 2026-05-07 | ✅ Ready | `form_vs_elo_expectation_home/away` = `ppg - (3*p_win + 0.27)`. UI label: `formVsEloLabel`. |

### Group 3 — New signals (next 1-2 weeks, new queries but no new API endpoints)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| LEAGUE-ELO-VAR | Add `league_elo_variance` — std dev of ELO ratings within the league. High-variance league (ELO range 400+): favorites reliable, upsets rare, draws uncommon. Low-variance (parity, range <200): home advantage is dominant factor. Helps calibrate how much weight to place on `elo_diff` per league. Computable from ELO table filtered by league. 3/4 AIs recommended this. | 1h | ✅ Done 2026-05-08 | ✅ Ready | Block 6b in `batch_write_morning_signals`. Groups ELO ratings by league from today's matches → stdev + range. Also emits `league_elo_range`. Needs ≥4 teams with ELO data to compute. |
| LEAGUE-SEASON-PHASE | Add `league_season_phase` — `games_played / total_games_in_season` normalized 0.0 (start) → 1.0 (final round). Draw rates, home win rates, and result predictability are non-stationary: early season = high uncertainty (new signings, fitness), mid = most predictable, late = urgency volatility. One field addition using fixtures metadata (round number). | 1h | ⬜ | ✅ Ready | `total_games` from league config or inferred from max round seen per league per season. |
| LEAGUE-DRAW-YTD | Add `league_draw_ytd` — season-specific draw rate for the current season only (faster-adapting than 200-match rolling `league_draw_pct` which spans multiple seasons). Some seasons have anomalously high/low draw rates. More relevant for BTTS/O/U bots where base rate matters. | 1h | ⬜ | ✅ Ready | Filter existing `league_draw_pct` query to `season = current_season` only. Run alongside other league-meta signals. |
| BOOKMAKER-COUNT | Add `bookmaker_count_active` — count of bookmakers with non-null odds for each match in the latest odds snapshot. Low count = thin market = inefficiency persists longer. Directly computable from `odds_snapshots`. Acts as liquidity proxy without any external data. 2/4 AIs flagged this. | 1h | ✅ Done 2026-05-08 | ✅ Ready | One line in block 3 of `batch_write_morning_signals` — reuses existing `seen_bm` dict built for bookmaker disagreement. Zero extra DB queries. |
| LINE-VELOCITY | Add line movement velocity and shape features. Not just how much Pinnacle moved, but how fast and whether it reversed. Fast early move = sharp positioning; slow drift = retail noise; reversal = conflicting information. Computable from existing timestamped `odds_snapshots`. 1/4 AIs flagged this as potentially top-3 Stage 3 feature family. | 2h | ⬜ | ✅ Ready | Requires multi-snapshot query per match: slope of implied prob over time windows (T-12h to T-6h, T-6h to T-2h, reversal detection). |
| LEAGUE-CLV-EFFICIENCY | Add `league_clv_efficiency` — historical average pseudo-CLV beatability per league, computed from our own `pseudo_clv` data. Which leagues have we historically beaten closing line in most often? This formalizes the Scotland League Two discovery: some leagues are structurally more beatable. Run weekly, stored as league-level signal. | 2h | ⬜ | ⏳ Need ~60d pseudo_clv data (~May 17+) | GROUP BY league from `matches.pseudo_clv_home/draw/away`. Requires enough data to be meaningful (>20 matches per league). |
| SUSPENSION-SIGNAL | Add `suspension_risk_home/away` from accumulated yellow card counts in `match_events`. A player at 4 yellows in a 5-yellow ban competition is a real pre-match signal. Coach may rest them preemptively (rotation) or play them with risk. Market must price a probability; we can look up the count. | 2h | ⬜ | ⏳ Need `match_events` yellow card counts per player + league ban thresholds config | Requires per-player card accumulation across a season (not just this match). League ban thresholds vary (5 in most, 3 in some cups). |

### Group 3b — Model signals implemented 2026-05-11

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| MODEL-SIGNALS | ✅ Done 2026-05-11 — **4 new model signals wired into match_feature_vectors + FEATURE_COLS.** (1) **`is_opening` flag** on `odds_snapshots` (migration 096): marks first insert per (match, bookmaker, market, selection); `store_odds()` and `fetch_odds.py` bulk path both set it; pruner now preserves `is_opening=true` rows alongside `is_closing`; backfill via `DISTINCT ON` sets existing data. (2) **Weather at kickoff** (migration 097): `match_weather` table now has a real feeder — `workers/jobs/fetch_weather.py` geocodes venues via Open-Meteo (free), fetches hourly forecast for kickoff time, stores `temp_c/wind_kmh/rain_mm/humidity`. `venues` table gets `city/country/lat/lon` so geocoding is cached. `_build_feature_row_batched` loads weather batch and adds `weather_temp_c/wind_kmh/rain_mm/humidity` to MFV. Added to `FEATURE_COLS` in `train.py`. Wired into `fetch_enrichment` `ALL_COMPONENTS` after venues fetch. (3) **Referee stats** — `build_referee_stats()` existed but was never called. Now called from `settlement.py` nightly after post-match enrichment, so `referee_stats` stays current. (4) **Pinnacle coverage** — confirmed 5% coverage, skipped per prior analysis (v11 showed zero net lift). | 3h | ✅ Done 2026-05-11 | ✅ Ready | Weather coverage will start near-zero (only matches with venue_af_id populated AND city geocodeable). Expect 30-50% coverage after 2 weeks of pipeline runs. Model retrain needed to use features — v15 candidate after 2+ weeks of weather data. |

### Group 4 — Stage 3 meta-model prep (when ready to train, ~mid-May)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| META-FEATURE-DESIGN | Finalize Stage 3 feature vector. Cap at 12-15 features (overfitting risk: 3000 rows × 40 features = guaranteed overfit with logistic regression). Final set per 4-AI review: `edge` (ensemble − pinnacle_implied), `odds_at_pick`, `model_disagreement`, `bookmaker_disagreement`, `sharp_consensus_home`, `pinnacle_line_move_home`, `pinnacle_ah_line_move` (cross-market confirmation), `odds_volatility`, `news_impact_score`, `league_tier`, `time_to_kickoff`, `importance_diff` (test), `venue_surface_artificial` (test). Drop all quality proxies (ELO, form, position). | 1h | ⬜ | ⏳ ~mid-May (need 3000+ match_feature_vectors rows) | Document final list in MODEL_WHITEPAPER.md before training. |
| LONGSHOT-GEO-AUDIT | Audit if 0.30-0.40 probability bin failures (42% predicted, 13% actual win rate) are geographically concentrated. If the miscalibration is mostly South American or Eastern European leagues, it may reflect structural home advantage inflation in those regions, not a global model flaw. | 2h | ⬜ | ✅ Ready | Query settled bets JOIN matches WHERE calibrated_prob BETWEEN 0.30 AND 0.40, GROUP BY league/region. May explain what Platt cannot fix. |

### Group 5 — Deferred (need player-level data from AF-PLAYER-RATINGS first)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| CUP-ROTATION | Add `rotation_risk_home/away` flag. When a team has a high-stakes fixture (cup semi, European game) within 72h of this match, they heavily rotate. Current form data treats cup wins against lower opposition the same as league wins. `rotation_risk_home/away` = (fixture within 72h AND competition tier of that fixture > current fixture tier). | 2h | ⬜ | ⏳ Needs fixture calendar with competition tier data | Requires cross-referencing fixtures across competitions per team. AF has competition type on fixtures. |
| GOALKEEPER-SIGNAL | Add `goalkeeper_absence_flag` — binary flag when the starting goalkeeper is absent (confirmed injured or suspended). Goalkeeper absences are massively underpriced in lower leagues, especially: backup keeper starts, youth keeper debut, emergency keeper. 1/4 AIs ranked this the highest-value specific missing signal after player weighting. | 2h | ⬜ | ⏳ Needs AF-PLAYER-RATINGS + lineup data | Requires confirmed starting lineups (T-75min) + GK position identification. When available, flag per side. |

---

## Tier 3 — 1-2 Months

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| HIST-BACKFILL | Historical data backfill (running on Railway) | — | 🔄 | 🔄 Active | Moved from GH Actions → Railway 02:00 UTC daily (2026-05-03). Fully psycopg2, self-stops when `backfill_complete.flag` exists. See § HIST-BACKFILL Plan |
| CODE-SONAR-ENGINE | Fix SonarCloud findings on odds-intel-engine: **C reliability (6 bugs)** + **6.9% duplication** + **4 security hotspots**. After repo cleanup (data/model_results removed): 491 code smells (359 CRITICAL — mostly `plsql:S1192` SQL string duplication in migrations = noise). 6 real bugs: identical sub-expressions (`supabase_client.py:2961`, `daily_pipeline_v2.py:1663/1683`), NULL comparison in migration 010, always-true condition (`improvements.py:629`), float equality (`odds_api.py:208`). Hotspots: Dockerfile root user + recursive COPY, GH Actions secret expansion. Security: A. | 2-3h | ✅ Done 2026-05-07 | ✅ Done | All 6 bugs fixed: NaN idiom `x != x` → `math.isnan(x)` in supabase_client.py + daily_pipeline_v2.py (+ added `import math`); improvements.py:629 guard suppressed with `# NOSONAR` (correct code, SonarCloud data-flow false positive); migration 010 `-- NOSONAR`; odds_api.py float equality → epsilon comparison. 4 hotspots are informational (Dockerfile root user, recursive COPY, GH Actions). Reliability rating should move C → A on next scan. |
| CODE-RUFF | Ruff lint pass: 171→69 issues. Fixed: 51 unused imports, 37 bare f-strings, 4 duplicate dict keys (real bugs in team_names.py + espn_results.py), 8 unused variables, 1 multi-import. Remaining 69: 55 E402 (structural sys.path before imports — correct pattern), 8 E701 (one-line ifs — cosmetic), 3 F841 (archival tennis scripts), 2 E741 (ambiguous names), 1 E702 (semicolon). All benign. | 30min | ✅ Done 2026-05-06 | ✅ Done | 102 issues fixed across 30+ files. |
| CODE-RADON | Structural complexity refactor: 3 god-files (supabase_client.py 3532 lines, daily_pipeline_v2.py 1902 lines, settlement.py 1542 lines). Key F-rated functions: `write_morning_signals` (CC=157), `batch_write_morning_signals` (CC=188), `run_morning` (CC=133), `run_live_tracker` (CC=77). | 3-5 days | ⬜ | ⏳ After SonarCloud + when no active tasks on these files | Do NOT start while active calibration/Pinnacle tasks are touching daily_pipeline_v2.py and supabase_client.py. Approach: split supabase_client into domain modules (signals.py, bets.py, features.py, match.py); extract sub-functions from F-rated pipeline functions. Carry real merge-conflict risk on active files. |
| XGB-HIST | Retrain XGBoost on backfilled data (~43K matches with stats+events) | 1 day | ⬜ | ⏳ After HIST-BACKFILL Phase 1 | Retrain result_1x2 + over_under on full AF features. Current: 96K Kaggle rows (limited features). New: richer per-match stats. **Include Pinnacle implied probability as a training feature** — current Kaggle data has no market context, so model can't distinguish "home win at 1.40" from "home win at 2.20". Adding Pinnacle implied teaches the model to predict residual edge vs market price rather than raw outcome probability. |
| AH-SIGNALS | Asian Handicap line + drift + bookmaker disagreement signals. AF bulk odds already include AH but `parse_fixture_odds` was silently dropping them. Added: AH parsing with `handicap_line` field (migration 066 adds column to odds_snapshots); `pinnacle_ah_line` (home team handicap, e.g. -0.75), `pinnacle_ah_line_move` (drift since first snapshot today), `ah_bookmaker_disagreement` (stdev across books) in batch_write_morning_signals block 3d. Also added `pinnacle_btts_yes_prob` (block 3e, BTTS was stored but never signaled). Data starts collecting on next odds run. | half day | ✅ Done 2026-05-07 | ✅ Done | Migration 066. `parse_fixture_odds` + `fetch_odds.py` (handicap_line column). `batch_write_morning_signals` blocks 3d + 3e. 6 smoke tests. |
| AH-XGBOOST | Add `pinnacle_ah_line`, `pinnacle_ah_line_move`, `ah_bookmaker_disagreement`, `pinnacle_btts_yes_prob` as XGBoost meta-model features. AH data collection starts 2026-05-07 — need ~2 weeks of settled matches (≥ ~200 rows) before these features have enough coverage to be informative. Train alongside meta-model milestone. | 2h | ⬜ | ⏳ ~May 17 (wait for data to accumulate) | Add to feature set in meta-model training script. Validate coverage before including in prod model. |
| AF-PLAYER-RATINGS | Player ratings + per-fixture stats via AF `/players?fixture={id}`. Each played player gets a Sofascore-style float rating (6.0-10.0), minutes, goals, assists, shots, key passes, dribbles, tackles, cards. Use case: (1) `team_avg_rating_home/away` rolling 5-game signal; (2) `squad_rotation_index` (fatigue detection); (3) `key_player_availability` flag; (4) data source for player-level injury weighting. Cache in `player_fixture_stats` table. Fetch post-settlement. AF updates ~30min after FT. 4-AI verdict: useful but needs meta-model consumer — collect from May 17 milestone onward. Medium signal strength (≤2% Brier improvement at team-aggregate level per academic lit). | 1 day | ⬜ | ⏳ Wait for meta-model milestone (~May 17) | AF `/players` endpoint. Post-settlement job. `player_fixture_stats` table (migration NNN). 150–280 calls/day (trivial vs 75K budget). |
| AF-VENUES | Venue surface + capacity signal via AF `/venues?id={venue_id}`. Surface (grass vs artificial turf) documented edge in Scandinavian/Eastern EU leagues (3–5% Brier improvement per Hvattum & Arntzen 2010). Venues cached once — near-zero ongoing API cost. Signal: `venue_surface_artificial` (1.0/0.0). 4-AI verdict: #1 "implement now" (3/4 models — strongest consensus). Done. | 2h | ✅ Done 2026-05-07 | ✅ Done | Migration 065 (venues table + matches.venue_af_id). `fetch_venues()` enrichment component. `venue_surface_artificial` signal in `batch_write_morning_signals()` block 11b. 5 smoke tests. |
| AF-BATCH | Batch fixture enrichment in settlement.py — use `/fixtures?ids=id1-id2-...-idN` (up to 20 per call) to pre-fetch all today's fixtures in bulk before ThreadPoolExecutor per-match enrichment. Reduces settlement API calls from N×3 individual calls to ⌈N/20⌉ batch calls + per-match fallback only on cache miss. | 1h | ✅ Done 2026-05-07 | ✅ Done | `get_fixtures_batch()` added to `api_football.py`. `fetch_post_match_enrichment()` in `settlement.py` pre-fetches batch before ThreadPoolExecutor; each `_enrich_one_match` uses prefetched data with fallback to individual calls. |
| AF-HALF-TIME-SIGNALS | Half-time tendency signals from stored `match_stats` `_ht` columns. `h1_shot_dominance_home/away`: rolling last-5-game avg of (shots_Xside_ht / shots_Xside). Frontend: added shots-on-target, fouls, yellow cards rows to H1 stats section in `match-detail-live.tsx`. Signal labels in `signal-labels.ts`. | 2h | ✅ Done 2026-05-07 | ✅ Done | Signal blocks 13 in `batch_write_morning_signals()`. `engine-data.ts` adds 6 `_ht` fields to `MatchStatsData`. `match-detail-live.tsx` H1 section extended. `signal-labels.ts` + `signal-accordion.tsx` for rendering. |
| AF-SIDELINED | Player career injury history via AF `/sidelined?player={id}`. Different from `/injuries` (fixture-specific) — full career sidelined history. Derived signals: `injury_recurrence_home/away` (avg career injury episodes for confirmed-out players). 7-day caching keeps cost at ~5-20 calls/day. | 3h | ✅ Done 2026-05-07 | ✅ Done | `fetch_player_sidelined()` in `fetch_enrichment.py` (7-day cache, reads from `match_injuries`). Signal block 12 in `batch_write_morning_signals()`. `engine-data.ts` adds `injuryCount` to `MatchInjury`. `match-detail-live.tsx` shows "Nx injury history" badge for players with ≥3 episodes. `signal-accordion.tsx` renders `injury_recurrence_home/away`. `signal-labels.ts`. Migration 068 adds `idx_player_sidelined_count` index. |
| AF-ODDS-MAPPING | ~~Use AF `/odds/mapping` to pre-filter fixtures with odds before polling.~~ CLOSED — already solved: `fetch_odds.py` uses bulk `/odds?date={date}` which only returns fixtures with active odds. No wasted per-fixture calls. 4-AI models flagged this as #1 priority but code review by model 4 confirmed it's already in place. | — | ✅ Done (pre-existing) | ✅ Already solved | `workers/jobs/fetch_odds.py` line 85: `get_odds_by_date(target_date)` is the bulk date endpoint. |
| AF-TRANSFERS | Mid-season squad disruption signal via AF `/transfers?team={id}`. 7-day caching + only fetching today's fixture teams keeps cost at ~5-40 calls/day (far below the 300-560/day AI models estimated, which assumed per-match uncached). Signal: `squad_disruption_home/away` = count of arrivals in last 60 days per team. | 3h | ✅ Done 2026-05-07 | ✅ Done | `fetch_transfers()` in `fetch_enrichment.py` (7-day cache). Signal block 14 in `batch_write_morning_signals()`. `signal-accordion.tsx` renders `squad_disruption_home/away`. `signal-labels.ts`. Migration 068 adds `idx_team_transfers_date_team` index. |
| H2H-SPLITS | Extract perspective-aware signals from h2h_raw JSONB. Added: `h2h_avg_goal_diff` (mean goal diff from home team's perspective — dominance signal), `h2h_recency_premium` (win rate last 3 vs overall — momentum signal). Also fixed latent bug: `home_team_api_id`/`away_team_api_id` were never stored on matches, so MGR-CHANGE block was silently doing nothing. Migration 067 adds both columns; store_match() and pipeline query updated. Backfilled 13,589 historical matches via `scripts/backfill_team_api_ids.py` (568 API calls) — both signals now active on full dataset. | 2h | ✅ Done 2026-05-07 | ✅ Done | Migration 067. store_match() backfill. daily_pipeline_v2.py query. batch_write block 2b. 4 smoke tests. scripts/backfill_team_api_ids.py. |
| INJURY-SEVERITY | Tag injury reason strings from `match_injuries` into severity buckets. Current `injury_count_home/away` treats all injuries equally. Signal: `injury_severity_home/away` (0=none, 1=minor muscle/knock, 2=medium hamstring/thigh, 3=serious ACL/fracture). Also `returning_player_risk` — players returning from >60-day absence underperform for 1–3 games. Low-medium signal strength but unique vs what public models use. Found in data-audit 2026-05-07. | 3h | ⬜ | ⏳ After meta-model (need severity as feature, not just raw count) | `match_injuries` table already has `reason` text field. Tag with regex/keyword rules. |
| B6 | Singapore/South Korea odds source | Unknown | ⬜ | ⏳ Research needed | +27.5% ROI signal, no live odds. AF has Korea K League odds but NOT Singapore. Pinnacle via Odds API ($20/mo) is best path |
| P5.2 | Footiqo: validate Singapore/Scotland ROI with 1xBet closing odds | Manual | ⬜ | ✅ Ready | Independent validation. If ROI holds on 2nd source, it's real |
| P3.1 | Odds drift as XGBoost input feature | 1-2 days | ⬜ | ⏳ ~June (needs more data) | Currently veto filter only. Strongest unused signal once data accumulates |
| P3.3 | Player-level injury weighting (by position/market value) | 2-3 days | ⬜ | ⏳ Low priority | ~90% captured by injury_count + news_impact already |
| S6-P2 | Graduate meta-model to XGBoost + full signal set | 2-3 days | ⬜ | ⏳ After ALN-1 (ALN-1 threshold now met — unblocked once ALN-1 implemented) | After alignment thresholds validated at 300+ quality bets (>= 2026-05-06). ALN-1 data threshold met 2026-05-12 — ETA moves from late June to soon after ALN-1 ships. |
| P4.1 | Audit trail ROI: stats-only vs after-AI vs after-lineups | 1 day | ⬜ | ⏳ Needs data | Proves value of each layer. Needed for Elite pricing rationale |
| P3.5 | Feature importance tracking per league | 1 day | ✅ Done 2026-05-05 | ✅ Done | `scripts/compute_feature_importance.py` + migration 040. Pearson r per (league, signal, market). Run manually or extend Sunday refit. |
| F7 | Stitch redesign (landing + matches page) | Awaiting designs | ⬜ | ⏳ Awaiting designs | Parked until after first users arrive |
| ELITE-BANKROLL | Personal bankroll analytics dashboard (Elite) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | `/bankroll` server page (Elite-gated). `getUserBankrollData()` in engine-data.ts. `bankroll-chart.tsx` (recharts AreaChart). Summary stats (ROI, hit rate, net units, avg CLV, max drawdown). Model benchmark comparison. Per-league breakdown table. Recent picks with CLV. Nav link shown for Elite/superadmin. |
| ELITE-LEAGUE-FILTER | League performance filter for Elite value bets | 1 day | ⬜ | ⏳ After 3mo data | "Show only leagues where model hit rate > 45%". Needs data to be meaningful |
| ELITE-ALERT-STACK | Custom multi-signal alert stacking (Elite) | 2-3 days | ⬜ | ⏳ After ENG-8 | "Alert when confidence > 65% AND edge > 8% AND line moved in model's direction" |

---

## Infrastructure & Platform Optimization

> Identified 2026-05-05 via infra audit — features we're paying for or have for free but not using. Sorted by priority: 🔴 Critical (do ASAP, launch is live) → 🟡 High (this week) → 🟢 Medium.

| ID | Task | Effort | ☑ | Priority | Notes |
|----|------|--------|----|----------|-------|
| INFRA-1 | ~~Stripe free trial (7-day Pro)~~ | 15 min | ✅ Done 2026-05-05, **reverted 2026-05-06** | 🔴 ASAP | Removed — free tier IS the trial. REDDIT promo code handles targeted free months. `allow_promotion_codes=true` kept. |
| INFRA-2 | Stripe promo code for Reddit launch | 5 min | ✅ Done 2026-05-05 | 🔴 ASAP | Code `REDDIT` — 100% off first month (duration=once). Created live via Stripe API. Added as reply to all 3 active Reddit posts (r/buildinpublic + 2 subs). |
| INFRA-3 | Supabase Custom SMTP + Auth email templates | 30 min | ✅ Done 2026-05-05 | 🔴 ASAP | Resend SMTP configured in Supabase Auth (smtp.resend.com:465, noreply@oddsintel.app). Magic link template updated with OddsIntel branding. Auth flow refactored from OTP code → magic link (`signInWithOtp` with `emailRedirectTo`). Server-side PKCE callback (`route.ts`). Unknown email on login auto-redirects to signup with email pre-filled. Supabase Site URL space removed, `https://oddsintel.app/**` wildcard added to redirect URLs. Apple Sign In setup deferred. |
| INFRA-12 | Apple Sign In | 1-2h | ⬜ | ⏳ When ready | Apple Developer account ready. Need: Services ID (`app.oddsintel.web`), Key (.p8 + Key ID + Team ID) → Supabase Auth → Sign In/Providers → Apple. Frontend: add `<AppleSignIn />` button alongside Google/Discord in login, signup, modal. Return URL: `https://jjdmmfpulofyykzwiuqr.supabase.co/auth/v1/callback`. Required if ever shipping iOS app. |
| INFRA-4 | PostHog conversion funnel setup | 1h | ✅ Done 2026-05-05 | ✅ Done | Funnel built in PostHog dashboard (Signup → Match → upgrade_clicked → upgrade_completed). Custom events added to pricing-cards.tsx + profile/page.tsx. upgrade_cancelled also tracked. |
| INFRA-5 | Vercel Speed Insights | 15 min | ✅ Done 2026-05-05 | 🟡 This week | `@vercel/speed-insights` installed. `<SpeedInsights />` added to root layout.tsx. Will auto-report LCP/FID/CLS to Vercel dashboard once deployed. |
| INFRA-6 | Sentry Crons monitoring for Railway jobs | 1h | ✅ Done 2026-05-05 → Reverted 2026-05-06 | ✅ Done | Reverted: Sentry cron monitors exceeded free tier budget. Removed `sentry-sdk` from engine, deleted all monitor/init code. Railway logs + health endpoint are sufficient. Frontend Sentry kept. |
| INFRA-7 | PostHog feature flags for Tips launch | 1h | ⬜ | 🟡 Before M3 | Create `tips_enabled` flag in PostHog. Gate Tips section on this flag instead of hardcoded condition. When bot_aggressive validates → flip flag, no deploy needed. |
| INFRA-8 | Resend webhook → email open/click tracking | 2h | ✅ Done 2026-05-05 | ✅ Done | Migration 041 adds `last_email_opened_at` + `last_email_clicked_at` to profiles. `/api/resend-webhook` route handles `email.opened` + `email.clicked`. Svix signature verification. Webhook created in Resend dashboard. `RESEND_WEBHOOK_SECRET` set in Vercel + local .env.local. |
| INFRA-9 | Vercel Edge Config for feature flags | 2h | ⬜ | 🟢 Week of May 12 | Replace any DB queries used for global on/off flags with Vercel Edge Config (~1ms reads vs ~20ms DB). Good for: tips_enabled, maintenance_mode, featured_match_id. |
| INFRA-10 | Supabase DB Webhooks → watchlist alerts backend | 1 day | ⬜ | 🟢 When building ENG-8 | Instead of building a polling job for ENG-8 (watchlist alerts), use Supabase DB Webhooks: INSERT on match_signals with high injury_impact → fire Next.js API route → send Resend email. Eliminates most of ENG-8 backend complexity. |
| INFRA-11 | Supabase Realtime → replace live polling | 2 days | ✅ Done 2026-05-08 | Migration 076: `live_match_snapshots` + `matches` added to supabase_realtime publication. `match-score-display.tsx` 60s poll → Realtime INSERT. `matches-client.tsx` 60s snapshot poll + 90s router.refresh() → Realtime INSERT/UPDATE. `live-odds-chart.tsx` 5min poll → Realtime-triggered fetch. ENG-1 viewing counter (presence) deferred. |

---

## Tier 4 — 2-3 Months (needs data accumulation)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| SIG-12 | xG overperformance rolling signal | 2h | ⬜ | ⏳ ~2 wks of post-match xG data | Regression to mean signal. Needs post-match xG from live snapshots |
| MOD-2 | Learned Poisson/XGBoost blend weights (replace fixed α) | 2h | ✅ Done 2026-05-05 | ✅ Done | `scripts/fit_blend_weights.py`: optimizes Poisson weight + per-tier shrinkage alpha. improvements.py loads from model_calibration, falls back to hardcoded. Weekly refit added to Sunday settlement. |
| P3.4 | In-play value detection model | 2-3 wks | 🔄 In Progress | ⏳ Phase 1B deployed 2026-05-09 (11 strategies). Phase 2 ML needs 500+ snapshots + 200 settled bets. See § INPLAY Plan for full 5-phase roadmap | Active bots: A, B, C, D, E, G, H (stats-based) + **I (Favourite Stall), J (Goal Debt O1.5), L (Goal Contagion)** (odds+score only — no stats required). F dropped 2026-05-08. All 9 + 3 new bots confirmed by 9-AI analysis (9 replies across 2 prompt rounds). Next: wait for 50+ bets per new strategy before calibration review. |
| P4.2 | A/B bot testing framework | 1-2 days | ⬜ | ⏳ Needs audit trail + data | Parallel bots with/without AI layers |
| P4.3 | Live odds arbitrage detector | 1-2 days | ⬜ | ⏳ ~July | Per-bookmaker odds exist. Low priority |
| RSS-NEWS | RSS news extraction pipeline ($30-90/mo) | 1-2 days | ⬜ | ⏳ After model proves profitable | Targets news before odds adjust. Re-evaluate when Elite has subscribers. **AI: +~$0.30/mo Gemini (data service $30-90/mo is the real cost)** |
| P3.2 | Stacked ensemble meta-learner (when Poisson vs XGBoost) | 1-2 days | ⬜ | ⏳ Needs settled bets with both predictions | Logistic regression on model disagreement |
| OTC-1 | Odds trajectory clustering (DTW) | 1-2 wks | ⬜ | ⏳ 1000+ snapshots | Low priority — volatility+drift captures ~same at 5% effort |

---

## Automation Sequels — Build Alongside Parent Task

> A model task is NOT done until its retraining is automated. Without these, calibration rots as data changes.

| ID | Parent | Task | Effort | ☑ | Ready? | Notes |
|----|--------|------|--------|----|--------|-------|
| PLATT-AUTO | PLATT | Weekly Platt recalibration in settlement | 1h | ✅ | ✅ Done | Sunday step runs `scripts/fit_platt.py` → `model_calibration` table |
| BLEND-AUTO | MOD-2 | Monthly Poisson/XGBoost blend weight recalculation | 1h | ✅ Done 2026-05-05 | ✅ Done | Weekly refit added to Sunday settlement step 5/5 alongside Platt. |
| META-RETRAIN | B-ML3 | Weekly meta-model retraining job | 2h | ⬜ | ⏳ After B-ML3 | Re-run on all `match_feature_vectors` rows, write to `model_versions` |
| XGB-RETRAIN | S6-P2 | Weekly XGBoost full-model retraining | 3-4h | ⬜ | ⏳ After S6-P2 | Train/val split, track feature importances over time |
| ALN-AUTO | ALN-1 | Monthly alignment threshold refresh | 1h | ⬜ | ⏳ After ALN-1 | Bin settled bets by alignment_count → ROI per bin → update thresholds |
| ALN-1-TUNE | ALN-1 | Tune HIGH/MEDIUM bumps in `_ALN_BUMP` based on actual ROI data | 1h | ⬜ | ⏳ When HIGH ≥ 50 settled bets AND MEDIUM ≥ 50 settled bets (currently HIGH=3, MEDIUM=11 — too small). Run `scripts/aln1_analysis.py` to check. If HIGH ROI > MEDIUM ROI > LOW ROI monotonically at that point, lower HIGH bump to -0.005 (accept at 0.5% less edge) and raise LOW bump to 0.015. If the monotonic pattern is absent, keep bumps flat and extend monitoring window. |
| BOT-HIGH-ALIGNMENT | ALN-1 | Launch `bot_high_alignment` — only bets when alignment_class=HIGH | 2h | ⬜ | ⏳ After ALN-1-TUNE (need meaningful HIGH ROI data first; 3 bets is not enough). Gate: HIGH class ≥ 50 settled bets with ROI clearly above MEDIUM. Implementation: add new bot row to `BOT_CONFIGS` in `daily_pipeline_v2.py` with `"selection_filter_alignment": ["HIGH"]` and tighter edge thresholds than existing bots. Will produce fewer bets but should show higher precision — useful as the tips-product candidate. |
| INPLAY-RETRAIN | P3.4 | Quarterly in-play model retraining | 2h | ⬜ | ⏳ After P3.4 | Seasonal — late-season desperation changes how game states map to results |

---

## ML Model Improvements (post-backfill research track)

> Origin: 2026-05-08 brainstorm + 4-AI research review (ML-RESEARCH ✅ Done). Research synthesized from Gemini 1.5 Pro, GPT-4o, Claude Opus, and a 4th model on 2026-05-08. Full prompt at `docs/ML_RESEARCH_PROMPT.md`. Implementation order below reflects consensus ranking. Most improvements can start now; a few are gated on backfill volume.

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ML-RETRAIN-1 | Subsumed by ML-PIPELINE-UNIFY — v10_pre_shadow trained on 47,292 rows 2026-05-10; v14 now production. Weekly retrain cron handles ongoing retrains. | 2h | ✅ Done 2026-05-12 | ✅ Done | Superseded. |
| ML-ELO-GAP | **Add ELO to `FEATURE_COLS` in train.py** — `home_elo`, `away_elo`, `elo_diff`, `home_elo_exp` are computed at inference (`xgboost_ensemble.py:152-157`) but absent from `FEATURE_COLS` in `train.py` so the new AF model never learns from them. Add all 4 to FEATURE_COLS, re-run train.py, compare log_loss. (Hvattum & Arntzen 2010: ELO is most valuable for promoted teams, post-international-break, early season.) | 1h | ✅ Done 2026-05-08 | ✅ Ready | Added `elo_home`, `elo_away`, `elo_diff` to FEATURE_COLS in `workers/model/train.py`. Also fixed all FEATURE_COLS to use `match_feature_vectors` column names (was Kaggle-era names — would have crashed on run). Target column fixed: `result` → `match_outcome`. `load_training_data()` added — just run `python3 workers/model/train.py` when data is ready. Training blocked until enough rows in match_feature_vectors. |
| ML-MISSING-DATA | **Fix aggressive row-dropping** — `X.notna().all(axis=1)` in train.py loses ~30-40% of training data (~5K→7-8K effective rows). **H2H features are the main culprit**: newly promoted teams have no prior meetings. Inference already defaults H2H to neutral (features.py:341-345), so training on a non-H2H subset is a biased sample. Fix: add missingness indicator flags for H2H cols, then fill with per-league-tier mean + global mean fallback. LightGBM native null handling is an alternative. (Saar-Tsechansky & Provost 2007 JMLR: league-mean imputation + indicator flags performs as well as KNN.) | 3h | ✅ Done 2026-05-10 | ✅ Ready | Implemented as ML-PIPELINE-UNIFY Stage 2a/2b: `_impute_features` in `workers/model/train.py` does per-league mean + global fallback + 0-fill, with `<col>_missing` indicators for h2h_win_pct, opening_implied_*, bookmaker_disagreement, referee_*. Smoke test `ML-PIPELINE-UNIFY Stage 2a` guards the rename. |
| ML-NEW-FEATURES | **Add live signals as XGBoost training features** — ELO + Pinnacle odds are high-lift. Manager change days and squad disruption should be **skipped** per R4 lit (Bryson 2011: manager effect priced into odds within 24h, too few post-sacking samples to learn reliably; squad disruption has thin literature and is swamped by team-quality signals). Scope: ELO (via ML-ELO-GAP), Pinnacle (via ML-PINNACLE-FEATURE), sharp consensus (use as continuous feature not binary filter per Forrest & Simmons 2008). | 1 day | ⬜ | ⏳ After ML-ELO-GAP + ML-PINNACLE-FEATURE done first | ELO and Pinnacle are orthogonal improvements that stack. Manager/squad disruption: omit from training features (still useful as live signals for bettors, just not as model inputs). |
| ML-PINNACLE-FEATURE | **Implemented 2026-05-10 — measured zero lift at current coverage.** Added `pinnacle_implied_home/draw/away` to `train.py` via `--include-pinnacle` flag (load_training_data joins per-match latest pre-kickoff Pinnacle 1X2 from `odds_snapshots`, MFV's `market_implied_*` is multi-bookmaker consensus and not Pinnacle-specific). Trained as `v11_pinnacle`. Pinnacle pre-match 1X2 covers 2,370 / 47,292 (~5%) of finished matches in the DB. CV stats vs v10: identical (1X2 log_loss 0.7576 vs 0.7578, BTTS acc 52.5% vs 52.6%, etc.). Feature importance: `pinnacle_implied_*_missing` indicators get 1.9-2.5% importance, but actual prob values get 0%. **Verdict: don't adopt v11.** The model only learns the missingness pattern, not the values. Revisit when Pinnacle coverage grows past ~30%. Smoke test `ML-PINNACLE-FEATURE — train.py supports --include-pinnacle` guards the feature add; running CV against `v11_pinnacle` is the integration test. | 2h | ✅ Done 2026-05-10 | ✅ Ready (don't adopt) | The CLOSING-vs-OPENING question from R4/R5 is moot at 5% coverage — neither carries enough signal yet. Path forward: improve Pinnacle ingestion coverage first (separate `PINNACLE-COVERAGE-EXPAND`), then re-test the feature. |
| ML-HYPERPARAMS | **Switch to CatBoost or LightGBM + tune hyperparameters** — R5 (Gemini) ranks CatBoost above LightGBM for small datasets: its ordered boosting reduces overfitting and it handles league/team categoricals natively. R4 prefers LightGBM. Both are within 0.2-0.5% log-loss of XGBoost. LightGBM key params: `LGBMClassifier(n_estimators=300, max_depth=6, lr=0.05, num_leaves=31, min_child_samples=20, subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0)`. Do NOT add both LightGBM + CatBoost as stacking inputs — too correlated. Pick one GBDT, pair with structurally different model (LogReg or Poisson). | 3h | ⬜ | ⏳ After ML-RETRAIN-1 | Experiment order: (1) add LightGBM, compare log_loss, (2) add CatBoost, compare. Keep whichever beats XGBoost by >0.5% on TimeSeriesSplit; keep XGBoost if both are within 0.2%. |
| ML-BLEND-DYNAMIC | **Per-tier blend weights shipped 2026-05-10.** `fit_blend_weights.py` extended with a per-tier 1X2 optimisation block: for each `tier ∈ {1,2,3,4}`, filters paired (poisson, xgb) settled rows to that tier and optimises the blend weight, stores as `blend_weight_1x2_t{tier}` in `model_calibration`. `xgboost_ensemble.load_blend_weight(tier=N)` prefers the tier-specific row, falls back to global `blend_weight_1x2`, then to 0.5. `ensemble_prediction(tier=N)` and the daily-pipeline call site updated to pass tier through. **First fit results (settled v9a data, 2026-05-10):** Tier 1 → Poisson 0.5574 (n=2,154). Tiers 2-4 skipped (≤12 paired samples — needs ~500). Will populate as data accumulates. **Striking side-result from the same run:** tier 1 1X2 shrinkage alpha optimised to 0.0025 (was 0.20) — model essentially zero-weighted vs raw bookmaker implied for top-tier 1X2; the model's edge is in goal-line markets (alpha 0.35 → 0.81). Implication: focus model improvement on goal-line accuracy, not 1X2. Stacked meta-learner deferred (R5 suggestion) — needs ~5x more settled data. Smoke test `ML-BLEND-DYNAMIC — load_blend_weight accepts tier`. | 2h | ✅ Done 2026-05-10 | ✅ Ready | Stacked LogReg meta-learner is the natural follow-up once we have ≥500 settled predictions per tier — separate `ML-STACKED-META` task. |
| ML-CALIBRATION-FIX | **Drop dual isotonic+Platt calibration → isotonic only** — R5 flags that applying isotonic regression then Platt scaling sequentially over-smooths probabilities, destroying edge at the tails where value bets live. Use ONE: isotonic for >1000 calibration samples (this is now satisfied — 586+ settled), Platt for smaller sets. Follow-up: add **beta calibration** or **Venn-Abers predictors** specifically for tail calibration improvement (standard isotonic optimises for center of distribution, not high-edge bet range). | 30m | ✅ Done 2026-05-08 | ✅ Ready | Removed `CalibratedClassifierCV` wrapper from all 3 training functions in `train.py`. XGBoost `multi:softprob`/`binary:logistic` is already calibrated; Platt applied at inference. Also eliminated redundant double-fit in `train_result_model`. |
| ML-PER-TIER | **Per-league-tier models (hierarchical)** — stay global until 500+ settled matches per league (Koopman & Lit 2015: per-league models fail OOS for most leagues due to variance). Right approach when ready: hierarchical — train global base model, then per-tier models that take global predictions as a feature. Top-5 European leagues will hit threshold first. | 1 day | ⬜ | ⏳ Month 3-4 of operation | Check settled bet counts per tier before starting. |
| ML-LOSS-FN | **Focal loss experiment** — R4 says stay with log_loss as primary training objective (Brier score has less gradient pressure at tails). Focal loss (gamma=2.0) is worth a 5-fold CV experiment: if log_loss in the 0.30-0.40 prob bin drops >3%, adopt it. More impactful: CAL-PLATT-UPGRADE which directly fixes the training/calibration mismatch. | 1 day | ⬜ | ⏳ After CAL-PLATT-UPGRADE | Focal loss code available in R4. Run after other model improvements to isolate effect. |
| ML-RESEARCH | **Run AI research prompt** — synthesize findings from 4 AI reviews (Gemini 1.5 Pro, GPT-4o, Claude Opus, GPT-4o R4) into task updates. | 1h | ✅ Done 2026-05-08 | ✅ Done | 4 replies received 2026-05-08. Tasks updated above. Key consensus: imputation > ELO gap > Pinnacle feature > CAL-PLATT-UPGRADE > LightGBM swap. Skip manager_change + squad_disruption as training features. Stay global model until 500+ settled/league. |

---

## Tier 5 — Future / Speculative

| ID | Task | ☑ | Ready? | Notes |
|----|------|----|--------|-------|
| SLM | Shadow Line Model: predict what opening odds *should be* | ⬜ | ⏳ Blocked | Needs opening odds timestamp storage |
| MTI | Managerial Tactical Intent: press conference classification | ⬜ | ⏳ Blocked | No reliable transcript source across leagues. **AI: ~$0.22/mo flat (10 calls/day, flash)** |
| RVB | Referee/Venue full bias features | ⬜ | ⏳ Blocked | Venue-level stats not yet collected |
| WTH | Weather signal (OpenWeatherMap, free) | ⬜ | ⏳ Low priority | Defer until O/U becomes a focus market |
| SIG-DERBY | Is-derby + travel distance signals | ⬜ | ⏳ Blocked | Needs team location data |

---

## Key Thresholds to Watch

| Milestone | Query | Target | Status (2026-05-08) | ETA |
|-----------|-------|--------|---------------------|-----|
| **Platt scaling ready** | Predictions with finished match outcomes | 500+ | ✅ Done 2026-04-30 | Done |
| **In-play model live** | Distinct matches in live_match_snapshots WITH xG | 500+ | ✅ ~400+ (live 1x2/O/U odds fixed 2026-05-07) | Done |
| Meta-model Phase 1 ready (B-ML3) | `match_feature_vectors WHERE match_date >= '2026-05-06'` | 3,000+ | **3,819 ✅** (2,266 with opening_implied) | ✅ Data ready — run NEWS-LINEUP-VALIDATE first |
| Post-mortem patterns readable | `model_evaluations WHERE market='post_mortem'` | 14+ | ~11 | ~May 13 |
| BOT-QUAL-FILTER ready | `simulated_bets WHERE result!='pending' AND created_at >= 2026-05-06` | 100+ | **590 ✅** | ✅ Done |
| Alignment threshold validation (ALN-1) | `simulated_bets WHERE result IN ('won','lost','void') AND created_at >= '2026-05-06'` | 300+ | **590 ✅** | ✅ Ready now — was ~June 5 estimate |
| News/lineup signal validation | Distinct match_ids with news/lineup signals | 100+ | **415 ✅** | ✅ Ready now — no task was tracking this |
| XGBoost retrain on backfill (ML-RETRAIN-1) | match_stats coverage | ~80% of finished | **73.4% (34,675 / 47,228) — terminal** | ✅ Ready 2026-05-10 |
| CAL-PLATT-UPGRADE ready — O/U | `SELECT COUNT(*) FROM simulated_bets WHERE market='O/U' AND result IN ('won','lost') AND selection ILIKE '%2.5%' AND calibrated_prob IS NOT NULL AND odds_at_pick IS NOT NULL` | 300+ | **73** (over: 20, under: 53) — 2026-05-12 | ~4-6 weeks (~3-5 O/U bets settle/day). Weekly Sunday refit auto-triggers once hit. |
| CAL-PLATT-UPGRADE ready — 1X2 | `SELECT COUNT(*) FROM simulated_bets WHERE market='1X2' AND result IN ('won','lost') AND model_version='v14'` | 300+ per outcome | **114 per outcome** — 2026-05-12 | ~2-3 weeks |
| CAL-PLATT-UPGRADE ready — AH/BTTS | Settled AH/BTTS bets since May 6 | 300+ each | AH: 7, BTTS: 20 | Months away |
| CLV rows for bot_meta_v1 | `simulated_bets WHERE clv IS NOT NULL AND created_at >= '2026-05-06'` | 3,000+ | **582** (~60/day avg) | ~late June |
| Meta-model Phase 2 ready | Settled bets with dimension_scores + CLV | 1,000+ | 590 (59%) | ~mid-June |

---

## § HIST-BACKFILL Plan — ✅ IMPLEMENTED (archived from PRIORITY_QUEUE 2026-05-05)

> Implementation complete. Script at `scripts/backfill_historical.py`, running on Railway 02:00 UTC daily.
> Phase 1: ~3,474 matches done. Full plan archived in git history.

---

## § INPLAY Plan — In-Play Value Detection Model

> Created: 2026-04-30. Original synthesis from 4 AI strategy reviews.
> Updated: 2026-05-06. Second round of 4 independent AI reviews (8 answers total) refined strategy conditions, added 5 new strategies (G-K), corrected xG formulation, and updated validation thresholds.
> Updated: 2026-05-09. Third round of 4 AI reviews on Kelly criterion and model_prob threshold. Consensus: flat stakes until 100-150 bets/strategy (exploratory) and 200-300/strategy (action). ECE < 5% is the Kelly gate. Edge is the right axis — no model_prob threshold. See §5b for full calibration roadmap.

### 1. Core Hypothesis (validated by all 8 reviews)

**"Conditional mispricing occurs when realized goal output < expected output, but forward-looking hazard rate remains high."**

The market adjusts live odds primarily on **time elapsed + scoreline**, but lags on **true chance quality (xG)** and **game state intensity (tempo, pressure)**. The edge is NOT "0-0 = bet Overs" — it's "0-0 but underlying goal process is ABOVE expectation."

### 2. Model Architecture (all 4 original reviews agreed)

**Target:** Predict `lambda_home_remaining` and `lambda_away_remaining` (Poisson rates for remaining goals per team) — NOT classification.

**Why:** One regression model derives ALL market probabilities:
- P(Over 2.5) = P(Poisson(λ_total_remaining) ≥ 2.5 - current_goals)
- P(BTTS) = derived from per-team lambdas via bivariate Poisson
- P(Home Win) = derived from goal difference distribution

**Algorithm:** LightGBM with `objective='poisson'` (primary) + XGBoost as ensemble partner.

**Time handling:** Single model with:
- `match_minute` as continuous feature
- `match_phase` as categorical: [0-15, 15-30, 30-45, 45-60, 60-75, 75-90]
- `time_remaining = 90 - match_minute`
- Non-linear transforms: `minute_squared`, `log(90 - minute)`

**Red cards:** V1: hard-skip matches with red cards. V2 (2000+ matches): add `man_advantage` + `minutes_since_red` features.

### 3. Feature Engineering (ranked by predictive power)

#### Tier 1 — Build immediately

| Feature | Formula | Signal |
|---------|---------|--------|
| **Bayesian xG rate** *(replaces raw ratio — unanimous across all 8 reviews)* | `posterior_rate = (prematch_xg + live_xg) / (1.0 + minute / 90)` | Shrinks early noise toward prior; converges with pace ratio by min 35 |
| **xG delta vs expectation** | `live_xg - (prematch_xg × minute / 90)` | Positive = game running hotter than pre-match model expected |
| **xG-to-score divergence** | `live_xg_total - actual_goals` | Large positive = "unlucky", regression due |
| **Implied probability gap** | `model_prob - (1 / live_odds)` | Direct value measure — trigger on this, not raw odds level |
| **Per-team shot quality** | `team_xg / team_shots` | High = dangerous chances; low = shooting from distance |
| **Odds velocity** | `(odds_t - odds_t_minus_5min) / odds_t_minus_5min` | Sharp moves without goals = information |
| **Odds staleness flag** | `NOW() - odds_last_updated > 60s` | Critical: if True, skip bet — odds may be frozen post-goal |

#### Tier 2 — Build by Phase 2
| Feature | Formula | Signal |
|---------|---------|--------|
| Possession efficiency | `team_xg / (possession_pct × minute / 90)` | Strips time-wasting possession |
| Score-state adjustment | All metrics segmented by leading/drawing/trailing | Trailing team stats more predictive |
| xG home/away share | `xg_home / (xg_home + xg_away)` | Away dominance in 0-0 may already be priced |
| Corner momentum | `corners_last_10min / corners_total` | Acceleration predicts pressure |
| Bookmaker consensus | `std(implied_probs_across_13_bookmakers)` | High disagreement = value opportunity |
| xG acceleration | `last_10_min_xg / previous_10_min_xg` | Momentum proxy — derive from snapshot deltas |

#### Tier 3 — Refinements
| Feature | Formula | Signal |
|---------|---------|--------|
| ELO-adjusted xG | `xG × (opponent_elo / league_avg_elo)` | xG vs strong defense worth more |
| Importance × score state | `importance × (trailing: 1.3, leading: 0.7, drawing: 1.0)` | Must-win + trailing = max pressure |

### 3b. Critical Engineering Fixes (from 8-review synthesis — build before first bet)

All 4 second-round tools flagged these independently:

1. **Staleness check (HIGH):** Before logging any paper bet, verify live odds updated in the last 60 seconds. API-Football odds can lag 30-60s post-goal. A stale odds snapshot could log a bet at pre-goal prices on a match that's already 1-0. Implementation: compare `captured_at` of the odds fields to current time.

2. **Score re-check at execution (HIGH):** When a trigger fires, re-read the latest score from the most recent snapshot before logging the bet. If score changed since the triggering snapshot, abort.

3. **League calibration filter (HIGH):** Only run in leagues with ≥ 20 completed matches with xG data in `live_match_snapshots`. AF's xG is less calibrated in lower tiers with sparse history.

4. **Split 0-0 and 1-0 scenarios (MEDIUM):** These are structurally different game states. Strategies A and D should have separate configs — 0-0 version and 1-0 version — logged with different `strategy_id` values so Phase 2 analysis can compare them.

5. **xG home/away direction (MEDIUM):** Log `xg_home_share` per bet. Away dominance in a 0-0 (away pressing, home defending deep) may already be priced by sharp books. Phase 2 feature engineering will test this.

### 4. Strategy Portfolio (A-K)

*A-F from original 4-AI synthesis. G-K added after second 8-answer round.*
*Over 3.5 bot: REJECTED by all 4 second-round tools — no O3.5 live odds, total overlap with A. Tag extreme A conditions as `strategy_tag='A_extreme'` instead.*

---

#### Strategy A: "xG Divergence Over" — Phase 1A bot
**Edge confidence:** Medium | **Trigger rate:** ~8-12% of matches | **Time to 200 bets:** ~14 days
- **Entry:** Min **25-35** (tightened from 20-35 — all 4 reviews agreed early window too noisy)
- **Score:** 0-0 only (split 1-0 into A2 for separate analysis)
- **Signal:** Bayesian posterior rate > prematch rate × 1.15 AND combined xG ≥ 0.9 AND shots on target ≥ 4 combined AND pre-match O2.5 > 54%
- **Market:** Over 2.5; trigger when `model_prob - (1/live_odds) ≥ 3%` (not static odds floor)
- **Skip:** xG per shot < 0.09, red card, odds staleness flag, league with < 20 xG matches
- **Edge:** 2-4% (revised down from 3-8% — Pinnacle's live market is sharper than assumed)

#### Strategy A2: "xG Divergence Over — 1-0 State"
**Edge confidence:** Medium | Separate bot, same logic as A but score = 1-0
- **Note:** Who is winning vs pre-match expectation matters — log `score_leader_is_favourite` for Phase 2 analysis

#### Strategy B: "BTTS Momentum"
**Edge confidence:** High | **Trigger rate:** ~15-20% | **Time to 200 bets:** ~8 days
- **Entry:** Min 15-40, score 1-0 or 0-1
- **Signal:** Trailing team xG ≥ 0.4 AND shots on target ≥ 2 AND pre-match BTTS > 48%
- **Market:** BTTS Yes
- **Skip:** Trailing team xG < 0.2, score becomes 2-0, red card for trailing team
- **Edge:** 4-7%

#### Strategy C: "Favourite Comeback"
**Edge confidence:** High | **Trigger rate:** ~5-8% | **Time to 200 bets:** ~22 days
- **Entry:** Min 25-60, pre-match favourite trailing by 1
- **Signal:** Favourite xG > underdog xG AND possession ≥ 60% AND shots on target ≥ opponent
- **Market:** Draw No Bet (favourite) — DNB gives cleaner CLV analysis than Double Chance
- **Skip:** Favourite not generating xG, underdog counter-xG high
- **Edge:** 3-6%

#### Strategy C_home: "Home Favourite Comeback" *(new — user idea, validated by all 4 tools)*
**Edge confidence:** High | **Trigger rate:** ~3-5% | **Time to 200 bets:** ~30 days
- **Entry:** Same as C but ONLY home team is pre-match favourite trailing 1-0
- **Signal:** Same as C + ELO confirms home team quality (elo_home > elo_away)
- **Possession threshold:** ≥ 55% (home crowd generates set-piece pressure at lower possession)
- **Minute cap:** ≤ 70 (post-70 crowd dynamics can shift to panic, opening counter-attacks)
- **Market:** Draw No Bet (home) — log draw outcome separately to also capture DC data
- **Mechanism:** COVID natural experiment showed ~6-8pp crowd effect on home win rate. Referee bias + urgency not captured in xG.
- **Edge:** 5-10%

#### Strategy D: "Late Goals Compression"
**Edge confidence:** Medium | **Trigger rate:** ~22-27% | **Time to 200 bets:** ~6 days (fastest)
- **Entry:** Min 55-75, score 0-0 or 1-0
- **Signal:** Combined xG ≥ 1.0 AND live odds > 2.50 AND pre-match expected goals > 2.3
- **Market:** Over 2.5 (proxy — we don't have O1.5 live odds)
- **Skip:** Combined xG < 0.6 (dead game)
- **Edge:** 3-6% — needs 500+ bets before trusting (high variance at these odds)

#### Strategy E: "Dead Game Unders"
**Edge confidence:** High | **Trigger rate:** ~12-16% | **Time to 200 bets:** ~10 days
- **Entry:** Min 25-50, score 0-0 or 1-0
- **Signal:** xG pace < 70% of expected AND shots slowing (derive from snapshot deltas) AND corners low
- **Market:** Under 2.5
- **Edge:** Market assumes constant hazard rate; tempo collapse is real and well-documented

#### Strategy F: "Odds Momentum Reversal"
**Edge confidence:** Low | **Trigger rate:** ~4-7% | **Time to 200 bets:** ~25 days
- **Entry:** Any minute, triggered by odds velocity
- **Signal:** Odds move > 15% in < 10 min WITHOUT goal AND score unchanged across last 3 polls AND contrary to xG trend
- **Market:** Fade the move direction
- **Skip:** Red card, score change, only 1 bookmaker moved, odds staleness flag
- **Edge:** 5-10% when triggered — but 30s polling makes distinguishing real moves from VAR/injury noise hard. Minimum 500+ bets before conclusions.

#### Strategy G: "Shot Quality Under" *(new — appeared in all 4 second-round tools)*
**Edge confidence:** Medium-High | **Trigger rate:** ~6-10% | **Time to 200 bets:** ~18 days
- **Entry:** Min 32-52, score combined ≤ 1
- **Signal:** Combined shots ≥ 12 AND (xg_home + xg_away) / (shots_home + shots_away) < 0.07 AND live Under 2.5 odds ≥ 1.70
- **Skip:** Pre-match O2.5 implied > 62% (expected goal-fest), red card
- **Market:** Under 2.5
- **Mechanism:** Market reacts to shot volume (visible, salient). Low xG/shot = teams shooting from distance, not generating danger. Under is mispriced.
- **Edge:** 5-8%

#### Strategy H: "Corner Pressure Over" *(new — appeared in 3/4 second-round tools)*
**Edge confidence:** Medium | **Trigger rate:** ~4-6% | **Time to 200 bets:** ~28 days
- **Entry:** Min 35-48, score combined ≤ 1
- **Signal:** Combined corners ≥ 8 AND combined xG ≥ 0.4 AND live O2.5 ≥ 1.90
- **Skip:** One team has ≥ 70% possession AND their corners > opponent (defensive clearances, not bilateral pressure)
- **Market:** Over 2.5
- **Mechanism:** High bilateral corners = sustained set-piece pressure underweighted by pure xG models
- **Edge:** 3-6% — run league-tier stratified; expect higher edge in Tier 3-4

#### Strategy I: "Possession Trap Under" *(new — appeared in 3/4 second-round tools)*
**Edge confidence:** Medium-High | **Trigger rate:** ~3-6% | **Time to 200 bets:** ~30 days
- **Entry:** Min 32-55, score **0-0 only** (1-0 with high possession = time-wasting, different mechanism)
- **Signal:** (possession_home ≥ 62 AND xg_home ≤ 0.30) OR (possession_home ≤ 38 AND xg_away ≤ 0.30) AND combined xG ≤ 0.40 AND live Under 2.5 ≥ 1.75
- **Market:** Under 2.5
- **Mechanism:** Market sees high possession → interprets attacking intent → misprices Under. Sterile possession (possession without penetration) is a distinct game state.
- **Edge:** 5-8%

#### Strategy J: "Dominant Underdog Win" *(new — appeared in 2/4 second-round tools)*
**Edge confidence:** Medium | **Trigger rate:** ~3-5% | **Time to 200 bets:** ~32 days
- **Entry:** Min 25-55, underdog leading 1-0 (identify via lower pre-match Pinnacle implied prob)
- **Signal:** Underdog xG > Favourite xG AND possession within 10% of 50/50 or favouring underdog AND live underdog win odds ≥ 2.80
- **Market:** 1X2 Underdog to Win
- **Mechanism:** Narrative bias ("favourite will come back") keeps underdog win odds too high even when data shows deserved lead
- **Edge:** 4-7% per bet — rare trigger but potentially highest edge-per-bet

#### Strategy K: "Second-Half Kickoff Burst" *(new — appeared in 2/4 second-round tools)*
**Edge confidence:** Low-Medium | **Trigger rate:** ~8-12% | **Time to 200 bets:** ~14 days
- **Entry:** Min 46-54 ONLY (narrow post-HT window)
- **Signal:** Score 0-0 or 1-0 AND combined first-half xG ≥ 0.70 AND pre-match O2.5 > 50% AND live O2.5 ≥ 1.90
- **Market:** Over 2.5
- **Mechanism:** Above-average goal rate in first 5-8 min of 2H from fresh legs + HT adjustments. Market applies smooth time-decay, missing this temporal spike.
- **Edge:** 2-4% — sharp books increasingly price this for major leagues. More relevant Tier 3-4.
- **Hold:** Start in Week 3 after A and F confirmed working cleanly.

### 4b. Launch Order (based on 8-review prioritisation consensus)

| Week | Start these bots | Rationale |
|------|-----------------|-----------|
| **Week 1** | A, A2, B, C, C_home, D, E, F | Core portfolio — fastest validators first |
| **Week 2** | G (Shot Quality Under), H (Corners Over) | Simple conditions, fields already collected |
| **Week 3** | I (Possession Trap), J (Dominant Underdog), K (2H Burst) | More complex logic, lower frequency |
| **Hold** | Over 3.5 bot | No O3.5 odds — tag extreme A triggers as `A_extreme` instead |

### 4c. Strategy Prioritisation Table (Tool 4 synthesis)

| Strategy | Est. bets/day | Days to 200 bets | Edge confidence | Notes |
|----------|--------------|-----------------|-----------------|-------|
| D — Late Goals | 33-40 | **~6 days** | Medium | High variance, need 500+ to trust |
| B — BTTS Momentum | 22-30 | ~8 days | **High** | Fastest high-confidence strategy |
| E — Dead Game Under | 18-24 | ~10 days | **High** | Strong mechanism, well-studied |
| K — 2H Kickoff Burst | 12-18 | ~14 days | Low-Medium | Start Week 3 |
| A — xG Divergence | 12-18 | ~14 days | Medium | Core strategy, run first |
| G — Shot Quality Under | 9-15 | ~18 days | Medium-High | Good contrast to A |
| C — Favourite Comeback | 8-12 | ~22 days | **High** | Needs patience |
| F — Odds Reversal | 6-10 | ~25 days | Low | 500+ minimum before conclusions |
| H — Corners Over | 6-9 | ~28 days | Medium | Noisy without timing data |
| C_home — Home Comeback | 5-8 | ~30 days | **High** | Rarest but strongest mechanism |
| I — Possession Trap | 5-9 | ~30 days | Medium-High | Low frequency, high edge |
| J — Dominant Underdog | 5-8 | ~32 days | Medium | Rarest, highest edge-per-bet |

### 5. Staking (in-play specific)

**Current state: flat €1 paper stakes.** Kelly is not yet appropriate — `model_prob` is theoretical Poisson math, not empirically calibrated. Kelly with an overconfident prior oversizes bets and compounds losses asymmetrically. See §5b for the calibration roadmap before touching stake sizing.

Target staking parameters once calibration gates pass:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Kelly fraction | **Eighth Kelly initially (0.125x), then Quarter Kelly (0.25x)** | Start conservative — uncalibrated inplay model; upgrade after ECE < 5% confirmed stable |
| Time decay | `(minutes_remaining / 90)^0.5` | Min 30: 82% stake, min 60: 58%, min 75: 41% |
| Max stake per bet | 1.5% bankroll | Lower than pre-match (2%) |
| Max exposure per match | 3% bankroll | Prevents doubling down on correlated strategies |
| Bankroll allocation | 70% pre-match, 30% in-play | Pre-match is more reliable |

**Minimum edge thresholds by minute:**

| Minute | Min edge | Rationale |
|--------|----------|-----------|
| 15-30 | 3% | Good signal, plenty of time |
| 30-45 | 4% | HT reset incoming |
| 46-60 | 5% | Post-HT uncertainty |
| 60-75 | 6% | Time running out |
| 75+ | 8% | Extreme value only |

### 5b. Calibration Roadmap (4-AI consensus, 2026-05-09)

> Reviewed by 4 independent AI tools. All 4 agreed on the core framework below.

**The key principle:** edge decides whether to bet, calibration decides how much to bet. Until calibration is stable, keep sizing tiny.

#### Checkpoint 1 — Exploratory (~100–150 settled bets per strategy)

Run now as a sanity check, treat as directional only — confidence intervals are wide.

- **Reliability diagram per strategy:** bin bets by model_prob (40-50%, 50-60%, 60-70%, 70%+), compare average model_prob per bin vs actual win rate. If the line sits well above the diagonal, the model is overconfident.
- **Edge bucket ROI:** group by edge (<3%, 3-6%, 6-10%, 10%+). If ROI is not monotonically increasing with edge, the edge calculation has a structural problem.
- **Brier score + ECE (Expected Calibration Error):** ECE < 5% is the target bar before trusting probabilities enough to size off them.
- **Do not change stake sizing based on this checkpoint.**

Current pace: ~10-30 bets/strategy/12 days → 100-150 bets ≈ 6-8 weeks (mid-June 2026).

#### Checkpoint 2 — Action threshold (~200–300 settled bets per strategy)

- Repeat reliability diagrams + ECE per strategy.
- If ECE < 5%: apply Platt scaling to correct systematic bias, then implement Eighth Kelly (0.125x) with hard per-bet cap.
- If ECE ≥ 5%: stay flat, investigate which strategies are miscalibrated and why.
- Evaluate per strategy independently — don't pool across strategies. Each has different failure modes (Strategy E betting Unders has completely different probability dynamics than Strategy C betting a comeback).

Current pace → 200-300 bets/strategy ≈ 2-3 months (July-August 2026).

#### Checkpoint 3 — Kelly upgrade (~500+ settled bets per strategy, stable calibration)

- Re-run calibration. Non-stationarity is real — bookmaker algorithms adjust over time. Plan to recalibrate every ~500 bets, not just once.
- If ECE still < 5% and edge monotonicity holds: upgrade to Quarter Kelly (0.25x).
- Full Kelly is never appropriate for inplay — execution latency, odds moving before fill, and correlated simultaneous exposure all break Kelly's independence assumption.

#### What NOT to do: model_prob threshold

A minimum model_prob filter (e.g. "only bet when model says >60%") is redundant and often harmful. A 55% model probability with 40% implied (edge +15%) is a stronger bet than 62% model with 60% implied (edge +2%). The model_prob threshold filters on confidence in isolation, not on actual edge over the market.

**Use instead:**
- Tighten the minimum edge floor if you want to filter more aggressively (e.g. raise from 2% to 4%)
- Odds ceiling of ~4.00 to control longshot variance (longshots with high model edge create massive drawdowns before the long run arrives)

#### Pitfalls to watch

| Pitfall | Description |
|---------|-------------|
| Selection bias | Calibration data is biased toward high-edge bets — you have no data on model accuracy at 0-3% edge since you never bet there |
| Non-stationarity | Calibration from month 1 may not hold in month 3. Recalibrate every ~500 bets |
| Correlated exposure | Multiple strategies can all express "more goals" simultaneously (HT surge + BTTS + late goals). Standard Kelly assumes independence — enforce the 3% per-match cap before implementing Kelly |
| xG input uncertainty | Poisson outputs look crisp but xG inputs have wide uncertainty, especially before min 30. Systematic overconfidence source that calibration will surface |
| Conditional calibration | Only evaluate calibration on executed bets, not all model outputs — these are structurally different populations |

### 6. Data Gaps to Fix

| Gap | Priority | Solution |
|-----|----------|----------|
| Odds staleness detection | **Critical** | Compare `captured_at` of odds to NOW() — skip if > 60s stale |
| Score re-check at trigger | **Critical** | Re-read latest score before logging bet, abort if changed |
| Open-play xG vs set-piece xG | High | Separate penalty/free-kick xG from open-play in snapshots |
| Substitution timestamps + type | High | Already in match_events — extract and add to snapshot features |
| Event-triggered odds capture | High | Snapshot odds at goal/red card moments, not just 5-min cycle |
| 1-minute trigger checks | Medium | When model flags potential entry, poll odds at 1-min for execution |
| Formation changes | Medium | Capture at HT and after goals |
| Dangerous attacks count | Low | Available from AF, add to snapshot |

### 7. Implementation Phases — Full Roadmap

Each phase has an explicit **gate** that must pass before the next phase begins.
All paper phases use AF live odds from `live_match_snapshots` — no bookmaker API needed.
Real money phases require a Betfair Exchange account + API integration (1-2 days to add).

---

#### 🟦 Paper Trading — Phase 1A: Rule-Based Single Strategy (START TODAY)
**Data needed:** None — starts immediately using live AF odds  
**Timeline:** May 2026 (build now, runs continuously)  
**What to build:**
- Live bot in scheduler: reads `live_match_snapshots` every 30s during active matches
- Computes Bayesian posterior: `posterior_rate = (prematch_xg + live_xg) / (1.0 + minute / 90)`
- Staleness check: if odds haven't updated in 60s → skip
- Score re-check: re-read latest score before logging → abort if changed since trigger snapshot
- Checks Strategy A conditions: minute 25-35, score 0-0, posterior_rate > prematch_rate × 1.15, combined xG ≥ 0.9, shots on target ≥ 4, pre-match O2.5 > 54%, `model_prob - implied_prob ≥ 3%`
- Logs to `simulated_bets`: `market='ou_25'`, `selection='over'`, `odds=live_ou_25_over`, `stake=1% fixed`, `strategy_id='inplay_a'`
- Settlement handled by existing pipeline at FT

**Gate to Phase 1B:** 200+ paper bets logged AND CLV positive on ≥ 55% of bets (revised from 80% — 8-tool consensus)

---

#### 🟦 Paper Trading — Phase 1B: Rule-Based All Strategies
**Data needed:** 200+ Phase 1A bets settled, ROI > 0% OR CLV > 0 on 60%+ of bets  
**Timeline:** Late May / early June 2026  
**What to build:**
- Extend bot to run all 6 strategies (A-F) simultaneously
- Strategy B (BTTS Momentum), C (Favorite Comeback), D (Late Goals Compression), E (Dead Game Unders), F (Odds Momentum Reversal)
- Each strategy logs independently — separate analysis per strategy
- Add `strategy_id` column to `simulated_bets` (or use `notes` field) to track which strategy triggered

**Gate to Phase 2:** 500+ Phase 1A/1B bets across strategies, identify which have ROI > 0% + CLV > 0 on 70%+ bets

---

#### 🟩 Paper Trading — Phase 2: ML Model Replaces Rules
**Data needed:** 500+ live match snapshots with xG (≈ May 7-8), 200+ settled paper bets for validation  
**Timeline:** June 2026  
**What to build:**
- Feature pipeline: `live_match_snapshots` → training rows at minute 15/30/45/60/75 checkpoints
- Train LightGBM with `objective='poisson'` on `lambda_home_remaining` + `lambda_away_remaining`
- Derive O/U, BTTS, 1X2 probabilities from lambda estimates
- Replace rule-based triggers with model probability: bet when `model_prob - implied_prob > edge_threshold`
- Backtest all 6 strategies on historical snapshots — confirm which have genuine edge
- Add XGBoost ensemble partner

**Gate to Phase 3:** ML model CLV > 0% on 300+ paper bets AND outperforms Phase 1 rules by ≥ 2% ROI

---

#### 🟩 Paper Trading — Phase 3: Full System (Kelly + Multi-Market + All Strategies)
**Data needed:** Phase 2 model validated (300+ bets)  
**Timeline:** July 2026  
**What to build:**
- Quarter Kelly staking with time decay: `stake = 0.25 × Kelly × (minutes_remaining / 90)^0.5`
- 1-minute trigger checks when model flags potential entry (switch from 30s to 1-min targeted poll)
- Multi-market bets per match: O/U + BTTS simultaneously when both conditions met
- Max 3% bankroll exposure per match across all in-play positions
- CLV tracking: entry odds vs closing odds (same as pre-match CLV pipeline)
- Per-strategy P&L dashboard on frontend (Elite-gated)

**Gate to Phase 4 (real money):** Phase 3 paper results: ROI > 3% on 500+ bets AND CLV > 0 on 80%+ AND Sharpe > 1.0 over 60-day window

---

#### 🔴 Real Money — Phase 4: Micro-Stakes Live (Betfair Exchange)
**Data needed:** Phase 3 gates passed  
**Timeline:** August 2026  
**What to build:**
- Betfair Exchange API integration (1-2 days): place lay/back bets programmatically
- Strategy A + best-performing Phase 3 strategy only — 2 strategies max
- Ultra-conservative staking: 0.25% bankroll max per bet (half of paper rate)
- Kill switch: auto-pause if drawdown > 10% in any 7-day window
- Real CLV tracking: execution price vs closing price on Betfair

**Gate to Phase 5:** 200+ real bets, ROI > 0%, no systematic execution issues (slippage < 2%)

---

#### 🔴 Real Money — Phase 5: Full Live Deployment
**Data needed:** Phase 4 validated (200+ real bets)  
**Timeline:** September 2026+  
**What to build:**
- All validated strategies (those with confirmed real-money edge from Phase 4)
- Full Quarter Kelly sizing
- Expand to multiple leagues (start with EPL + top 5, expand to lower leagues where limits allow)
- Automated limit monitoring (Betfair exchange limits are less of an issue than fixed-odds books)
- Monthly model retraining as data accumulates

---

#### Summary

| Phase | Type | Start condition | Est. timeline | Key metric |
|-------|------|----------------|---------------|------------|
| **1A** — Rule bot, Strategy A | 📄 Paper | TODAY | May 2026 | 200 bets logged |
| **1B** — Rule bot, all strategies | 📄 Paper | 200 bets settled | Late May | Best strategy identified |
| **2** — LightGBM model | 📄 Paper | 500 snapshots + 200 bets | June 2026 | Model CLV > 0% |
| **3** — Full system, Kelly | 📄 Paper | Model validated | July 2026 | ROI > 3%, CLV 80%+ |
| **4** — Real money micro | 💰 Real | Phase 3 gates | Aug 2026 | ROI > 0%, no slippage issues |
| **5** — Real money full | 💰 Real | Phase 4 validated | Sep 2026+ | Sharpe > 1.0, scaling |

### 8. What This Unlocks

- **Entirely new revenue stream** — in-play betting is ~60% of global sports betting volume
- **Pro/Elite product differentiation** — "Live Win Probability" updating every 5 min on match detail
- **Higher bet volume** — each match can generate multiple in-play bets at different checkpoints
- **xG-based edge** — most retail bettors and many bookmaker algorithms anchor on scoreline, not xG

---

## § RAILWAY Plan — ✅ COMPLETE (archived from PRIORITY_QUEUE 2026-05-05)

> All 5 phases complete 2026-04-30. Architecture running on Railway. Full plan archived in git history.

---

## Source Legend

| Source | Meaning |
|--------|---------|
| Internal | Planned before external AI analysis — from ROADMAP/BACKLOG/MODEL_ANALYSIS |
| AI Analysis (2026-04-28) | Identified during external 4-agent AI architecture review session on 2026-04-28 |
| ROADMAP Frontend Backlog | From the Frontend Data Display Backlog section of ROADMAP.md |
| Internal (MODEL_ANALYSIS X.X) | Exists in MODEL_ANALYSIS.md but was not yet tracked in this queue |
| UX Review (2026-04-29) | Identified during 4 independent UX/product reviews of signal surfacing strategy. Full details in SIGNAL_UX_ROADMAP.md |
| 4-AI Match UX Review (2026-04-29) | 4 independent AI tools assessed 11 match list UX improvements. Unanimous on: filter tabs, live timer, team crests, predicted score (THE differentiator). Strong consensus on: odds movement arrows (Pro), bookmaker count badge. Skip: odds freshness (highlights 2h staleness as a weakness). |
| 4-AI Calibration Review (2026-05-06) | 4 independent AI tools analyzed calibration failure on 77 settled bets (42% pred vs 26% actual on 1X2 home). Consensus: conditional miscalibration at high odds, not global. Priority fixes: Pinnacle shrinkage anchor, odds-conditional alpha, sharp consensus gate, draw inflation. |
| Data Analysis (2026-04-29) | From pipeline refactor + data source audit session (2026-04-29) |
| Launch Plan (2026-04-29) | From LAUNCH_PLAN.md pre-launch preparation |
| Tier Access Matrix | From TIER_ACCESS_MATRIX.md feature checklist |
| Data Sources | From DATA_SOURCES.md remaining cleanup |
| Landing Page Review (2026-04-29) | From landing page pricing/UX review |
