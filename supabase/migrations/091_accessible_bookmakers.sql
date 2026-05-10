-- SELF-USE-VALIDATION Phase 2.1
-- Registry of bookmakers the user can actually place real-money bets at.
-- Maintained manually. Status flips to 'limited' or 'banned' if a book starts
-- restricting account; admin/place UI filters suggestions accordingly.

CREATE TABLE IF NOT EXISTS accessible_bookmakers (
  bookmaker   TEXT PRIMARY KEY,                                -- e.g. 'Coolbet', 'Bet365'
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active', 'limited', 'banned', 'inactive')),
  notes       TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed: Coolbet (preferred) + Bet365 (secondary). Idempotent.
INSERT INTO accessible_bookmakers (bookmaker, status, notes) VALUES
  ('Coolbet', 'active', 'Estonia-licensed, Kambi-powered. Preferred. Unibet odds (also Kambi) used as proxy until direct Coolbet API integrated.'),
  ('Bet365',  'active', 'Direct via API-Football. Secondary book. Watch for limit-on-winners — sharps usually limited within 4–12 weeks.')
ON CONFLICT (bookmaker) DO NOTHING;

-- Make this RLS-locked: only service-role + superadmin profiles read it.
ALTER TABLE accessible_bookmakers ENABLE ROW LEVEL SECURITY;

-- Idempotent: drop existing policies before recreating (CREATE POLICY has no IF NOT EXISTS).
DROP POLICY IF EXISTS "Superadmins read accessible_bookmakers" ON accessible_bookmakers;
DROP POLICY IF EXISTS "Superadmins manage accessible_bookmakers" ON accessible_bookmakers;

CREATE POLICY "Superadmins read accessible_bookmakers"
  ON accessible_bookmakers FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
        AND COALESCE(profiles.is_superadmin, false) = true
    )
  );

CREATE POLICY "Superadmins manage accessible_bookmakers"
  ON accessible_bookmakers FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
        AND COALESCE(profiles.is_superadmin, false) = true
    )
  );

COMMENT ON TABLE accessible_bookmakers IS
  'SELF-USE-VALIDATION: which bookmakers the user can place real bets at, with rate-limit/ban status. Superadmin-only.';
