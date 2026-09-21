#!/bin/bash
# MAC-FS-UNSWEPT (2026-09-21) — sweep leaked FlareSolverr sessions on the Mac.
#
# `job_flaresolverr_sweep` (hourly, :37) runs in the VPS scheduler and targets
# the VPS FlareSolverr. But Coolbet placement, the Coolbet odds sweep, the
# in-play collector and Epicbet all route through the OPERATOR'S MAC FS
# (COOLBET_FS_LOCAL_URL; COOLBET_NO_FS must stay unset — runbook §6). Nothing
# ever swept that one, so a leaked session lived forever inside a 1 GiB
# container cap whose own comment sizes it for ONE session while three feeds
# share it.
#
# Not hypothetical: on 2026-09-21 the Mac held `coolbet_audit_probe` (an old
# ad-hoc session with no creator left in the repo) and `wd_freshprobe_...`
# (a watchdog throwaway whose own reap had been posting to a dead Railway host
# since before RAILWAY-ELIMINATION — see COOLBET-WEDGE-SELFHEAL-NEVER-FIRED).
#
# --fs-url is passed EXPLICITLY rather than relying on the script's default.
# The default reads FLARESOLVERR_URL, and this whole family of bugs comes from
# one process assuming which FlareSolverr it is talking to.
#
# launchd gives a minimal PATH, so binaries are called by absolute path.
set -uo pipefail

REPO="/Users/margussellin/www/odds-intel-engine"
PY=/usr/bin/python3
FS_URL="http://localhost:8191"

cd "$REPO" || exit 1

# Prefer the .env value when present — the URL must follow the box, not this file.
ENV_URL="$(/usr/bin/grep -E '^COOLBET_FS_LOCAL_URL=' "$REPO/.env" 2>/dev/null | /usr/bin/cut -d= -f2- | /usr/bin/tr -d '[:space:]')"
[ -n "$ENV_URL" ] && FS_URL="$ENV_URL"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] mac-fs-sweep -> $FS_URL"
exec "$PY" "$REPO/scripts/coolbet/sweep_stale_sessions.py" --fs-url "$FS_URL"
