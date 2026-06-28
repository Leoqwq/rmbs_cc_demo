#!/usr/bin/env bash
# Teammate runtime: open the two tunnels, gate on chain + TEE health, start the
# decryption nodes, gate on them, then start the oracle agents. Shared cloud infra is
# assumed already up (owner ran 'make infra-up'). Logs + pids land in .run/.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
activate_venv
[ -f "$RUN_DIR/oracles.pid" ] && die "already up — run 'make down' first (or delete .run/ if stale)"

warn "Run the demo ONE PERSON AT A TIME — teammates share oracle keys; concurrent agents collide on nonces."

# Ensure tee-node is up + SSH-ready before opening tunnels — the TEE port-forward SSHes into
# it, so a stopped node (forgot 'make infra-up') or one still booting would fail with [4003].
wait_for_ssh tee-node "$ZONE_A"

# 1) Tunnels (chain RPC + TEE port-forward). 127.0.0.1 everywhere (Besu allowlist + IPv4).
start_bg tunnel-chain gcloud compute start-iap-tunnel validator-1 8545 \
  --local-host-port=127.0.0.1:8545 --zone="$ZONE_A"
start_bg tunnel-tee gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  -- -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:8000:127.0.0.1:8000

# 2) Health gates. The chain gate requires real block PRODUCTION (not just an RPC reply), so
#    a sub-quorum chain (<3 validators) is caught here rather than hanging a later submit().
wait_for_blocks http://127.0.0.1:8545 90
wait_for "TEE service" 90 curl -sf http://127.0.0.1:8000/tee_address

# 3) Decryption nodes (BASE_PORT avoids macOS AirPlay on 5000).
start_bg decnodes env PYTHONUNBUFFERED=1 BASE_PORT="${DEC_BASE_PORT:-5005}" python run_decryption_nodes.py
[ -n "${DECRYPTION_NODE_URLS:-}" ] || die "DECRYPTION_NODE_URLS not set in .env — run 'make sync'"
IFS=',' read -ra _NODES <<< "$DECRYPTION_NODE_URLS"
for url in "${_NODES[@]}"; do
  url="${url//[[:space:]]/}"  # strip surrounding whitespace
  wait_for "decryption node $url" 30 curl -sf -o /dev/null "${url%/}/docs"
done

# 4) Oracle agents (one per ORACLE_KEYS entry).
start_bg oracles env PYTHONUNBUFFERED=1 python run_oracle_agents.py

log "up complete. 'make status' to inspect · 'make demo' to run · 'make down' to stop."
