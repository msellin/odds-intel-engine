-- 386 — CLV AGAINST THE DE-VIGGED SHARP CLOSE, for every leg of every ledger ([[#024]], 2026-09-23)
--
-- Step 2 of the Block D re-order (dev/active/block-d-order-2026-09-23.md), the handover's T2.
-- The published forward test decides on CLV against the pick's OWN soft book's close
-- (7.8-11% margin). The research standard is the de-vigged SHARP close, which settles an
-- edge in ~135-150 bets instead of ~13,500. This table holds that number for every settled
-- leg of simulated_bets, shadow_bets and picks_forward_test, in ONE definition:
--
--   clv_sharp = odds × Shin(Pinnacle close) − 1
--
-- where the close is the latest COMPLETE Pinnacle market assembled within ±2 min, no older
-- than 60 min at kickoff (close_age_min stored). Deliberately NOT the existing
-- get_devigged_pinnacle_close_prob, whose close has no age bound and fetches each side
-- separately. A separate table rather than new ledger columns: settlement is untouched, and
-- the pre-registered forward test keeps its own decision variable (clv_margin_corrected) —
-- this is reported BESIDE it, never instead of it.

CREATE TABLE IF NOT EXISTS leg_clv_sharp (
    ledger          text        NOT NULL,   -- 'simulated_bets' | 'shadow_bets' | 'picks_forward_test'
    leg_id          uuid        NOT NULL,
    match_id        uuid        NOT NULL,
    market          text        NOT NULL,
    selection       text        NOT NULL,
    odds            numeric,               -- the price the leg is judged at
    odds_basis      text,                  -- 'published' | 'executable' (odds_at_pick_live) | 'high_water' (odds_at_pick)
    p_close         numeric,               -- Shin-de-vigged Pinnacle closing probability
    close_ts        timestamptz,
    close_age_min   numeric,               -- kickoff − close_ts
    clv_sharp       numeric,               -- odds × p_close − 1 ; NULL unless status = 'ok'
    status          text        NOT NULL,   -- 'ok' | 'no_fresh_close' | 'unsupported_market'
    computed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ledger, leg_id)
);

CREATE INDEX IF NOT EXISTS leg_clv_sharp_match ON leg_clv_sharp (match_id);
