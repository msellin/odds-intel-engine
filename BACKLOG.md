# OddsIntel Engine — Data & ML Backlog

> Prioritized list of improvements to make bots smarter, data richer, and every information source measurable.
> Last updated: 2026-04-27

---

## Guiding Principle

**Every information source must prove its value.** If we can't measure whether AI news, lineup data, or odds movement improves ROI, we can't sell it as a premium feature — and we can't trust our own edge. The prediction audit trail is the foundation for everything else.

---

## Priority 1 — Measure What We Have (This Week)

These capture data we already produce but currently throw away.

### P1.1 — Prediction audit trail ✅ DONE
- `prediction_snapshots` table: 4 stages (stats_only → post_ai → pre_kickoff → closing)
- Pipeline saves Stage 1 at bet placement, Stage 2 after AI news check, Stage 4 at closing
- **Committed:** `3900d45` (migration 004 + pipeline integration)

### P1.2 — Populate match_stats from live tracker ✅ DONE
- Live tracker now calls `store_match_stats()` when Sofascore status_code = 100 (match ended)
- Stores final xG, shots, possession, corners to `match_stats` table
- **Files:** `supabase_client.py` (store function), `live_tracker.py` (integration)

### P1.3 — Store ELO ratings to DB ✅ DONE
- New table `team_elo_daily` (migration 005) — stores ELO per team per date
- Settlement pipeline computes ELO updates (K=30, home advantage, goal diff multiplier) after settling bets
- **Files:** `supabase_client.py` (store_team_elo), `settlement.py` (update_elo_ratings)

### P1.4 — Populate model_evaluations daily ✅ DONE
- Settlement pipeline now aggregates settled bets into `model_evaluations` by date/market
- Stores: total_bets, hits, hit_rate, ROI, avg_clv
- **Files:** `supabase_client.py` (store_model_evaluation), `settlement.py` (compute_model_evaluations)

### P1.5 — Cache team form metrics ✅ DONE
- New table `team_form_cache` (migration 005) — rolling 10-match form per team per date
- Settlement pipeline computes form for all teams that played today
- Stores: win%, draw%, loss%, PPG, goals scored/conceded avg, clean sheet%, O/U 2.5%, BTTS%
- **Files:** `supabase_client.py` (compute_team_form_from_db, store_team_form), `settlement.py` (update_team_form_cache)

---

## Priority 2 — Enrich Data Sources (Next 1-2 Weeks)

These add new information that the model doesn't currently have.

### P2.1 — Per-bookmaker odds storage ✅ DONE 2026-04-28
- API-Football integration stores 13 bookmakers separately in `odds_snapshots` (bookmaker field populated)
- **Value delivered:** Sharp-vs-soft money detection now enabled — Pinnacle vs Bet365 divergence trackable
- Per-bookmaker best-odds comparison live in frontend (MatchDetailLive)

### P2.2 — Odds movement velocity ✅ DONE
- `compute_odds_movement()` in `workers/model/improvements.py` computes drift, drift_pct, velocity from odds_snapshots
- Soft penalty on Kelly for adverse movement, hard veto only >10%
- Stored per bet: odds_at_open, odds_drift
- **Integrated into:** `daily_pipeline_v2.py` morning run

### P2.3 — B4: News checker runs 4x/day
- Add 12:30, 16:30, 19:30 UTC cron runs (currently only 09:00)
- The 19:30 run catches confirmed lineups for evening matches
- Save Stage 3 (pre_kickoff) snapshot in the audit trail
- **Value:** Lineup-confirmed info is the highest-impact moment. Most injuries are public by then
- **Effort:** Add cron entries + modify news_checker to save stage 3 snapshots

### P2.4 — Half-time stats storage ✅ DONE 2026-04-28 (via T4)
- AF `/fixtures/statistics?half=1` + `?half=2` called post-match during settlement
- All `match_stats` `*_ht` columns populated (shots, possession, corners, fouls, saves, xG at half-time)
- **Value delivered:** HT vs full-time comparison available for in-play model training (P3.4)

---

## Priority 3 — ML Model Upgrades (2-4 Weeks Out)

These require accumulated data from P1/P2 before they're useful.

### P3.1 — Odds movement as XGBoost input feature
- ✅ Odds drift computed and stored per bet (P2.2)
- ✅ Soft penalty on stake for adverse movement
- ✅ XGBoost now in live pipeline (50/50 blend with Poisson)
- ⬜ Feed `odds_drift` as an actual XGBoost input feature (model retraining needed)
- **Depends on:** Enough settled bets with odds_drift populated to retrain
- **Value:** Market information is the strongest signal you're not using

### P3.2 — Stacked ensemble (meta-learner)
- Instead of fixed 50/50 Poisson + XGB blend, train a logistic regression meta-learner
- Learns when Poisson is better (low-data leagues) vs XGB (data-rich leagues)
- Input: both models' predictions + league tier + data availability features
- **Depends on:** Enough settled bets with both predictions stored
- **Value:** Potentially +1-3% ROI from smarter blending

### P3.3 — Player-level injury weighting
- Populate `players` table with Sofascore player data (market value, position, minutes played)
- When news_checker flags an injury, weight impact by player importance
- "Starting striker out" ≠ "3rd-choice goalkeeper out"
- **Depends on:** P2.3 (more frequent news checks) + player data population
- **Value:** More accurate AI adjustments → better Stage 2 predictions

