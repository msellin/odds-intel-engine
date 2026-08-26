-- SHADOW-CLV-BOOKMAKER-FIX-2026-08-26
--
-- shadow_bets had exactly one CLV column, `clv`, computed as
--     odds_at_pick / get_closing_odds(match, market, selection)
-- where get_closing_odds() had no bookmaker filter. Every book writes its
-- closing snapshot in the same batch, so they tie on timestamp and
-- `ORDER BY timestamp DESC LIMIT 1` returned an arbitrary one — observed
-- suppliers were 10Bet (77), Bet365 (45), William Hill (28), with 114 rows
-- falling through to the pre-kickoff fallback. Observed spread across books on
-- a single 1X2 home selection: 3.90 → 5.00.
--
-- That is not merely noisy. Line-shopping bots set odds_at_pick to the MAX
-- across accessible books by construction, so max(13 books) / one_arbitrary_book
-- reads positive whether or not the bet had edge — which is why the admin page
-- could show +9 to +11% CLV next to negative ROI.
--
-- Backtest (scripts/clv_variant_backtest.py, 3,446 settled picks, all three
-- variants on the SAME rows) ranked the candidates:
--
--     variant                              rho     Q5-Q1 spread   monotone
--     clv (any book, today's value)      +0.0592     +23.4pp        3/4
--     clv vs the book we picked at       +0.0326     +25.0pp        2/4
--     clv vs raw Pinnacle close          +0.0780     +36.1pp        3/4
--     clv vs DE-VIGGED Pinnacle close    +0.0784     +34.3pp        4/4
--
-- Pinnacle-anchored CLV is the better validator on every measure. Raw and
-- de-vigged rank almost identically (+0.0780 vs +0.0784) — the de-vig does not
-- improve the ORDERING, it fixes the ZERO POINT: raw Pinnacle CLV carries
-- Pinnacle's overround, so it reads positive by roughly the margin even on a
-- bet with no edge. Since the whole point of the column is to answer "is this
-- above or below fair", the zero point is the part that has to be right.
--
-- `clv` is deliberately left alone: ~15 reporting queries average it, and
-- redefining it in place would silently move numbers in the weekly digest, the
-- bot ledger and the meta-CLV score.

ALTER TABLE shadow_bets
  ADD COLUMN IF NOT EXISTS clv_pinnacle DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS closing_bookmaker TEXT;

COMMENT ON COLUMN shadow_bets.clv_pinnacle IS
  'odds_at_pick * devig(Pinnacle close) - 1. Shin de-vig, so 0 means exactly '
  'Pinnacle-fair. The validator to judge a shadow bot on; prefer it over clv.';
COMMENT ON COLUMN shadow_bets.closing_bookmaker IS
  'Which book supplied closing_odds. Was previously unrecorded and arbitrary.';

ALTER TABLE simulated_bets
  ADD COLUMN IF NOT EXISTS clv_pinnacle_devig DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS closing_bookmaker TEXT;

COMMENT ON COLUMN simulated_bets.clv_pinnacle_devig IS
  'Shin-de-vigged Pinnacle CLV. simulated_bets.clv_pinnacle is the RAW variant '
  '(carries Pinnacle overround, so its zero point is ~+2-4% off); this column '
  'is the one whose sign is meaningful.';

-- SHADOW-UNIQUE-VIEW-ALIGN-2026-08-26: migration 282 created this view keyed on
-- the LATEST pick_time, while both /admin/shadow-bots pages dedupe on the
-- EARLIEST. Same data, two different answers to "what did this bot return".
-- Earliest wins: it is the price the bot first identified, before any drift,
-- and it is what the operator has been reading. (Measured drift between first
-- and last cohort row is only +0.20% over 403 multi-cohort picks, so this
-- changes almost nothing numerically — but one definition beats two.)
CREATE OR REPLACE VIEW shadow_bets_unique AS
SELECT DISTINCT ON (bot_id, match_id, market, selection) *
  FROM shadow_bets
 ORDER BY bot_id, match_id, market, selection, pick_time ASC;

COMMENT ON VIEW shadow_bets_unique IS
  'One row per (bot_id, match_id, market, selection) — the EARLIEST pick_time, '
  'matching how /admin/shadow-bots dedupes. Use this, not shadow_bets, for '
  'ROI / hit-rate / graduation-gate counts: the 30-min refresh writes one row '
  'per shadow_cohort by design, ~48 rows per pick per day.';
