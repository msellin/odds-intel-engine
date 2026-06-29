#!/usr/bin/env bash
# RAILWAY-ELIMINATION (2026-06-29): one-shot setup script for the Hetzner VPS.
#
# Run as root on a fresh Hetzner Ubuntu 22.04/24.04 VPS:
#   bash local/setup-hetzner.sh
#
# What this does:
#   1. Installs system packages (Python 3, pip, git, Docker)
#   2. Clones / pulls the repo to /opt/odds-intel-engine
#   3. Installs Python dependencies
#   4. Installs + starts FlareSolverr via Docker Compose
#   5. Installs + enables the systemd unit
#
# BEFORE running: copy your .env file to /opt/odds-intel-engine/.env
# (or run this script first, then write .env, then: systemctl start oddsintel-scheduler)
#
# After a code update:
#   cd /opt/odds-intel-engine && git pull && pip3 install -r requirements.txt -q
#   systemctl restart oddsintel-scheduler

set -euo pipefail

REPO_URL="https://github.com/msellin/odds-intel-engine.git"
REPO_DIR="/opt/odds-intel-engine"
SERVICE_NAME="oddsintel-scheduler"

echo "=== OddsIntel Hetzner Setup ==="

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/5] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl ca-certificates

# Docker (official install — idempotent)
if ! command -v docker &>/dev/null; then
    echo "  Installing Docker..."
    curl -fsSL https://get.docker.com | sh
else
    echo "  Docker already installed, skipping."
fi

# Docker Compose plugin (v2)
if ! docker compose version &>/dev/null 2>&1; then
    apt-get install -y -qq docker-compose-plugin
fi

# ── 2. Clone / pull repo ──────────────────────────────────────────────────────
echo "[2/5] Cloning / updating repo to $REPO_DIR..."
if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$REPO_DIR"
fi

# ── 3. Python dependencies ───────────────────────────────────────────────────
echo "[3/5] Installing Python dependencies..."
pip3 install -q --break-system-packages -r "$REPO_DIR/requirements.txt"

# ── 4. FlareSolverr ──────────────────────────────────────────────────────────
echo "[4/5] Starting FlareSolverr..."
cd "$REPO_DIR/local/systemd"
docker compose pull -q
docker compose up -d
echo "  Waiting for FlareSolverr to be healthy..."
for i in $(seq 1 12); do
    if curl -sf http://localhost:8191/ >/dev/null 2>&1; then
        echo "  FlareSolverr is up."
        break
    fi
    sleep 5
done

# ── 5. Systemd unit ──────────────────────────────────────────────────────────
echo "[5/5] Installing systemd unit..."
cp "$REPO_DIR/local/systemd/$SERVICE_NAME.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

if [ -f "$REPO_DIR/.env" ]; then
    echo "  .env found — starting scheduler now."
    systemctl start "$SERVICE_NAME"
    sleep 3
    systemctl status "$SERVICE_NAME" --no-pager
else
    echo ""
    echo "  ⚠ No .env file found at $REPO_DIR/.env"
    echo "  Write your .env (copy from Railway vars + local secrets), then:"
    echo "    systemctl start $SERVICE_NAME"
fi

echo ""
echo "=== Setup complete ==="
echo "Useful commands:"
echo "  journalctl -u $SERVICE_NAME -f          # live logs"
echo "  systemctl status $SERVICE_NAME          # health"
echo "  systemctl restart $SERVICE_NAME         # restart after code changes"
echo "  curl http://localhost:8191/             # FlareSolverr health"
