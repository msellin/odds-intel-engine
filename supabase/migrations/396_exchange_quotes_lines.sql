-- 396 — exchange_quotes: handicap / goal lines ([[#119]] step D, 2026-09-24)
--
-- The exchange prices Asian Handicap and Asian goal lines on EVERY line, quarter lines
-- included (e.g. Norway v Denmark: AH €76k matched, goal lines €6k). Readers can bet
-- them even where we cannot from Estonia, so they are a PICKS market with a sharp fair
-- price. Same convention as odds_snapshots: asian_handicap rows carry the HOME line on
-- both the home and away row; goal_line rows carry the total.
ALTER TABLE exchange_quotes ADD COLUMN IF NOT EXISTS handicap_line numeric;
