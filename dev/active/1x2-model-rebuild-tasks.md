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
- [x] **A. Finish the AF fixture-details fetch** — ✅ 2026-09-24 19:22 UTC, 11,133 calls, 2 fixtures not returned — `python3 -u scripts/fetch_fixture_details_cache.py --max-calls 25000`
      (resumable; skips done fixtures; reserve 20k; stops 23:45 UTC; 350/min). 6,600 / 19,733 calls done at 18:38 UTC
      09-24. Cache: `data/models/_research/1x2/fixture_details/` on the owner's Mac (gitignored) — a session on another
      machine must re-run it there (~1 h, ~20k calls).
- [x] **A2. Round 3c test** — ✅ 2026-09-24: hl 365 chosen; CONFIRM all four FAIL (plan doc); presence-flag fix applied before confirm — `python3 scripts/ab_1x2_lineups.py --select` (half-life 180 vs 365 on 08-01..08-30),
      record the choice in the plan doc, then `--confirm --half-life <chosen>` ONCE. Record results (plan doc "ROUND 3c").
- [x] **A3. DROPPED — 3c failed (2026-09-24).** Was: if 3c passes: production player strength — private table for fixture details, daily fetch of yesterday's
      fixtures (`/fixtures?ids=`, 20 per call), budget-aware backfill (owner: up to ~50k/day on quiet days, less on busy),
      XI features in the combined model's refit + refresh (confirmed lineups ~1 h before KO; PREV XI before that).
- [x] **B. Honest backtest** — ✅ 2026-09-24: B, B2 (4/4 PASS), B3 grid 13,824 configs; results in plan doc. Display on /admin/bots still open → B-display below. Was: of `bot_rating_1x2_v1`, `bot_combined_1x2_v1` and `bot_v10_1x2` (baseline):
      window FIXED at 2026-08-31..2026-09-24 (the only out-of-sample window — do NOT pick windows by result);
      same bot rules; opening prices for the bet (only opening + close are retained after 7 days), Pinnacle-close CLV;
      every pick; stored SEPARATELY (not in simulated_bets) and labelled "Backtest (simulated)"; shown on /admin/bots
      beside "Live since 2026-09-25". Public display = owner decision after seeing the numbers.
- [ ] **B-display.** Show the backtest on /admin/bots as "Backtest (simulated)" beside "Live since 2026-09-25": a private
      home for the summary JSON on the VPS (table without anon grant, or a deployed file), a superadmin server-side reader,
      the ROI-is-noise and book-split caveats. Admin only; never /performance.
- [x] **B4. NEW+ outlier shadow bots** — ✅ 2026-09-24: `bot_combined_1x2_ev5_v1` / `_ev8_v1` (migration 418), smoke NEWPLUS-EV-BOTS. Was: — pre-register then add `bot_combined_1x2_ev_v1` (N2/N3-style: edge = p·odds − 1,
      EV ≥ 5% or 8%, Pinnacle price required, no min_prob, odds 1.30–6.00, all books), judged forward on CLV from its creation date.
- [ ] 🔄 **B5. Takeability of opening outlier quotes** (started 2026-09-24; gates #148) — for picks like B2's, how often is the above-consensus quote still
      there 1 h / at Telegram send time, per book; AF-fed vs our own sweepers. Decides which books NEW+ picks may name.
- [ ] **H. Shots/xG rating round** — pre-register (Wheatcroft: shots beat goals as rating input) using the fixture_details
      cache's shots / shots on target / xG; confirm FORWARD (08-31..09-24 is used up by rounds 1, 2, 3b, 3c) — owner approves design first.
- [ ] **C. Forward check ~2026-09-27** — `python3 scripts/ab_1x2_rating_arms.py --forward` (both versions vs served vs Pinnacle).
- [ ] **D. OWNER SAID YES (2026-09-24): serve the combined model's 1X2.** Consumer audit: ~30 readers of the in-memory
      `pred` 1X2 and the stored `predictions` ensemble rows (every 1X2/DC/DNB/AH bot, AH prediction rows, meta model, in-play,
      triggers, previews, two calibration fits). Most do NOT filter by model_version and already mix in the shadow version →
      **fix #147 first**, then swap: keep bots' decision inputs unchanged in the same commit unless each bot's rule_version is
      bumped (a changed input is a rule change on /admin/bots), bypass/refit the 1X2 Platt + shrinkage for the new source,
      write the NEW+ rows under their own model_version, fall back to the old blend where NEW+ has no row. Was: in place of the `predictions` ensemble
      (0.635·Poisson + 0.365·XGB, measured WORSE than uniform, 1.144). 1X2 only; the 1X2 Platt params in
      `model_calibration` were fitted on the old blend → refit or bypass; `daily_pipeline_v2.py` ~3140 / `ensemble_prediction`.
- [x] **E. DECIDED (owner 2026-09-24):** no hard rule — review checkpoints at 20 / 50 / 100 settled picks, bots clearly separated on /admin/bots so the owner can check the bets. Was: (write before results exist): after 150 settled picks, CLV > 0 with CI above
      zero AND above the grade-B consensus bot → public "Grade A (testing)" (owner idea; threshold/wording = owner).
- [ ] **F. Model ideas still open:** Tonybet/Sportradar fair probs + Betfair exchange as consensus inputs once they have
      history; power/Shin de-vig and per-book accuracy weights; whether the rating-only bot should move to the combined model.
- [ ] **G. Close-out:** MODEL_WHITEPAPER/ROADMAP/SYSTEM_MAP final state, archive these dev docs, close #141.

Related rows filed 2026-09-24: #144 (AF predictions + Tonybet fair evaluation, after #141), #145 (pandas 3.0.4 segfault).
