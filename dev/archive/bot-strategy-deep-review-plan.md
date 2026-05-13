# BOT-STRATEGY-DEEP-REVIEW — Plan

**Goal:** answer two questions across all live bots:
1. Are existing strategies' filter chains + thresholds *reasonable* — or are some so tight they barely fire, or so loose they catch noise?
2. Are there *better strategies we don't trade today* — patterns with known edge in published research or in the 8-AI inplay reviews that we never shipped?

**Why now (2026-05-13):** inplay bots are firing ~5–15/day across 13 strategies, with one bot (`inplay_j`) at 0 settled bets in 14 days. A funnel diagnostic showed J's `prematch_o25 ≥ 0.62` gate clears only 0.24% of mid-game snapshots — intentional design, not a bug, but possibly mis-calibrated for actual market behavior. Before tuning individual gates ad-hoc, do a single systematic audit so changes are coherent rather than reactive.

**Non-goal:** retiring or replacing existing bots. The audit produces a *ranked list of proposed adjustments*; each adjustment ships as its own task with smoke tests.

## Scope

**23 prematch bots:** `bot_aggressive`, `bot_ah_away_dog`, `bot_ah_home_fav`, `bot_btts_all`, `bot_btts_conservative`, `bot_conservative`, `bot_dc_strong_fav`, `bot_dc_value`, `bot_dnb_away_value`, `bot_dnb_home_value`, `bot_draw_specialist`, `bot_greek_turkish`, `bot_high_roi_global`, `bot_lower_1x2`, `bot_opt_away_british`, `bot_opt_away_europe`, `bot_opt_home_lower`, `bot_opt_ou_british`, `bot_ou15_defensive`, `bot_ou25_global`, `bot_ou35_attacking`, `bot_proven_leagues`, `bot_v10_all`.

**13 inplay strategies:** `inplay_a` through `inplay_q` (a, b, c, d, e, g, h, i, j, l, m, n, q).

## Approach — three independent threads

Each thread produces a deliverable. Threads can run in any order; run in parallel by separate sessions if convenient.

### Thread 1 — Audit existing strategies (1–2 days)

For each bot, produce a one-screen profile:

| Field | What it captures |
|---|---|
| Filter chain | Sequenced gates with their threshold values |
| Funnel (last 14d) | Count + % surviving each gate, like the inplay_j diagnostic |
| Limiting gate | The gate that drops most candidates |
| Settled volume | n bets / settled / pending |
| ROI / CLV | Real performance at current thresholds |
| Sensitivity replay | Re-replay last 14d with limiting gate at ±10% to estimate fire-rate elasticity and projected ROI |

**Tooling:** extend `scripts/bot_perf_report.py` or build `scripts/bot_strategy_audit.py`. Funnel needs strategy-specific SQL (the filters live in code, not config) so the script encodes one query per bot — tedious but mechanical.

**Output:** `dev/active/bot-strategy-audit-results.md` — one section per bot, plus a summary ranking by "loosen-this-gate-for-most-impact."

### Thread 2 — Identify gaps (1–2 days)

Survey known patterns we don't trade:

- **Re-read 8-AI inplay review docs** (`dev/active/inplay-bot-*.md`). For each strategy the panel recommended that we *did not* ship, capture: what is it, why we skipped it, has the reason changed?
- **Published live-betting patterns** the literature touches:
  - Asian handicap live momentum (half-time line value)
  - Second-half handicap repricing after low-tempo first half
  - Half-time / full-time bets after late first-half goal
  - "Comeback" pricing post-equalizer (1-1 from 1-0)
  - Derby discount vs neutral matches
  - Promoted-team early-season volatility
  - Cup vs league fixture priors
- **Prematch gaps:** corners markets, cards, both-halves-over, exact-score, scorecast — we trade none of these.

**Output:** appended to the audit results doc — "Strategies we don't trade today" section with one line per candidate: rationale, expected fire-rate, data we need to backtest.

### Thread 3 — Data sufficiency (0.5 day)

For each Thread 2 candidate strategy:
- Do we have the signals already? (`live_match_snapshots`, `odds_snapshots`, `match_events`, etc.)
- If not, what's the collection lead time + API cost?
- What's the minimum replay window before we can validate edge?

**Output:** appended to the audit results doc — "Data readiness" column.

## Deliverable

A single `dev/active/bot-strategy-audit-results.md` with:
1. Per-bot profile (Thread 1)
2. Ranked adjustment list — top 5 "loosen X" / "tighten Y" candidates with expected fire-rate delta and replay-projected ROI
3. Ranked new-strategy list (Thread 2) with data readiness (Thread 3)

Each ranked item becomes its own follow-up task in `PRIORITY_QUEUE.md`, with smoke tests, so changes ship incrementally rather than as one big "rewrite all bots" PR.

## Out of scope

- Implementing any change. This task ends at the ranked list.
- ML-side improvements (calibration, retrain). Tracked separately in `ML-PIPELINE-UNIFY`.
- Live odds source upgrade. Tracked as `INPLAY-ODDS-SOURCE`.

## Risk

- Easy to scope-creep into "rewrite the bot system." Discipline: each ranked item must be small enough to ship in <1 day with its own smoke test. If an item is bigger, split it.
- Replay numbers from the existing 14-day window are directional only — pre-cohort data, varying odds coverage, sparse inplay snapshots. Treat as "should we ship a real A/B?" not "ship this verbatim."

## Estimated effort

~3-4 days of agent work across 2-3 sessions. Run when a quiet window opens. No data threshold gates this — all needed data is already in the DB.
