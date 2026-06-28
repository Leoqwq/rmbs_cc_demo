#!/usr/bin/env bash
# Owner: restart the TEE service (no code push).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
wait_for_ssh tee-node "$ZONE_A"
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='sudo systemctl restart rmbs-tee && sleep 2 && systemctl is-active rmbs-tee'
log "rmbs-tee restarted."
