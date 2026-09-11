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

### `backup-oddsintel.sh`
- **VPS path**: `/opt/oddsintel/backup-oddsintel.sh` — cron `30 3 * * *`
- **Purpose**: nightly `pg_dump` of `oddsintel` → local `/var/backups/pg/nightly` + rsync to Hetzner Storage Box.
- **Retention**: **3 days local, 90 days remote.** Brought into this repo 2026-09-11 (VPS-DISK-AUDIT).

### `backup-crossrank.sh`
- **VPS path**: `/opt/crossrank/backup-crossrank.sh` — cron `0 3 * * *`
- **Belongs to the CrossRank project**, not this one, but shares this VPS, this Postgres instance and this Storage Box — so it is kept here to stop the two scripts silently fighting again (see below).
- **Retention**: 3 days local, 90 days remote.

### `prune-pg-logs.sh`
- **VPS path**: `/opt/oddsintel/prune-pg-logs.sh` — cron `20 4 * * *`
- **Purpose**: expire Postgres' own logs in `PGDATA/log` older than 7 days.
- **Why it exists**: Postgres runs `logging_collector=on` and writes to `/var/lib/postgresql/17/main/log`, **not** `/var/log/postgresql` — so Debian's packaged `postgresql-common` logrotate entry never saw these files and nothing ever deleted them. They reached **15 GB / 38 files**.

### `logrotate-docker-containers`
- **VPS path**: `/etc/logrotate.d/docker-containers`
- **Purpose**: bound Docker's `json-file` logs (daily, 3 rotations, `maxsize 200M`, `copytruncate`).
- **Why `copytruncate`**: it keeps the daemon's open fd valid, so no container restart is needed. A `daemon.json` `log-opt` would only apply to containers created *after* it, which would mean recreating `oddsintel-postgrest-1` and taking `api.oddsintel.app` down.

## Related

- **`odds-scheduler.service.disabled-20260713`** on VPS (renamed, not deleted) — old duplicate systemd unit that crash-looped on port 8080. Neutralized 2026-07-13.
- **`oddsintel-scheduler.service`** on VPS — the canonical scheduler unit. Managed manually on VPS (not in this repo yet — should be added on next migration).

## The two backup scripts must not fight (VPS-DISK-AUDIT, 2026-09-11)

Both scripts write into the same `/var/backups/pg/nightly` and push to the same
Storage Box account. Two bugs came out of that shared state, and both were
invisible because each script's *comment* described the intent, not the code:

1. **`backup-oddsintel.sh` pruned CrossRank's dumps.** Its cleanup deleted both
   `oddsintel-*.dump` and `crossrank-*.dump` at `-mtime +3`. Because it runs at
   03:30, *after* CrossRank's 03:00, it silently overrode `backup-crossrank.sh`'s
   own 14-day rule. Each script now prunes only the pattern it creates.
2. **CrossRank's remote sweep could never have worked.** It pruned the Storage
   Box with `ssh <box> 'find crossrank/ -mtime +90 -delete'`. **Hetzner Storage
   Box runs a restricted shell with no `find`** — the call returned `Command not
   found` (exit 8) every night and the trailing `|| true` swallowed it. Nothing
   had ever been deleted. It had not yet *shown* as growth only because the
   oldest dump was 65 days old, still inside the 90-day window.

**If you touch either script: the Storage Box shell has only `ls`, `rm`, `mkdir`.**
No `find`, no `xargs`, no `sh -c`. Any retention sweep must list remotely and
decide locally — which is what both scripts now do.
