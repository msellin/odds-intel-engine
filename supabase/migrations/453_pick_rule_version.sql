-- 453 — #162 owner decision (b), 2026-09-25: every pick carries the RULE VERSION it was made under.
--
-- WHY. The owner chose "no twins": a live bot's rule is changed directly, and each pick is tagged
-- with the version of the rule that made it — one ledger per bot, split by tag when a before/after
-- comparison is needed (instead of a second bot whose record starts from zero). Until now a pick
-- carried only `model_version` (the prediction bundle), so a change to a bot's GATES (floor, filter,
-- one-per-match rule) left no mark on its rows and the before/after split was unrecoverable.
--
-- HOW. The version lives on the bot (`bots.rule_version`); the code registry
-- (workers/registry/bot_registry.py `BotSpec.rule_version`) is the source of truth and the scheduler
-- writes it to `bots` at start-up, i.e. at the deploy that ships the rule change. A BEFORE INSERT
-- trigger copies the bot's current version onto every new simulated_bets / shadow_bets row that does
-- not set one — so all ~15 writers are covered without each having to remember. Historical rows stay
-- NULL = "made before tagging began (2026-09-25)"; nothing is guessed backwards.

SET lock_timeout = '3s';

ALTER TABLE bots ADD COLUMN IF NOT EXISTS rule_version text NOT NULL DEFAULT 'r1';
ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS rule_version text;
ALTER TABLE shadow_bets ADD COLUMN IF NOT EXISTS rule_version text;

CREATE OR REPLACE FUNCTION public.stamp_pick_rule_version()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    IF NEW.rule_version IS NULL AND NEW.bot_id IS NOT NULL THEN
        SELECT b.rule_version INTO NEW.rule_version FROM bots b WHERE b.id = NEW.bot_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS simulated_bets_rule_version ON simulated_bets;
CREATE TRIGGER simulated_bets_rule_version BEFORE INSERT ON simulated_bets
    FOR EACH ROW EXECUTE FUNCTION public.stamp_pick_rule_version();

DROP TRIGGER IF EXISTS shadow_bets_rule_version ON shadow_bets;
CREATE TRIGGER shadow_bets_rule_version BEFORE INSERT ON shadow_bets
    FOR EACH ROW EXECUTE FUNCTION public.stamp_pick_rule_version();
