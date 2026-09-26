-- 471 — [[#182]] OWN board prices off the v2 ANCHOR RESOLVER (workers/utils/anchor.py, #113 / #119), not
-- Pinnacle alone (owner 2026-09-26: "does the sharp engine also use the Betfair exchange and a consensus
-- of books as fallback?"). Order: Pinnacle + Betfair Exchange (blend; a conflict = NO price), Pinnacle
-- tight, exchange liquid, then a >= 5-book consensus without the book being priced, then Pinnacle wide.
-- Every row records which anchor priced it; `pin_age_min` now holds the anchor's oldest-member age.
-- depends on: 470_own_bet_board.sql
SET lock_timeout = '3s';
ALTER TABLE public.own_bet_board ADD COLUMN IF NOT EXISTS anchor_source text NOT NULL DEFAULT 'none';
ALTER TABLE public.own_bet_board ADD COLUMN IF NOT EXISTS anchor_books  integer NOT NULL DEFAULT 0;
COMMENT ON COLUMN public.own_bet_board.anchor_source IS
  'sharp_blend | pinnacle_tight | exchange_liquid | sharp_conflict | consensus | pinnacle_wide | none (anchor.py)';
