-- 397 — Ops dashboard: SHARP coverage instead of Pinnacle-only ([[#119]], 2026-09-24)
-- A fixture has a sharp anchor when Pinnacle prices it OR the Betfair Exchange market
-- is LIQUID (all 3 match-odds runners within 5% spread, >= EUR 1k matched, one capture).
-- matches_with_pinnacle / matches_without_pinnacle stay for history.
ALTER TABLE ops_snapshots ADD COLUMN IF NOT EXISTS matches_with_exchange_liquid integer;
ALTER TABLE ops_snapshots ADD COLUMN IF NOT EXISTS matches_with_sharp integer;
ALTER TABLE ops_snapshots ADD COLUMN IF NOT EXISTS matches_without_sharp integer;
