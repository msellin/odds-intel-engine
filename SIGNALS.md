# OddsIntel — Signals

> Combined reference for every signal we collect and how we surface it in the UI.
> Part 1: Architecture (what we collect, store, and feed into the model).
> Part 2: UX Strategy (how we expose signals to users by tier to drive engagement and conversion).
> Tasks tracked in PRIORITY_QUEUE.md (IDs: SIG-*, SUX-*).
> Last updated: 2026-04-29

---

# Part 1 — Signal Architecture

## Core Principle

A signal is any piece of information that is:
1. Available before the match ends
2. Potentially predictive of outcome or market edge
3. Independent enough from other signals to add information

We do not decide upfront which signals matter. We collect everything, store it with the time it was captured, and let accumulated match outcomes teach the model which signals have predictive power.

---

## Signal Inventory

### Group 1 — Model Signals (probability estimates)

| Signal | Where stored | When written | Status |
|--------|-------------|-------------|--------|
| `poisson_prob` | `predictions` (source='poisson') | Morning pipeline | ✅ Running |
| `xgboost_prob` | `predictions` (source='xgboost') | Morning pipeline | ✅ Running |
| `af_pred_prob` | `predictions` (source='af') | Morning pipeline | ✅ Running |
| `ensemble_prob` | `predictions` (source='ensemble') | Morning pipeline | ✅ Running |
| `model_disagreement` | `simulated_bets` + `match_feature_vectors` | Morning pipeline | ✅ Running |

Data tier system:
- **Tier A**: team in targets_v9.csv (European leagues) — Poisson + XGBoost available
- **Tier B**: team in targets_global.csv (global ELO dataset) — Poisson only
- **Tier D**: no historical data — AF prediction only (ensemble = AF directly)

---

### Group 2 — Market Signals (what bookmakers think)

| Signal | Signal name in match_signals | When written | Status |
|--------|------------------------------|-------------|--------|
| Opening implied prob (home) | `market_implied_home` | Morning pipeline | ✅ Running |
| Opening implied prob (draw) | `market_implied_draw` | Morning pipeline | ✅ Running |
| Opening implied prob (away) | `market_implied_away` | Morning pipeline | ✅ Running |
| Bookmaker disagreement (max−min implied) | `bookmaker_disagreement` | Morning pipeline | ✅ Running |
| Overnight line move (yesterday close → today open) | `overnight_line_move` | Morning pipeline | ✅ Running |
| Odds drift (open → now, implied prob delta) | `odds_drift` | On bets (simulated_bets) | ✅ Running |
| Steam move flag (>3% drift) | `steam_move` | On bets | ✅ Running |
| Odds volatility (std of implied prob, 24h) | `odds_volatility` | Morning pipeline | ✅ Running |
| CLV (closing line value) | `pseudo_clv_home/draw/away` on `matches` | Settlement | ✅ Running |

> `odds_drift` and `steam_move` are currently stored on `simulated_bets` and `match_feature_vectors`, not in `match_signals`. Future: move to match_signals for all matches.

---

### Group 3 — Team Quality Signals

