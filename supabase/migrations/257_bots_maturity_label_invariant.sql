-- BOT-MATURITY-LABEL-INVARIANT (2026-06-21)
--
-- Memory queue task #1. Reconcile maturity_label with is_active.
--
-- Origin: 2026-06-17 audit found 28 bots with is_active=false but
-- maturity_label != 'retired' (stale labels include 'calibrated',
-- 'active', 'beta', 'testing', 'experimental'). Every weekly_bot_review,
-- per-bot audit, and admin surface that displays maturity_label has
-- to special-case "this label is meaningless because the bot is inactive"
-- — and at least one prior bug (the 2026-06-13 bot_high_alignment
-- incident) traced back to maturity_label being checked without an
-- accompanying is_active check.
--
-- Two pieces:
--   1. One-time UPDATE — flip the 28 stale labels to 'retired'. Existing
--      retired_at + retired_reason are preserved (they're the audit trail);
--      this only fixes the label column that's the documented "current
--      state" surface.
--   2. Trigger — whenever is_active flips to false in the future,
--      auto-set maturity_label='retired'. Keeps the invariant honest
--      without forcing every code path that retires a bot to remember.
--
-- The trigger does NOT fire the other way (is_active=true does NOT auto-
-- reset maturity_label) because re-activation is an explicit operator
-- choice with deliberate label semantics (e.g. bot_btts_all reactivated
-- 2026-06-21 as 'beta', not whatever it was before retirement).

-- Step 1: one-time reconcile.
UPDATE bots
   SET maturity_label = 'retired',
       updated_at = NOW()
 WHERE is_active = FALSE
   AND COALESCE(maturity_label, '') <> 'retired';

-- Step 2: trigger to maintain the invariant going forward.
CREATE OR REPLACE FUNCTION bots_set_retired_label() RETURNS TRIGGER AS $$
BEGIN
    -- Only fire when is_active transitions to false (or starts at false).
    -- Skip the no-op case (is_active was already false and stays false).
    IF NEW.is_active = FALSE
       AND (TG_OP = 'INSERT' OR OLD.is_active IS DISTINCT FROM NEW.is_active)
    THEN
        NEW.maturity_label := 'retired';
        IF NEW.retired_at IS NULL THEN
            NEW.retired_at := NOW();
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bots_maturity_retired_invariant ON bots;
CREATE TRIGGER bots_maturity_retired_invariant
    BEFORE INSERT OR UPDATE OF is_active ON bots
    FOR EACH ROW
    EXECUTE FUNCTION bots_set_retired_label();

COMMENT ON FUNCTION bots_set_retired_label() IS
    'Maintains the invariant: is_active=false implies maturity_label=retired.
     Reactivation (false → true) is deliberately NOT mirrored — the operator
     picks the new maturity label (beta / calibrated / etc.) explicitly.';
