-- Enable RLS on simulated_bets to block direct browser queries.
-- The Next.js server uses the service role key (bypasses RLS) and enforces
-- its own tier-based sanitization before sending data to the client.
-- Any direct query via the anon key (browser console, PostgREST) is denied
-- unless the user's profile is pro or elite.

ALTER TABLE simulated_bets ENABLE ROW LEVEL SECURITY;

-- Pro and Elite users can query directly (e.g. future API uses).
-- Free and anon users are denied — they get data only through the
-- server-sanitized RSC payload.
CREATE POLICY "pro_elite_read_simulated_bets"
  ON simulated_bets
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
        AND (
          profiles.tier IN ('pro', 'elite')
          OR profiles.is_superadmin = true
        )
    )
  );

-- The pipeline (service role) always bypasses RLS — no policy needed for writes.