| Signal | Signal name in match_signals | When written | Status |
|--------|------------------------------|-------------|--------|
| ELO home | `elo_home` | Morning pipeline | ✅ Running |
| ELO away | `elo_away` | Morning pipeline | ✅ Running |
| ELO differential | `elo_diff` | Morning pipeline | ✅ Running |
| Form PPG (10-match rolling) home | `form_ppg_home` | Morning pipeline | ✅ Running |
| Form PPG (10-match rolling) away | `form_ppg_away` | Morning pipeline | ✅ Running |
| Form slope (PPG last-5 minus PPG prior-5) home | `form_slope_home` | Morning pipeline | ✅ Running |
| Form slope away | `form_slope_away` | Morning pipeline | ✅ Running |
| Season goals for avg home | `goals_for_avg_home` | Morning pipeline (Tier A only) | ✅ Running |
| Season goals against avg home | `goals_against_avg_home` | Morning pipeline (Tier A only) | ✅ Running |
| Season goals for avg away | `goals_for_avg_away` | Morning pipeline (Tier A only) | ✅ Running |
| Season goals against avg away | `goals_against_avg_away` | Morning pipeline (Tier A only) | ✅ Running |
| Venue goals for — home team at home | `goals_for_venue_home` | Morning pipeline (Tier A only) | ✅ Running |
| Venue goals against — home team at home | `goals_against_venue_home` | Morning pipeline (Tier A only) | ✅ Running |
| Venue goals for — away team at away | `goals_for_venue_away` | Morning pipeline (Tier A only) | ✅ Running |
| Venue goals against — away team at away | `goals_against_venue_away` | Morning pipeline (Tier A only) | ✅ Running |
| League position (normalised rank) home | `league_position_home` | Morning pipeline | ✅ Running |
| League position away | `league_position_away` | Morning pipeline | ✅ Running |
| Points to title home | `points_to_title_home` | Morning pipeline | ✅ Running |
| Points to title away | `points_to_title_away` | Morning pipeline | ✅ Running |
| Points to relegation home | `points_to_relegation_home` | Morning pipeline | ✅ Running |
| Points to relegation away | `points_to_relegation_away` | Morning pipeline | ✅ Running |
| H2H home win pct (last 10 meetings) | `h2h_win_pct` | Morning pipeline | ✅ Running |
| H2H total meetings | `h2h_total` | Morning pipeline | ✅ Running |
| Rest days home | `rest_days_home` | Morning pipeline | ✅ Running |
| Rest days away | `rest_days_away` | Morning pipeline | ✅ Running |

**Not yet built:**
- `xg_proxy_home/away` (shots-based xG estimate) — needs match_stats from prior matches
- `h2h_avg_goals` — only win_pct + total built so far

---

### Group 4 — Information Signals (real-world events, priced slowly by market)

| Signal | Signal name | Where stored | When written | Status |
|--------|-------------|-------------|-------------|--------|
| News impact score | `news_impact_score` | `match_signals` + `simulated_bets` | News checker (4×/day) | ✅ Running |
| Injury count home | `injury_count_home` | `match_signals` | Morning pipeline | ✅ Running |
| Injury count away | `injury_count_away` | `match_signals` | Morning pipeline | ✅ Running |
| Players out home | `players_out_home` | `match_signals` | Morning pipeline | ✅ Running |
| Players out away | `players_out_away` | `match_signals` | Morning pipeline | ✅ Running |
| Lineup confirmed | `lineup_confirmed` | `simulated_bets` | News checker | ✅ Running |
| Lineup confidence | `lineup_confidence` | `simulated_bets` | News checker | ✅ Running |

**Not yet built:**
- `key_player_missing` — boolean, requires player importance weighting (P3.3, deprioritised)
- `players_doubtful_home/away` — Questionable status tracked in match_injuries but not yet a signal

---

### Group 5 — Context Signals (situational factors)

| Signal | Signal name in match_signals | When written | Status |
|--------|------------------------------|-------------|--------|
| Referee cards per game | `referee_cards_avg` | Morning pipeline | ✅ Running |
| Referee home win pct | `referee_home_win_pct` | Morning pipeline | ✅ Running |
| Referee over 2.5 pct | `referee_over25_pct` | Morning pipeline | ✅ Running |
| Fixture importance (max urgency, 0–1) | `fixture_importance` | Morning pipeline | ✅ Running |
| Fixture importance home team | `fixture_importance_home` | Morning pipeline | ✅ Running |
| Fixture importance away team | `fixture_importance_away` | Morning pipeline | ✅ Running |
| Importance asymmetry (home − away urgency) | `importance_diff` | Morning pipeline | ✅ Running |
| League home win pct (last 200 finished) | `league_home_win_pct` | Morning pipeline | ✅ Running |
| League draw pct | `league_draw_pct` | Morning pipeline | ✅ Running |
| League avg goals | `league_avg_goals` | Morning pipeline | ✅ Running |

