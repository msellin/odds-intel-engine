#!/usr/bin/env bash
# Nightly pg_dump of crossrank DB + push to Hetzner Storage Box.
#
# Local retention:  3 days on VPS disk (see note below).
# Remote retention: 90 days on Storage Box, swept VPS-side.
#
# RETENTION SWEEP — why it is VPS-side (fixed 2026-09-11, VPS-DISK-AUDIT):
# This script used to prune the Storage Box with:
#     ssh <box> 'find crossrank/ -name "crossrank-*.dump" -mtime +90 -delete'
# Hetzner Storage Box runs a RESTRICTED shell that has no `find`. The call
# returned "Command not found" (exit 8) on every run, and the trailing
# `|| true` swallowed it — so remote retention had NEVER deleted anything and
# nobody noticed. At audit time the box held 65 dumps / 298 GB and the oldest
# was 65 days old, i.e. still inside 90 days, so the breakage was about to
# become unbounded growth at ~5 GB/day rather than a visible failure.
# backup-oddsintel.sh already worked around this by listing remotely and
# deciding locally; this script now does the same. Do not reintroduce `find`,
# `xargs`, or any other coreutils here — only `ls`, `rm` and `mkdir` exist.
#
# LOCAL RETENTION — why 3 days and not the 14 this file used to claim:
# backup-oddsintel.sh runs at 03:30, after this one at 03:00, and its cleanup
# step deleted BOTH oddsintel-*.dump and crossrank-*.dump at -mtime +3. So the
# real local retention for crossrank was 3 days, never the 14 in this comment.
# Each script now prunes only its own pattern, and both use 3 days — which is
# the behaviour that has actually been in effect. The Storage Box's 90 days is
# the real recovery window; local copies are only a fast-restore convenience.
set -euo pipefail

BACKUP_DIR=/var/backups/pg/nightly
mkdir -p "$BACKUP_DIR"
TS=$(date +%Y-%m-%d)
DUMPFILE="$BACKUP_DIR/crossrank-$TS.dump"

# 1. Local dump
if [ -f "$DUMPFILE" ] && [ $(stat -c%s "$DUMPFILE") -gt 100000000 ]; then
  echo "[$(date -Is)] Local backup for $TS already exists."
else
  echo "[$(date -Is)] Dumping crossrank to $DUMPFILE..."
  sudo -u postgres pg_dump --format=custom --compress=6 --no-owner --no-acl \
    --file="$DUMPFILE.tmp" crossrank
  mv "$DUMPFILE.tmp" "$DUMPFILE"
  echo "[$(date -Is)] Local backup: $(du -m $DUMPFILE | cut -f1) MB"
fi

# 2. Push to Storage Box via rsync-over-SSH
if [ -f /opt/crossrank/.storage-box.env ]; then
  set -a && . /opt/crossrank/.storage-box.env && set +a
  echo "[$(date -Is)] Pushing $DUMPFILE to Storage Box..."
  SSHPASS="$STORAGE_BOX_PASSWORD" sshpass -e rsync \
    -av --progress \
    -e "ssh -p $STORAGE_BOX_PORT -o StrictHostKeyChecking=accept-new" \
    "$DUMPFILE" \
    "$STORAGE_BOX_USER@$STORAGE_BOX_HOST:crossrank/" 2>&1 | tail -5

  # Remote retention: keep 90 days. List remotely, decide locally, rm remotely.
  CUTOFF=$(date -d '90 days ago' +%Y%m%d)
  REMOTE_LIST=$(SSHPASS="$STORAGE_BOX_PASSWORD" sshpass -e ssh -p $STORAGE_BOX_PORT \
    -o StrictHostKeyChecking=accept-new \
    $STORAGE_BOX_USER@$STORAGE_BOX_HOST 'ls crossrank/' 2>/dev/null || echo '')
  if [ -z "$REMOTE_LIST" ]; then
    echo "[$(date -Is)] WARN: remote listing empty — skipping remote retention sweep"
  fi
  for f in $REMOTE_LIST; do
    # Extract YYYY-MM-DD from filename; skip anything that does not match.
    DATE_STR=$(echo "$f" | sed -n 's/^crossrank-\([0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}\)\.dump$/\1/p')
    [ -z "$DATE_STR" ] && continue
    FILE_YMD=$(echo "$DATE_STR" | tr -d '-')
    if [ "$FILE_YMD" -lt "$CUTOFF" ]; then
      echo "[$(date -Is)] Storage Box remote retention: removing crossrank/$f"
      SSHPASS="$STORAGE_BOX_PASSWORD" sshpass -e ssh -p $STORAGE_BOX_PORT \
        -o StrictHostKeyChecking=accept-new \
        $STORAGE_BOX_USER@$STORAGE_BOX_HOST "rm crossrank/$f" 2>&1 | head -3 || true
    fi
  done
else
  echo "[$(date -Is)] WARN: no /opt/crossrank/.storage-box.env — skipping remote push"
fi

# 3. Local retention: 3 days. Only our own pattern (see header note).
find "$BACKUP_DIR" -name 'crossrank-*.dump' -mtime +3 -delete 2>/dev/null || true

echo "[$(date -Is)] Backup complete."
