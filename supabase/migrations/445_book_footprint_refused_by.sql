-- 445 — book_footprint.refused_by: WHO refused a request, not just how many (#151, 2026-09-25)
--
-- #151: Coolbet refusals kept appearing in hours well under the 500 budget (09-25 15:00:
-- 1 refused at 39/500) with NO "footprint REFUSED Coolbet" line anywhere in the VPS
-- journal. The refusal log (#142) only helps when the refusing process's stdout lands in
-- that journal; an ad-hoc run from another host or shell does not. Each flush now adds
-- the distinct "host/proc/pid" of every process that refused into the hour's row
-- (workers/utils/footprint.py), so the next stray refusal names its source on the row itself.

ALTER TABLE book_footprint ADD COLUMN IF NOT EXISTS refused_by text[];
