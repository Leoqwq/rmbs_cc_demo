#!/usr/bin/env bash
# Owner: start the shared instances and ensure the TEE service is running on tee-node.
# Idempotent: if the TEE already answers, the remote step is a no-op.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "starting shared instances (QBFT needs >=3 validators online to produce blocks)..."
gcloud compute instances start $INSTANCES_A --zone="$ZONE_A"
gcloud compute instances start $INSTANCES_B --zone="$ZONE_B"
gcloud compute instances start $INSTANCES_C --zone="$ZONE_C"

log "ensuring the TEE service is up on tee-node (tmux session 'tee')..."
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap --command='
  set -e
  if curl -sf http://127.0.0.1:8000/tee_address >/dev/null 2>&1; then
    echo "TEE already running"; exit 0
  fi
  cd ~/rmbs_cc_demo
  tmux kill-session -t tee 2>/dev/null || true
  TERM=xterm-256color tmux new-session -d -s tee \
    "source .venv/bin/activate && python -m tee.tee_service"
  echo "TEE started in tmux session tee"
'
log "infra-up done. Open tunnels (make up, or just the tunnels) and confirm block production."
