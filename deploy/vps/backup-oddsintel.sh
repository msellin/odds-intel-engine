#!/usr/bin/env bash
# Nightly pg_dump of oddsintel DB + push to Hetzner Storage Box.
# Local retention: 3 days on VPS disk (this is what the code has always done;
# the comment used to claim 14 — corrected 2026-09-11, VPS-DISK-AUDIT).
# Remote retention: 90 days on Storage Box (VPS-side sweep — Hetzner shell
# is restricted, no server-side find).
# Provisioned 2026-07-09 as part of SUPABASE-TO-VPS Phase 8.
set -euo pipefail

BACKUP_DIR=/var/backups/pg/nightly
mkdir -p "$BACKUP_DIR"
TS=$(date +%Y-%m-%d)
DUMPFILE="$BACKUP_DIR/oddsintel-$TS.dump"

# 1. Local dump
if [ -f "$DUMPFILE" ] && [ $(stat -c%s "$DUMPFILE") -gt 500000000 ]; then
  echo "[$(date -Is)] Local backup for $TS already exists."
else
  echo "[$(date -Is)] Dumping oddsintel to $DUMPFILE..."
  sudo -u postgres pg_dump --format=custom --compress=6 --no-owner --no-acl \
    --file="$DUMPFILE.tmp" oddsintel
  mv "$DUMPFILE.tmp" "$DUMPFILE"
  echo "[$(date -Is)] Local backup: $(du -m $DUMPFILE | cut -f1) MB"
fi

# 2. Push to Storage Box via rsync-over-SSH
if [ -f /opt/oddsintel/.storage-box.env ]; then
  set -a && . /opt/oddsintel/.storage-box.env && set +a
  echo "[$(date -Is)] Pushing $DUMPFILE to Storage Box..."
  SSHPASS="$STORAGE_BOX_PASSWORD" sshpass -e ssh -p $STORAGE_BOX_PORT \
    -o StrictHostKeyChecking=accept-new \
    $STORAGE_BOX_USER@$STORAGE_BOX_HOST 'mkdir oddsintel' 2>/dev/null || true
  SSHPASS="$STORAGE_BOX_PASSWORD" sshpass -e rsync \
    -av --progress \
    -e "ssh -p $STORAGE_BOX_PORT -o StrictHostKeyChecking=accept-new" \
    "$DUMPFILE" \
    "$STORAGE_BOX_USER@$STORAGE_BOX_HOST:oddsintel/" 2>&1 | tail -5

  # Remote retention: keep 90 days (VPS-side sweep — Hetzner restricted shell
  # has no server-side find). Parse date from filename oddsintel-YYYY-MM-DD.dump.
  CUTOFF=$(date -d '90 days ago' +%Y%m%d)
  REMOTE_LIST=$(SSHPASS="$STORAGE_BOX_PASSWORD" sshpass -e ssh -p $STORAGE_BOX_PORT \
    -o StrictHostKeyChecking=accept-new \
    $STORAGE_BOX_USER@$STORAGE_BOX_HOST 'ls oddsintel/' 2>/dev/null || echo '')
  for f in $REMOTE_LIST; do
    # Extract YYYY-MM-DD from filename; skip if not matching
    DATE_STR=$(echo "$f" | sed -n 's/^oddsintel-\([0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}\)\.dump$/\1/p')
    [ -z "$DATE_STR" ] && continue
    FILE_YMD=$(echo "$DATE_STR" | tr -d '-')
    if [ "$FILE_YMD" -lt "$CUTOFF" ]; then
      echo "[$(date -Is)] Storage Box remote retention: removing oddsintel/$f"
      SSHPASS="$STORAGE_BOX_PASSWORD" sshpass -e ssh -p $STORAGE_BOX_PORT \
        -o StrictHostKeyChecking=accept-new \
        $STORAGE_BOX_USER@$STORAGE_BOX_HOST "rm oddsintel/$f" 2>&1 | head -3 || true
    fi
  done
else
  echo "[$(date -Is)] WARN: no /opt/oddsintel/.storage-box.env — skipping remote push"
fi

# 3. Local retention: 3 days. Only our own pattern.
# Until 2026-09-11 this also deleted crossrank-*.dump, silently overriding
# backup-crossrank.sh's own 14-day rule (this script runs at 03:30, after that
# one at 03:00). Each script now prunes only what it creates; both use 3 days,
# which is the retention that has actually been in effect all along.
find "$BACKUP_DIR" -name 'oddsintel-*.dump' -mtime +3 -delete 2>/dev/null || true

echo "[$(date -Is)] Backup complete."
