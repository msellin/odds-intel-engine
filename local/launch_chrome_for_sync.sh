#!/bin/bash
# COOLBET-MAC-DAEMON CDP launcher (2026-06-12, v2 — separate-profile fix)
#
# Newer Chrome (>= ~120) silently disables --remote-debugging-port when
# the profile dir matches the default user profile — anti-session-theft
# safety. Workaround: one-time copy of your default profile to a
# dedicated dir, then ALWAYS launch with --user-data-dir pointing at the
# copy. The copy carries your cookies + localStorage + Imperva trust —
# enough state for Coolbet to recognise you without re-login.
#
# After the one-time copy, the CDP-Chrome runs ALONGSIDE your normal
# Chrome. Two separate Chromes, two profiles. The CDP one is a "view"
# of your account at the time of the copy, and stays warm as long as
# you don't log out of it.
#
# Run:
#   ./local/launch_chrome_for_sync.sh
# Then verify the daemon can talk to it:
#   PYTHONPATH=. python3 -m workers.automation.coolbet_browser_sync --cdp-fetch

set -e

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_PROFILE="$HOME/Library/Application Support/Google/Chrome"
CDP_PROFILE="$HOME/Library/Application Support/Google/Chrome-CDP-OddsIntel"
PORT=9222

if [ ! -x "$CHROME" ]; then
    echo "✗ Chrome not found at $CHROME"
    exit 1
fi

# One-time copy of the default profile so the CDP instance has cookies +
# localStorage + Imperva trust state. macOS keychain entry for "Chrome
# Safe Storage" is shared across paths, so encrypted cookies decrypt
# normally in the copy.
if [ ! -d "$CDP_PROFILE/Default" ]; then
    echo "✓ First run — copying your Chrome profile to $CDP_PROFILE"
    echo "  (one-time; ~30s; the copy carries your existing Coolbet session)"
    mkdir -p "$CDP_PROFILE"
    # rsync the essential subdirs only. Skip caches/history that are huge
    # and irrelevant for session state. --no-perms because the target
    # owns the files differently than the source-as-running-Chrome.
    rsync -a --no-perms --no-owner \
        --exclude='Cache' --exclude='Code Cache' --exclude='GPUCache' \
        --exclude='Media Cache' --exclude='Service Worker/CacheStorage' \
        --exclude='ShaderCache' --exclude='Storage/ext' \
        --exclude='Crashpad' \
        "$DEFAULT_PROFILE/Default/" "$CDP_PROFILE/Default/"
    # Local State (top-level) holds the encryption key handle — must be copied.
    cp -p "$DEFAULT_PROFILE/Local State" "$CDP_PROFILE/" 2>/dev/null || true
    echo "✓ Profile copied"
fi

# Kill any previous CDP-Chrome (NOT your default Chrome — different
# profile dir, different process group).
if pgrep -lf "Chrome-CDP-OddsIntel" >/dev/null; then
    echo "⚠ Previous CDP-Chrome running — quitting it"
    pkill -f "Chrome-CDP-OddsIntel" || true
    sleep 2
fi

echo "✓ Launching CDP-Chrome on port $PORT (separate from your normal Chrome)"
# --profile-directory="Default" hard-pins to the seeded profile so Chrome
# never boots into chrome://profile-picker/ (which CDP cannot DOM-drive,
# stalling auto_self_heal at the chrome_at_profile_picker bailout).
#
# COOLBET-SESSION-FREEZE-FIX (2026-09-10, COOLBET-DAEMON-DEATH-RECURRING): the
# three --disable-*background* flags are the ROOT-CAUSE fix for the recurring
# logged-out session. This is an automation window the operator never looks at,
# so it is permanently occluded; without these flags Chrome backgrounds and
# then FREEZES the hidden renderer after ~5 min, which suspends Coolbet's SPA
# renew-token timer (~20-min cadence). The ~30-min JWT then lapses and the
# frontend clears `cbauth` in localStorage IN PLACE (no redirect to /login —
# which is why the operator saw the STAY-COOL page with the token simply gone).
# Diagnosed 2026-09-10: cbauth flapped on a ~30-min cycle, reappearing only when
# the daemon's CDP read woke the frozen renderer. These flags keep the tab's
# JS running at full speed so renew-token fires on schedule and the session
# stays alive on its own. NB: takes effect only on the NEXT launch — after
# adding them you must quit this CDP-Chrome, re-run this script, and log in once.
"$CHROME" \
    --remote-debugging-port=$PORT \
    --user-data-dir="$CDP_PROFILE" \
    --profile-directory="Default" \
    --no-first-run \
    --no-default-browser-check \
    --disable-background-timer-throttling \
    --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    >/dev/null 2>&1 &

# Poll the CDP endpoint until it accepts connections.
for i in $(seq 1 30); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:$PORT/json/version', timeout=2)" 2>/dev/null; then
        echo "✓ CDP ready on http://localhost:$PORT"
        echo
        echo "  A second Chrome window opened — that's the CDP instance."
        echo "  It's a copy of your profile so Coolbet recognises your session."
        echo "  Keep this window open for the daemon to sync via CDP."
        echo
        echo "  Sanity-test:"
        echo "    PYTHONPATH=. python3 -m workers.automation.coolbet_browser_sync --cdp-fetch"
        exit 0
    fi
    sleep 1
done

echo "✗ CDP port didn't open in 30s — investigate:"
echo "    python3 -c \"import urllib.request; print(urllib.request.urlopen('http://localhost:$PORT/json/version').read())\""
echo "    pgrep -lf 'Chrome-CDP-OddsIntel'"
exit 3
