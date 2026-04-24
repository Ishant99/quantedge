#!/bin/bash
# =============================================================================
# deploy.sh — Manual deploy script for Oracle VM
# Run this ON the Oracle VM (or via: ssh ubuntu@<IP> 'bash ~/quantedge/deploy/deploy.sh')
# =============================================================================

set -e

REPO_DIR="/home/ubuntu/quantedge"
BRANCH="main"

echo "======================================================"
echo "  Quantedge — Manual Deploy"
echo "  Branch : $BRANCH"
echo "  Time   : $(date '+%Y-%m-%d %H:%M:%S')"
echo "======================================================"

cd "$REPO_DIR"

# 1. Pull latest code
echo ""
echo "[1/4] Pulling latest code from $BRANCH..."
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"
echo "      Commit: $(git log -1 --format='%h %s')"

# 2. Rebuild Docker image (only if source changed)
echo ""
echo "[2/4] Building Docker image..."
docker compose build --no-cache

# 3. Restart containers (zero-downtime: bring new up, old comes down)
echo ""
echo "[3/4] Restarting containers..."
docker compose up -d --remove-orphans

# 4. Health check
echo ""
echo "[4/4] Checking health..."
sleep 5
docker compose ps

SCHED_STATUS=$(docker inspect --format='{{.State.Status}}' quantedge_scheduler 2>/dev/null || echo "not found")
DASH_STATUS=$(docker inspect --format='{{.State.Status}}' quantedge_dashboard 2>/dev/null || echo "not found")

echo ""
echo "  scheduler : $SCHED_STATUS"
echo "  dashboard : $DASH_STATUS"
echo ""

if [ "$SCHED_STATUS" = "running" ] && [ "$DASH_STATUS" = "running" ]; then
    echo "  Deploy successful."
else
    echo "  WARNING: one or more containers not running — check logs:"
    echo "    docker compose logs --tail=50 scheduler"
    echo "    docker compose logs --tail=50 dashboard"
    exit 1
fi

echo "======================================================"
