#!/bin/bash
# COOLBET-DAEMON-KEEPALIVE (2026-09-10, COOLBET-DAEMON-DEATH-RECURRING) — auto-revive
# the Coolbet mac-daemon. The daemon is launchd KeepAlive/RunAtLoad, but launchd does
# NOT reliably respawn it after the Mac sleeps (observed silent ~35h), and
# `launchctl load -w` does NOT start it — only `launchctl kickstart` does. StartInterval
# watchdog jobs DO fire on wake, so this checks the process every few minutes and
# kickstarts it if it died. Cheap when healthy (one pgrep, exit).
#
# It respects a DELIBERATE stop: if the operator `launchctl unload`'d the daemon (job
# not loaded), the watchdog does NOT revive it — it only kickstarts a LOADED-but-dead
# job. So `unload -w` still means "stay stopped".
#
# launchd gives a minimal PATH, so binaries are called by absolute path.
set -uo pipefail

LABEL="com.oddsintel.coolbet-mac-daemon"
UID_="$(/usr/bin/id -u)"
LOG_TS() { /bin/date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# healthy — the common path, silent
if /usr/bin/pgrep -f coolbet_mac_daemon >/dev/null 2>&1; then
  exit 0
fi

# process is down. Only revive if the job is LOADED (i.e. not deliberately unloaded).
if ! /bin/launchctl list 2>/dev/null | /usr/bin/grep -q "$LABEL"; then
  echo "$(LOG_TS) mac-daemon down AND $LABEL not loaded — deliberate stop, leaving it."
  exit 0
fi

echo "$(LOG_TS) coolbet mac-daemon process is DOWN but loaded — kickstarting $LABEL"
/bin/launchctl kickstart -k "gui/$UID_/$LABEL" 2>&1 || true
/bin/sleep 4
if /usr/bin/pgrep -f coolbet_mac_daemon >/dev/null 2>&1; then
  echo "$(LOG_TS)   revived ✓"
else
  echo "$(LOG_TS)   kickstart did NOT bring it up — needs a manual check (session/login?)"
fi
