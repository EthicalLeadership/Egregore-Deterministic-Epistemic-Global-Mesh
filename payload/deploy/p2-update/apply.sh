#!/bin/bash
set -e

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

echo "[deploy] Updating source files..."
cp deploy/p2-update/proxy.py src/egregore/ems/proxy.py
cp deploy/p2-update/federation_handshake_watcher.py scripts/federation_handshake_watcher.py

echo "[deploy] Installing systemd units..."
mkdir -p ~/.config/systemd/user
cp deploy/p2-update/egregore-ems-proxy.service ~/.config/systemd/user/
cp deploy/p2-update/egregore-federation-watcher.service ~/.config/systemd/user/

echo "[deploy] Updating .env..."
if ! grep -q "EGREGORE_MOUNT_FEDERATION_ON_EMS" .env; then
cat >> .env <<'EOFENV'

# Federation cluster configuration (WireGuard)
EGREGORE_CLUSTER_NODES=pioneer1=10.200.200.1:8001,pioneer2=10.200.200.2:8001,pioneer3=192.168.2.133:8443
PIONEER2_HOST=10.200.200.1
PIONEER2_PORT=8001
EGREGORE_PORT=8443
EGREGORE_NODE_ID=pioneer2
PEER_NODE_ID=pioneer1
EGREGORE_CONSTITUTION_PATH=config/egregore_constitution.yaml
EGREGORE_EMS_PROXY_HOST=0.0.0.0
EGREGORE_MOUNT_FEDERATION_ON_EMS=true
EGREGORE_SCHEME=http
EGREGORE_LOCAL_SCHEME=http
EGREGORE_LOCAL_PORT=8001
EOFENV
fi

echo "[deploy] Reloading systemd..."
systemctl --user daemon-reload
systemctl --user enable egregore-ems-proxy egregore-federation-watcher

echo "[deploy] Restarting services..."
systemctl --user restart egregore-ems-proxy egregore-federation-watcher

echo "[deploy] Verifying..."
sleep 3
curl -s http://127.0.0.1:8001/health/ready || true
echo
systemctl --user status egregore-ems-proxy egregore-federation-watcher --no-pager
