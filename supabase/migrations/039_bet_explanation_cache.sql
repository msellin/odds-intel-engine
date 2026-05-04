-- Cache AI-generated bet explanations on the bet row.
-- First user to click "Why this pick?" for a bet triggers a Gemini call;
-- all subsequent requests (any user, any tier) return the cached text.
-- This bounds total API calls to the number of unique bets per day (~20-50),
-- not to the number of users.

ALTER TABLE simulated_bets
  ADD COLUMN IF NOT EXISTS ai_explanation TEXT,
  ADD COLUMN IF NOT EXISTS ai_explanation_at TIMESTAMPTZ;
