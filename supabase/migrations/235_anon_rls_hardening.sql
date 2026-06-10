-- ============================================================================
-- ANON-AUTH PHASE-1 HARDENING + SECURITY ADVISOR FIXES
--
-- Two concerns bundled (both became urgent when anonymous sign-ins were
-- enabled in the Supabase project on 2026-06-10):
--
-- 1) Supabase Security Advisor flagged 9 tables with RLS disabled. With
--    anonymous auth on, these are reachable by anyone who lands on the
--    site — including bots running signInAnonymously. Enable RLS on all
--    of them. Tables fall into two buckets:
--      - PUBLIC sports data → grant public_read (USING true) policy
--      - INTERNAL ops/quarantine/backup → no policy = service-role only
--
-- 2) Anonymous users inherit the `authenticated` role per Supabase docs
--    (https://supabase.com/docs/guides/auth/auth-anonymous#access-control).
--    Two of our user-keyed write policies should NOT apply to anonymous
--    users because they're abuse vectors:
--      - match_votes  (vote inflation if anon can spawn infinite accounts)
--      - match_notes  (spam content)
--    Add a JWT-claim check that excludes anonymous users.
-- ============================================================================

-- ─── PART 1: Enable RLS on flagged tables ────────────────────────────────────

-- Public sports data — grant SELECT to everyone (anon role + authenticated).
ALTER TABLE tennis_value_bets       ENABLE ROW LEVEL SECURITY;
ALTER TABLE tennis_fixtures_today   ENABLE ROW LEVEL SECURITY;
ALTER TABLE cs2_hltv_team_stats     ENABLE ROW LEVEL SECURITY;
ALTER TABLE cs2_hltv_top_players    ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    CREATE POLICY "public_read" ON tennis_value_bets       FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE POLICY "public_read" ON tennis_fixtures_today   FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE POLICY "public_read" ON cs2_hltv_team_stats     FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE POLICY "public_read" ON cs2_hltv_top_players    FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Internal ops / quarantine / backup tables — RLS on, NO policies.
-- service_role bypasses RLS, so the pipeline still works. Public/authenticated
-- roles get zero access, which is correct for these tables.
ALTER TABLE manual_placement_queue                          ENABLE ROW LEVEL SECURITY;
ALTER TABLE bet_telegram_alerts                             ENABLE ROW LEVEL SECURITY;
ALTER TABLE odds_snapshots_quarantined                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE matches_dupe_quarantined                        ENABLE ROW LEVEL SECURITY;
ALTER TABLE simulated_bets_pre_inplay_normalize_2026_05_17  ENABLE ROW LEVEL SECURITY;

-- ─── PART 2: Block anonymous users from abuse-vector writes ──────────────────

-- match_votes: existing policy lets any authenticated user insert their own
-- vote. With anon enabled, anyone can spawn an account in 1 API call and vote.
-- Add JWT claim check: must be a non-anonymous authenticated session.
DROP POLICY IF EXISTS "Users can insert own vote" ON match_votes;
CREATE POLICY "Users can insert own vote"
    ON match_votes FOR INSERT
    TO authenticated
    WITH CHECK (
        auth.uid() = user_id
        AND (auth.jwt() ->> 'is_anonymous')::boolean IS NOT TRUE
    );

DROP POLICY IF EXISTS "Users can update own vote" ON match_votes;
CREATE POLICY "Users can update own vote"
    ON match_votes FOR UPDATE
    TO authenticated
    USING (
        auth.uid() = user_id
        AND (auth.jwt() ->> 'is_anonymous')::boolean IS NOT TRUE
    )
    WITH CHECK (
        auth.uid() = user_id
        AND (auth.jwt() ->> 'is_anonymous')::boolean IS NOT TRUE
    );

-- match_notes: same logic — spam vector if open to anon.
DROP POLICY IF EXISTS "Users can insert own notes" ON match_notes;
CREATE POLICY "Users can insert own notes"
    ON match_notes FOR INSERT
    TO authenticated
    WITH CHECK (
        auth.uid() = user_id
        AND (auth.jwt() ->> 'is_anonymous')::boolean IS NOT TRUE
    );

DROP POLICY IF EXISTS "Users can update own notes" ON match_notes;
CREATE POLICY "Users can update own notes"
    ON match_notes FOR UPDATE
    TO authenticated
    USING (
        auth.uid() = user_id
        AND (auth.jwt() ->> 'is_anonymous')::boolean IS NOT TRUE
    )
    WITH CHECK (
        auth.uid() = user_id
        AND (auth.jwt() ->> 'is_anonymous')::boolean IS NOT TRUE
    );

-- NOTE: The following user-keyed write policies INTENTIONALLY remain
-- accessible to anonymous users — they are the activation surface for
-- Phase 2 (lazy ensureAnonUser on first save):
--   - user_picks                (tracker picks)
--   - saved_matches             (favorite matches)
--   - user_match_favorites      (favorite teams)
--   - daily_unlocks             (free-tier AI pick unlock)
--   - wc_bracket_picks / wc_user_picks / wc_group_predictions  (gamification)
-- These all key on auth.uid() = user_id so anon users can only write
-- against their own row, no cross-user impact.
