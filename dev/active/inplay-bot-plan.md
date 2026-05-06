# In-Play Paper Trading Bot — Accepted Plan
> Task: P3.4 | Created: 2026-05-06 | Phase: 1A building now

## Objective

Build a rule-based in-play paper trading bot that runs alongside the existing LivePoller, reads `live_match_snapshots` every 30s, and logs paper bets to `simulated_bets` for each of 11 strategies (A-K). No real money. Validate edge via CLV and ROI before advancing to Phase 2 ML model.

## Architecture Decision

**Not** a separate Railway worker process. Runs as a scheduled job inside the existing `workers/scheduler.py` APScheduler on a 30-second interval — same host as LivePoller, reads from DB only (no extra API calls, zero API budget impact).

## Phase 1A — What we build first

Single strategy (A: xG Divergence Over) to validate infrastructure before adding all 11.

**Entry conditions:**
- Minute 25-35
- Score 0-0 (A2 handles 1-0 separately)
- Bayesian posterior: `(prematch_xg + live_xg) / (1 + minute/90)` > `(prematch_xg / 90) × 1.15`
- Combined xG ≥ 0.9
- Combined shots on target ≥ 4
- Pre-match O2.5 implied prob > 54%
- Model edge: `model_prob - (1 / live_ou_25_over)` ≥ 3%

**Safety checks (all must pass before logging):**
1. Staleness: `NOW() - captured_at(odds) < 60s`
2. Score re-check: latest snapshot score = triggering snapshot score
3. League filter: league has ≥ 20 matches with xG data in `live_match_snapshots`
4. No existing `simulated_bets` row for this match + strategy (no double-trigger)
5. No red card in latest `match_events` for this match

**What gets logged to `simulated_bets`:**
- `market = 'ou_25'`, `selection = 'over'`
- `odds = live_ou_25_over` at trigger moment
- `model_prob` = derived from Bayesian posterior → Poisson CDF
- `stake = 1.0` (fixed unit — Phase 1 uses no Kelly)
- `strategy_id = 'inplay_a'`
- `notes = JSON: {minute, xg_home, xg_away, posterior_rate, prematch_xg_total, score, staleness_ms}`

**Settlement:** existing settlement pipeline handles it at FT (already reads `simulated_bets`).

## Phase 1B — All strategies (Week 1-3 rollout)

Add strategies A2, B, C, C_home, D, E, F in Week 1; G, H in Week 2; I, J, K in Week 3.
Each strategy = separate `strategy_id`. No code changes needed to DB or frontend.

## Phase 2 — LightGBM model (June 2026)

Replaces rule-based triggers. Target: `lambda_home_remaining` + `lambda_away_remaining`.
Gate: 500+ snapshots AND 200+ settled paper bets.

## Phase 3-5 — See PRIORITY_QUEUE.md § INPLAY Plan

## Risks

| Risk | Mitigation |
|------|-----------|
| AF xG data quality in lower leagues | League filter (≥ 20 xG matches) |
| Odds staleness post-goal | 60s staleness check — hard abort |
| Double-trigger on same match | Unique check before insert |
| Strategy A fires on dead ball xG (penalties) | Skip if xG spike > 0.3 in single poll (penalty xG) |
| No closed-line odds for CLV | Use pre-KO odds from `odds_snapshots` as proxy closing line |