**Not yet built:**
- `is_derby` / `travel_distance` — needs team location data
- `venue_altitude` — needs venue metadata
- `is_cup` — fixture metadata partially available, not wired

---

### Group 6 — Live Signals (in-play, updated every 5 minutes)

| Signal | Where stored | Status |
|--------|-------------|--------|
| `live_score_home/away` | `live_match_snapshots` | ✅ Running |
| `live_minute` | `live_match_snapshots` | ✅ Running |
| `live_shots_home/away` | `live_match_snapshots` | ✅ Running |
| `live_xg_home/away` | `live_match_snapshots` | ✅ Running |
| `live_possession_home` | `live_match_snapshots` | ✅ Running |
| `live_odds` | `odds_snapshots` (is_live=true) | ✅ Running |
| `live_red_cards` | `match_events` | ✅ Running |
| `live_goals` | `match_events` | ✅ Running |

---

## Signal Count Per Match (as of 2026-04-29)

| Group | Signals | Notes |
|-------|---------|-------|
| Group 1 (model) | 4 | poisson, xgboost, af, ensemble |
| Group 2 (market) | 8 | implied probs ×3, bdm, olm, volatility, drift, clv |
| Group 3 (quality) | 22 | ELO ×3, form ×4, goals ×8, standings ×6, H2H ×2, rest ×2 (some Tier A only) |
| Group 4 (information) | 6 | news, injuries ×4, lineup ×2 |
| Group 5 (context) | 10 | referee ×3, importance ×3, league meta ×3, importance_diff |
| Group 6 (live) | 8 | score, minute, shots, xg, possession, live_odds, cards, goals |
| **Total** | **~58** | |

---

## Signal Timeline Per Match

```
T-24h   Fixtures published (AF)
T-16h   Pipeline runs (04:00-06:00 UTC — fixtures, enrichment, odds, predictions, betting):
          → Group 1: Model signals (Poisson, XGBoost, AF prediction, ensemble)
          → Group 2: Opening market odds + bookmaker_disagreement + overnight_line_move + odds_volatility
          → Group 3: ELO, form PPG, form slope, season stats, venue splits,
                     standings signals, H2H, rest days
          → Group 4: Injury counts
          → Group 5: Referee stats, fixture importance + asymmetry, league meta
T-14h   Odds snapshot #1
T-12h   Odds snapshot #2 + news scan #2 (news_impact_score update)
T-8h    Odds snapshot #3
T-6h    Odds snapshot #4 + news scan #3
T-4h    Odds snapshot #5
T-2h    Odds snapshot #6
T-1h    Lineups published → lineup_confirmed signal
T-30m   Final news scan #4
T-0h    Match kicks off
T+5m    Live tracker starts (every 5min) → Group 6 signals
T+FT    Settlement: result recorded, pseudo_clv computed
T+1h    Post-match enrichment: T4/T8/T12
```

---

## Storage

### `match_signals` table (append-only EAV)
One row per `(match_id, signal_name, captured_at)`. Same signal gets a new row each time it's updated. ML training query uses value closest to kickoff.

```
match_id | signal_name          | signal_value | signal_group  | data_source | captured_at
---------|----------------------|-------------|---------------|-------------|-------------
<uuid>   | elo_diff             | 85.3         | quality       | derived     | 2026-04-29T08:01Z
<uuid>   | news_impact_score    | -0.4         | information   | gemini      | 2026-04-29T09:05Z
<uuid>   | odds_volatility      | 0.003        | market        | derived     | 2026-04-29T08:01Z
```

### `predictions` table
One row per `(match_id, market, source)`.

