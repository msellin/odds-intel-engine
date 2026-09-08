-- COOLBET-PLACER-CONTROL-2026-09-08 — remove the paused line-shop 1x2 bot
-- (bot_coolbet_value_v1) from the placer control panel. Its signal loses
-- out-of-sample (BOT-2D-AUDIT) and model-edge (bot_coolbet_1x2_model_v1) now
-- places 1x2 instead, so it is no longer a real-money placement option.
-- Deleting the coolbet_placer_bots row (a) removes it from the /admin control
-- block and (b) drops it from the effective allowlist (PLACEABLE_BOTS ∩ enabled
-- rows), so it cannot place even though it stays in PLACEABLE_BOTS. It remains a
-- tracked SHADOW bot (still on /admin/shadow-bots) for the line-shop-vs-model
-- comparison. Reversible: re-INSERT the row (ui_place_enabled=false).
DELETE FROM coolbet_placer_bots WHERE bot_name = 'bot_coolbet_value_v1';
