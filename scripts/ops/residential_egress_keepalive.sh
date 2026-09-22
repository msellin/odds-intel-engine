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
MICROSOCKS=/opt/homebrew/bin/microsocks
CONF="${OI_WG_CONF:-/Users/margussellin/.wireguard/oi-egress.conf}"
TUN_ADDR=10.8.0.2
SOCKS_PORT=1080
LOG_TS() { /bin/date -u +"%Y-%m-%dT%H:%M:%SZ"; }

tunnel_up() { /sbin/ifconfig 2>/dev/null | /usr/bin/grep -q "inet ${TUN_ADDR} "; }
socks_up()  { /usr/sbin/lsof -nP -iTCP:${SOCKS_PORT} -sTCP:LISTEN 2>/dev/null \
                | /usr/bin/grep -q "${TUN_ADDR}:${SOCKS_PORT}"; }

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

# ── 2. socks proxy ──────────────────────────────────────────────────────────
# Bound to the TUNNEL address only, never 0.0.0.0 — this must not become an open
# proxy on the LAN or, worse, reachable from the internet.
if ! socks_up; then
  echo "$(LOG_TS) SOCKS DOWN — starting microsocks on ${TUN_ADDR}:${SOCKS_PORT}"
  /usr/bin/pkill -f "microsocks -i ${TUN_ADDR}" >/dev/null 2>&1
  "$MICROSOCKS" -i "$TUN_ADDR" -p "$SOCKS_PORT" >/dev/null 2>&1 &
  sleep 1
  if socks_up; then echo "$(LOG_TS) socks up"; else echo "$(LOG_TS) ERROR socks failed to bind"; exit 1; fi
else
  echo "$(LOG_TS) socks ok"
fi
