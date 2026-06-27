#!/usr/bin/env bash
# Owner: stop the shared instances (cost control). Persistent disk state survives.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

gcloud compute instances stop $INSTANCES_A --zone="$ZONE_A"
gcloud compute instances stop $INSTANCES_B --zone="$ZONE_B"
gcloud compute instances stop $INSTANCES_C --zone="$ZONE_C"
log "infra-down done."
