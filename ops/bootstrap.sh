#!/usr/bin/env bash
# Owner: idempotent ensure-provisioned. Each mutating step is guarded by a probe in
# provision_checks.py and SKIPS when already satisfied (spec §2/§7). On a fully
# provisioned repo this changes nothing on disk or chain.
# Prereq: 'make infra-up' done + chain/TEE tunnels open.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
activate_venv
FORGE="${FORGE:-$HOME/.foundry/bin/forge}"

wait_for "chain RPC" 30 \
  curl -sf -X POST -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' "$RPC_URL"
wait_for "TEE service" 30 curl -sf "${TEE_URL%/}/tee_address"

# 1) Oracle identities. We never auto-generate over an existing set (that would orphan the
#    deployed contract's registered oracles); if missing, stop with instructions.
if python provision_checks.py oracle-keys; then
  log "oracle keys present — skip generation"
else
  die "ORACLE_ADDRESSES/ORACLE_KEYS missing or unequal length. Generate n keys with
       'cast wallet new' (x4), then set ORACLE_ADDRESSES and ORACLE_KEYS in .env, and re-run."
fi

# 2) Contract. Deploy only if not already provisioned on-chain.
if python provision_checks.py contract; then
  log "contract already provisioned on-chain — skip deploy"
else
  log "deploying ConfidentialCompute..."
  ( cd "$ROOT" && "$FORGE" build >/dev/null )
  ( cd "$ROOT" && "$FORGE" script script/Deploy.s.sol:Deploy \
      --rpc-url "$RPC_URL" --broadcast --legacy ) | tee "$RUN_DIR/deploy.log"
  ADDR="$(grep -oE 'deployed at: 0x[0-9a-fA-F]{40}' "$RUN_DIR/deploy.log" \
          | grep -oE '0x[0-9a-fA-F]{40}' | tail -1 || true)"
  [ -n "$ADDR" ] || die "could not parse deployed address from .run/deploy.log"
  # We just deployed, so force-write the new address (only reached when not provisioned).
  python config_env.py set --force --into "$ROOT/.env" "CONTRACT_ADDRESS=$ADDR"
  log "deployed at $ADDR (written to .env, backup made)"
  load_env
fi

# 3) Umbral keygen. Skip when kd/umbral_state.json already matches the live enclave key.
if python provision_checks.py umbral; then
  log "umbral state matches the live enclave key — skip keygen"
else
  log "running keygen (shares=${UMBRAL_SHARES:-3}, threshold=${UMBRAL_THRESHOLD:-2})..."
  python keygen.py --shares "${UMBRAL_SHARES:-3}" --threshold "${UMBRAL_THRESHOLD:-2}"
  warn "keygen produced new kfrags — run 'make publish-config' to push umbral_state.json to the TEE."
fi

# 4) Fund oracles. provision_checks prints under-funded addresses (and exits 1) when any.
UNDER="$(python provision_checks.py funded || true)"
if [ -z "$UNDER" ]; then
  log "all oracles funded — skip"
else
  log "funding under-funded oracles: $UNDER"
  # shellcheck disable=SC2086
  python fund_oracles.py $UNDER
fi

log "bootstrap complete."