```
(match_id, '1x2_home', 'poisson')   ← Poisson probability
(match_id, '1x2_home', 'xgboost')   ← XGBoost probability
(match_id, '1x2_home', 'af')         ← AF /predictions
(match_id, '1x2_home', 'ensemble')   ← Consensus
```

### `match_feature_vectors` table (wide ML training table)
One row per finished match. Materialized nightly by `build_match_feature_vectors()` in settlement. 36+ columns covering all signal groups.

### `matches` table
`pseudo_clv_home/draw/away` — closing line value for every finished match. Computed by settlement. Primary ML training target.

---

## How Signals Flow into the Model

```
Morning pipeline
    │
    ├─ Group 1: Poisson + XGBoost + AF → predictions table
    │           ensemble_prob = calibrated blend
    │
    ├─ Group 2-5: match_signals (EAV, ~25 signals per match)
    │
    └─ Edge calculation:
           calibrated_prob = α × model_prob + (1-α) × market_implied
           α = {T1: 0.20, T2: 0.30, T3: 0.50, T4: 0.65}
           edge = calibrated_prob - (1 / odds)
           kelly = (calibrated_prob × odds - 1) / (odds - 1)
           stake = min(kelly × 0.15 × bankroll, 0.01 × bankroll) × data_tier_mult

Settlement (nightly)
    │
    ├─ pseudo_clv = (1/open_odds) / (1/close_odds) - 1  [all ~280 matches]
    │
    └─ match_feature_vectors ETL:
           wide row per match, pivoting match_signals + predictions + ELO + form
           → ML training table

Meta-model (Phase 1 ~May 9, Phase 2 ~June)
    │
    └─ Logistic regression on match_feature_vectors
           Target: pseudo_clv > 0 (was this bet +EV?)
           Features (META-2 design — market structure gaps only):
                     edge (ensemble_prob − market_implied_home),
                     odds_drift, bookmaker_disagreement, overnight_line_move,
                     model_disagreement, league_tier,
                     news_impact_score, odds_volatility
           Note: raw ELO/form excluded — market already priced those in
```

---

## Open Gaps

For task status and priority, see **PRIORITY_QUEUE.md** (single source of truth for all tasks).

Relevant queue IDs: PIN-1 (Pinnacle anchor), SIG-12 (xG overperformance), MOD-2 (learned blend weights), SIG-DERBY (is-derby/travel), P3.3 (player injury weighting).

---

---

# Part 2 — Signal UX Strategy

> How to surface the 58-signal engine in the UI to build trust, drive engagement, and convert Free → Pro → Elite.
> Synthesised from 4 independent UX/product reviews (2026-04-29).

---

## Core Philosophy

**Progressive revelation, not progressive hiding.** Every user sees the same matches. Depth of analysis increases by tier.

| Tier | Experience | Mental State |
|------|-----------|-------------|
| **Free** | "Something interesting is happening" | Curiosity |
| **Pro** | "I understand what's happening" | Insight |
| **Elite** | "I know what to do" | Conviction |

All 4 reviewers unanimously agreed: the biggest opportunity is making the invisible 58-signal engine visible. The signal meter alone changes perceived value from "another odds site" to "an intelligence engine that happens to show odds."

---

## The 3-Layer Signal Visibility Model

| Layer | Free | Pro | Elite |
|-------|------|-----|-------|
| **Signal Existence** | X/58 count + grade (A/B/C/D) | Same + signal group icons colored | Same + full signal breakdown |
| **Signal Data** | Locked with 1-2 teaser hooks per match | Raw signal values, plain-English labels | Raw values + model interpretation |
| **Model Output** | No prediction | Directional lean only (Home/Away/Even) | Exact %, edge %, "Why This Pick" reasoning |

---

## Phase 1 — Signal Meter + Match Pulse (foundation)

**Goal:** Make the engine's work visible on every match card. Zero new data needed — signals already exist.

### SUX-1: Match Intelligence Score

