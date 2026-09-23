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
      # bootout is the counterpart of bootstrap; `unload` is the same legacy verb
      # that broke resume. Fall back to unload so an old launchd still works.
      if launchctl bootout "gui/$(id -u)/$j" 2>/dev/null || launchctl unload "$LA/$j.plist" 2>/dev/null; then
        echo "unloaded $j"
      else
        echo "already unloaded $j"
      fi
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
    # ARMING USES BOOTSTRAP TOO — and this is the worst place to use the broken
    # verb. If the resume AGENT fails to arm, there is no resume at all: the jobs
    # stay unloaded and nothing ever retries. That is the exact silent multi-day
    # outage this script's header promises to prevent. Same reasoning as the
    # resume branch below (RESUME-LOAD-VERB-IS-THE-BUG, 2026-09-23).
    launchctl bootout "gui/$(id -u)/com.oddsintel.coolbet-resume" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$LA/com.oddsintel.coolbet-resume.plist" 2>/dev/null || true
    # Verify against launchd rather than trusting the exit code — the same
    # lesson the resume branch learned on 2026-09-20.
    if launchctl list 2>/dev/null | grep -q "	com.oddsintel.coolbet-resume\$"; then
      echo "resume agent ARMED — Coolbet jobs reload in ${MINUTES}min (verified in launchctl list)"
    else
      # Fail LOUD. A pause whose resume did not arm is the outage this fixes.
      echo "!! RESUME AGENT FAILED TO ARM — re-run '$0 resume' BY HAND, or the"
      echo "!! Coolbet feed stays down indefinitely."
      exit 1
    fi ;;
  resume)
    # VERIFY, DO NOT TRUST THE EXIT CODE (2026-09-20, RESUME-LOADED-BUT-NOT-RUNNING).
    # `launchctl load` returned 0 and this branch logged "loaded" for BOTH jobs,
    # TWICE, while `launchctl list` showed neither — the Mac was asleep across the
    # fire time and the registration never took. The feed stayed down 14h after a
    # 90-minute pause, which is precisely the silent outage this script's header
    # promises to prevent, reached by a different route than the 2026-09-12 bug.
    #
    # An exit code says "the command ran", not "the job is scheduled". The only
    # honest check is to ask launchd what it is actually running, so every load is
    # now confirmed against `launchctl list` and a failure is LOUD. Retried once —
    # a wake-race deserves a second attempt, a genuine failure must not loop.
    failed=""
    for j in "${JOBS[@]}"; do
      for attempt in 1 2; do
        # BOOTSTRAP, NOT LOAD (2026-09-23, RESUME-LOAD-VERB-IS-THE-BUG).
        #
        # Third failure of this branch in four days: 14h on 09-20, 4.3h on 09-22,
        # ~4h overnight into 09-23. Each time it logged "loaded" and the job was
        # not in `launchctl list` afterwards. The verification added on 09-20 was
        # correct and did not help, because the problem is not the checking — it
        # is `launchctl load`, the legacy verb, which names no domain and fails
        # silently when the GUI session is not in the state it assumes (notably
        # on a wake from sleep, which is exactly when this agent fires).
        #
        # `bootstrap gui/<uid>` names the domain explicitly. It has worked on
        # every occasion `load` did not this week, including all three manual
        # recoveries. It errors loudly when a job is ALREADY loaded, which is
        # harmless here — the verification below is what decides success.
        launchctl bootstrap "gui/$(id -u)" "$LA/$j.plist" 2>/dev/null || true
        if launchctl list 2>/dev/null | grep -q "	$j\$"; then
          echo "loaded $j (verified in launchctl list)"
          break
        fi
        [ "$attempt" = 1 ] && { echo "load of $j did not register — retrying once"; sleep 2; }
      done
      launchctl list 2>/dev/null | grep -q "	$j\$" || { failed="$failed $j"; }
    done
    if [ -n "$failed" ]; then
      # Do NOT self-destruct: the agent re-firing every 90 min is the only thing
      # that will retry after a wake, and a disarmed agent plus unloaded jobs is
      # the worst of both. Leave it armed and say so.
      echo "!! RESUME INCOMPLETE — not registered in launchd:$failed"
      echo "!! The resume agent is LEFT ARMED so it retries. Fix by hand with:"
      echo "!!   launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.oddsintel.coolbet-odds-snapshot.plist"
      echo "!!   (bootstrap, NOT load — load is the verb that caused this)"
      echo "!! Verify with: $0 status  (jobs must appear in the launchctl list above)"
      exit 1
    fi
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
    [ -f "$LA/com.oddsintel.coolbet-resume.plist" ] && echo "  resume agent: ARMED" || echo "  resume agent: not armed"
    # ARMED alone told us nothing on 2026-09-20: the agent was armed, had fired
    # twice, and the jobs were still down. State the thing that actually matters.
    for j in "${JOBS[@]}"; do
      launchctl list 2>/dev/null | grep -q "	$j\$" \
        && echo "  $j: RUNNING" \
        || echo "  $j: ⛔ NOT LOADED — the feed is down until this is fixed"
    done ;;
  *) echo "usage: $0 {pause|resume|status}"; exit 2 ;;
esac
