# OddsIntel — Agent Task Queue

> Track tasks given to agents and their status.
> When you start a new task, copy the prompt to the agent and mark status -> In Progress.
> When done, summarise what was built and mark -> Done.

---

## Active Tasks

### TASK-06 — Model Improvements P1-P4 (In Progress)
**Status:** Code complete, pending migration 006 + live validation
**What was built:**
- P1: Tier-specific calibration (`calibrate_prob()` with α per tier: T1=0.55, T2=0.65, T3=0.80, T4=0.85)
- P2: Odds movement from snapshots (`compute_odds_movement()` with soft penalty, hard veto >10% only)
- P3: External-signal alignment (`compute_alignment()` — LOG-ONLY, 4 external dimensions: odds_move, news, lineup, situational)
- P4: Kelly-based stake sizing (`compute_kelly()` + `compute_stake()` — 1/4 Kelly, 1.5% max cap)
- Migration 006: 11 new columns on simulated_bets (dimension_scores, calibrated_prob, kelly_fraction, etc.)
- Validation script: `scripts/validate_improvements.py` (calibration curve, ROI by alignment, CLV, Kelly vs flat)
- Pipeline integration: `daily_pipeline_v2.py` fully wired with P1-P4 flow

**Pending manual steps:**
- [ ] Run migration 006 in Supabase dashboard
- [ ] Validate with first day of live bets
- [ ] Run `validate_improvements.py` after 50+ settled bets

---

## Pending Tasks (not yet assigned)

### TASK-02 — Tier B Backtest
Validate Scotland/Austria/Ireland/South Korea ROI with current Poisson model before fully trusting live Tier B bets.
Script: `scripts/backtest_tier_b.py` in odds-intel-engine.
See NEXT_STEPS.md for full spec.

### TASK-03 — Pro Tier (Milestone 2)
B3 + B4 + F4 + F8 + F9.
Do not start until Milestone 1 is live and design review is done.
Remaining work: tier-aware data API, news checker 4x/day, live scores, Stripe integration, onboarding flow.

### TASK-04 — Singapore/South Korea odds source
Research Pinnacle API or OddsPortal scraping for Asian leagues.
+27.5% ROI signal (Singapore) currently has no odds feed.

### TASK-05 — BSD Sports Data API integration (B-OPS8)
Free API, 41+ bookmakers, best-odds comparison. Complements existing odds sources for real-time breadth across 34 leagues.

---

## Completed Tasks

### TASK-01 — Free Tier Foundation (F1 + B1 + B2)
**Status:** Done
**Project:** odds-intel-web (frontend)
**Milestone:** Milestone 1 — Free Tier Launch

**What was built:**
- B1: `getPublicMatches()` — public Supabase query via anon key, no auth required
- B2: `interestScore()` — match interest indicator (hot/warm/neutral)
- F1: Public `/matches` page — works without login, smart sort (odds first), dual layout, view toggle
- F1b: Public `/matches/[id]` — match detail with best odds + pro teaser
- RLS public read policies added to all data tables
- Auth fully implemented (login/signup via Supabase Auth, middleware protection)
- Track record page connected to real `simulated_bets` data
- F2: Tier-gated match depth (free: best odds + blurred pro teaser, auth: full odds table)
- F3: "All matches / With odds only" view toggle
