-- ============================================================================
-- ANON-AUTH-PHASE-1: support for anonymous Supabase users (signInAnonymously)
--
-- Plan: dev/active/anon-auth-plan.md
--
-- Anonymous users have NULL email until they upgrade (linkIdentity or
-- updateUser({email})). The handle_new_user trigger currently coerces
-- email to '' (empty string) because profiles.email is NOT NULL — this
-- creates an invalid-looking row for anonymous users. Fix:
--
--   1. Drop NOT NULL on profiles.email
--   2. Update handle_new_user to pass new.email through unchanged so
--      anonymous rows land with email IS NULL (semantically correct)
--   3. Normalise any pre-existing email='' rows to email=NULL so app
--      code can use a single null-check rather than two equality checks
--
-- Phase 1 only — does NOT enable anonymous sign-ins in the Supabase
-- project (that's a dashboard toggle, separate manual step).
-- ============================================================================

-- 1. Drop NOT NULL constraint on profiles.email
ALTER TABLE profiles
    ALTER COLUMN email DROP NOT NULL;

-- 2. Update trigger to pass email through unchanged
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, email, tier)
    VALUES (
        new.id,
        new.email,  -- May be NULL for anonymous users; that's intentional
        'free'
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO public.user_notification_settings (user_id)
    VALUES (new.id)
    ON CONFLICT (user_id) DO NOTHING;

    RETURN new;
END;
$$;

-- 3. Normalise any pre-existing email='' rows to email=NULL
-- (Defensive — should be 0 rows in practice since all 38 historical
-- signups came via Google OAuth or magic link, both of which provide
-- a real email. Belt-and-braces.)
UPDATE profiles
SET email = NULL
WHERE email = '';
