-- 369 — allow the consensus arm on the pre-registered ledger ([[#068]], 2026-09-22)
--
-- `picks_forward_test_arm_check` allowed exactly ARRAY['live', 'junk_anchor'].
-- The consensus arm's first real run hit it and refused to insert — correctly.
-- The constraint is the reason a typo'd arm name cannot quietly create a third
-- population in a pre-registered ledger, so it is WIDENED explicitly here rather
-- than dropped.
--
-- Worth recording that this fired at the right moment: the failure happened
-- inside `claim()`, which runs BEFORE the Telegram send (PUBLISH-CLAIM-BEFORE-SEND).
-- So the run aborted with nothing published and no half-state — the ledger and
-- the channel stayed in agreement, which is the entire point of claiming first.
ALTER TABLE picks_forward_test DROP CONSTRAINT picks_forward_test_arm_check;
ALTER TABLE picks_forward_test ADD CONSTRAINT picks_forward_test_arm_check
    CHECK (arm = ANY (ARRAY['live'::text, 'junk_anchor'::text,
                            'consensus_anchor'::text]));
