#!/usr/bin/env bash
# Show tracked processes (.run/*.pid) and chain/TEE reachability.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

shopt -s nullglob
pids=("$RUN_DIR"/*.pid)
if [ ${#pids[@]} -eq 0 ]; then
  log "no tracked processes (.run empty)"
else
  for f in "${pids[@]}"; do
    name="$(basename "$f" .pid)"; pid="$(cat "$f")"
    if kill -0 "$pid" 2>/dev/null; then echo "RUNNING  $name (pid $pid)"
    else echo "DEAD     $name (stale pidfile)"; fi
  done
fi

curl -sf http://127.0.0.1:8000/tee_address >/dev/null 2>&1 \
  && echo "TEE      reachable" || echo "TEE      unreachable"
curl -sf -X POST -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
  http://127.0.0.1:8545 >/dev/null 2>&1 \
  && echo "chain    reachable" || echo "chain    unreachable"
