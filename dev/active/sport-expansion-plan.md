# Sport Expansion — Plan & Recommendations

Written 2026-06-07. Context: soccer season is winding down, we want to offer bets on other sports.

---

## Recommendation: Tennis First

**Why tennis, not the others:**

1. **Data is already downloaded** — 20 years of ATP matches + odds in `data/raw/tennis/`. Zero setup time.
2. **Year-round calendar** — No off-season. Fills the June–August soccer gap perfectly.
3. **Clear profitability path** — We know exactly what model is needed (Knottenbelt point-level Markov). The research is done.
4. **Pinnacle odds already in the data** — tennis-data.co.uk PSW/PSL columns give us Pinnacle odds for devigging and CLV tracking.
5. **Our stack is reusable** — ELO, Platt calibration, Kelly sizing, CLV tracking all transfer directly.

**Why not the others (right now):**
- NFL: free data with odds (nflverse games.csv), but season starts September. 3 months away. Build it in July.
- MLB: no free historical odds available (SBRO is dead). Would need paid API.
- Esports: historical data costs €400+/mo per game (PandaScore). Too expensive for initial validation.
- Cricket: amazing ball-by-ball data (Cricsheet), but zero free odds data — can't backtest.

---

## Phase 1 — Tennis Model (Start Monday)

**Goal**: A profitable tennis model vs Pinnacle closing line. Target: CLV > 0 at ≥5% edge threshold.

### What we know from this session's backtest

Current model results (2022-2024 backtest, 7,734 matches vs Pinnacle):
- Simple Elo: -7.9% ROI at 0% edge, worse as threshold increases
- Ranking logistic: -7.8% ROI (nearly identical to Elo)
- Serve stats model: 59.5% accuracy (WORSE than Elo) — because only 9.7% of matches joined due to name format mismatch
- Grand Slam serve model: 66.4% accuracy (vs 68% Pinnacle) — **most promising**

Core problem: We're 4pp below Pinnacle accuracy (64% vs 68%). Even with perfect edge detection, we can't overcome this gap with match-level features alone.

**The path**: Point-level Markov model (Knottenbelt et al., 2016). Models each serve point → game → set → match. Literature shows 3.8% ROI vs closing line.

### Why the point-level model works

Pinnacle prices match outcomes. A point-level model estimates `P(server wins this point)` from:
- Serve statistics (1stIn%, 1stWon%, 2ndWon%) → rolling 30 matches per (player, surface)
- Return statistics (1stReturn%, 2ndReturn%)
- Pressure points: break points, tie-breaks

From `P(point)` the Markov chain computes `P(game) → P(set) → P(match)` analytically.
The market prices based on rankings/head-to-head/surface Elo. If our point model disagrees, that's real alpha.

### ⚠️ License warning on Sackmann repos

