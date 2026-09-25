-- 438 — owner 2026-09-25: retire bot_ou35_model_v1 after the first "review this bot" flag (#155 rule, view bot_review_flag,
-- migration 437): 460 settled legs, sharp-anchor CLV −4.5%, upper 95% CI −4.0%. #152 step 3 read −8% on the old model.
-- Its picks stay in the "work done" totals and the retired section (#157). The job's bot lookup now excludes retired bots.
UPDATE bots SET is_active = false, retired_at = now()
 WHERE name = 'bot_ou35_model_v1' AND retired_at IS NULL;
