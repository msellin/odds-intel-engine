-- BOT-RETIREMENT-ON-CLV (2026-09-14). Retire five paper bots whose
-- margin-corrected OWN-BOOK CLV is decisively negative.
--
-- Evidence: scripts/bot_status_board.py. EV = (1+clv)/(1+m)-1 with m computed
-- PER ROW from the closing book's own market, on own-book rows only (rows
-- without closing_bookmaker came through the arbitrary-book fallback retired
-- today and read 4-10pp high).
--
--   bot_coolbet_trigger_ou_v1        n=371  EV -6.27%  CI [-6.51, -6.03]
--   bot_team_total_paper_shadow_v1   n=310  EV -4.98%  CI [-5.64, -4.32]
--   bot_1h_1x2_paper_shadow_v1       n=165  EV -6.25%  CI [-7.10, -5.40]
--   bot_trigger_ou_model_v1          n=164  EV -5.94%  CI [-6.42, -5.46]
--   bot_unibet_trigger_ou_v1         n=221  EV -5.61%  CI [-5.98, -5.24]
--
-- ON THE n THRESHOLD, stated openly rather than quietly relaxed. The
-- pre-registered PROMOTION rule is n>=300, and only the first two clear it.
-- The threshold is applied ASYMMETRICALLY here, deliberately:
--
--   * promotion needs power to detect a SMALL positive effect, so it needs n
--   * retirement of something sitting 5-6pp BELOW break-even with a CI whose
--     upper bound is still 5pp negative needs far less. There is no plausible
--     world where these turn positive; waiting for n=300 only spends days.
--
-- That asymmetry is a decision, not an oversight, and it does not license
-- relaxing n in the promotion direction — where it would manufacture winners.
--
-- NOTE THEY ARE ALL O/U OR BOLT-ON, AND FOUR OF FIVE ARE MODEL-ANCHORED. This
-- is the same separation the day's work found everywhere: model-anchored bots
-- are uniformly negative (-4.8% to -7.0%), sharp-anchored ones are mixed. The
-- surviving sharp bots are NOT retired here even where their point estimate is
-- negative, because their CIs still straddle or barely exclude zero and the
-- anchor comparison is the open question.
--
-- ALSO: this shrinks the /admin/shadow-bots "upcoming picks" table from 194
-- pending rows to ~60. That table's purpose is "one place to look before
-- placing real money", and 194 rows from bots we know lose is noise that hides
-- the few that matter.
--
-- NOT retired, but flagged: bot_corners_paper_shadow_v1 (45 pending) and
-- bot_ou35_model_v1 (17 pending) have ZERO own-book closes, so they have no
-- verdict at all — their ROI is unanchored. That is a DEFECT to fix, not
-- grounds to retire. Filed in PRIORITY_QUEUE.

UPDATE bots
   SET is_active = FALSE,
       retired_at = now(),
       maturity_label = 'retired'
 WHERE name IN (
   'bot_coolbet_trigger_ou_v1',
   'bot_team_total_paper_shadow_v1',
   'bot_1h_1x2_paper_shadow_v1',
   'bot_trigger_ou_model_v1',
   'bot_unibet_trigger_ou_v1'
 )
   AND retired_at IS NULL;