All JeffSackmann repos (tennis_atp, tennis_wta, tennis_slam_pointbypoint, MatchChartingProject) are licensed **CC-BY-NC-SA 4.0 — NON-COMMERCIAL**. Using them in a paid product (OddsIntel Pro/Elite) may violate the license. Options:
1. Contact Sackmann directly for commercial license (he's responsive, used by academic researchers)
2. Use tennis-data.co.uk as primary source once it comes back up (no NC restriction)
3. Use The Odds API + a commercial results provider for production
For backtesting/development work (non-commercial use internally), the repos are fine.

### Bonus: Point-by-point Grand Slam data (NEW)

Agent discovered `tennis_slam_pointbypoint` (same author, LIVE):
- URL: https://github.com/JeffSackmann/tennis_slam_pointbypoint
- Point-by-point data from all 4 Grand Slams (2011–present)
- Columns: shot type, direction, serve speed, rally length, Hawkeye, break point state
- **This makes the Knottenbelt Markov model much easier to build for Grand Slams**
- Step 1b: Download this repo → `data/raw/tennis/slam_pointbypoint/`

### Serve stats data availability

Sackmann ATP data already in `data/raw/tennis/atp_matches_*.csv` has these columns:
- `w_svpt` — winner's serve points
- `w_1stIn` — winner's first serves in
- `w_1stWon` — winner's first serve points won
- `w_2ndWon` — winner's second serve points won
- Same `l_*` columns for loser
- Also: `w_ace`, `w_df`, `w_bpSaved`, `w_bpFaced`

**Name join problem**: Sackmann uses "Rafael Nadal", tennis-data.co.uk uses "Nadal R." — only 9.7% join rate currently. Fix: build a fuzzy name map (`firstname lastname` → `lastname initial`).

### Step-by-step implementation plan

**Step 1**: Fix the name join (~2 hours)
- Build `name_map.py`: normalize Sackmann names to `Lastname F.` format
- Validate: target >85% join rate on 2022-2024 test set
- File: `scripts/tennis/build_name_map.py`

**Step 2**: Build rolling serve stats cache (~3 hours)
- Per (player, surface): rolling 30-match serve win rate
- `serve_win_pct = (w_1stWon + w_2ndWon) / w_svpt`
- Also: `first_serve_in_pct = w_1stIn / w_svpt`
- Store as dict indexed by (player_key, surface, match_date) for efficient lookup
- File: `scripts/tennis/compute_serve_stats.py`

**Step 3**: Implement Markov chain (~4 hours)
- `p_game(p_serve)` — P(server wins game) from per-point serve win probability
- `p_set(p_serve_a, p_serve_b, set_format)` — P(player A wins set)
- `p_match(p_serve_a, p_serve_b, match_format)` — P(player A wins match)
- Include tie-break model (different probabilities at TB)
- File: `scripts/tennis/markov_model.py`

**Step 4**: Backtest (~2 hours)
- Run on 2022-2024 test set
- Compare: Markov prob vs Pinnacle de-vigged prob
- Compute ROI at 0%, 2%, 5%, 8%, 10% edge thresholds
- Target: ROI > 0% at ≥5% threshold
- File: `scripts/backtest_tennis_markov.py`

**Step 5**: If ROI > 0%, productionize
- Add tennis to the pipeline (fixtures, odds, predictions, betting)
- Wire into Railway scheduler
- Add tennis bots to `BOTS_CONFIG`
- Update DATA_SOURCES.md, WORKFLOWS.md, MODEL_WHITEPAPER.md

### Data sources for productionization

| Data type | Source | Cost | Status |
|-----------|--------|------|--------|
| Historical training (serve stats) | Sackmann ATP GitHub | Free | Already in repo |
| Historical odds (PSW/PSL) | tennis-data.co.uk (2005-2025) | Free | Already in repo |
| Live fixtures + current odds | API-Football Tennis | Included in $29/mo? | Need to verify |
| OR: live fixtures + odds | The Odds API (OA_KEY) | ~10 credits/call | Already in stack |
| Live serve stats | ATP Tour API / scraping | Unknown | Needs research |

**Critical gap**: Live serve stats for in-game/post-match are not in API-Football.
- Option A: Add live serve tracking from ATP Tour API (if exists)
- Option B: Use only pre-match statistics (last 30 match rolling average)
- Option B is safer for v1 — no live dependency, still better than Elo

### Realistic ROI expectations

| Model | Expected accuracy | Expected ROI |
|-------|------------------|-------------|
| Simple Elo (current) | 64% | -7.9% |
| Surface Elo + ranking | 64-65% | -7.8% |
| **Point-level Markov (target)** | **66-68%** | **+1% to +4%** |
| Knottenbelt (literature) | ~68% | +3.8% |

Even at +2% ROI, tennis is worth building. Pinnacle margin is only 2.6%, so a good model needs 67%+ accuracy to net positive.

---

## Phase 2 — NFL Model (Start July)

**Goal**: A backtested NFL model ready for the September 2026 season start.

### Data available (free)

`https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv`

Columns include:
- `away_moneyline`, `home_moneyline` (American odds)
- `spread_line`, `away_spread_odds`, `home_spread_odds`
- `total_line`, `under_odds`, `over_odds`
- `away_qb_name`, `home_qb_name` (key signal — QB quality)
- `temp`, `wind`, `roof`, `surface` (weather/conditions)
- Multi-season history

### Why NFL might be more profitable than soccer

- Weekly games → more time to research each matchup
- Spread betting (not 3-way 1X2) → no draw outcome
- Weather is a quantifiable edge signal (cold/wind suppresses scoring)
- QB injury/replacement = massive market inefficiency within 24h
- Total/over-under has a very strong literature on weather models

### Implementation plan (July)

1. Download `games.csv` → `data/raw/nfl/`
2. Build basic Elo model (QB-adjusted) — backtest 2015-2023 training, 2024 holdout
3. Devig from moneyline (American ML format)
4. Compute ROI at 0%, 5%, 10% edge thresholds
5. If positive ROI at any threshold → productionize

**Caveat**: NFL market is very sharp (US professional bettors). Don't expect big edge without proprietary signals (injury feed, weather API, snap count data).

---

## Phase 3 — MLB (2027 Season)

**Blockers to resolve first**:
1. Free historical odds: SBRO is dead. Options:
   - Pay for The Odds API historical data (~$79/mo for 3K calls)
   - Find sports-statistics.com MLB odds dataset (site is alive, MLB odds page unclear)
   - Use Retrosheet (161 fields) for results + The Odds API for odds at $79/mo
2. Key signals: Starting pitcher ERA (most important MLB signal, not in Retrosheet)

**Do not start until**: Free or cheap historical odds source confirmed.

---

## Priority Queue Entries to Add

When starting tennis implementation, add these to `PRIORITY_QUEUE.md`:

| Task ID | Task | Priority |
|---------|------|----------|
| TENNIS-NAME-MAP | Fix Sackmann↔tennis-data.co.uk name join (target >85%) | P1 |
| TENNIS-SERVE-STATS | Build rolling serve stat cache per (player, surface) | P1 |
| TENNIS-MARKOV | Implement point-level Markov model | P1 |
| TENNIS-BACKTEST | Backtest Markov vs Pinnacle, 2022-2024 | P1 |
| TENNIS-PIPELINE | Wire tennis into Railway scheduler | P2 |
| NFL-DATA | Download nflverse games.csv + backtest | P3 |

---

## Open Questions for Day 1

1. **Does API-Football Ultra ($29/mo) cover tennis?** If yes, we have live tennis fixtures + odds already. If not, we need The Odds API.
2. **Can we get live ATP serve stats anywhere free?** Check ATP Tour website, Tennis Abstract API, or similar. Pre-match rolling averages work for v1 without this.
3. **Is tennis-data.co.uk going to recover?** It was ECONNREFUSED on 2026-06-07. If it stays down, use OddsPortal (scraping, free, 15+ years) or OddsPapi (LIVE, claims Pinnacle + free historical — verify after registration) for post-2025 odds.
4. **OddsPapi commercial terms**: Site claims "free historical odds for backtesting" with Pinnacle as a sharp book. Verify: (a) what "free" means after registration, (b) whether commercial use is allowed.
5. **Sackmann NC license**: Before shipping tennis to Pro/Elite users, either get commercial license from Sackmann or switch to a commercial data source for production. Development/backtesting use is fine.
6. **OddsPortal scraping**: Site is accessible and has 15+ years of odds for all major sports. A scraper (e.g. `pyoddsportal` or custom Playwright-based) would give us historical odds for ALL sports without paying. Worth building as a fallback layer.
7. **Download `tennis_slam_pointbypoint`**: The point-by-point Grand Slam repo is live. Download it to `data/raw/tennis/slam_pointbypoint/` — it's the key ingredient for the Markov model beyond standard Elo/ranking.
