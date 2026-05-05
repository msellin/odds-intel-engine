-- ============================================================================
-- Fix: Auto-create user_notification_settings on signup + backfill existing
--
-- The handle_new_user trigger only created profiles, not notification settings.
-- This means no user has ever had a row in user_notification_settings, so
-- the email digest (which JOINs this table) sends to nobody.
--
-- Fix:
--   1. Update handle_new_user() to also insert notification settings
--   2. Backfill all existing profiles that are missing a settings row
-- ============================================================================

-- 1. Update trigger to also create notification settings on signup
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
        COALESCE(new.email, ''),
        'free'
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO public.user_notification_settings (user_id)
    VALUES (new.id)
    ON CONFLICT (user_id) DO NOTHING;

    RETURN new;
END;
$$;

-- 2. Backfill all existing profiles missing a notification settings row
INSERT INTO public.user_notification_settings (user_id)
SELECT id FROM public.profiles
WHERE id NOT IN (SELECT user_id FROM public.user_notification_settings)
ON CONFLICT (user_id) DO NOTHING;
