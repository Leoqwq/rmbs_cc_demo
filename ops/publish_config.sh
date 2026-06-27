#!/usr/bin/env bash
# Owner: build the ABI and push the member bundle (members.env + umbral_state.json + ABI)
# to the shared tee-node so teammates can 'make sync'.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
FORGE="${FORGE:-$HOME/.foundry/bin/forge}"
ABI="$ROOT/out/ConfidentialCompute.sol/ConfidentialCompute.json"

log "building ABI for the member bundle..."
( cd "$ROOT" && "$FORGE" build >/dev/null )
[ -f "$ABI" ] || die "ABI not found at $ABI after forge build"

gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='mkdir -p ~/rmbs_cc_demo/share'
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/.env" tee-node:~/rmbs_cc_demo/share/members.env
[ -f "$ROOT/kd/umbral_state.json" ] || die "kd/umbral_state.json not found — run 'make bootstrap' first (step 3 runs keygen)"
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/kd/umbral_state.json" tee-node:~/rmbs_cc_demo/share/umbral_state.json
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ABI" tee-node:~/rmbs_cc_demo/share/ConfidentialCompute.json
log "published members.env + umbral_state.json + ABI to tee-node:~/rmbs_cc_demo/share/"
