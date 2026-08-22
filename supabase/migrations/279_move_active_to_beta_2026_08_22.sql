-- MOVE-ACTIVE-TO-BETA + RETIRE-DORMANT-OPT 2026-08-22
--
-- Reconcile the maturity_label taxonomy + clear out the 3 dormant opt-*
-- bots. Five bots carried `maturity_label = 'active'` — a pre-taxonomy
-- label from April 2026 launch, before the calibrated / beta /
-- experimental / shadow scheme was formalised. `active` today has no
-- distinct meaning vs `beta` and shows up as a ghost bucket on the
-- /performance cohort breakdown.
--
-- ROI audit (flat €10 stake, since 2026-05-04):
--   bot_opt_home_lower    n=58  ROI +4.55%   → move to beta (retooled 2026-07-31,
--                                             still proving out on new config)
--   bot_conservative      n=23  ROI +12.26%  → move to beta (starved-by-selectivity
--                                             but the strategy still fires occasionally)
--   bot_opt_away_british  n=0   never fired  → RETIRE
--   bot_opt_away_europe   n=0   never fired  → RETIRE (min_prob 0.40 for AWAY at
--                                             odds 2.20-3.50 = extremely tight gate)
--   bot_opt_ou_british    n=0   never fired  → RETIRE (odds 2.50-4.00 for OU is
--                                             longshot territory; likely a stale config)
--
-- Retirement decision: 4 months of zero fires means either the config is
-- broken or the strategy doesn't fit current-data candidates. The fresh
-- shadow-bot lineup (bot_sweep_*, bot_no_pin_home_v1, bot_pin_*) launched
-- 2026-08-18 → 2026-08-21 covers similar territory with well-tuned edge
-- floors, so keeping the dormant opt-* bots on the roster adds no
-- optionality. Historical simulated_bets rows are preserved for future
-- audits (0 rows for these 3 — no historic loss to hero).
--
-- Effect on /performance cohort breakdown:
--   before: calibrated 468 · beta 263 · active 81   (three buckets)
--   after:  calibrated 468 · beta 344              (two buckets, cleaner story)
-- Placement pipeline (COOLBET_RECORD_ALLOWED_MATURITY='calibrated') is
-- unaffected — none of these bots were placing real money.

-- ── Move the two firing bots to beta ─────────────────────────────
UPDATE bots
SET maturity_label = 'beta',
    updated_at     = NOW()
WHERE name IN ('bot_opt_home_lower', 'bot_conservative')
  AND maturity_label = 'active'
  AND retired_at IS NULL;

-- ── Retire the three zero-fire opt-* bots ────────────────────────
UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'MOVE-ACTIVE-TO-BETA-2026-08-22: 0 fires ever since 2026-04-28 creation. Away T2-4 British Isles config looked reasonable but production data never produced matching candidates. Fresh shadow-bot lineup (bot_sweep_*, bot_no_pin_home_v1) covers similar territory with better-tuned floors. Historical simulated_bets: 0 rows.',
    updated_at    = NOW()
WHERE name = 'bot_opt_away_british'
  AND retired_at IS NULL;

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'MOVE-ACTIVE-TO-BETA-2026-08-22: 0 fires ever since 2026-04-28 creation. Likely config bug — min_prob 0.40 for AWAY selections at odds 2.20-3.50 requires the model to be MORE confident than the market on the underdog side. Effectively closed gate. Fresh shadow-bot lineup covers similar territory.',
    updated_at    = NOW()
WHERE name = 'bot_opt_away_europe'
  AND retired_at IS NULL;

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'MOVE-ACTIVE-TO-BETA-2026-08-22: 0 fires ever since 2026-04-28 creation. Config likely stale — odds 2.50-4.00 for OU is longshot territory (typical OU 2.5 line prices 1.60-2.30). Combined with min_prob 0.40 the gate never triggers. Superseded by bot_sweep_ou25_v1 + bot_sweep_ou35_v1 on the shadow lineup.',
    updated_at    = NOW()
WHERE name = 'bot_opt_ou_british'
  AND retired_at IS NULL;

COMMENT ON COLUMN bots.maturity_label IS
    'Bot lifecycle stage. calibrated = proven at n≥50 with positive ROI + CLV. beta = production strategy being volume-tested. experimental = shadow-only pre-graduation. retired = kill flag set, historical only. `active` is legacy (pre-taxonomy) — as of 2026-08-22 no live bot carries it; MOVE-ACTIVE-TO-BETA reconciled the last five (two moved to beta, three retired).';
