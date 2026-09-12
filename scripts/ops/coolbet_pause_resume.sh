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
    done
    # PAUSE-WITHOUT-RESUME (fixed 2026-09-12). The header above has always
    # promised "the resume is a launchd job, not a note to a human" — and this
    # branch only ever unloaded. Nothing was ever armed, so every use of the
    # documented §7 lever created precisely the silent multi-day outage the
    # comment warns about. Found by running `status` straight after `pause`
    # and reading "resume agent: not armed".
    #
    # One-shot agent: it fires once after MINUTES, calls our own `resume`, and
    # `resume` deletes it (self-destruct is already implemented below). Written
    # with RunAtLoad false so arming it does not resume immediately.
    MINUTES="${2:-90}"
    SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    /bin/cat > "$LA/com.oddsintel.coolbet-resume.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.oddsintel.coolbet-resume</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$SELF</string><string>resume</string>
  </array>
  <key>StartInterval</key><integer>$((MINUTES * 60))</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$(cd "$(dirname "$0")/../.." && pwd)/dev/active/coolbet-resume.log</string>
  <key>StandardErrorPath</key><string>$(cd "$(dirname "$0")/../.." && pwd)/dev/active/coolbet-resume.log</string>
</dict></plist>
PLIST
    launchctl unload "$LA/com.oddsintel.coolbet-resume.plist" 2>/dev/null || true
    if launchctl load "$LA/com.oddsintel.coolbet-resume.plist" 2>/dev/null; then
      echo "resume agent ARMED — Coolbet jobs reload in ${MINUTES}min"
    else
      # Fail LOUD. A pause whose resume did not arm is the outage this fixes.
      echo "!! RESUME AGENT FAILED TO ARM — re-run '$0 resume' BY HAND, or the"
      echo "!! Coolbet feed stays down indefinitely."
      exit 1
    fi ;;
  resume)
    for j in "${JOBS[@]}"; do
      launchctl load "$LA/$j.plist" 2>/dev/null && echo "loaded $j" || echo "already loaded $j"
    done
    # Self-destruct the one-shot resume agent. ORDER IS LOAD-BEARING, and the
    # first version got it wrong (2026-09-12): it called `launchctl unload` on
    # the agent that was RUNNING THIS SCRIPT, so launchd killed the process
    # group mid-branch and `rm` never executed. The plist survived, the agent
    # stayed armed, and it re-fired every 90 minutes — the log shows the two
    # "loaded" lines repeated with no "resume agent removed" after either.
    #
    # So: delete the file FIRST (nothing can re-load it), then remove the agent
    # BY LABEL as the very last statement — `launchctl remove` needs no plist
    # on disk, and if it kills us here everything else has already happened.
    rm -f "$LA/com.oddsintel.coolbet-resume.plist"
    echo "resume agent removed"
    launchctl remove com.oddsintel.coolbet-resume 2>/dev/null || true ;;
  status)
    launchctl list | grep -i "oddsintel.coolbet" || echo "  (no coolbet agents loaded)"
    [ -f "$LA/com.oddsintel.coolbet-resume.plist" ] && echo "  resume agent: ARMED" || echo "  resume agent: not armed" ;;
  *) echo "usage: $0 {pause|resume|status}"; exit 2 ;;
esac
