#!/bin/bash
# FLARESOLVERR-KEEPALIVE (2026-09-07, COOLBET-FS-WATCHDOG-AND-ENV)
#
# Auto-revive FlareSolverr. The 2026-09-07 outage was FS being down for ~a day;
# alerting alone is not enough — this REVIVES it, fast, without a human.
#
# Runs every few minutes from launchd. Cheap when healthy (one HTTP probe, exit).
# When down it walks the whole dependency chain: Docker daemon up? container up?
# — and fixes whatever is broken. Idempotent and safe to run concurrently with
# the daemon (docker compose up -d on an already-running container is a no-op).
#
# launchd gives a minimal PATH, so every binary is called by absolute path.
set -uo pipefail

DOCKER=/usr/local/bin/docker
FS_URL="http://localhost:8191"
COMPOSE_DIR="$(cd "$(dirname "$0")/../../local/flaresolverr" && pwd)"
LOG_TS() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

probe() {
  # 200 + ready banner = healthy
  /usr/bin/curl -s --max-time 8 "$FS_URL/" 2>/dev/null | /usr/bin/grep -q "FlareSolverr is ready"
}

if probe; then
  exit 0          # healthy — the common path, silent
fi

echo "$(LOG_TS) FS unreachable at $FS_URL — reviving"

# 1) Docker daemon reachable?
if ! "$DOCKER" info >/dev/null 2>&1; then
  echo "$(LOG_TS)   Docker daemon down — launching Docker.app"
  /usr/bin/open --background -a Docker 2>/dev/null
  # Docker Desktop takes ~30-60s to accept connections; wait up to 90s.
  for i in $(seq 1 18); do
    sleep 5
    "$DOCKER" info >/dev/null 2>&1 && { echo "$(LOG_TS)   Docker daemon up after ~$((i*5))s"; break; }
  done
  if ! "$DOCKER" info >/dev/null 2>&1; then
    echo "$(LOG_TS)   Docker daemon STILL down after 90s — cannot revive FS here"
    exit 1        # the health watchdog will page; a human must check Docker
  fi
fi

# 2) Bring the container up (no-op if already running; recreates if crashed).
cd "$COMPOSE_DIR" || { echo "$(LOG_TS)   compose dir missing: $COMPOSE_DIR"; exit 1; }
"$DOCKER" compose up -d 2>&1 | /usr/bin/tail -3

# 3) Re-probe — FS boots Chrome, give it up to ~40s.
for i in $(seq 1 8); do
  sleep 5
  if probe; then
    echo "$(LOG_TS)   FS revived after ~$((i*5))s"
    exit 0
  fi
done

echo "$(LOG_TS)   FS still not answering after compose up — escalating to watchdog alert"
exit 1
