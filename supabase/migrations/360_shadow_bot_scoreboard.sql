-- 360_shadow_bot_scoreboard.sql
-- OWN Phase 6 correction (2026-09-15) — found by the decision-surface review.
--
-- WHY. `/admin/shadow-bots`' scoreboard computed BOTH its "n settled" and its
-- ROI from `shadow_bets_own_book_clv`, which by construction holds only settled
-- picks whose recommended book had a COMPLETE closing market. That subset is
-- not a random sample of a bot's record, so the ROI it produces is not the
-- bot's ROI — measured on the live data the review flipped the SIGN on four of
-- eleven bots:
--
--     bot_ou35_model_v1            true -11.3 pct over n=226  ->  shown +30.0 pct over n=23
--     bot_high_roi_global_v2       true +13.3 pct over n=35   ->  shown -37.7 pct over n=11
--     bot_coolbet_1x2_model_v1     true  +5.2 pct over n=13   ->  shown -25.6 pct over n=9
--     bot_coolbet_trigger_sharp_ou true +14.9 pct over n=33   ->  shown +37.2 pct over n=21
--
-- A paper bot whose real record is -11 pct rendering as +30 pct, on the one
-- screen the operator uses to decide what to stake by hand, is the exact class
-- of defect this project keeps paying for (ANALYSIS_GOTCHAS s55: evaluate at the
-- executable price, and never let the subset that HAPPENS to have an anchor
-- stand in for the population).
--
-- WHAT. One row per non-retired bot, with the two populations kept SEPARATE and
-- each labelled by its own n, so a reader cannot mistake one for the other:
--   * settled_*  -> EVERY settled pick (the honest ROI denominator)
--   * clv_*      -> only picks with an own-book close (the CLV/verdict
--                   population; the pre-registered decision variable)
-- The page reads this instead of aggregating rows in the browser, which also
-- removes a full-ledger fetch from every page load.
--
-- ROI uses the executable price (`odds_at_pick_live` when present, else
-- `odds_at_pick`) at a flat EUR 10, matching FLAT-ROI-EVERYWHERE and the
-- placer's own stake. `pnl` is NOT summed directly: it was written at a
-- best-of-books snapshot for the general bots (SHADOW-PAGE-ROI-INFLATED).
--
-- Re-appliable.

CREATE OR REPLACE VIEW shadow_bot_scoreboard AS
WITH s AS (
    SELECT u.bot_id,
           u.bot_name,
           u.result,
           COALESCE(u.odds_at_pick_live, u.odds_at_pick)::numeric AS exec_odds,
           u.clv_margin_corrected,
           u.closing_bookmaker,
           u.decision_quote_age_min
      FROM shadow_bets_unique u
     WHERE u.bot_retired_at IS NULL
       AND u.result IN ('won', 'lost')
)
SELECT
    bot_id,
    bot_name,
    -- EVERY settled pick — the honest ROI population
    count(*)                                                        AS settled_n,
    count(*) FILTER (WHERE result = 'won')                          AS settled_won,
    round(sum(CASE WHEN result = 'won' THEN (exec_odds - 1) * 10 ELSE -10 END), 2) AS settled_pnl_eur,
    round(
        sum(CASE WHEN result = 'won' THEN (exec_odds - 1) * 10 ELSE -10 END)
        / NULLIF(count(*) * 10.0, 0), 5)                            AS settled_roi,
    -- own-book-closed subset — the CLV / verdict population
    count(clv_margin_corrected)                                     AS clv_n,
    round(avg(clv_margin_corrected), 5)                             AS clv_mc_mean,
    round(stddev_samp(clv_margin_corrected), 5)                     AS clv_mc_sd,
    count(*) FILTER (WHERE decision_quote_age_min IS NOT NULL
                       AND decision_quote_age_min <= 60)            AS decision_fresh_n,
    count(decision_quote_age_min)                                   AS decision_age_known_n
  FROM s
 GROUP BY bot_id, bot_name;

COMMENT ON VIEW shadow_bot_scoreboard IS
    'Per-bot scoreboard for /admin/shadow-bots. TWO populations, never mixed: settled_* covers '
    'EVERY settled pick (the honest ROI denominator, executable price, flat EUR 10), clv_* covers '
    'only picks with an own-book close (the pre-registered decision variable). Computing ROI over '
    'the clv_ subset flipped the sign on 4 of 11 bots — see migration 360. OWN Phase 6, 2026-09-15.';
