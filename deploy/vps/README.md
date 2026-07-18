# VPS deployment artifacts

Files here live on the Hetzner VPS (`204.168.199.8`) outside of `/opt/odds-intel-engine/` proper. Kept in git for reproducibility — if the VPS ever needs to be rebuilt, these are what makes the system come back up.

## Files

### `pipeline-heartbeat-alert.sh`
- **VPS path**: `/opt/oddsintel/pipeline-heartbeat-alert.sh`
- **Purpose**: standalone Telegram alert when the scheduler goes silent >30 min. Independent of `workers/scheduler.py` — runs from its own systemd timer so a full scheduler crash still gets caught.
- **Reads**: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` from `/opt/odds-intel-engine/.env`
- **State**: `/var/lib/oddsintel-heartbeat.state` (idempotent — one alert per stale window)
- **Installed**: 2026-07-18 during pre-vacation reliability sprint

### `oddsintel-heartbeat.service` + `oddsintel-heartbeat.timer`
- **VPS path**: `/etc/systemd/system/oddsintel-heartbeat.{service,timer}`
- **Schedule**: every 15 min (via timer)

## Deploy commands

```bash
# From this repo, after any change here:
scp deploy/vps/pipeline-heartbeat-alert.sh root@204.168.199.8:/opt/oddsintel/
scp deploy/vps/oddsintel-heartbeat.{service,timer} root@204.168.199.8:/etc/systemd/system/
ssh root@204.168.199.8 'chmod +x /opt/oddsintel/pipeline-heartbeat-alert.sh; systemctl daemon-reload; systemctl restart oddsintel-heartbeat.timer'
```

## Related

- **`odds-scheduler.service.disabled-20260713`** on VPS (renamed, not deleted) — old duplicate systemd unit that crash-looped on port 8080. Neutralized 2026-07-13.
- **`oddsintel-scheduler.service`** on VPS — the canonical scheduler unit. Managed manually on VPS (not in this repo yet — should be added on next migration).
- **`/opt/oddsintel/backup-oddsintel.sh`** on VPS — nightly Storage Box backup at 03:30 UTC. Not in this repo either (predates the deploy/ convention).
