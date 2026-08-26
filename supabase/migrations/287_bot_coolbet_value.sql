-- COOLBET-VALUE-BOT-2026-08-26
--
-- A shadow bot that bets at the ONLY price the operator can actually take.
--
-- Why this exists. Every existing line-shop bot gates on the best price across
-- six accessible books. The operator places at Coolbet. Measured on the live
-- pick list 2026-08-26, that gap is fatal:
--
--     bot                  picks  edge shown  edge AT COOLBET  still >= 3%
--     bot_sweep_ou25_v1       38      +7.0%          -7.0%          0
--     bot_sweep_ou35_v1       19      +7.5%          -5.2%          0
--     bot_pin_1x2_home_v1      1      +6.3%          +6.3%          1
--
-- 57 of 58 picks were negative-EV at the venue they would be placed at. Not
-- because Coolbet is uncompetitive — it is the best price 38.1% of the time,
-- more often than any other book in the set, and beats Pinnacle's raw 1X2 quote
-- 61.8% of the time. The bots simply surface the 62% of cases where somebody
-- else led, and hand the operator a number he cannot have.
--
-- Taking the max across books also has a subtle poison: it selects for whichever
-- book is most WRONG, and book_bias_probe showed the books that win that auction
-- most often are the worst calibrated. Gating on one book removes the selection
-- effect entirely — the edge measured is the edge received.
--
-- Fair value still comes from the DE-VIGGED PINNACLE close (Shin), not from
-- Coolbet. Pricing Coolbet against Coolbet is circular: a book can never look
-- mispriced against itself. Coolbet decides WHAT WE PAY; Pinnacle decides WHAT
-- IT IS WORTH. Only the first of those has to be reachable.
--
-- Expect single-digit picks per day against the ~58 currently listed. That is
-- the point: at the CLV instrument's precision (~100 picks to decide) this needs
-- about three weeks to judge, and five placeable picks beat 58 unplaceable ones.
--
-- Ships as `experimental`. Judged by the CLV gate like everything else, and it
-- can be judged honestly because for once the price in the row is the price the
-- operator would have got.

INSERT INTO bots (name, strategy, description, strategy_description,
                  starting_bankroll, current_bankroll, is_active, maturity_label)
VALUES (
    'bot_coolbet_value_v1',
    'coolbet_value',
    'Coolbet-priced value — bets Coolbet''s own quote when it beats de-vigged Pinnacle by 3%+',
    'Line-shopping with the shop fixed. For each (match, market, selection) takes '
    'COOLBET''s price (the only obtainable one), values it against the Shin-de-vigged '
    'Pinnacle close, and fires at >= 3% true edge. Tiers 1-2. Soft-price outlier guard '
    'vs Pinnacle retained. Unlike the sweep/pin bots it never quotes a price the '
    'operator cannot take: measured 2026-08-26, 57 of their 58 live picks were '
    'negative-EV at Coolbet despite showing +7% on the page.',
    1.00, 1.00, TRUE, 'experimental'
)
ON CONFLICT (name) DO NOTHING;

COMMENT ON TABLE bots IS
  'Bot roster. retired_at IS NULL = live. Retired bots keep writing shadow_bets '
  '(SHADOW-RETIRED-OK 2026-05-20) so re-enable decisions stay evidence-based. '
  'As of 2026-08-26 the graduation gate is a CLV t-statistic, not raw ROI — see '
  'CLV-FIRST-DEV-LOOP and docs/ANALYSIS_GOTCHAS.md §8. Note bot_coolbet_value_v1 '
  'is the only bot whose quoted price is guaranteed obtainable by the operator; '
  'every other line-shop bot quotes the best of six books.';
