#!/usr/bin/env bash
# Owner one-time: push the installer to tee-node and run it (installs + enables rmbs-tee).
# Needs IAP SSH + sudo on tee-node. The TEE runs in place from your ~/rmbs_cc_demo, so the
# existing tee/kd/ signing key is read unchanged (TEE_ADDRESS does not move).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "pushing installer to tee-node ..."
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/ops/install_tee_service.sh" tee-node:~/install_tee_service.sh

log "running installer on tee-node (will prompt for nothing; uses sudo) ..."
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='bash ~/install_tee_service.sh'

log "tee-install done — the TEE now auto-starts on boot (rmbs-tee.service)."
