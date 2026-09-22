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
# Resolve the container by IMAGE, not by a hardcoded name. The Mac runs
# `oi_local_flaresolverr` (local/flaresolverr/docker-compose.yml) and the VPS runs
# `oi_hetzner_flaresolverr` (local/systemd/docker-compose.yml) — until 2026-09-22
# the VPS was wrongly running the Mac's file, which is how it ended up with a
# laptop-sized 1 GiB cap AND port 8191 bound to 0.0.0.0 on a public IP. Both are
# fixed; this stops the same script from silently no-op'ing on whichever host it
# was not written for.
FS_CONTAINER="$("${DOCKER}" ps -a --filter ancestor=ghcr.io/flaresolverr/flaresolverr:latest \
                 --format '{{.Names}}' 2>/dev/null | /usr/bin/head -1)"
FS_CONTAINER="${FS_CONTAINER:-oi_local_flaresolverr}"
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

# COOLBET-SPECIFIC WEDGE (2026-09-13). `can_serve` fetches example.com, and on
# 2026-09-13 it PASSED while every Coolbet request hung — FlareSolverr was
# serving fine in general and wedged on one site. The Coolbet feed sat dead for
# 3.3h behind a green capability probe. "Liveness is not capability" was the
# right lesson; this is the sharper version: **capability against the site you
# actually need**.
#
# WHY THIS IS GATED ON FEED STALENESS RATHER THAN RUN EVERY TICK. This script
# runs every 180s. Probing Coolbet on every tick would add ~480 requests/day
# from one residential IP — and our own request volume is precisely what earns
# the Imperva escalation (runbook §7). Curing the disease by spreading it is
# not a fix.
#
# So the expensive probe is tied to the SYMPTOM: ask the database (free, no
# network) whether the Coolbet feed has actually gone stale, and only then
# spend one real request finding out whether FS is the reason. Healthy system,
# zero added footprint.
coolbet_feed_stale() {
  /opt/homebrew/bin/python3 - <<'STALE' 2>/dev/null
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd()))
try:
    from workers.api_clients.db import execute_query
    r = execute_query("""SELECT EXTRACT(EPOCH FROM (NOW()-MAX(timestamp)))/60.0 AS m
                           FROM odds_snapshots WHERE bookmaker='Coolbet'""")
    mins = float((r[0]["m"] if r and r[0]["m"] is not None else 9999))
except Exception:
    sys.exit(1)          # cannot tell -> do NOT escalate (fail quiet, not loud)
# 75 min = two and a half missed :03/:33 passes. Below that it is ordinary jitter.
sys.exit(0 if mins > 75 else 1)
STALE
}

# One real Coolbet fetch through a throwaway session. Only ever called when the
# feed is already stale, so it costs nothing on a healthy day.
coolbet_can_serve() {
  local sess="kacb_$$_$(date +%s)"
  local out
  out=$(/usr/bin/curl -s --max-time 90 -X POST "$FS_URL/v1" \
        -H 'Content-Type: application/json' \
        -d "{\"cmd\":\"request.get\",\"url\":\"https://www.coolbet.com/s/sbgate/category/fo-tree/et?country=EE\",\"session\":\"$sess\",\"maxTimeout\":60000}" 2>/dev/null)
  /usr/bin/curl -s --max-time 10 -X POST "$FS_URL/v1" \
        -H 'Content-Type: application/json' \
        -d "{\"cmd\":\"sessions.destroy\",\"session\":\"$sess\"}" >/dev/null 2>&1
  echo "$out" | /usr/bin/grep -q '"status": *"ok"'
}

if probe; then
  if can_serve; then
    # FS serves in general. Now the sharper question, and only when the feed
    # says something is wrong.
    if coolbet_feed_stale; then
      echo "$(LOG_TS) Coolbet feed stale — probing FS against COOLBET specifically"
      if coolbet_can_serve; then
        echo "$(LOG_TS)   FS serves Coolbet fine — the fault is NOT FlareSolverr."
        echo "$(LOG_TS)   Look at the sweep, the session, or Imperva (runbook 2a/6/7)."
        exit 0
      fi
      echo "$(LOG_TS)   FS serves example.com but WEDGES ON COOLBET — restarting"
      cd "$COMPOSE_DIR" || exit 1
      "$DOCKER" restart "$FS_CONTAINER" 2>&1 | /usr/bin/tail -2
      for i in $(seq 1 8); do
        sleep 5
        if probe && coolbet_can_serve; then
          echo "$(LOG_TS)   FS serving Coolbet again after ~$((i*5))s"
          exit 0
        fi
      done
      echo "$(LOG_TS)   still wedged on Coolbet after restart — escalating"
      exit 1
    fi
    exit 0        # genuinely healthy — the common path, silent
  fi
  echo "$(LOG_TS) FS answers on $FS_URL but CANNOT SERVE A REQUEST — restarting"
  echo "$(LOG_TS)   (this is the 2026-09-12 shape: 'ready' banner, healthy container,"
  echo "$(LOG_TS)    sessions.list ok, and every request 500s. Liveness != capability.)"
  cd "$COMPOSE_DIR" || { echo "$(LOG_TS)   compose dir missing: $COMPOSE_DIR"; exit 1; }
  "$DOCKER" restart "$FS_CONTAINER" 2>&1 | /usr/bin/tail -2
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
