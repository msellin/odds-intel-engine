-- 356_promo_terms_and_ledger.sql
-- OWN Phase 2 — promotions, boosts, free bets, acca insurance (2026-09-15)
-- dev/active/own-implementation-plan.md §Phase 2; docs/OWN_STRATEGY_AUDIT_2026_09_15.md §4.
--
-- WHY. Every pre-match and in-play strategy at the books we can legally place
-- at (Coolbet, Epicbet, Unibet-Site) measures at or below the vig. The one OWN
-- lever with a positive expectation that needs NO prediction is the books'
-- own promotional spend: an odds boost, a free bet, acca insurance. Their EV is
-- arithmetic on a fair price we already compute well (Shin de-vig of a tight
-- consensus) — PROVIDED the terms are applied: min odds, max stake, stake-not-
-- returned, rollover, single-use. Real terms decide the sign, and no exchange is
-- EMTA-licensed so nothing can be hedged; every promo bet is variance-bearing.
--
-- WHAT. Two tables. `promo_terms` is the owner-maintained catalogue of live
-- offers with their exact terms and source URL (captured_at + valid_to so a
-- silently changed T&C is visible). `promo_ledger` records every promo actually
-- taken WITH ITS EV COMPUTED BEFORE THE BET (the guard `PROMO-LEDGER-EV-BEFORE-BET`
-- refuses a row without ev_eur), then the realised P&L after settlement, so the
-- monthly review compares realised against Σ EV and kills the lever on two
-- consecutive months more than 1.5 sd below expectation.
--
-- Re-appliable.

CREATE TABLE IF NOT EXISTS promo_terms (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book             TEXT NOT NULL,               -- 'Coolbet' | 'Epicbet' | 'Unibet-Site' | 'Olybet' | 'Optibet' | 'Betsafe' | 'Paf' | 'Tonybet' | 'bet365.ee'
    promo_type       TEXT NOT NULL CHECK (promo_type IN ('odds_boost', 'free_bet', 'acca_insurance', 'deposit_bonus', 'cashback', 'other')),
    title            TEXT NOT NULL,
    -- terms that decide the sign
    boost_pct        NUMERIC,                     -- odds_boost: +50 => decimal odds × 1.5 on the profit part (see promo_ev)
    boost_applies_to TEXT CHECK (boost_applies_to IN ('profit', 'odds')),  -- boost on winnings (usual) or on full decimal odds
    face_value_eur   NUMERIC,                     -- free_bet: the token's face value
    stake_returned   BOOLEAN NOT NULL DEFAULT FALSE,  -- free bet: is the token stake paid back on a win? (usually NOT — "SNR")
    min_odds         NUMERIC,                     -- minimum decimal odds the offer requires (often 1.50–2.00)
    max_stake_eur    NUMERIC,                     -- cap on the boosted / insured stake
    min_legs         INTEGER,                     -- acca_insurance: minimum legs
    refund_eur       NUMERIC,                     -- acca_insurance / cashback: refund amount (as free bet unless refund_cash)
    refund_cash      BOOLEAN NOT NULL DEFAULT FALSE,
    rollover_x       NUMERIC,                     -- deposit_bonus: turnover multiple before withdrawal
    deposit_eur      NUMERIC,                     -- deposit_bonus: the deposit the rollover applies to (rollover is on deposit+bonus at EE books)
    single_use       BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from       TIMESTAMPTZ,
    valid_to         TIMESTAMPTZ,
    source_url       TEXT,
    terms_text       TEXT,
    captured_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_by      TEXT NOT NULL DEFAULT 'owner',
    active           BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS promo_terms_active_idx ON promo_terms (active, valid_to);
COMMENT ON TABLE promo_terms IS
    'Owner-maintained catalogue of live promotions with the EXACT terms that decide their EV. '
    'OWN Phase 2 (2026-09-15). Re-capture when a T&C changes; valid_to makes silent changes visible.';

CREATE TABLE IF NOT EXISTS promo_ledger (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promo_terms_id   UUID NOT NULL REFERENCES promo_terms(id),
    book             TEXT NOT NULL,
    match_id         UUID REFERENCES matches(id),
    market           TEXT,
    selection        TEXT,
    fair_prob        NUMERIC NOT NULL,            -- Shin-de-vigged consensus probability of the selection
    fair_prob_books  INTEGER NOT NULL,            -- how many books the consensus used
    price            NUMERIC NOT NULL,            -- decimal odds taken (pre-boost)
    stake_eur        NUMERIC NOT NULL,
    ev_eur           NUMERIC NOT NULL,            -- computed BEFORE the bet, under the stated terms
    ev_note          TEXT,                        -- the formula path used (promo_ev.explain)
    taken_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at       TIMESTAMPTZ,
    realised_pnl_eur NUMERIC,
    real_bet_id      UUID REFERENCES real_bets(id),   -- when the underlying stake is also in real_bets
    notes            TEXT
);
CREATE INDEX IF NOT EXISTS promo_ledger_taken_idx ON promo_ledger (taken_at);
COMMENT ON TABLE promo_ledger IS
    'Every promotion taken, with EV computed BEFORE the bet and realised P&L after. '
    'Monthly review: realised vs Σ EV; kill on two consecutive months > 1.5 sd below. OWN Phase 2.';
