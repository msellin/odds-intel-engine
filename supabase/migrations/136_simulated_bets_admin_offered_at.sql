-- Track the first time a bet was surfaced on the admin /place page.
-- NULL = never shown to admin (generated after kickoff, or match went live before pipeline ran).
-- Non-null = admin had the opportunity to place this bet at this timestamp.
ALTER TABLE simulated_bets
  ADD COLUMN IF NOT EXISTS admin_offered_at TIMESTAMPTZ DEFAULT NULL;
