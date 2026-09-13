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
# PROFILE-COPY-ATOMIC-AND-VANISH-TOLERANT (2026-09-13). Two bugs made recovery
# fail exactly when it was needed most, both hit while re-bootstrapping a walled
# CDP profile:
#
#   1. rsync exits 24 ("partial transfer due to vanished source files") whenever
#      the SOURCE Chrome is running — it constantly creates and deletes
#      IndexedDB blobs. With `set -e` that aborted the whole script. Observed:
#      `.../https_web.telegram.org_0.indexeddb.blob/1/65/6511: No such file`.
#      A vanished cache blob is not a failure; a session copy does not need it.
#   2. The completeness test was `[ -d "$CDP_PROFILE/Default" ]`, so a copy that
#      aborted half-way left a directory that LOOKS complete and is silently
#      skipped forever after. That is how a broken profile becomes permanent.
#
# Fixed by copying into a staging dir and moving it into place only on success,
# so the real path is either absent or complete — never half-made.
copy_profile() {
    local stage="${CDP_PROFILE}.staging.$$"
    rm -rf "$stage"
    mkdir -p "$stage/Default"
    # `|| rc=$?` is LOAD-BEARING under `set -e`: without it a non-zero rsync
    # aborts the whole script before the next line can even read $?, so the
    # rc==24 tolerance below never runs. That is what happened on 2026-09-13 —
    # a vanished `Sessions/Session_*` file killed the heal and left Chrome down.
    # Also skip Sessions/ outright: it is the tab-restore state, it churns
    # constantly while Chrome is live, and an automation window does not want
    # the operator's 20 restored tabs anyway.
    local rc=0
    rsync -a --no-perms --no-owner \
        --exclude='Cache' --exclude='Code Cache' --exclude='GPUCache' \
        --exclude='Media Cache' --exclude='Service Worker/CacheStorage' \
        --exclude='ShaderCache' --exclude='Storage/ext' \
        --exclude='Crashpad' --exclude='IndexedDB/*.blob' \
        --exclude='Sessions' --exclude='Session Storage' \
        "$DEFAULT_PROFILE/Default/" "$stage/Default/" || rc=$?
    # 0 = clean, 24 = source files vanished mid-copy (expected: Chrome is live).
    if [ $rc -ne 0 ] && [ $rc -ne 24 ]; then
        echo "✗ profile copy failed (rsync rc=$rc)"
        rm -rf "$stage"
        return 1
    fi
    cp -p "$DEFAULT_PROFILE/Local State" "$stage/" 2>/dev/null || true
    rm -rf "$CDP_PROFILE"
    mv "$stage" "$CDP_PROFILE"
    return 0
}

if [ ! -f "$CDP_PROFILE/Local State" ] || [ ! -d "$CDP_PROFILE/Default" ]; then
    echo "✓ Copying your Chrome profile to $CDP_PROFILE"
    echo "  (~30s; the copy carries your current Coolbet session + Imperva trust)"
    echo "  NOTE: quit your normal Chrome first for the freshest cookies — a live"
    echo "  Chrome holds recent session state in memory and may not have flushed it."
    copy_profile || exit 1
    echo "✓ Profile copied"
fi

# Kill any previous CDP-Chrome (NOT your default Chrome — different
# profile dir, different process group).
if pgrep -lf "Chrome-CDP-OddsIntel" >/dev/null; then
    echo "⚠ Previous CDP-Chrome running — quitting it"
    # CDP-LIFECYCLE-LOG: record that WE killed it. Without this line a later
    # "cdp_down" is indistinguishable from a crash, which is exactly why
    # "why does CDP-Chrome keep dying?" could not be answered on 2026-09-13.
    python3 - <<'LOGKILL' 2>/dev/null || true
import json, pathlib, datetime
p = pathlib.Path("dev/active/cdp-lifecycle.jsonl")
p.parent.mkdir(parents=True, exist_ok=True)
with p.open("a") as f:
    f.write(json.dumps({
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "event": "killed", "why": "launch_chrome_for_sync.sh", "source": "launcher",
    }) + "\n")
LOGKILL
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
        # ENSURE-BOTH-BOOK-TABS (2026-09-13). A relaunch brings Chrome back
        # with whatever the profile restores — which on 2026-09-13 was a
        # coolbet.com tab and NO unibet.ee tab. Both feeds need one:
        #
        #   unibet_odds_feed.run_bulk  -> "no unibet.ee tab open in CDP-Chrome"
        #                                 stored 0 rows, feed went stale
        #   best_price_router          -> "resolve_event_url: no Unibet event URL"
        #                                 a routed real-money bet was NOT placed
        #
        # That second one cost a real bet: Cacereño at Unibet 3.30 (edge 11.2%)
        # routed correctly, failed to dispatch for want of a tab, and by the
        # time one existed the price had drifted to 3.10 (edge 9.2%, below the
        # floor). So opening the tabs is not cosmetic — it is the difference
        # between the Unibet arm working and silently not.
        #
        # Opened via CDP so it works headlessly and needs no window focus.
        python3 - "$PORT" <<'ENSURE_TABS'
import json, sys, urllib.request
port = sys.argv[1]
WANT = {"coolbet.com": "https://www.coolbet.com/et/",
        "unibet.ee":   "https://www.unibet.ee/betting/odds"}
try:
    with urllib.request.urlopen(f"http://localhost:{port}/json/list", timeout=8) as r:
        tabs = json.loads(r.read())
except Exception as e:
    print(f"  (could not list tabs: {e})")
    raise SystemExit(0)
have = " ".join((t.get("url") or "").lower() for t in tabs)
for host, url in WANT.items():
    if host in have:
        print(f"  ✓ {host} tab already open")
        continue
    try:
        urllib.request.urlopen(
            f"http://localhost:{port}/json/new?{urllib.parse.quote(url, safe='')}"
            if False else
            urllib.request.Request(f"http://localhost:{port}/json/new?{url}",
                                   method="PUT"), timeout=15)
        print(f"  ✓ opened {host} tab")
    except Exception as e:
        print(f"  ! could not open {host} tab ({e}) — the {host} feed will "
              f"report 'no tab open' until one exists")
ENSURE_TABS

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