Every match card displays:
- **Signal count:** "42/58 signals" — implies serious computation
- **Grade:** A/B/C/D mapped from data tiers (A=Tier A, B=Tier B, etc.)
- **Visual:** Segmented bar with 6 segments (one per signal group), filled=available, hollow=missing

```
┌─────────────────────────────────────────────────┐
│  Arsenal vs Chelsea          Tomorrow 15:00      │
│  ██ ██ ██ ██ ██ ░░   52/58 signals  ·  Grade A  │
│  1.85  ·  3.40  ·  4.20      [View Analysis →]  │
└─────────────────────────────────────────────────┘
```

**Why it works:** Anchors perceived value. Explains confidence variance (Grade A > Grade D). Creates curiosity ("Why is this match Grade A?") that hits a tier gate.

### SUX-2: Match Pulse Indicator

A composite "is this match interesting?" signal on match cards. Values: **Routine / Interesting / High Alert**.

Derived from: `model_disagreement`, `bookmaker_disagreement`, `importance_diff`, `steam_move`.

**Key rule:** Only ~15-20% of matches get a visible badge. Scarcity makes badges compelling.

```
┌─ Arsenal vs Chelsea ────── Tomorrow 15:00 ──┐
│  ⚡ Sharp movement   ·   ⚠️ High uncertainty │
│  Grade A  ·  52/58 signals                   │
│  1.85  ·  3.40  ·  4.20                     │
└──────────────────────────────────────────────┘
```

### SUX-3: Free-Tier Signal Teasers

On notable matches (30-40%), show 1-2 teaser hooks. No numbers — just curiosity gaps:

- "Odds shifted significantly overnight"
- "High bookmaker disagreement"
- "Away team declining form"
- "2 key absences confirmed for Away"

**Conversion trigger:** "You're seeing 2 of 42 signals. Upgrade to Pro to see full analysis."

---

## Phase 2 — Match Detail Signal Views (Pro value unlock)

**Goal:** Give Pro users raw signal data organized as an analytical workspace. They explore, form opinions, then want Elite to validate.

### SUX-4: Summary Tab — The Killer Feature

Default view on match detail. Cherry-picks the most interesting signal from each group in plain English. Readable in 30 seconds:

```
Grade A · 52/58 signals

FORM: Arsenal trending up (2.1 PPG, improving). Chelsea declining away (0.9 PPG).
MARKET: Sharp money moved toward Home 2h ago. Bookmakers largely agree.
CONTEXT: Arsenal fighting for title. Chelsea mid-table, nothing to play for.
NEWS: Chelsea missing 2 key players. Arsenal full strength.

[Pro] Directional lean: Home ↑↑
[Elite] Model: Home 58.2% · Edge: +6.1% · [Why this pick →]
```

Most users read this and move on. Power users drill into signal group sections.

### SUX-5: Signal Group Accordion Sections

Accordion cards (not tabs — better mobile) in priority order:

1. **Key Signals** (summary, always open)
2. **Market** — odds comparison, steam moves, disagreement, volatility, overnight shift
3. **Form & Strength** — ELO, form PPG, form slope, venue splits, rest days
4. **Context** — fixture importance, importance asymmetry, league meta stats, referee tendencies
5. **News & Injuries** — injury count, players out, lineup confidence, news impact
6. **Live** (during match only)

### SUX-6: Plain-English Signal Translation

Never show raw numbers without context. Translate everything:

| Raw Signal | Translation |
|-----------|------------|
| `odds_volatility: 0.73` | "Volatile — odds are shifting" |
| `form_slope_home: 0.4` | ↑↑ Strongly improving |
| `form_slope_home: 0.1` | ↑ Improving |
| `form_slope_home: -0.1` | ↓ Declining |
| `bookmaker_disagreement: 0.15` | "HIGH — bookmakers can't agree" |
| `model_disagreement: 0.08` | "Our models strongly agree" |
| `elo_home: 1842` | "Top 15 in Europe" or percentile |
| `fixture_importance: 0.85` | "Title decider" |
| `fixture_importance: 0.15` | "Nothing to play for" |

