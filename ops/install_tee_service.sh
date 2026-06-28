#!/usr/bin/env bash
# Runs ON tee-node (pushed by `make tee-install`). Installs + enables the rmbs-tee systemd
# service so the TEE auto-starts on boot. Run as the owner (the SSH user); idempotent.
# Renders the unit from the owner's own identity/paths so nothing is hardcoded.
set -euo pipefail

USER_NAME="$(whoami)"
REPO="$HOME/rmbs_cc_demo"
PY="$REPO/.venv/bin/python"

[ -x "$PY" ] || { echo "ERROR: $PY not found — repo + venv must exist in $HOME"; exit 1; }
[ -f "$REPO/tee/tee_service.py" ] || { echo "ERROR: $REPO/tee/tee_service.py not found"; exit 1; }

# Hand off port 8000 from any manually-started (tmux) TEE before enabling the service.
tmux kill-session -t tee 2>/dev/null || true

echo "writing /etc/systemd/system/rmbs-tee.service ..."
sudo tee /etc/systemd/system/rmbs-tee.service >/dev/null <<EOF
[Unit]
Description=RMBS Confidential Compute TEE enclave
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$REPO
ExecStart=$PY -m tee.tee_service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now rmbs-tee

echo "waiting for the TEE to answer ..."
for _ in $(seq 1 10); do
  if curl -sf http://127.0.0.1:8000/tee_address; then
    echo; echo "rmbs-tee is up and enabled on boot."; exit 0
  fi
  sleep 2
done
echo "WARNING: rmbs-tee did not answer /tee_address yet — check 'sudo systemctl status rmbs-tee'"
exit 1
