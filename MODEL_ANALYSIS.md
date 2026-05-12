# OddsIntel Prediction Model — Analysis & Improvement Roadmap

> Internal agent working doc — assessment history, improvement roadmap, and implementation status.
> For the full technical model specification (features, calibration pipeline, known limitations), see **MODEL_WHITEPAPER.md**.
> Written so any agent or developer can pick this up and start working.

---

## 1. External Assessment Consensus

Two rounds of independent AI evaluation shaped the current architecture.

### Round 1 (2026-04-27): Alignment Filter Architecture

Submitted to 4 AI evaluators. Key verdicts that were implemented:

| Verdict | Status |
|---------|--------|
| Keep ensemble (don't replace with dimension counting) | ✅ Done |
| Alignment as BET FILTER (LOG-ONLY pending validation) | ✅ Implemented |
| Odds movement is most valuable unused signal | ✅ Soft penalty + veto |
| Fix calibration (market shrinkage by tier) | ✅ Done |
| Kelly fraction for stake sizing | ✅ Done |
| H2H is mostly noise — remove from alignment | ✅ Removed |
| Real edge = lower leagues + information speed | ✅ Bot config confirmed |

### Round 3 (2026-05-06): Live Calibration Failure Analysis

Submitted to 4 AI evaluators after 77 settled bets showed systematic failure on 1X2 home bets (42% predicted vs 26% actual). Key findings and actions taken:

| Verdict | Status |
|---------|--------|
| Failure is **conditional miscalibration at high odds**, not global — 0.30-0.40 bin (23 bets, 13% actual) is the problem | ✅ Confirmed |
| Single Platt sigmoid cannot fix conditional miscalibration — the 0.40-0.50 bin is well-calibrated; both cannot be fixed by one sigmoid | ✅ Confirmed. Platt refit will not self-correct |
| Switch shrinkage anchor to Pinnacle specifically (not market avg) — Pinnacle vig 2-3% vs soft 5-8% | Tracked as CAL-PIN-SHRINK / PIN-6 |
| Hard veto when `calibrated_prob - pinnacle_implied > 0.12` on home bets | ✅ Done 2026-05-06 — PIN-VETO implemented. Empirically catches 22/34 losses, filters 6/40 wins |
| Add sharp_consensus gate for 1X2 (skip when `sharp_consensus_home < -0.02`) | Tracked as CAL-SHARP-GATE |
| Odds-conditional α boost: `if odds > 3.0 → alpha += 0.20` | Tracked as CAL-ALPHA-ODDS |
| Draw inflation factor (`raw_draw × 1.08`, renormalize) — Dixon-Coles only patches (0,0)-(1,1) corner | Tracked as CAL-DRAW-INFLATE |
| Replace Platt with 2-feature logistic `[shrunk_prob, log(odds)]` at 300+ settled bets | Tracked as CAL-PLATT-UPGRADE |
| Add `model_prob - pinnacle_implied`, `odds_at_pick`, `time_to_kickoff` to meta-model; drop/combine `overnight_line_move` (collinear with `odds_drift`) | Updated in B-ML3 notes |
| CLV is the primary long-run EV validator — track per market, not just aggregate | Already tracked via B-ML1 pseudo-CLV |

### Round 2 (2026-04-28): Multi-Signal Architecture

Submitted to 4 AI evaluators. Key verdicts that were implemented:

| Verdict | Status |
|---------|--------|
| Two-stage: keep outcome model (Stage 1) separate from meta-model (Stage 2) | ✅ Already correct |
| Pseudo-CLV for ALL matches (not just bets) → 10x training data | ✅ Done (B-ML1) |
| Materialized wide table for ML training (not EAV directly) | ✅ Done (B-ML2) |
| Start with 5-feature logistic regression before full XGBoost | See B-ML3 in PRIORITY_QUEUE.md |
| Calibration before everything — validate on settled predictions | See CAL-1 (done) in PRIORITY_QUEUE.md |
| Bookmaker disagreement + Pinnacle anchor as signals | See BDM-1 (done), PIN-1 in PRIORITY_QUEUE.md |

---

## 2. Current Architecture (Implemented)

```
┌─────────────────────────────────────────────────────────────┐
│ Stage 1: ENSEMBLE (running since 2026-04-27)                │
│                                                             │
│  XGBoost Classifier ──┐                                     │
│                       ├── 50/50 blend → calibrated_prob     │
│  XGBoost Poisson ─────┘                                     │
│                                                             │
│  Calibration (2-stage + veto gates, updated 2026-05-06):     │
│    Stage 1: tier-specific market shrinkage                   │
│      α = model weight {T1: 0.20, T2: 0.30, T3: 0.50, T4: 0.65} │
│      Anchor: Pinnacle-implied (soft-book fallback) ✅CAL-PIN-SHRINK │
│      Longshot: odds>3.0 → α = max(α-0.20, 0.10) ✅CAL-ALPHA-ODDS │
│    Stage 2: calibration correction per market                │
│      1X2: Platt sigmoid — sigmoid(a*shrunk+b)               │
│        α/β fitted weekly from predictions table              │
│      O/U: 2-feature logistic ✅CAL-PLATT-UPGRADE 2026-05-12 │
│        sigmoid(w0*shrunk + w1*log(odds) + intercept)         │
│        Fitted from simulated_bets (needs odds at bet time)   │
│        1X2 upgrade pending (≥300 settled 1X2 needed)         │
│    Veto gates (1X2 home):                                    │
│      cal_prob - pinnacle_implied > 0.12 → skip (PIN-VETO)   │
│        Empirical: catches 22/34 losses, filters 6/40 wins    │
│        Pending PIN-3: extend to draw/away/O/U                │
│      sharp_consensus_home < -0.02 → skip ✅CAL-SHARP-GATE   │
│  Disagreement: abs(poisson - xgb) stored per bet            │
│  Fallback: Tier D uses AF /predictions probability          │
├─────────────────────────────────────────────────────────────┤
│ Stage 2: EDGE + FILTER + SIZING (running since 2026-04-27)  │
│                                                             │
│  edge = calibrated_prob - (1 / odds)                        │
│  kelly = (calibrated_prob * odds - 1) / (odds - 1)         │
│  stake = min(kelly * 0.15 * bankroll, 0.010 * bankroll)     │
│                                                             │
│  Multipliers: × tier_mult × data_tier_mult × lineup_mult    │
│  Alignment filter: LOG-ONLY (pending 300 bot bet validation) │
│  Odds veto: >10% adverse move → hard skip                   │
├─────────────────────────────────────────────────────────────┤
│ Stage 3: SIGNAL COLLECTION (running since 2026-04-28)       │
│                                                             │
│  match_signals: append-only EAV signal store                │
│  pseudo_clv: computed for ALL ~280 matches/day              │
│  match_feature_vectors: wide ML training table (nightly)    │
│  source on predictions: poisson/xgboost/af/ensemble rows    │
├─────────────────────────────────────────────────────────────┤
│ Stage 4: META-MODEL (pending data accumulation)             │
│                                                             │
│  Phase 1 (~mid-May 2026): 8-feature logistic regression     │
│    Target: pseudo_clv > 0 (all matches, not just bets)      │
│    Ready when: 3000+ rows in match_feature_vectors (~11d)   │
│                                                             │
│  Feature design (META-2 + calibration review 2026-05-06):   │
│    DO NOT use raw fundamentals (ELO, form) — market         │
│    already priced those in. Use market structure gaps:      │
│    • edge = ensemble_prob − market_implied_home             │
│    • odds_drift (open → now implied prob delta)             │
│    • bookmaker_disagreement (max−min implied)               │
│    • model_disagreement (|poisson_prob − xgboost_prob|)     │
│    • league_tier (T1–T4 data quality proxy)                 │
│    • news_impact_score (Gemini — validate AUC>0.52 first)  │
│    • odds_volatility (std of implied prob, 24h)             │
│    • model_prob − pinnacle_implied (likely strongest signal) │
│    • odds_at_pick (raw) — 5% edge at 1.5 ≠ 5% edge at 5.0 │
│    • time_to_kickoff (hours) — early bets ≠ late bets       │
│    DROP overnight_line_move — correlated 0.7+ with odds_drift│
│                                                             │
│  Phase 2 (~June 2026): XGBoost + full signal set            │
│    Replaces fixed edge thresholds with ML-predicted EV      │
│    Ready when: 1000+ settled bot bets with alignment data   │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Alignment Filter (LOG-ONLY — pending validation)

Computes 7 independent dimension scores per bet. Currently stored but not used for decisions.

| # | Dimension | Scale | Status |
|---|-----------|-------|--------|
| 1 | Strength Differential (ELO diff) | ±1 | ✅ Stored |
| 2 | Form Momentum (PPG trend) | ±1 | ✅ Stored |
| 3 | xG Over/Underperformance | ±1 | ✅ Stored |
| 4 | News / External Info (Gemini) | -1 to +1 | ✅ Stored |
| 5 | Odds Movement direction | ±1 | ✅ Stored |
| 6 | Situational Context (motivation/rest) | ±1 | ✅ Stored |
| **H2H** | Removed — too noisy at small samples | — | ❌ Removed |

**Activation:** After 300+ settled bot bets show ROI increases monotonically with alignment class (HIGH/MED/LOW). Script: `python scripts/validate_improvements.py`.

---

## 4. Current Task List

> All open tasks with priorities are in **`PRIORITY_QUEUE.md`**. The items below are for quick reference only.

**Do now (data foundation):**
- Run `python scripts/check_calibration.py` once 200+ predictions have outcomes — **segment by `model_version`**, do not mix versions (see MODEL_WHITEPAPER.md §5.2)
- Check readiness queries in Section 10 to know when to train meta-model

**Next milestones:**
- 3000+ `match_feature_vectors` rows → run `python3 workers/model/train.py` (ready — 28 features, `load_training_data()` loads from DB automatically). Also train Phase 1 logistic regression meta-model. (~mid-May/June)
- 300+ settled bot bets with alignment → activate alignment filter (~late May)
- 1000+ settled bot bets → train Phase 2 XGBoost meta-model (~June)

**See PRIORITY_QUEUE.md for the full ordered task list (57 items as of 2026-04-29).**

---

## 5. What to Store per Bet (Schema Addition)

Add to `simulated_bets` table:

```sql
-- New columns
dimension_scores      JSONB,     -- {"strength": 0.7, "form": 0.4, ...}
alignment_count       INTEGER,   -- e.g., 5
alignment_total       INTEGER,   -- e.g., 7
alignment_class       TEXT,      -- "HIGH" / "MEDIUM" / "LOW"
kelly_fraction        FLOAT,     -- Kelly-optimal fraction
odds_at_open          FLOAT,     -- opening odds (for CLV)
odds_at_close         FLOAT,     -- closing odds (for CLV)
odds_drift            FLOAT,     -- implied prob change since open
model_disagreement    FLOAT,     -- abs(xgboost_prob - poisson_prob)
calibrated_prob       FLOAT,     -- after shrinkage/Platt correction
news_impact_score     FLOAT,     -- -1 to +1 from Gemini
lineup_confirmed      BOOLEAN,   -- was XI known at bet time?
```

This data enables all future analysis: ROI by alignment bin, CLV tracking, meta-model training.

---

## 6. Validation Checkpoints

Before trusting any change in production paper trading:

| Change | Validation Required |
|--------|-------------------|
| Calibration fix | Predicted prob vs actual win rate plot (5% bins). Should be near-diagonal. |
| Odds movement features | Backtest ROI with/without features. Must improve on held-out season. |
| Alignment filter | Track ROI by alignment bin for 500+ bets. ROI must increase monotonically with alignment. |
| Kelly sizing | Simulate bankroll curves with Kelly vs flat stake on historical bets. Kelly should show higher Sharpe ratio. |
| Market shrinkage | Compare `edge_before_shrinkage` vs `edge_after_shrinkage` hit rates. After-shrinkage should be better calibrated. |
| Meta-model | Only attempt after 1000+ bets with all dimension scores stored. |

---

## 7. Key Formulas Reference

```python
# Edge (current)
edge = model_prob - (1 / odds)

# EV (correct formula — accounts for odds magnitude)
ev = model_prob * odds - 1

# Kelly fraction (for ranking and stake sizing)
kelly = (model_prob * odds - 1) / (odds - 1)

# Fractional Kelly stake (0.15× Kelly, 1.0% cap — updated 2026-04-29)
stake = min(kelly * 0.15 * bankroll, 0.010 * bankroll)

# Market-shrunk probability (tier-specific calibration — updated 2026-04-29)
# alpha = {T1: 0.20, T2: 0.30, T3: 0.50, T4: 0.65}
adjusted_prob = alpha * model_prob + (1 - alpha) * implied_prob

# CLV (closing line value — ground truth for edge detection)
clv = (odds_at_pick / odds_at_close) - 1

# Rank score for UI
rank = kelly * alignment_multiplier
```

---

## 8. Summary: Where the Edge Actually Lives

| Source | Evidence | Priority |
|--------|----------|----------|
| **Fix overconfidence** | 10-15% leak is costing more than any feature will gain | P1 |
| **Lower league inefficiency** | +5% ROI in Tier 3-4 confirmed across backtests | P5 |
| **Information speed** (news, lineups) | Only edge not already priced by bookmakers | P6 |
| **Odds movement** (sharp money signal) | Most valuable unused data we already collect | P2 |
| **Alignment filtering** (remove fragile bets) | Reduces volume but improves ROI by cutting bad bets | P3 |
| **Kelly sizing** (variance-adjusted stakes) | Proper capital allocation vs flat stakes | P4 |
| **Better statistical features** | Minimal — bookmakers already model form/ELO/xG | Low |

---

## 9. Implementation Status & Deferred Items (Updated 2026-04-27)

### Implemented (active in production pipeline)

| Priority | What | How | Key Decision |
|----------|------|-----|-------------|
| P1 | Calibration | Tier-specific α: T1=0.20, T2=0.30, T3=0.50, T4=0.65 (updated 2026-04-29) | Lower α for higher tiers — market is more efficient in T1/T2, trust it more |
| P2 | Odds movement | Soft penalty on Kelly for adverse drift, hard veto only >10% | Soft penalty over hard veto — markets overshoot |
| P3 | Alignment | 4 external-signal dimensions, LOG-ONLY mode | Only external signals (odds_move, news, lineup, situational). ELO/form/xG removed — already in model. |
| P4 | Kelly sizing | 0.15× Kelly, 1.0% max cap, data-tier multiplier (updated 2026-04-29) | Reduced from 0.25×/1.5% — 6 concurrent bots were stacking to 9% bankroll exposure |
| P6 | News timing | 4x/day: 09:00, 12:30, 16:30, 19:30 UTC | Catches lineup confirmations 1-2h before kickoff |

### Implemented (logging only, not affecting decisions)

| Item | What | Activate When |
|------|------|--------------|
| Alignment filter | Stores dimension_scores + alignment_class on every bet | After 300+ settled bets show ROI correlating with alignment class |
| Alignment thresholds | HIGH/MEDIUM/LOW at 0.75/0.50 (provisional) | Replace with data-driven thresholds from ROI inflection points |

### New prerequisite tasks (from 2026-04-28 external review)

> 4 independent AI evaluations unanimously recommended these before building the meta-model.
> Both are low-effort and eliminate the main data accumulation bottleneck.

| Item | What | Effort | Unblocks |
|------|------|--------|---------|
| **Pseudo-CLV for all matches** | Compute `(1/opening_odds) / (1/closing_odds) - 1` for ALL ~280 daily fixtures, not just bet matches. Store in `matches` or `odds_snapshots`. Grows training dataset from 2-5 labeled rows/day to ~280/day. | Low — closing odds already in odds_snapshots | Meta-model in weeks not months |
| **match_feature_vectors table** | Nightly ETL that pivots `match_signals` EAV rows into a wide table: one row per match, one column per signal, value closest to kickoff. This is the actual ML training table. | Medium — ~1 day | Clean ML training input without complex joins |

### Deferred (need data accumulation)

| Item | What | Data source | Min data needed | Estimated timeline |
|------|------|-------------|-----------------|-------------------|
| **Platt scaling** | ✅ DONE (2026-04-30). Sigmoid post-hoc calibration per market (1x2_home/draw/away). `scripts/fit_platt.py` fits α/β → `model_calibration` table. Applied in `calibrate_prob()` after tier shrinkage. Weekly recalibration in settlement workflow (Sundays). | — | — | — |
| **XGBoost in live pipeline** | ✅ DONE (2026-04-27). Loads v9a_202425 saved models, computes features from CSV, blends 50/50 with Poisson for Tier A teams. `workers/model/xgboost_ensemble.py` | — | — | — |
| **Model disagreement** | ✅ DONE. `model_disagreement = abs(poisson_prob - xgb_prob)` stored on every Tier A bet. | — | — | — |
| **Dynamic alignment thresholds** | Set HIGH/MED/LOW cutoffs from ROI inflection points | `simulated_bets` with alignment_class populated (bot bets only, ~10-20/day) | 300+ settled bot bets with alignment data | ~3-4 weeks (late May 2026). **Note: requires actual placed bets — pseudo-CLV does NOT substitute here.** |
| **Meta-model** | Second-stage model predicting bet profitability, target=CLV not binary profit | `matches` with pseudo-CLV (all fixtures) + `match_feature_vectors` (wide table) | 3000+ matches with pseudo-CLV populated (~11 days at 280/day) | **~mid-May 2026** (was July 2026 — unblocked by pseudo-CLV approach). Start with 5-feature logistic regression. |

**How to check readiness:** Run these queries against Supabase to see if you've hit the thresholds:
```sql
-- Platt scaling: predictions with known outcomes
SELECT COUNT(*) FROM predictions p
JOIN matches m ON p.match_id = m.id
WHERE m.status = 'finished';
-- Need: 500+

-- Dynamic alignment: settled bets with alignment data (requires actual placed bets)
SELECT COUNT(*) FROM simulated_bets
WHERE result != 'pending' AND alignment_class IS NOT NULL;
-- Need: 300+

-- Meta-model: all matches with pseudo-CLV (not just bot bets)
SELECT COUNT(*) FROM matches
WHERE status = 'finished' AND pseudo_clv IS NOT NULL;
-- Need: 3000+ (~11 days at 280/day)

-- Meta-model (fallback check using bot bets only):
SELECT COUNT(*) FROM simulated_bets
WHERE result != 'pending' AND dimension_scores IS NOT NULL AND clv IS NOT NULL;
-- Need: 1000+
```

### Deprioritized (verified low impact)

| Item | Finding | Action |
|------|---------|--------|
| **Negative binomial for O/U** | Overdispersion ratio = 1.016 (barely above 1.0). 0-0 draws underestimated by 8% but 4+ goals dead-on. Tier 3 actually underdispersed (0.986). | Not worth switching. 1X2 is our profitable market. Revisit only if O/U becomes a focus. |
| **H2H dimension** | 3/4 independent assessments flagged as noise. Small samples, narrative bias, already priced by market. | Removed from alignment dimensions. |

### Validation cadence

Check these milestones against the DB queries above.

| Milestone | Action | Script |
|-----------|--------|--------|
| **Now** | Build pseudo-CLV computation for all matches | New script needed |
| **Now** | Build `match_feature_vectors` materialized ETL table | New script/migration needed |
| 200+ predictions with results | First calibration ECE check (uses ALL predictions, not just bets) | `python scripts/validate_improvements.py` |
| 50+ settled bot bets | First alignment + Kelly check | Same script |
| 500+ predictions with results | Fit Platt scaling (replace/complement tier-specific shrinkage) | New script needed |
| **3000+ matches with pseudo-CLV** (~11 days) | Train first meta-model: 5-feature logistic regression on all matches | New script needed |
| 300+ settled bot bets | Validate alignment filter (check ROI by alignment bin on actual bets) | Same script |
| 1000+ settled bot bets | Graduate meta-model: XGBoost with full signal set | New script needed |

---

## 10. AI Usage Roadmap (Consolidated from 4 Independent Assessments, 2026-04-27)

> Where AI adds value beyond current usage. Assessed by 4 independent evaluators against our
> actual data stack. Only ideas that work with data we have or are actively collecting are included.
> Generic "use deep learning" suggestions were filtered out.

### Key Takeaway

Our current AI usage (Gemini for news, XGBoost/Poisson for prediction) covers the obvious spots.
The next gains come from three areas, in order of ROI:

1. **Speed** — getting information before odds adjust (structured news extraction, lineup confidence)
2. **Live data** — exploiting our 5-min match snapshots for in-play edge (all 4 assessments agree)
3. **Market intelligence** — understanding odds movement patterns, not just magnitude (trajectory clustering, regime classification)

### Data Infrastructure Upgrade (2026-04-28)

Migrated from fragile multi-scraper setup to **API-Football Ultra ($29/mo)** as primary data source.
See `DATA_SOURCES.md` for full details.

**Impact on model improvement timeline:**
- Settlement now works reliably (164+ finished matches/day from API-Football vs 0 on day 1)
- Post-match stats (shots, possession, corners) now collected automatically after settlement
- Multi-bookmaker odds (13 bookmakers) stored on every match — feeds Platt scaling + CLV
- Fixtures with venue + referee stored — new potential features
- **All deferred items below are now unblocked** — data accumulation has started

**Backfill plan:** Use spare API quota (~67K requests/day) to fetch historical matches with stats + odds.
This enables retraining XGBoost on broader data sooner than waiting for daily accumulation.

### Tier 1: Do Now (low effort, uses existing data)

#### 11.1 Structured News Scoring + Lineup Confidence ✅ DONE
**Consensus: 4/4 assessments recommend this.**
**Implemented:** 2026-04-27 in `workers/jobs/news_checker.py`

Gemini prompt rewritten to output structured JSON per bet:
- Per-player impact scores with position and severity (`players_out`, `players_doubtful`, `players_returning`)
- `lineup_confidence` (0.0-1.0): how certain is the XI
- `home_net_impact` / `away_net_impact`: net effect on each team
- `news_impact_score`: computed per bet based on selection direction, stored in `simulated_bets`
- `lineup_confirmed`: boolean (true when lineup_confidence >= 0.9), stored in `simulated_bets`
- Each flagged player also stored as structured row in `news_events` table (with impact_magnitude)
- `lineup_confidence` wired into alignment dimension 3 (`_dim_lineup` in `improvements.py`)

**Validation (not yet possible — needs data):**
- After 100+ matches with news flags: correlate `news_impact_score` magnitude with actual outcome divergence from base model
- After 100+ matches with lineup data: check whether `lineup_confidence=0.9` correctly predicts actual XI 90% of the time
- **Timeline: ~1-2 weeks** (4 runs/day × 20-40 matches/run = 80-160 data points/day)
- **How to check:** `SELECT COUNT(*) FROM simulated_bets WHERE news_impact_score IS NOT NULL AND result != 'pending';` — need 100+

#### 11.2 LLM Team Name Resolution ✅ DONE
**Source: Assessment #4. Unique, highly practical.**
**Implemented:** 2026-04-27 as `scripts/resolve_team_names.py`

Script batch-resolves 204 unique unmatched team names against known teams from targets_poisson_history.csv + targets_global.csv using Gemini Flash. Results cached in `data/processed/llm_team_name_cache.json`, optionally written to `KAMBI_TO_FOOTBALL_DATA` in `team_names.py` with `--apply` flag.

**Validation (immediate — run now):**
1. Before: `wc -l data/logs/unmatched_teams.log` → 2,287 entries, 204 unique teams
2. Run: `python scripts/resolve_team_names.py --apply`
3. After: Run pipeline, check log shrinks. Count resolved teams in output.
4. Manual spot-check: verify 20 random resolved pairs are correct
- **Status: READY TO VALIDATE** — run the script to get immediate before/after numbers

#### 11.3 Market-Implied Team Strength Feature ✅ DONE
**Source: Assessment #4. Novel, uses existing data.**
**Implemented:** 2026-04-27 as `compute_market_implied_strength()` in `supabase_client.py`

Computes rolling 5-match average of a team's market-implied win probability from `odds_snapshots`. Queries recent finished matches for the team, gets latest 1X2 odds, computes 1/odds as implied probability, averages.

**Validation (deferred — needs XGBoost + data):**
- Feature is computed and callable but not yet wired into any model (live pipeline is Poisson-only)
- Wire as XGBoost input feature when XGBoost goes live in pipeline
- Backtest with/without feature on historical data to measure impact
- **Blocked on:** XGBoost in live pipeline + enough odds_snapshots for finished matches
- **Timeline: ~4-6 weeks** (needs accumulated match data with odds + XGBoost integration). **Accelerated** — API-Football now stores 13-bookmaker odds per match
- **How to check readiness:** `SELECT COUNT(DISTINCT m.id) FROM matches m JOIN odds_snapshots o ON m.id = o.match_id WHERE m.status = 'finished';` — need 200+

### Tier 2: Do Soon (medium effort, high value)

#### 11.4 Daily Post-Mortem LLM Analysis ✅ DONE
**Consensus: 2/4 assessments recommend this.**
**Implemented:** 2026-04-27 in `workers/jobs/settlement.py` → `run_post_mortem()`

Runs automatically after settlement. Sends all settled bets (with full context: calibrated_prob, odds_drift, CLV, alignment, news_impact) to Gemini Flash. Classifies each loss as:
- `VARIANCE` — reasonable bet, bad luck
- `INFORMATION_GAP` — missed news/odds movement
- `MODEL_ERROR` — model was wrong about team strength
- `TIMING` — right pick, should have waited for lineup

Also outputs: daily summary, patterns noticed, one actionable suggestion.
Results stored in `model_evaluations` table (market="post_mortem", notes=JSON analysis).

**Cost:** ~$0.01-0.02/day.
**Validation:** After 2 weeks of daily post-mortems, check:
- Are loss categories consistent? (e.g., "70% of T1 losses are MODEL_ERROR" → confirms T1 is hard)
- Do suggestions lead to measurable changes?
- **How to check:** `SELECT notes FROM model_evaluations WHERE market = 'post_mortem' ORDER BY date DESC LIMIT 7;`
- **Timeline:** First useful patterns after ~14 days of settlement data

#### 11.5 RSS News Extraction Pipeline (Speed Edge) — DEFERRED
**Source: Assessment #4. Directly targets our core thesis.**
**Deferred:** Cost $30-90/month. Will revisit when model proves profitable enough to justify.

#### 11.6 Cross-Match Correlation / Exposure Management ✅ DONE
**Source: Assessment #4. Simple risk management we don't do.**
**Implemented:** 2026-04-27 in `workers/jobs/daily_pipeline_v2.py` → `_check_exposure_concentration()`

After all bets are placed, checks if any bot has 3+ bets in the same league on the same day. Logs a warning with total stake exposure. Correlated outcomes (same league matchday) can amplify drawdowns.

Currently: warning-only. Future: auto-reduce stakes proportionally when correlated.

**Cost:** $0 (pure computation).
**Validation:** After 4+ weeks of data, compare:
- Drawdown on days with exposure warnings vs days without
- **How to check:** Cross-reference pipeline logs with daily P&L from settlement
- **Timeline:** ~4 weeks for enough data points

### Tier 3: Do When Data Allows (2-6 months)

#### 11.7 In-Play Model at Fixed Checkpoints
**Consensus: 4/4 assessments recommend this. Highest untapped ROI.**

Train a gradient boosting model: given match state at minute 30/45/60/75 (score, xG, shots, possession, cards, live odds), predict P(Home Win), P(Over 2.5), P(BTTS). Compare to live odds for in-play edge.

Key insight from our own data: "High-xG game, 0-0 at minute 10-15 → O/U odds drift upward → potential value."

**Implementation:** Train LightGBM on `live_match_snapshots` at fixed minute checkpoints.
**Min data needed:** ~500 completed matches × 10 snapshots = 5,000 snapshot rows.
**Expected impact:** +2-5% ROI on in-play bets. Opens entirely new revenue stream.
**Timeline:** July-August 2026 (2-3 months of live data collection).
**Status (2026-04-28):** Live tracker needs rewiring to use API-Football live endpoint (task pending). Once wired, data accumulation starts immediately.

```sql
-- Check readiness:
SELECT COUNT(DISTINCT match_id) FROM live_match_snapshots;
-- Need: 500+
```

#### 11.8 Odds Trajectory Clustering
**Consensus: 2/4 assessments recommend this. Novel approach.**

Use DTW (Dynamic Time Warping) to cluster full odds timelines by shape: "steady shortening", "late steam move", "reversal", "stable". Different shapes mean different things even if total drift is identical.

**Implementation:** Cluster historical odds trajectories, map clusters to outcomes.
**Min data needed:** ~1,000 matches with full 6+ snapshot timelines.
**Expected impact:** Better alignment scoring, differentiates sharp vs public money.
**Timeline:** Late 2026.

```sql
-- Check readiness:
SELECT COUNT(*) FROM (
  SELECT match_id, COUNT(*) as snapshots
  FROM odds_snapshots GROUP BY match_id HAVING COUNT(*) >= 6
) sub;
-- Need: 1000+
```

#### 11.9 CLV Prediction / Meta-Model
**Consensus: 3/4 assessments recommend this. Target CLV, not binary profit.**

Train a model predicting: "will this bet beat the closing line?" Features: model_prob, edge, kelly, alignment, odds_drift, league_tier, hours_to_kickoff. Target: CLV (continuous), not won/lost (binary).

**Min data needed:** 1,000+ settled bot bets with all dimension fields populated.
**Timeline:** July 2026.

### Tier 4: Future / Speculative

#### 11.10 Shadow Line Model
**Source: Assessment #3. Most original idea across all assessments.**

Instead of predicting match outcomes, predict what the bookmaker's opening odds *should be*. If your model predicts opening at 2.00 but bookie opens at 2.30, fire immediately before sharp money corrects. Turns CLV into the primary objective from the start.

**Blocked on:** Systematic opening odds timestamp storage (not currently collected).

#### 11.11 Managerial Tactical Intent
**Source: Assessment #3.**

Scrape pre-match press conferences, classify intent: `[Expected_Rotation: High/Low]`, `[Tactical_Posture: Defensive/Aggressive]`. Most valuable in cup matches and late-season relegation battles.

**Blocked on:** Reliable transcript sources across leagues/languages.

#### 11.12 Referee/Venue Bias Features
**Source: Assessment #4.**

Some referees consistently produce more goals/cards. API-Football provides referee data in fixtures. Already implemented as `referee_cards_avg`, `referee_home_win_pct`, `referee_over25_pct` signals.

**Implementation:** ✅ Done — referee stats computed from AF data in morning pipeline.
**Expected impact:** +1-2% on O/U markets specifically.

### Bet Explanations (Product Feature, All Tiers)

**Consensus: 3/4 assessments recommend this for user-facing product.**

Generate natural language bet justifications from dimension scores, alignment, Kelly, news. Sellable as Elite tier feature. Not a model improvement — a product feature.

**Implementation:** LLM prompt in frontend API, using stored bet data.
**Expected impact:** Zero betting ROI, high commercial ROI (subscriber retention).
**When:** When building Pro/Elite tier in frontend.
