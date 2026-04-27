-- =============================================================================
-- OddsIntel — Migration 003: Bet deduplication constraint
-- =============================================================================
-- Prevents the same bot from placing the same bet on the same match twice.
-- Safe to run multiple times (IF NOT EXISTS / DO NOTHING).
-- =============================================================================

-- Unique constraint: one bet per bot + match + market + selection
-- This means re-running the morning pipeline never creates duplicates.
ALTER TABLE simulated_bets
  ADD CONSTRAINT uq_bet_per_bot_match_market_selection
  UNIQUE (bot_id, match_id, market, selection);
