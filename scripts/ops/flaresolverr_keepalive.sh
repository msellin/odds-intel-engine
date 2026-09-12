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
  # 200 + ready banner = the PORT is answering. Necessary, not sufficient —
  # see can_serve() below.
  /usr/bin/curl -s --max-time 8 "$FS_URL/" 2>/dev/null | /usr/bin/grep -q "FlareSolverr is ready"
}

# FS-LIVENESS-IS-NOT-CAPABILITY (2026-09-12) — why a second probe exists.
#
# On 2026-09-12 the Coolbet odds feed was dead for 3h and EVERY green light
# stayed green: the container reported `Up 6 weeks (healthy)`, `/` answered
# "FlareSolverr is ready", `sessions.list` answered `ok`, the launchd sweep
# exited 0, and `pipeline_runs` logged `completed` twelve times. FlareSolverr
# was alive and simply could not serve a request — every session returned the
# payload once and then HTTP 500'd forever. A `docker restart` fixed it in
# seconds, and nothing in this script would ever have done it, because this
# script asked "is the port up?" and the fault was one layer in.
#
# So probe the CAPABILITY: create a throwaway session, fetch a trivial page,
# destroy it. That is the thing callers actually need, and it is the only
# check that can tell "up" from "working".
#
# Cost control: this runs every 180s, so the fetch is a tiny page with a short
# maxTimeout, and it only runs when the cheap probe has already passed.
can_serve() {
  local sess="keepalive_$$_$(date +%s)"
  local out
  out=$(/usr/bin/curl -s --max-time 45 -X POST "$FS_URL/v1" \
        -H 'Content-Type: application/json' \
        -d "{\"cmd\":\"request.get\",\"url\":\"https://example.com\",\"session\":\"$sess\",\"maxTimeout\":30000}" 2>/dev/null)
  # Best-effort cleanup: a leaked session pins a Chrome context, which is the
  # very resource exhaustion this check exists to catch.
  /usr/bin/curl -s --max-time 10 -X POST "$FS_URL/v1" \
        -H 'Content-Type: application/json' \
        -d "{\"cmd\":\"sessions.destroy\",\"session\":\"$sess\"}" >/dev/null 2>&1
  echo "$out" | /usr/bin/grep -q '"status": *"ok"'
}

if probe; then
  if can_serve; then
    exit 0        # genuinely healthy — the common path, silent
  fi
  echo "$(LOG_TS) FS answers on $FS_URL but CANNOT SERVE A REQUEST — restarting"
  echo "$(LOG_TS)   (this is the 2026-09-12 shape: 'ready' banner, healthy container,"
  echo "$(LOG_TS)    sessions.list ok, and every request 500s. Liveness != capability.)"
  cd "$COMPOSE_DIR" || { echo "$(LOG_TS)   compose dir missing: $COMPOSE_DIR"; exit 1; }
  "$DOCKER" restart oi_local_flaresolverr 2>&1 | /usr/bin/tail -2
  for i in $(seq 1 8); do
    sleep 5
    if probe && can_serve; then
      echo "$(LOG_TS)   FS serving again after ~$((i*5))s"
      exit 0
    fi
  done
  echo "$(LOG_TS)   FS still not serving after restart — escalating"
  exit 1
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
