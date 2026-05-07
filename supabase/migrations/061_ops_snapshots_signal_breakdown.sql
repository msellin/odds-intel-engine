-- Migration 061: Add per-signal and odds-market breakdown columns to ops_snapshots
-- Enables Fixtures section to show separate counts for ELO, form, H2H, injuries, standings.

ALTER TABLE ops_snapshots
  ADD COLUMN IF NOT EXISTS signals_with_elo        integer,
  ADD COLUMN IF NOT EXISTS signals_with_form        integer,
  ADD COLUMN IF NOT EXISTS signals_with_h2h         integer,
  ADD COLUMN IF NOT EXISTS signals_with_injuries    integer,
  ADD COLUMN IF NOT EXISTS signals_with_standings   integer,
  ADD COLUMN IF NOT EXISTS odds_market_match_winner integer,
  ADD COLUMN IF NOT EXISTS odds_market_goals_ou     integer,
  ADD COLUMN IF NOT EXISTS odds_market_btts         integer;
