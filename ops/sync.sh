#!/usr/bin/env bash
# Teammate one-time-per-machine: pull the shared bundle from tee-node, merge it into the
# local .env (backing up any existing one), drop the ABI + umbral state into place, then
# run doctor. Does NOT provision anything — teammates join the existing deployment.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
activate_venv
mkdir -p "$ROOT/kd" "$ROOT/out/ConfidentialCompute.sol"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

log "pulling shared config from tee-node:~/rmbs_cc_demo/share/ ..."
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  tee-node:~/rmbs_cc_demo/share/members.env "$TMP/members.env"
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  tee-node:~/rmbs_cc_demo/share/umbral_state.json "$TMP/umbral_state.json"
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  tee-node:~/rmbs_cc_demo/share/ConfidentialCompute.json "$TMP/ConfidentialCompute.json"

cp "$TMP/umbral_state.json" "$ROOT/kd/umbral_state.json"
cp "$TMP/ConfidentialCompute.json" "$ROOT/out/ConfidentialCompute.sol/ConfidentialCompute.json"

# Shared values are the source of truth -> --force, but config_env still backs up .env first.
python config_env.py merge --from "$TMP/members.env" --into "$ROOT/.env" --force
log "config merged into .env (backup written). Checking tooling + config..."
# config-only: sync runs BEFORE 'make up', so TEE/decryption-node reachability can't pass
# yet — checking them here would always false-alarm. 'make doctor' (full) is for after up.
python doctor.py --config-only \
  && log "sync complete — config looks good. Next: 'make up', then 'make doctor'." \
  || warn "config check found problems above — fix them before 'make up'."
