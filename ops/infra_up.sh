#!/usr/bin/env bash
# Owner: start the shared instances and ensure the TEE service is running on tee-node.
# Idempotent: if the TEE already answers, the remote step is a no-op.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "starting shared instances (QBFT needs >=3 validators online to produce blocks)..."
# Best-effort: a single zone running out of capacity (ZONE_RESOURCE_POOL_EXHAUSTED) must
# not abort startup — QBFT tolerates one validator down (3 of 4), and the TEE step below
# must still run. 'make up' / doctor verify actual block production afterwards.
gcloud compute instances start $INSTANCES_A --zone="$ZONE_A" \
  || warn "some instances in $ZONE_A failed to start (capacity?) — continuing"
gcloud compute instances start $INSTANCES_B --zone="$ZONE_B" \
  || warn "some instances in $ZONE_B failed to start (capacity?) — continuing"
gcloud compute instances start $INSTANCES_C --zone="$ZONE_C" \
  || warn "some instances in $ZONE_C failed to start (capacity?) — continuing; chain still runs if >=3 validators are up"

log "ensuring the TEE service is up on tee-node (tmux session 'tee')..."
# A freshly-started tee-node needs ~30-60s before sshd accepts connections; the first SSH
# right after boot fails with '[4003] Failed to connect to port 22'. Retry until the VM is
# reachable instead of aborting on that transient boot window.
TEE_CMD='
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
for attempt in $(seq 1 8); do
  if gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap --command="$TEE_CMD"; then
    break
  fi
  [ "$attempt" -eq 8 ] && die "could not SSH to tee-node after 8 tries — it may still be booting; wait ~30s and re-run 'make infra-up'"
  warn "tee-node not SSH-ready yet (attempt $attempt/8, VM likely still booting) — retrying in 15s..."
  sleep 15
done
log "infra-up done. Open tunnels (make up, or just the tunnels) and confirm block production."
