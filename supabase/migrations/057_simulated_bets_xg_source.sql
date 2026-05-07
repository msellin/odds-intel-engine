-- Add xg_source column to simulated_bets for inplay bot segmentation.
-- 'live'       = real AF xG from stats endpoint (top leagues only)
-- 'shot_proxy' = estimated from shots: sot*0.10 + off_target*0.03
-- NULL         = prematch bots (not applicable)
ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS xg_source TEXT;

-- Backfill existing inplay bets from reasoning JSON
UPDATE simulated_bets
SET xg_source = reasoning::jsonb->>'xg_source'
WHERE reasoning IS NOT NULL
  AND reasoning::jsonb->>'xg_source' IS NOT NULL;
