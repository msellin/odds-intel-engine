-- COOLBET-FEED-PAIRING-2026-08-28 — make cross-book pairing staleness auditable.
--
-- The line-shop bots compare a Coolbet price to a Pinnacle price, but nothing
-- recorded how far apart in TIME those two quotes were taken. Measured on
-- bot_coolbet_value_v1's last 79 picks: median gap 1.63h, 56% paired more than
-- an hour apart, 37% more than four hours, max 15.6h. Coolbet swings 14-19%
-- intraday, so a stale pairing can manufacture edge out of nothing — that is
-- exactly what produced apparent +55.6% double-chance edges from quotes 16h
-- apart during the 2026-08-28 probe.
--
-- Without this column the only way to audit staleness is to reconstruct it
-- afterwards from odds_snapshots, which is slow and impossible once rows age
-- out. Recording it at pick time makes "was this edge real or stale?" a query
-- rather than an investigation.

ALTER TABLE shadow_bets
    ADD COLUMN IF NOT EXISTS pair_gap_hours NUMERIC(6,2);

COMMENT ON COLUMN shadow_bets.pair_gap_hours IS
  'Hours between the two bookmakers'' quotes used to compute this pick''s edge '
  '(Coolbet vs Pinnacle for the line-shop bots). NULL for bots that do not '
  'pair books. Large values mean the edge may be staleness, not opportunity.';

CREATE INDEX IF NOT EXISTS idx_shadow_bets_pair_gap
    ON shadow_bets (pair_gap_hours)
    WHERE pair_gap_hours IS NOT NULL;
