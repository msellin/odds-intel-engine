#!/bin/bash
# RESIDENTIAL-EGRESS-KEEPALIVE (2026-09-22, VPS-CONSOLIDATION-2026-09-16 phase 1)
#
# Keeps the VPS's path out through this machine's Estonian residential line alive.
# Two things have to be up, and either can die independently:
#
#   1. the WireGuard tunnel  (utun holding 10.8.0.2)  — wg-quick, needs root
#   2. microsocks            (SOCKS5 on 10.8.0.2:1080) — what the VPS actually talks to
#
# Why this matters: the VPS-side Epicbet collector is configured with
# OI_RESIDENTIAL_PROXY and DELIBERATELY has no fallback — if this proxy is gone,
# collection FAILS rather than quietly collecting from the Hetzner IP. That is the
# right behaviour (see EPICBET-403-FROM-VPS: six silent days) but it means the
# tunnel being down is a real outage, so it gets a reviver rather than an alert.
#
# Same shape as flaresolverr_keepalive.sh: cheap when healthy (two local checks,
# exit), walks the chain and fixes what is broken when not. Idempotent.
#
# launchd gives a minimal PATH, so every binary is called by absolute path.
# Runs as ROOT (LaunchDaemon) because wg-quick configures a network interface.
set -uo pipefail

WG=/opt/homebrew/bin/wg-quick
CONF="${OI_WG_CONF:-/Users/margussellin/.wireguard/oi-egress.conf}"
TUN_ADDR=10.8.0.2
SOCKS_PORT=1080
LOG_TS() { /bin/date -u +"%Y-%m-%dT%H:%M:%SZ"; }

tunnel_up() { /sbin/ifconfig 2>/dev/null | /usr/bin/grep -q "inet ${TUN_ADDR} "; }

# ── 1. tunnel ───────────────────────────────────────────────────────────────
if ! tunnel_up; then
  echo "$(LOG_TS) TUNNEL DOWN — bringing up $CONF"
  # `down` first so a half-configured interface from a crash can't block `up`.
  "$WG" down "$CONF" >/dev/null 2>&1
  if "$WG" up "$CONF" 2>&1; then
    echo "$(LOG_TS) tunnel up"
  else
    echo "$(LOG_TS) ERROR wg-quick up failed"
    exit 1
  fi
else
  echo "$(LOG_TS) tunnel ok"
fi

# ── 2. socks proxy: NOT OUR JOB ANYMORE ─────────────────────────────────────
#
# BUG FIXED 2026-09-22, caught before anything depended on it. This script used
# to start microsocks with `&`. launchd reaps the whole process group when a
# StartInterval job exits, so the proxy died within seconds of every tick and
# was restarted on the next one — the log read
#
#     07:07:19Z SOCKS DOWN - starting microsocks
#     07:07:20Z socks up
#     07:09:21Z SOCKS DOWN - starting microsocks      <-- dead again
#
# i.e. a proxy that flaps on a 2-minute cycle. After the in-play cutover that
# would have surfaced as Epicbet collection failing intermittently for no visible
# reason, which is the worst kind of bug this repo keeps re-learning.
#
# A long-running process belongs to launchd, not to a script. microsocks is now
# com.oddsintel.residential-socks with KeepAlive=true: launchd starts it, watches
# it, and restarts it if it exits (including when it cannot bind because the
# tunnel is not up yet — ThrottleInterval stops that becoming a tight loop).
#
# This script keeps only the tunnel, which is genuinely short-lived work:
# wg-quick configures an interface and exits.
socks_state() {
  if /usr/sbin/lsof -nP -iTCP:${SOCKS_PORT} -sTCP:LISTEN 2>/dev/null \
       | /usr/bin/grep -q "${TUN_ADDR}:${SOCKS_PORT}"; then echo ok; else echo DOWN; fi
}
echo "$(LOG_TS) socks(supervised by launchd): $(socks_state)"
