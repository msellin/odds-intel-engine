-- JUNK-ARM-DEGENERATE-2026-09-14
--
-- The junk-anchor arm of the PICKS forward test is the negative control: the
-- same rule with the Shin-de-vigged Pinnacle anchor replaced by a shuffled one
-- from a different fixture. It exists so that a broken harness is detectable —
-- if the junk arm makes money, the live arm's number means nothing.
--
-- On day one it could not do that job. `junk_anchor_arm()` took the eight LIVE
-- picks, overwrote each one's `p_sharp` with a donor's, and recorded them. The
-- SELECTION never changed, and selection is the only thing the anchor does in
-- this rule. So all eight junk rows carry the same (match_id, market, selection,
-- odds, bookmaker) as a live row and are guaranteed to settle to an identical
-- outcome. A control that cannot disagree with the treatment is not a control.
--
-- Verified before writing this migration: all 8 junk rows published 2026-09-14
-- match a live row on (match_id, market, selection, odds, bookmaker).
--
-- The publisher is fixed (the junk arm now re-runs the whole rule — floor and
-- top-N — over a pool of shuffled anchors, and on the same day's data selects a
-- set with ZERO overlap with the live picks). These eight rows are NOT deleted:
-- removing rows from a pre-registered ledger is worse than annotating them. They
-- are re-stamped so the exclusion is mechanical rather than a doc footnote —
-- any control analysis filters on rule_version, which it must do anyway.
--
-- They still settle, and they still cost nothing: they are simply the live arm
-- measured twice.

UPDATE picks_forward_test
   SET rule_version = 'sharp_edge_v1_2026_09_14+DEGENERATE_JUNK_DAY1'
 WHERE arm = 'junk_anchor'
   AND rule_version = 'sharp_edge_v1_2026_09_14'
   AND published_at < '2026-09-15T00:00:00Z';

COMMENT ON COLUMN picks_forward_test.arm IS
  'live = published to @oddsintelpicks. junk_anchor = the negative control, '
  'recorded and settled but never published: the same rule with the Pinnacle '
  'anchor shuffled to a different fixture, so selection is driven by a number '
  'carrying no information. Expected to lose roughly the vig. NOTE: rows with '
  'rule_version ending +DEGENERATE_JUNK_DAY1 are the 2026-09-14 junk rows, '
  'which duplicate the live arm exactly (JUNK-ARM-DEGENERATE-2026-09-14) and '
  'must be excluded from any control analysis.';
