#!/usr/bin/env bash
# Keep the match-details processor running while the walker fills the queue.
# Exits cleanly when both walker is gone AND queue is empty for 5 consecutive checks.
set -u
cd "$(dirname "$0")/../.."
mkdir -p dev/active
LOG=dev/active/match_details_local.log
echo "=== orchestrator start  $(date -u +%FT%TZ) ===" | tee -a "$LOG"

empty_streak=0
while true; do
  # Run processor — exits when queue empties
  FLARESOLVERR_URL=https://flaresolverr-cf-production.up.railway.app \
    python3 scripts/esports/cs2_hltv_match_details.py --process 5000 2>&1 | tee -a "$LOG"

  # Check pending count
  pending=$(python3 -c "
from workers.api_clients.db import execute_query
r = execute_query(\"SELECT COUNT(*) c FROM cs2_hltv_match_queue WHERE fetched_at IS NULL AND error IS NULL\")[0]['c']
print(r)
" 2>/dev/null | tail -1)

  walker_alive=$(pgrep -f "cs2_hltv_match_details.py --queue" | wc -l | tr -d ' ')

  echo "  [orchestrator] pending=$pending  walker_alive=$walker_alive  empty_streak=$empty_streak" | tee -a "$LOG"

  if [[ "$pending" == "0" ]]; then
    if [[ "$walker_alive" == "0" ]]; then
      empty_streak=$((empty_streak + 1))
      if [[ $empty_streak -ge 3 ]]; then
        echo "  [orchestrator] queue empty + walker gone — exiting" | tee -a "$LOG"
        break
      fi
    fi
    sleep 30
  else
    empty_streak=0
    sleep 5
  fi
done

echo "=== orchestrator end  $(date -u +%FT%TZ) ===" | tee -a "$LOG"
