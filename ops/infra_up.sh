#!/usr/bin/env bash
# Owner: start the shared instances. The TEE auto-starts on boot via the rmbs-tee systemd
# service (see `make tee-install`), so no SSH into tee-node is needed here — `make up`'s
# health-gate verifies the TEE once tunnels are open.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "starting shared instances (QBFT needs >=3 validators online to produce blocks)..."
# Best-effort: a single zone running out of capacity (ZONE_RESOURCE_POOL_EXHAUSTED) must
# not abort startup — QBFT tolerates one validator down (3 of 4). 'make up' / doctor verify
# actual block production + TEE reachability afterwards.
gcloud compute instances start $INSTANCES_A --zone="$ZONE_A" \
  || warn "some instances in $ZONE_A failed to start (capacity?) — continuing"
gcloud compute instances start $INSTANCES_B --zone="$ZONE_B" \
  || warn "some instances in $ZONE_B failed to start (capacity?) — continuing"
gcloud compute instances start $INSTANCES_C --zone="$ZONE_C" \
  || warn "some instances in $ZONE_C failed to start (capacity?) — continuing; chain still runs if >=3 validators are up"

log "infra-up done. The TEE auto-starts on boot; run 'make up' to open tunnels + verify it."
