#!/bin/bash
# COOLBET-FOOTPRINT-PAUSE-2026-09-07
# Unload the two Coolbet jobs that fire into the Imperva wall, and schedule an
# automatic reload. Pausing without a guaranteed resume is how a "temporary"
# stop becomes a silent multi-day outage, so the resume is a launchd job, not a
# note to a human.
set -euo pipefail
LA="$HOME/Library/LaunchAgents"
JOBS=(com.oddsintel.coolbet-odds-snapshot com.oddsintel.coolbet-feed-watchdog)

case "${1:-}" in
  pause)
    for j in "${JOBS[@]}"; do
      launchctl unload "$LA/$j.plist" 2>/dev/null && echo "unloaded $j" || echo "already unloaded $j"
    done ;;
  resume)
    for j in "${JOBS[@]}"; do
      launchctl load "$LA/$j.plist" 2>/dev/null && echo "loaded $j" || echo "already loaded $j"
    done
    # self-destruct the one-shot resume agent
    launchctl unload "$LA/com.oddsintel.coolbet-resume.plist" 2>/dev/null || true
    rm -f "$LA/com.oddsintel.coolbet-resume.plist"
    echo "resume agent removed" ;;
  status)
    launchctl list | grep -i "oddsintel.coolbet" || echo "  (no coolbet agents loaded)"
    [ -f "$LA/com.oddsintel.coolbet-resume.plist" ] && echo "  resume agent: ARMED" || echo "  resume agent: not armed" ;;
  *) echo "usage: $0 {pause|resume|status}"; exit 2 ;;
esac
