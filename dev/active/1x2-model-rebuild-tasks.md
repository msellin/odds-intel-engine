Parent: [[#141]] 1X2-MODEL-REBUILD-2026-09-24 (PRIORITY_QUEUE.md) — 🔴 P0 TOP PRIORITY (owner 2026-09-24). START HERE: dev/active/1x2-model-rebuild-HANDOVER.md

# 1X2 model rebuild — tasks

## Done (2026-09-24)
- [x] Read-only audit (weekly eval, versions, coverage, features)
- [x] Claim #141, plan + pre-registration before training
- [x] Weekly retrain eval fixed (`_cut14` twin) — `d35bbc27`
- [x] Rating model (Elo / pi / dynamic Poisson from scores + 258k prior-season results): 1.0078 vs 1.0711 — `ratings_1x2.py`
- [x] Rating model in shadow (migration 412, `rating_1x2_shadow` 05:30/17:30, subprocess; pandas 3.0.4 segfault handled)
- [x] Bot `bot_rating_1x2_v1` "1x2 market NEW" (migration 414)
- [x] Round 3a score-only tuning: all fail (ceiling) — recorded
- [x] Round 3b COMBINED model (ratings + 18-book consensus + Pinnacle + AF where unpriced): 0.9763 — `combined_1x2.py`, `market_consensus_1x2.py`
- [x] Combined model in shadow (migration 415, refit 2×/day, refresh :10/:40) — verified on the VPS
- [x] Bot `bot_combined_1x2_v1` "1x2 market NEW+" (migration 416)
- [x] Round 3c pre-registered + code (`player_strength_1x2.py`, `ab_1x2_lineups.py`, fetcher) — `4b29586a`
- [x] CI unbroken (smoke_test.py syntax from `9836228e`) — `63726b99`

## Remaining — in this order
- [ ] **A. Finish the AF fixture-details fetch** — `python3 -u scripts/fetch_fixture_details_cache.py --max-calls 25000`
      (resumable; skips done fixtures; reserve 20k; stops 23:45 UTC; 350/min). 6,600 / 19,733 calls done at 18:38 UTC
      09-24. Cache: `data/models/_research/1x2/fixture_details/` on the owner's Mac (gitignored) — a session on another
      machine must re-run it there (~1 h, ~20k calls).
- [ ] **A2. Round 3c test** — `python3 scripts/ab_1x2_lineups.py --select` (half-life 180 vs 365 on 08-01..08-30),
      record the choice in the plan doc, then `--confirm --half-life <chosen>` ONCE. Record results (plan doc "ROUND 3c").
- [ ] **A3. If 3c passes:** production player strength — private table for fixture details, daily fetch of yesterday's
      fixtures (`/fixtures?ids=`, 20 per call), budget-aware backfill (owner: up to ~50k/day on quiet days, less on busy),
      XI features in the combined model's refit + refresh (confirmed lineups ~1 h before KO; PREV XI before that).
- [ ] **B. Honest backtest** of `bot_rating_1x2_v1`, `bot_combined_1x2_v1` and `bot_v10_1x2` (baseline):
      window FIXED at 2026-08-31..2026-09-24 (the only out-of-sample window — do NOT pick windows by result);
      same bot rules; opening prices for the bet (only opening + close are retained after 7 days), Pinnacle-close CLV;
      every pick; stored SEPARATELY (not in simulated_bets) and labelled "Backtest (simulated)"; shown on /admin/bots
      beside "Live since 2026-09-25". Public display = owner decision after seeing the numbers.
- [ ] **C. Forward check ~2026-09-27** — `python3 scripts/ab_1x2_rating_arms.py --forward` (both versions vs served vs Pinnacle).
- [ ] **D. OWNER DECISION (recommended yes): serve the combined model's 1X2** in place of the `predictions` ensemble
      (0.635·Poisson + 0.365·XGB, measured WORSE than uniform, 1.144). 1X2 only; the 1X2 Platt params in
      `model_calibration` were fitted on the old blend → refit or bypass; `daily_pipeline_v2.py` ~3140 / `ensemble_prediction`.
- [ ] **E. Promotion rule for the NEW bots** (write before results exist): after 150 settled picks, CLV > 0 with CI above
      zero AND above the grade-B consensus bot → public "Grade A (testing)" (owner idea; threshold/wording = owner).
- [ ] **F. Model ideas still open:** Tonybet/Sportradar fair probs + Betfair exchange as consensus inputs once they have
      history; power/Shin de-vig and per-book accuracy weights; whether the rating-only bot should move to the combined model.
- [ ] **G. Close-out:** MODEL_WHITEPAPER/ROADMAP/SYSTEM_MAP final state, archive these dev docs, close #141.

Related rows filed 2026-09-24: #144 (AF predictions + Tonybet fair evaluation, after #141), #145 (pandas 3.0.4 segfault).