### SUX-7: Signal-Based Conversion Hooks

**Free → Pro triggers:**
1. Contextual teasers that fire only when genuinely interesting (not every match)
2. "+3 signals updated" badge — Free sees badge but can't see what changed
3. Post-match reveal — one retrospective insight: "Our signals detected sharp movement toward Home 4h before kickoff. Home won 2-0."

**Pro → Elite triggers:**
1. **Model conclusion lock** at bottom of every signal group: "Our model analyzed all 52 signals. See the full probability breakdown." The user just spent time reading signals, forming an opinion — the itch to see if the model agrees is the conversion.
2. **Signal divergence alert**: "Our signals and the market disagree on this match. Elite members can see our model's take."
3. **Weekly email**: "You would have found 3 value bets today" — count without revealing which matches.

---

## Phase 3 — Signal Timeline + Engagement

**Goal:** Turn the static match page into a living analysis that builds toward kickoff. The retention play.

### SUX-8: Signal Timeline Component

Vertical stepping-line (like GitHub commit history) showing signal events chronologically:

```
┌─ Signal Timeline ────────────────────────────┐
│  ● NOW                                        │
│  │  Lineups confirmed — prediction updated    │
│  │  Signal meter: 52/58 → 55/58              │
│  ● 2h ago                                     │
│  │  ⚡ Steam move detected on Home Win        │
│  ● 6h ago                                     │
│  │  News scan: "Chelsea confirm Mudryk out"   │
│  ● 14h ago                                    │
│  │  First odds published — 13 bookmakers      │
│  ○ Upcoming                                   │
│     Next odds snapshot in 1h 42m              │
│     Live signals start at kickoff             │
└────────────────────────────────────────────────┘
```

**Tier visibility:**
- **Free:** Timeline structure with event count badges ("4 updates today") but content locked
- **Pro:** All signal updates with values
- **Elite:** How each update affected the model's prediction ("Lineup confirmation moved Home probability from 54.1% to 57.8%")

### SUX-9: Signal Delta ("what changed since last visit")

```
Since you last checked:
+ Steam move toward Away
+ Lineups confirmed
→ Prediction shifted: Home 58% → 52%
```

Requires tracking last-visited timestamp per user per match. Creates habit, trust, and return visits.

### SUX-10: Post-Match Signal Reveal (Free)

After settlement, show one interesting retrospective signal to Free users:

"Our market signals detected sharp movement toward Home 4h before kickoff. Home won 2-0."

Low effort, high conversion value — retrospective proof that signals have value.

---

## Phase 4 — Elite Intelligence Layer

**Goal:** Resolve uncertainty. Give Elite users the model's conclusions with full transparency.

### SUX-11: "Why This Pick" Reasoning Card

Natural language summary referencing specific signals:

```
Why Home Win (+4.2% edge):
✔ Market moved strongly toward Home
✔ Home form trending up (+0.8 PPG)
✔ 2 key Away players missing
✔ Model agreement: High (Poisson + XGBoost aligned)
Confidence: High (48/58 signals, Tier A)
```

Explicitly connects: **signals → reasoning → outcome**. This is the trust builder.

Note: BET-EXPLAIN in PRIORITY_QUEUE.md covers the LLM generation side. SUX-11 covers the UI/UX design and signal mapping.

### SUX-12: CLV Tracking Dashboard

Historical chart of closing line value across all predictions:
- Running CLV% over time
- Win rate
- ROI if user followed all value bets

Post-match notification: "Your bet beat the closing line by 2.1%." Reinforces long-term profitability framing.

---

## What NOT to Show (all 4 reviewers agree)

