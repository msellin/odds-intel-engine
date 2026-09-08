-- COOLBET-LINESHOP-BOT-RETIRED-2026-09-08 — retire bot_coolbet_value_v1, the
-- Coolbet LINE-SHOP bot (Coolbet price vs de-vigged Pinnacle). Owner-authorized.
--
-- Why: line-shop loses out-of-sample (the whole COOLBET-OWN strategy audit — it
-- was −17% on O/U every month and the 1x2 raw signal is negative OOS, §52). The
-- two MODEL-EDGE bots (bot_coolbet_ou_model_v1, bot_coolbet_1x2_model_v1) are the
-- real-money path the UI placer uses now. This bot could not place anyway — its
-- coolbet_placer_bots toggle was removed in migration 314 — and it does NOT feed
-- /performance (experimental/shadow, writes shadow_bets not simulated_bets), so
-- retiring it is clean: no published figure moves.
--
-- Also stopped in code this commit: _run_coolbet_value_pass generation calls
-- (daily_pipeline_v2) and its PLACEABLE_BOTS / BOT_THRESHOLDS / DEFAULT_BOT
-- entries (place_coolbet_ui.py). Its 3,152 historical shadow_bets are KEPT for
-- the line-shop-vs-model-edge analysis record.

UPDATE bots
   SET is_active     = FALSE,
       retired_at    = NOW(),
       retired_reason = 'Line-shop retired 2026-09-08 — loses OOS (§52); the two model-edge Coolbet bots are the real-money path. Could not place (toggle removed mig 314); does not feed /performance. History kept.',
       updated_at    = NOW()
 WHERE name = 'bot_coolbet_value_v1';

-- Belt-and-braces: ensure no real-money toggle row lingers (mig 314 already
-- deleted it; this is idempotent and safe if it is already gone).
DELETE FROM coolbet_placer_bots WHERE bot_name = 'bot_coolbet_value_v1';
