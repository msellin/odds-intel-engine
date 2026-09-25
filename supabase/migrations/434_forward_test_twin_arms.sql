-- 434 — two RECORDED, NEVER-PUBLISHED twin arms on the forward-test ledger ([[#161]], 2026-09-25, owner-approved)
--
-- WHY. The [[#156]] audit pointed at one fix per published arm, but both arms are
-- pre-registered, so neither may be changed mid-test. Each fix therefore runs as a
-- TWIN: the parent's rule in every gate plus ONE gate, under its own `arm` and
-- `rule_version`, recorded beside the parent and never sent — like the junk control.
-- Pre-registered before their first pick in dev/active/picks-forward-test-preregistration.md
-- ("TWIN ARMS — 2026-09-25"); scored by scripts/picks_forward_test_checkpoint.py --twins.
--
--   sharp_own_book_aligned   live v4 + at Coolbet / Unibet-Site / Epicbet / Tonybet the
--                            book's quote and the Pinnacle anchor quote <= 5 min apart
--   consensus_pin_confirmed  consensus v2 + where a fresh tight Pinnacle exists, EV >= 0
--                            against it
--
-- WHAT THIS DOES
--   1. Widens picks_forward_test_arm_check EXPLICITLY (as migration 369 did) — the
--      constraint is what stops a typo'd arm quietly creating a population.
--   2. Adds `twin_gate jsonb` — the per-row evidence for the extra gate
--      ({own_book, gap_min} / {pin_tight, pin_p, pin_ev}). NULL on every other arm. The
--      publisher writes it in a separate UPDATE for twin rows only, so the published arms'
--      INSERT is unchanged and cannot break if code deploys before this migration.
--   3. Registers the two bots as EXPERIMENTAL (#155: admins only), show_on_picks = false,
--      show_on_performance = false. Nothing public reads these arms: every public view
--      (picks_public_all, picks_forward_test_public / _summary / _by_market, the #156/#158
--      record views) filters an explicit allow-list ('live', 'consensus_anchor'), so a new
--      arm is private by default. Settlement and leg_clv_sharp are arm-agnostic.
ALTER TABLE picks_forward_test DROP CONSTRAINT picks_forward_test_arm_check;
ALTER TABLE picks_forward_test ADD CONSTRAINT picks_forward_test_arm_check
    CHECK (arm = ANY (ARRAY['live'::text, 'junk_anchor'::text, 'consensus_anchor'::text,
                            'sharp_own_book_aligned'::text, 'consensus_pin_confirmed'::text]));

ALTER TABLE picks_forward_test ADD COLUMN IF NOT EXISTS twin_gate jsonb;
COMMENT ON COLUMN picks_forward_test.twin_gate IS
  '#161: evidence for a twin arm''s one extra gate (sharp_own_book_aligned: own_book, gap_min; consensus_pin_confirmed: pin_tight, pin_p, pin_ev). NULL on live / consensus_anchor / junk_anchor.';

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll, show_on_picks,
    show_on_performance, display_name
) VALUES
(
    'bot_sharp_aligned_v1',
    'Twin of the sharp picks ([[#161]]) — the pre-registered v4 sharp rule plus one gate: at our own books (Coolbet, Unibet-Site, Epicbet, Tonybet) the price and the Pinnacle anchor must be quoted within 5 minutes of each other. Recorded, never published.',
    'sharp_forward_test',
    'picks_forward_test WHERE arm=''sharp_own_book_aligned''. Readout vs arm=''live'' and the junk control at n=50/100 (preregistration doc, TWIN ARMS). Never sent, never staked.',
    TRUE, 'experimental', 1.00, 1.00, FALSE, FALSE,
    'Sharp-line twin — own-book quotes aligned'
),
(
    'bot_consensus_pinconf_v1',
    'Twin of the consensus picks ([[#161]]) — consensus v2 plus one gate: where a fresh tight Pinnacle line exists, the pick must also be value (EV >= 0) against it. Recorded, never published.',
    'consensus_anchor',
    'picks_forward_test WHERE arm=''consensus_pin_confirmed''. Readout vs arm=''consensus_anchor'' and the junk control at n=50/100 (preregistration doc, TWIN ARMS). Never sent, never staked.',
    TRUE, 'experimental', 1.00, 1.00, FALSE, FALSE,
    'Consensus twin — Pinnacle-confirmed'
)
ON CONFLICT (name) DO NOTHING;
