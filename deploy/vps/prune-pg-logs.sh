#!/usr/bin/env bash
# Expire Postgres' own logs in PGDATA/log.
#
# Why this exists (VPS-DISK-AUDIT, 2026-09-11):
# Postgres runs with logging_collector=on and writes to PGDATA/log, NOT to
# /var/log/postgresql — so Debian's packaged `postgresql-common` logrotate entry
# never saw these files and nothing ever deleted them. They reached 15 GB across
# 38 files (~1-4 GB/day; log_min_duration_statement=1000 dumps full statement
# text, and the odds_snapshots thinning DELETEs carry multi-KB UUID ARRAY
# literals).
#
# Why cron and not logrotate: Postgres already rotates by filename daily
# (log_filename=postgresql-%Y-%m-%d.log). logrotate would *rename* the file
# Postgres currently holds open, and writes would follow the inode into the
# rotated name. We only need expiry, not rotation — so just delete old files.
# Postgres holds an fd on today's file only; -mtime +7 never touches it.
set -euo pipefail
LOGDIR=/var/lib/postgresql/17/main/log
RETAIN_DAYS=7
find "$LOGDIR" -maxdepth 1 -name 'postgresql-*.log' -mtime +"$RETAIN_DAYS" -delete
