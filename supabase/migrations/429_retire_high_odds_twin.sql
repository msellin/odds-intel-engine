-- 429 — #152 (2026-09-25, owner): retire the High-odds NEW+ twin created earlier today.
-- Its exact rule (the original High-odds thresholds on NEW+) made 0 picks in the backtest window, and a widened
-- high-odds lane was weak on the confirm half (LANES). The original High-odds bot stays. Revisit after #154.
UPDATE bots SET is_active = false, retired_at = now()
 WHERE name = 'bot_high_roi_global_v2_newplus_v1' AND retired_at IS NULL;
