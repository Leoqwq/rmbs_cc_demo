#!/usr/bin/env bash
# Owner: push updated TEE *code* (.py only) to tee-node and restart the service.
#
# SAFETY (do not "simplify" this): it copies ONLY .py files and NEVER tee/kd/. Copying
# tee/kd/ (e.g. via `scp --recurse tee/`) overwrites the remote signing + enclave keys,
# which changes TEE_ADDRESS and forces a contract redeploy + re-keygen. The globs below are
# non-recursive, so `tee/*.py` cannot match anything under tee/kd/.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

wait_for_ssh tee-node "$ZONE_A"
log "pushing tee/*.py + tee/engine/*.py + abi_digest.py/umbral_io.py (NOT tee/kd/) ..."
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT"/tee/*.py tee-node:~/rmbs_cc_demo/tee/
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT"/tee/engine/*.py tee-node:~/rmbs_cc_demo/tee/engine/
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/abi_digest.py" "$ROOT/umbral_io.py" tee-node:~/rmbs_cc_demo/

log "restarting rmbs-tee ..."
# Poll for readiness ON tee-node (the service can take a few seconds to bind after restart;
# a hard `sleep 2` would falsely fail a slow start). 127.0.0.1:8000 is local to the node.
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='sudo systemctl restart rmbs-tee && for _ in $(seq 1 15); do curl -sf http://127.0.0.1:8000/tee_address && echo && exit 0; sleep 2; done; echo "TEE not ready after restart — check: make tee-logs"; exit 1'

log "tee-deploy done (tee/kd/ untouched). If requirements.txt changed, pip install on tee-node first."
