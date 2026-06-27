#!/usr/bin/env bash
# Stop everything 'up' started (agents, decryption nodes, both tunnels). Leaves the
# shared cloud instances and the remote TEE untouched.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

for name in oracles decnodes tunnel-tee tunnel-chain; do
  stop_pidfile "$RUN_DIR/$name.pid"
done
log "down complete (shared instances + remote TEE untouched)."
