#!/usr/bin/env bash
# Local stopgap while Railway cs2_scanner cron is misfiring.
# Runs the ELO scanner every 30 min so bo3.gg odds (via the `coeff`
# field on bookmakers data) stay fresh in cs2_upcoming_matches —
# without this the bot can't compute edge and won't fire any picks.
#
# Run: nohup bash scripts/esports/run_cs2_scanner_loop.sh &
# Stop: pkill -f run_cs2_scanner_loop.sh
set -u
cd "$(dirname "$0")/../.."
mkdir -p dev/active
LOG=dev/active/cs2_scanner_loop.log
INTERVAL_SEC=1800   # 30 min — same cadence as the Railway cron should fire

echo "=== cs2_scanner_loop start  $(date -u +%FT%TZ)  interval=${INTERVAL_SEC}s ===" | tee -a "$LOG"
while true; do
  echo "" | tee -a "$LOG"
  echo "--- run start  $(date -u +%FT%TZ) ---" | tee -a "$LOG"
  python3 scripts/esports/cs2_elo_scanner.py --record 2>&1 | tail -50 | tee -a "$LOG"
  echo "--- v8 predict ---" | tee -a "$LOG"
  python3 scripts/esports/cs2_v8_predict.py --record 2>&1 | tail -10 | tee -a "$LOG"
  echo "--- bot pick scan ---" | tee -a "$LOG"
  python3 scripts/esports/cs2_bot.py --record 2>&1 | tail -10 | tee -a "$LOG"

  python3 -c "
from workers.api_clients.db import execute_query
r = execute_query(\"\"\"
  SELECT
    COUNT(*) FILTER (WHERE kickoff_time >= NOW()) AS upcoming,
    COUNT(*) FILTER (WHERE kickoff_time >= NOW() AND bookie_odds1 IS NOT NULL) AS with_bookie
  FROM cs2_upcoming_matches
\"\"\")[0]
print(f'  state: upcoming={r[\"upcoming\"]} with_bookie_odds={r[\"with_bookie\"]}')
" 2>/dev/null | tee -a "$LOG"

  echo "--- sleeping ${INTERVAL_SEC}s ---" | tee -a "$LOG"
  sleep "$INTERVAL_SEC"
done
