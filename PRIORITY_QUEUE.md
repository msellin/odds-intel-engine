# OddsIntel — Master Priority Queue

> Single source of truth for all open tasks across ROADMAP.md, BACKLOG.md, and MODEL_ANALYSIS.md.
> Synthesised after external AI architecture review (2026-04-28).
> Update status here as tasks complete; propagate back to source docs.
> Last updated: 2026-04-28

---

## Tier 0 — Do This Week (foundation for everything)

| # | ID | Task | Effort | Status | Notes |
|---|-----|------|--------|--------|-------|
| 1 | B-ML1 | Pseudo-CLV for all ~280 daily matches | 2-3h | ✅ Done 2026-04-28 | `(1/open) / (1/close) - 1` for every finished match. Grows ML training data 2-5/day → 280/day |
| 2 | B-ML2 | `match_feature_vectors` nightly ETL (wide ML training table) | 1 day | ✅ Done 2026-04-28 | Pivots signals + predictions + ELO/form → wide row per match. The actual ML training table |
| 3 | CAL-1 | Calibration validation script | 2h | ✅ Done 2026-04-28 | `scripts/check_calibration.py` — predicted vs actual win rate in 5% bins |
| 4 | S1+S2 | Migration 010: `source` on predictions + `match_signals` table | 2-3h | ✅ Done 2026-04-28 | Unique constraint on (match_id, market, source). Append-only signal store |

---

## Tier 1 — Next 1-2 Weeks

| # | ID | Task | Effort | Status | Notes |
|---|-----|------|--------|--------|-------|
| 5 | S3 | Wire existing signals into match_signals | 1 day | ⬜ | odds_drift, news_impact, injuries, lineup, ELO diff, form, fixture importance — all already computed |
| 6 | S4 | Referee signals (referee_stats table + daily enrichment) | 1 day | ⬜ | Data in matches.referee already. Depends on S2 |
| 7 | S5 | Fixture importance signal (standings → 0-1 urgency score) | <2h | ⬜ | Depends on S2 |
| 8 | B-ML3 | First meta-model: 5-feature logistic regression, target=pseudo_clv>0 | 1 day | ⬜ | Train after ~11 days when 3000+ pseudo-CLV rows exist. Features: ensemble_prob, odds_drift, elo_diff, league_tier, model_disagreement |
| 9 | STRIPE | Stripe setup: Pro €19/mo + Elite €49/mo products, keys to Vercel | External | ⬜ | Blocking Milestone 2 |
| 10 | B3 | Tier-aware data API (Next.js strips fields by tier) | 1-2 days | ⬜ | Blocking Milestone 2 |
| 11 | SENTRY | Sentry error monitoring (free tier) | 1h | ⬜ | Pre-launch checklist item |

---

## Tier 2 — 2-4 Weeks

| # | ID | Task | Effort | Status | Notes |
|---|-----|------|--------|--------|-------|
| 12 | PLATT | Platt scaling once 500+ predictions have outcomes | 1 day | ⬜ | Replaces/complements tier-specific shrinkage. ~mid-May 2026 |
| 13 | P5.1 | European Soccer DB (Kaggle): 13-bookmaker sharp/soft analysis | 1-2 days | ⬜ | `bookmaker_sharpness_rankings.csv` + `sharp_money_signal` feature. Strongest unused signal |
| 14 | PIN-1 | Pinnacle anchor signal: `model_prob - pinnacle_implied` as feature | 2-3h | ⬜ | Low effort. Depends on P5.1 to confirm Pinnacle is in our 13 bookmakers |
| 15 | BDM-1 | Bookmaker disagreement signal: `max(implied) - min(implied)` across 13 bookmakers | 1h | ⬜ | Data already in odds_snapshots |
| 16 | F8 | Stripe integration (Pro + Elite, webhook, tier column update) | 2-3 days | ⬜ | Blocking Milestone 2 |
| 17 | F5 | Value bets page redesign (free=teaser, Pro=directional, Elite=full picks) | 1-2 days | ⬜ | Blocking Milestone 3 |
| 18 | ALN-1 | Dynamic alignment thresholds (300+ settled bot bets → ROI by alignment bin) | 2h | ⬜ | Needs actual placed bets — pseudo-CLV does NOT substitute |

---

## Tier 3 — 1-2 Months

