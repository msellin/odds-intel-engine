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
"$CHROME" \
    --remote-debugging-port=$PORT \
    --user-data-dir="$CDP_PROFILE" \
    --no-first-run \
    --no-default-browser-check \
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
