-- PIN-5: Add Pinnacle-anchored CLV to simulated_bets
-- clv_pinnacle = (odds_at_pick / pinnacle_closing_odds) - 1
-- Pinnacle CLV is the industry standard metric for betting model validation:
-- consistently positive = finding edge before sharp money moves the line.
ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS clv_pinnacle FLOAT;
