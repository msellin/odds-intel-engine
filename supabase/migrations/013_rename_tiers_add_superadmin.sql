-- 1. Add is_superadmin flag to profiles (default false)
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS is_superadmin boolean DEFAULT false;

-- 2. Rename tier enum values: scout->free, analyst->pro, sharp->elite, syndicate->elite
--    PostgreSQL can't rename enum values directly, so we create a new enum and swap.

-- Drop the default first (references the old enum)
ALTER TABLE profiles ALTER COLUMN tier DROP DEFAULT;

-- Create new enum
CREATE TYPE user_tier_new AS ENUM ('free', 'pro', 'elite');

-- Convert the column
ALTER TABLE profiles
  ALTER COLUMN tier TYPE user_tier_new
  USING CASE tier::text
    WHEN 'scout' THEN 'free'::user_tier_new
    WHEN 'analyst' THEN 'pro'::user_tier_new
    WHEN 'sharp' THEN 'elite'::user_tier_new
    WHEN 'syndicate' THEN 'elite'::user_tier_new
  END;

-- Drop old enum, rename new one
DROP TYPE user_tier;
ALTER TYPE user_tier_new RENAME TO user_tier;

-- Restore default
ALTER TABLE profiles ALTER COLUMN tier SET DEFAULT 'free'::user_tier;

-- Update the auth trigger function to use new default
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
        'free'::user_tier
    );
    RETURN new;
END;
$$;
