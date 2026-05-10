-- SELF-USE-VALIDATION Phase 2.1
-- Real-money bets placed manually at accessible bookmakers, parallel to
-- simulated_bets (paper trading). Settlement code mirrors simulated_bets path.

CREATE TABLE IF NOT EXISTS real_bets (
  id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  simulated_bet_id    UUID         REFERENCES simulated_bets(id) ON DELETE SET NULL,
  bot_id              UUID         REFERENCES bots(id),
  match_id            UUID         NOT NULL REFERENCES matches(id),
  market              TEXT         NOT NULL,
  selection           TEXT         NOT NULL,
  bookmaker           TEXT         NOT NULL REFERENCES accessible_bookmakers(bookmaker),
  captured_odds       NUMERIC(8,4),                                  -- what the UI showed at click time
  actual_odds         NUMERIC(8,4) NOT NULL,                         -- what the user actually got
  slippage_pct        NUMERIC(8,4) GENERATED ALWAYS AS (
                        CASE
                          WHEN captured_odds IS NULL OR captured_odds = 0 THEN NULL
                          ELSE (actual_odds - captured_odds) / captured_odds * 100
                        END
                      ) STORED,
  stake               NUMERIC(10,2) NOT NULL CHECK (stake > 0),
  placed_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  result              TEXT          NOT NULL DEFAULT 'pending'
                      CHECK (result IN ('pending', 'won', 'lost', 'void', 'half_won', 'half_lost')),
  pnl                 NUMERIC(10,2),
  resolved_at         TIMESTAMPTZ,
  notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_real_bets_match     ON real_bets (match_id);
CREATE INDEX IF NOT EXISTS idx_real_bets_bot       ON real_bets (bot_id);
CREATE INDEX IF NOT EXISTS idx_real_bets_placed_at ON real_bets (placed_at DESC);
CREATE INDEX IF NOT EXISTS idx_real_bets_pending   ON real_bets (placed_at DESC) WHERE result = 'pending';

ALTER TABLE real_bets ENABLE ROW LEVEL SECURITY;

-- Idempotent: drop existing policies before recreating (CREATE POLICY has no IF NOT EXISTS).
DROP POLICY IF EXISTS "Superadmins read real_bets"  ON real_bets;
DROP POLICY IF EXISTS "Superadmins write real_bets" ON real_bets;

CREATE POLICY "Superadmins read real_bets"
  ON real_bets FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
        AND COALESCE(profiles.is_superadmin, false) = true
    )
  );

CREATE POLICY "Superadmins write real_bets"
  ON real_bets FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
        AND COALESCE(profiles.is_superadmin, false) = true
    )
  );

COMMENT ON TABLE real_bets IS
  'SELF-USE-VALIDATION: real-money bets placed manually at Coolbet/Bet365. Slippage_pct is auto-computed from captured vs actual odds. Settled by workers/jobs/settlement.py:_settle_real_bets, same cadence as simulated_bets.';
