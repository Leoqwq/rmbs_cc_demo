#!/usr/bin/env bash
# Owner: build the ABI and push the member bundle (members.env + umbral_state.json + ABI)
# to the shared tee-node so teammates can 'make sync'.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
FORGE="${FORGE:-$HOME/.foundry/bin/forge}"
ABI="$ROOT/out/ConfidentialCompute.sol/ConfidentialCompute.json"

wait_for_ssh tee-node "$ZONE_A"
log "building ABI for the member bundle..."
( cd "$ROOT" && "$FORGE" build >/dev/null )
[ -f "$ABI" ] || die "ABI not found at $ABI after forge build"

[ -f "$ROOT/kd/umbral_state.json" ] || die "kd/umbral_state.json not found — run 'make bootstrap' first (step 3 runs keygen)"

# Absolute, user-independent share dir (so a teammate's 'make sync' reads the same path the
# owner wrote — a `~` path would point at different homes). Owner-owned + dir-traversable.
log "preparing $SHARE_DIR on tee-node ..."
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='sudo mkdir -p /opt/rmbs-share && sudo chown "$USER":"$(id -gn)" /opt/rmbs-share && sudo chmod 755 /opt/rmbs-share'

gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/.env" tee-node:"$SHARE_DIR/members.env"
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/kd/umbral_state.json" tee-node:"$SHARE_DIR/umbral_state.json"
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ABI" tee-node:"$SHARE_DIR/ConfidentialCompute.json"

# Make the bundle readable by other SSH users (teammates scp it down). On a private demo VM
# this exposes the shared deployer/oracle keys to anyone who can SSH — accepted (FUTURE_WORK).
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='chmod 644 /opt/rmbs-share/members.env /opt/rmbs-share/umbral_state.json /opt/rmbs-share/ConfidentialCompute.json'
log "published members.env + umbral_state.json + ABI to tee-node:$SHARE_DIR/"
