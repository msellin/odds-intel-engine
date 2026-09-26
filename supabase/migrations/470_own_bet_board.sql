-- 470 — [[#182]] OWN-BETTING-BOARD: "where do I put my real money right now?"
--
-- One row per pending pre-match bot selection (match, market, selection) with the latest price at
-- every book we can bet from Estonia (ACCESSIBLE_BOOKMAKERS), Pinnacle's de-vigged fair price by the
-- ONE sharp engine (workers/automation/sharp_engine.py: devig.fair_prob, the shared anchor-age rule,
-- 60-min book freshness, the 8% ceiling, the outlier cap and the wrong-fixture sanity guard), the
-- edge at each book, the best clean book, and `take_at` = the lowest price still worth taking
-- (EV >= the board's floor vs fair), so the operator can try to match it at another book.
--
-- Written every 10 min by workers/jobs/own_bet_board.py (job `own_bet_board`), which REPLACES the
-- whole table in one transaction. PRIVATE (service_role only): read server-side by the admin OWN
-- board (/admin/shadow-bots), never by anon.
SET lock_timeout = '3s';

CREATE TABLE IF NOT EXISTS public.own_bet_board (
  match_id     uuid        NOT NULL,
  market       text        NOT NULL,
  selection    text        NOT NULL,
  kickoff      timestamptz NOT NULL,
  home         text,
  away         text,
  league       text,
  bots         jsonb       NOT NULL,      -- [{bot, display, status, vip, source, pick_id, pick_time}]
  n_bots       integer     NOT NULL,
  p_fair       double precision,          -- Pinnacle de-vigged, NULL = no fresh complete Pinnacle line
  fair_odds    double precision,
  take_at      double precision,          -- (1 + floor) / p_fair
  pin_age_min  double precision,
  prices       jsonb       NOT NULL,      -- {book: {odds, age_min, edge, refusal}}; refusal NULL = clears
  best_book    text,                      -- best clearing book, NULL when none clears
  best_odds    double precision,
  best_edge    double precision,
  clears       boolean     NOT NULL,      -- at least one Estonian book clears every sharp gate now
  computed_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (match_id, market, selection)
);
COMMENT ON TABLE public.own_bet_board IS
  '[[#182]] OWN board: pending bot selections x Estonian-book prices x Pinnacle fair (sharp engine). Job own_bet_board, replaced every 10 min. Private.';

REVOKE ALL ON public.own_bet_board FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.own_bet_board TO service_role;