### P3.4 — In-play value detection model
- Use accumulated `live_match_snapshots` data (needs ~2-3 months)
- Train model: given match state at minute X (score, xG, possession, live odds), predict final result
- Compare model probability to live odds → find in-play edge
- **Depends on:** P1.2 (match_stats), P2.4 (HT snapshots), accumulated live data
- **Value:** Opens entirely new bet type (live betting) — potential new revenue stream

### P3.5 — Feature importance tracking per league
- After each backtest or daily evaluation, log feature importances by league
- Track which features matter where (ELO matters in top leagues, form matters in lower leagues)
- **Depends on:** P1.4 (model_evaluations populated)
- **Value:** Guides which data sources to invest in per market

---

## Priority 4 — Strategic / Long-term

### P4.1 — Audit trail ROI comparison dashboard
- Script or page that shows: "Stats-only ROI: X%, After AI: Y%, After lineups: Z%"
- Proves (or disproves) value of each information layer
- **Depends on:** P1.1 (audit trail) + 30+ settled bets with all stages populated
- **Value:** This is the evidence that decides pricing tiers and whether to sell picks at all

### P4.2 — A/B bot testing framework
- Run parallel bots: one with AI adjustment, one without
- Same matches, same model, different information layers
- Compare ROI over 100+ bets
- **Depends on:** P1.1 (audit trail) + enough data
- **Value:** Definitive proof of AI value-add

### P4.3 — Live odds arbitrage detector
- Cross-bookmaker comparison in real-time during odds snapshot runs
- Flag when bookmaker odds diverge enough for guaranteed profit
- **Depends on:** P2.1 (per-bookmaker odds)
- **Value:** Risk-free profit opportunities (rare but valuable)

---

## Mapping to Roadmap

| Backlog Item | Roadmap Task | Status |
|---|---|---|
| P1.1 | Milestone 1.5 | Done — committed `3900d45` |
| P1.2 | Milestone 1.5 | Done — live tracker integration |
| P1.3 | Milestone 1.5 | Done — migration 005 |
| P1.4 | Part of B3 (Milestone 2) | Done — settlement pipeline |
| P1.5 | Milestone 1.5 | Done — migration 005 |
| P2.3 | B4 (Milestone 2) | Not started |
| P3.4 | Future (Milestone 3+) | Needs data accumulation |
| P4.1 | Validates Elite tier pricing | Needs P1.1 data (now accumulating) |

---

## Priority 5 — External Data Sources

### P5.1 — European Soccer DB: multi-bookmaker sharp/soft analysis
- Download the Kaggle European Soccer Database (SQLite, 25K matches, 2008-2016)
- Extract per-match odds from 13 bookmakers (Bet365, Pinnacle, William Hill, Ladbrokes, etc.)
- **Analysis a) Historical backtest:** Compute per-bookmaker closing line accuracy — which bookmaker's closing odds best predict outcomes? Train a "sharp bookmaker" classifier.
- **Analysis b) Live prediction:** Build a "consensus divergence" feature — when Pinnacle's implied probability diverges from soft bookmaker average by >3%, flag as sharp money signal. Test if adding this as a model feature improves ROI in backtest.
- **Output:** Script that produces `bookmaker_sharpness_rankings.csv` + a boolean `sharp_money_signal` feature for the model
- **Depends on:** Download + SQLite parsing script
- **Value:** Strongest unused signal in sports betting. If Pinnacle moves and Bet365 doesn't, that's information.

### P5.2 — Footiqo: gap league closing odds + minute-interval data
- Browse footiqo.com/database/leagues/ for Singapore S.League, South Korea K League, Scotland League Two
- If available: download closing odds (1xBet source) as CSV
- **Analysis a) Historical backtest:** Re-run Singapore +27.5% ROI backtest with independent 1xBet closing odds (vs Beat the Bookie odds). If signal persists across two independent odds sources, it's real.
- **Analysis b) Live prediction:** Download minute-interval goal/corner data. Compute patterns: "probability of Over 2.5 given 0 goals at minute X" from historical minute-level data. Feed into future in-play model (P3.4).
- **Output:** Validated (or invalidated) Singapore/Scotland ROI signal + minute-interval lookup table
- **Depends on:** Manual check of league availability
- **Value:** Independent validation of our best signals + rare in-play training data

### P5.3 — OddAlerts API: live multi-bookmaker odds
- Evaluate OddAlerts API/Pro plan for real-time odds from 20+ bookmakers
- **Use case a):** Replace or supplement Kambi scraping — broader bookmaker coverage for live odds snapshots
- **Use case b):** Real-time sharp money detection using P5.1's trained model
- **Use case c):** Better odds comparison for frontend users (Pro tier feature)
- **Depends on:** P5.1 (need the sharp/soft model first to know what to do with multi-bookmaker data)
- **Value:** 20+ bookmakers >> our current 2-3. Both for internal edge AND as a paid product feature.

---

## Notes

- **Don't optimize the model until you can measure it.** P1.1 (audit trail) is the prerequisite for everything.
- **Singapore S.League (+27.5% ROI) and Scotland League Two (+12.3%/+21%) are the strongest signals.** Any data improvement that helps these leagues is highest priority.
- **The question "should we sell this or keep it?" depends entirely on P4.1.** If the model + AI consistently beats closing lines by 3%+, that's genuine alpha worth keeping. If it's 0.5%, sell the analysis as a product.
- **External data priority:** P5.1 (European Soccer DB) is actionable immediately — download + parse. P5.2 (Footiqo) needs a manual league check first. P5.3 (OddAlerts) is blocked on P5.1 analysis.
