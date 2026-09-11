#!/bin/bash
# oddsintel pipeline heartbeat — Telegram alert on scheduler silence.
#
# Installed 2026-07-18 during pre-vacation reliability sprint. Sibling to
# workers/scheduler.py's built-in Kuma push (which is a no-op until
# KUMA_URL_BASE + KUMA_TOKENS are set). This script is standalone — it
# runs from a systemd timer, independent of the scheduler process, so
# even a total scheduler crash still fires an alert.
#
# Deployed to /opt/oddsintel/pipeline-heartbeat-alert.sh on VPS.
# Timer at /etc/systemd/system/oddsintel-heartbeat.timer (every 15 min).
#
# Reads TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from /opt/odds-intel-engine/.env.
# Queries pipeline_runs.max(started_at); if silent >30min sends Telegram.
# Idempotent — writes /var/lib/oddsintel-heartbeat.state so it doesn't
# spam. Alerts on stale AND on recovery.

set -u

STATE_FILE=/var/lib/oddsintel-heartbeat.state
STALE_MIN=30
ENV_FILE=/opt/odds-intel-engine/.env

BOT_TOKEN=$(awk -F= '/^TELEGRAM_BOT_TOKEN=/{print $2; exit}' "$ENV_FILE")
CHAT_ID=$(awk -F= '/^TELEGRAM_CHAT_ID=/{print $2; exit}' "$ENV_FILE")

if [ -z "$BOT_TOKEN" ] || [ -z "$CHAT_ID" ]; then
  echo "$(date -u +%FT%TZ) [heartbeat] no telegram creds — skipping" >&2
  exit 0
fi

send_tg() {
  curl -sS -o /dev/null -X POST \
    "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}" \
    --data-urlencode "text=${1}" 2>&1 || true
}

AGE_SECS=$(sudo -u postgres psql -d oddsintel -Xtc \
  "SELECT EXTRACT(EPOCH FROM (now() - max(started_at)))::int FROM pipeline_runs;" \
  2>/dev/null | tr -d ' ')

if [ -z "$AGE_SECS" ]; then
  send_tg "🚨 oddsintel heartbeat: cannot query pipeline_runs — DB unreachable?"
  echo "unknown" > "$STATE_FILE"
  exit 1
fi

AGE_MIN=$(( AGE_SECS / 60 ))
PREV=$(cat "$STATE_FILE" 2>/dev/null || echo "ok")

if [ "$AGE_MIN" -gt "$STALE_MIN" ]; then
  if [ "$PREV" != "stale" ]; then
    send_tg "🚨 oddsintel scheduler stale: no pipeline_runs in ${AGE_MIN}min (threshold ${STALE_MIN}min). Kick with: ssh root@204.168.199.8 systemctl restart oddsintel-scheduler"
  fi
  echo "stale" > "$STATE_FILE"
elif [ "$PREV" = "stale" ]; then
  send_tg "✅ oddsintel scheduler recovered — last pipeline_run ${AGE_MIN}min ago"
  echo "ok" > "$STATE_FILE"
else
  echo "ok" > "$STATE_FILE"
fi