### Never Reveal (Any Tier)
- Raw feature weights or XGBoost importance scores
- Exact blending formula between models
- Raw Poisson lambda values
- Training data sources or hyperparameters
- Pre-kickoff edge before odds update (prevents front-running)

### Translate, Don't Expose
- Model disagreement → "Our models strongly agree / see this differently / mixed signals"
- ELO ratings → percentile or "Top N in Europe" (raw 1842 means nothing)
- Form slope → arrows (↑↑/↑/→/↓/↓↓)
- Odds volatility → "Stable market / Volatile"
- Signal contribution (Elite) → relative impact bars, never coefficient values

### Responsible Gambling Guardrails
- No "guaranteed" or "sure bet" language
- Frame edge as "analytical advantage" not "profit opportunity"
- Show losing predictions too — honesty builds trust
- Show Grade C/D with lower confidence — "we don't have enough data" is credible
- Persistent responsible gambling link
- No flashing colors, countdown timers on odds, or "BET NOW" patterns

---

## Differentiators vs. Competitors

All 4 reviewers identified these as unique positioning:

1. **Signal Transparency** — Showing WHY a prediction exists, not just what it is. Rare in the industry.
2. **Match Intelligence Score** — No competitor has a visible "analysis depth" indicator. Brand asset.
3. **Living Analysis** — Signal timeline that evolves toward kickoff vs. static predictions posted once.
4. **Honest Uncertainty** — Showing Grade C/D with lower confidence. Counterintuitively builds trust.
5. **Post-Match Learning Loop** — CLV tracking + retrospective signal analysis closes the feedback loop.

---

## Reviewer Consensus Matrix

| Feature | Reply 1 | Reply 2 | Reply 3 | Reply 4 | Verdict |
|---------|:-------:|:-------:|:-------:|:-------:|---------|
| Match Intelligence Score | ✅ | ✅ | ✅ | ✅ | **Do — Phase 1** |
| Match Pulse / Interest indicator | ✅ | — | ✅ | ✅ | **Do — Phase 1** |
| Summary tab (key signals) | ✅ | ✅ | — | ✅ | **Do — Phase 2** |
| Signal group sections | ✅ | ✅ | ✅ | ✅ | **Do — Phase 2** |
| Plain-English translations | — | ✅ | — | ✅ | **Do — Phase 2** |
| Free-tier teasers (scarcity) | ✅ | ✅ | ✅ | ✅ | **Do — Phase 2** |
| Signal Timeline | ✅ | ✅ | ✅ | ✅ | **Do — Phase 3** |
| Signal Delta ("what changed") | — | ✅ | — | — | **Do — Phase 3** (unique, high value) |
| Post-match signal reveal (Free) | — | — | ✅ | ✅ | **Do — Phase 3** |
| "Why This Pick" reasoning | ✅ | ✅ | ✅ | ✅ | **Do — Phase 4** |
| CLV dashboard | ✅ | ✅ | ✅ | ✅ | **Do — Phase 4** |
| Signal contribution chart | ✅ | — | — | ✅ | **Defer** — risks exposing methodology |
| Push notifications for signals | ✅ | ✅ | ✅ | — | **Defer** — after core UX is built |
| Match Momentum live chart | — | — | ✅ | — | **Defer** — nice-to-have for live |
| Gamification (badges, streaks) | ✅ | ✅ | — | — | **Skip** — risks feeling like gambling site |

---

## Phase Dependencies

- **Phase 1** — ✅ No blockers, signal data already exists in `match_signals` table. Done 2026-04-29.
- **Phase 2** — ✅ Tier-aware data API (B3) complete — pro data stripped server-side. Stripe live. No remaining blockers.
- **Phase 3** — needs signal event logging (timestamp when each signal was computed/updated)
- **Phase 4** — ✅ Stripe + Elite tier live. Blocked only on data accumulation for CLV dashboard (needs settled bets).
- **SUX-11** ("Why This Pick") builds on BET-EXPLAIN (#33) — share the LLM prompt work
