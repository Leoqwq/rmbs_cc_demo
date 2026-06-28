#!/usr/bin/env bash
# Owner: show the last 50 lines of the TEE service log.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
wait_for_ssh tee-node "$ZONE_A"
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='journalctl -u rmbs-tee -n 50 --no-pager'