| # | ID | Task | Effort | Status | Notes |
|---|-----|------|--------|--------|-------|
| 19 | B6 | Singapore/South Korea odds source (Pinnacle API or OddsPortal) | Unknown | ⬜ | +27.5% ROI signal has no live odds feed. Biggest gap |
| 20 | P5.2 | Footiqo: validate Singapore/Scotland ROI with independent 1xBet closing odds | Manual first | ⬜ | Independent validation. If ROI holds on 2nd source, it's real |
| 21 | P3.1 | Odds drift as XGBoost input feature (model retraining) | 1-2 days | ⬜ | Currently veto filter only. Strongest unused signal once data is there |
| 22 | P3.3 | Player-level injury weighting (weight by position/market value) | 2-3 days | ⬜ | "Starting striker out" ≠ "3rd-choice GK out" |
| 23 | S6-P2 | Graduate meta-model to XGBoost + full signal set (1000+ bot bets) | 2-3 days | ⬜ | After alignment thresholds validated |
| 24 | P4.1 | Audit trail ROI comparison: stats-only vs after-AI vs after-lineups | 1 day | ⬜ | Proves value of each information layer. Needed for Elite tier pricing |
| 25 | P3.5 | Feature importance tracking per league | 1 day | ⬜ | Which signals matter in which markets |
| 26 | F10 | My bets / tip tracking (user_bets table, personal P&L) | 2 days | ⬜ | Skip until Stripe + Elite launch |
| 27 | F7 | Stitch redesign (landing + matches page) | Awaiting designs | ⬜ | Parked until after M1 go-live |

---

## Tier 4 — 2-3 Months (needs data accumulation)

| # | ID | Task | Effort | Status | Notes |
|---|-----|------|--------|--------|-------|
| 28 | P3.4 | In-play value detection model (minute X state → final result) | 2-3 weeks | ⬜ | Needs 500+ completed matches in live_match_snapshots |
| 29 | P4.2 | A/B bot testing framework (parallel bots with/without AI) | 1-2 days | ⬜ | Needs audit trail + data |
| 30 | P4.3 | Live odds arbitrage detector (cross-bookmaker real-time) | 1-2 days | ⬜ | P2.1 per-bookmaker odds ✅ — can build but low priority |
| 31 | P5.3 | OddAlerts API evaluation (20+ bookmakers real-time) | Research | ⬜ | Depends on P5.1 sharp/soft model |
| 32 | OTC-1 | Odds trajectory clustering (DTW on full timelines, cluster shapes) | 1-2 weeks | ⬜ | Needs 1000+ matches with 6+ snapshots each |
| 33 | P3.2 | Stacked ensemble meta-learner (logistic regression: when Poisson vs XGBoost) | 1-2 days | ⬜ | Needs settled bets with both predictions stored |

---

## Tier 5 — Future / Speculative

| # | ID | Task | Notes |
|---|-----|------|-------|
| 34 | SLM | Shadow Line Model: predict what opening odds *should be*, fire before market corrects | Blocked on opening odds timestamp storage |
| 35 | MTI | Managerial Tactical Intent: press conference classification | Blocked on reliable transcript sources across leagues |
| 36 | RVB | Referee/Venue full bias features (beyond S4) | Venue-level stats not yet collected |
| 37 | WTH | Weather signal (OpenWeatherMap, free) | Low effort, defer until O/U becomes a focus market |

---

## Key Thresholds to Watch

| Milestone | Query | Target | Current |
|-----------|-------|--------|---------|
| Platt scaling ready | `SELECT COUNT(*) FROM predictions p JOIN matches m ON p.match_id = m.id WHERE m.status = 'finished'` | 500+ | ~? |
| Meta-model Phase 1 ready | `SELECT COUNT(*) FROM matches WHERE status = 'finished' AND pseudo_clv_home IS NOT NULL` | 3000+ | 0 (just built) |
| Alignment threshold validation | `SELECT COUNT(*) FROM simulated_bets WHERE result != 'pending' AND alignment_class IS NOT NULL` | 300+ | ~? |
| Meta-model Phase 2 ready | `SELECT COUNT(*) FROM simulated_bets WHERE result != 'pending' AND dimension_scores IS NOT NULL AND clv IS NOT NULL` | 1000+ | ~? |
| In-play model ready | `SELECT COUNT(DISTINCT match_id) FROM live_match_snapshots` | 500+ | ~? |
