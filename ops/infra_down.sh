#!/usr/bin/env bash
# Owner: stop the shared instances (cost control). Persistent disk state survives.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Stopping VMs is slow (often a minute or two). Tell the operator to be patient so they
# don't close the terminal mid-stop and leave instances half-stopped (still billing).
warn "Stopping the shared instances — this can take a minute or two. Please wait until you see 'infra-down done' before closing the terminal."

gcloud compute instances stop $INSTANCES_A --zone="$ZONE_A"
gcloud compute instances stop $INSTANCES_B --zone="$ZONE_B"
gcloud compute instances stop $INSTANCES_C --zone="$ZONE_C"
log "infra-down done."
