-- BOT-ROSTER-CLEANUP-2026-08-21
--
-- Two-part cleanup. Reasons and full analysis in
-- dev/active/bot-roster-review-2026-08-21.md.
--
-- Part A — 5 losing soccer / in-play bots:
--   * bot_greek_turkish — 1 bet 60d @ -100% ROI, -30% CLV. Zombie.
--   * inplay_c — 18 bets 60d @ -27.6%, 30d @ -69.1%. Same fixture-mix
--     shift pattern that killed inplay_e (retired 2026-07-31).
--   * inplay_btts_press_v1 — 70 bets 60d @ -13.4%, 30d @ -22.6%.
--     Highest-volume loser. Not converging.
--   * inplay_d — 2 bets 60d @ -100%. Effectively dead.
--   * bot_ou_specialist — 2 bets 60d @ -37%, 30d @ -100%, CLV -0.26%.
--     Near-zombie with no signal.
--
-- Part B — 11 CS2 bots:
--   Already functionally retired by CS2-DISABLE-2026-07-31 (env
--   CS2_ENABLED=false at the scheduler level). Every CS2 job is a
--   no-op. Marking them retired in DB removes roster clutter — the
--   /performance leaderboard funnel now honestly reflects "N tested".
--   Zero hero impact (CS2 markets not in the calibrated public
--   cohort: 1x2 + o/u + btts on soccer only).
--
-- Estimated hero movement post-Part-A (per per-bot delta analysis):
--   ROI +10.37% → ~+12.5%  ·  CLV +9.64% → ~+10%
--   n 1,208 → ~1,030      ·  PnL €751 → ~€830
-- Post-Part-B: unchanged (CS2 not in cohort).

-- ── Part A: losing soccer + in-play bots ──────────────────────────
UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'BOT-ROSTER-CLEANUP-2026-08-21: 1 bet 60d @ -100% ROI, -30% CLV. Zombie — no volume, no signal. See dev/active/bot-roster-review-2026-08-21.md.'
WHERE name = 'bot_greek_turkish';

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'BOT-ROSTER-CLEANUP-2026-08-21: 18 bets 60d @ -27.63% ROI, 11 bets 30d @ -69.09%. Getting worse — same fixture-mix shift pattern that killed inplay_e (2026-07-31).'
WHERE name = 'inplay_c';

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'BOT-ROSTER-CLEANUP-2026-08-21: 70 bets 60d @ -13.36% ROI, 49 bets 30d @ -22.62%. Highest-volume losing bot — not converging.'
WHERE name = 'inplay_btts_press_v1';

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'BOT-ROSTER-CLEANUP-2026-08-21: 2 bets 60d @ -100% ROI. Effectively dead.'
WHERE name = 'inplay_d';

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'BOT-ROSTER-CLEANUP-2026-08-21: 2 bets 60d @ -36.79% ROI, 1 bet 30d @ -100%, CLV -0.26%. Near-zombie — no volume, no signal.'
WHERE name = 'bot_ou_specialist';

-- ── Part B: CS2 roster cleanup ────────────────────────────────────
UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    maturity_label= 'retired',
    retired_reason= 'BOT-ROSTER-CLEANUP-2026-08-21: CS2-DISABLE cleanup — CS2_ENABLED=false at scheduler since 2026-07-31 (audit showed CS2 model overconfident, ~-30% ROI across bots). Roster cleanup only; zero paper/real activity since disable.'
WHERE name IN (
    'bot_cs2_a1m_specialist_v1',
    'bot_cs2_aggressive_v1',
    'bot_cs2_clean_sweep_v1',
    'bot_cs2_dog_v1',
    'bot_cs2_fav_v1',
    'bot_cs2_hltv_v1',
    'bot_cs2_map1_winner_v1',
    'bot_cs2_total_maps_v1',
    'bot_cs2_v7',
    'bot_cs2_v8',
    'bot_cs2_value_v1'
);
