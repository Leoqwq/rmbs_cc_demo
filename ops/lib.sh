#!/usr/bin/env bash
# Shared helpers for the ops/ scripts: env loading, the instance/zone table, the .run/
# PID+log registry, and a bounded health poller. Source this at the top of each ops script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"

# Instance -> zone layout.
ZONE_A="us-central1-a"; ZONE_B="us-central1-b"; ZONE_C="us-central1-c"
INSTANCES_A="bootnode-a validator-1 validator-4 tee-node"
INSTANCES_B="bootnode-b validator-2"
INSTANCES_C="validator-3"

# Pin the GCP project so the ops scripts never depend on the caller's gcloud default — a
# teammate whose active project differs would otherwise misroute every command. Override
# with PROJECT=... if the deployment ever moves.
export CLOUDSDK_CORE_PROJECT="${PROJECT:-rmbs-495107}"

# User-independent share dir on tee-node for the member config bundle (publish-config writes
# it, sync reads it). Absolute on purpose: a `~` path resolves to *different* homes for the
# owner (who publishes) vs a teammate (who syncs), which would break sync for real teammates.
SHARE_DIR="/opt/rmbs-share"

log()  { printf '\033[36m[ops]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[ops]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[ops] %s\033[0m\n' "$*" >&2; exit 1; }

load_env() {
  [ -f "$ROOT/.env" ] || die ".env not found — teammates run 'make sync' first"
  set -a; . "$ROOT/.env"; set +a
}

activate_venv() {
  [ -f "$ROOT/.venv/bin/activate" ] || die ".venv missing — create it (see README Setup)"
  # shellcheck disable=SC1091
  . "$ROOT/.venv/bin/activate"
}

start_bg() {  # start_bg <name> <cmd...>  — run in background, record pid + log
  local name="$1"; shift
  "$@" >"$RUN_DIR/$name.log" 2>&1 &
  echo $! >"$RUN_DIR/$name.pid"
  log "started $name (pid $!) -> .run/$name.log"
}

stop_pidfile() {  # stop_pidfile <path-to-.pid>
  local f="$1" name pid
  [ -f "$f" ] || return 0
  name="$(basename "$f" .pid)"; pid="$(cat "$f")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    log "stopped $name (pid $pid)"
  fi
  rm -f "$f"
}

wait_for() {  # wait_for <desc> <max_secs> <cmd...>  — poll until cmd succeeds or timeout
  local desc="$1" max="$2"; shift 2
  local i=0
  until "$@" >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -ge "$max" ] && die "timeout waiting for $desc (${max}s) — check .run/*.log"
    sleep 1
  done
  log "ready: $desc"
}

wait_for_ssh() {  # wait_for_ssh <instance> <zone> [max_tries=8]  — wait until the VM accepts
                  # SSH over IAP (a freshly-started node isn't SSH-ready for ~30-60s: [4003]).
  local inst="$1" zone="$2" max="${3:-8}" status i=0
  status="$(gcloud compute instances describe "$inst" --zone="$zone" \
            --format='value(status)' 2>/dev/null || true)"
  [ "$status" = "RUNNING" ] \
    || die "$inst is not RUNNING (status: ${status:-unknown}) — run 'make infra-up' first"
  until gcloud compute ssh "$inst" --zone="$zone" --tunnel-through-iap --command=true \
        >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -ge "$max" ] && die "$inst not SSH-ready after $max tries — still booting? wait ~30s and retry"
    warn "$inst not SSH-ready yet (attempt $i/$max, likely still booting) — waiting 15s..."
    sleep 15
  done
  log "$inst SSH-ready."
}

rpc_block() {  # rpc_block <rpc_url>  — echo the current block number (decimal), empty on failure
  local out hex
  out="$(curl -sf -m 5 -X POST -H 'content-type: application/json' \
    --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' "$1" 2>/dev/null || true)"
  hex="$(printf '%s' "$out" | sed -n 's/.*"result":"\(0x[0-9a-fA-F]*\)".*/\1/p')"
  [ -n "$hex" ] && printf '%d' "$hex" || true
}

wait_for_blocks() {  # wait_for_blocks <rpc_url> <max_secs>  — wait until the chain is actually
                     # PRODUCING blocks (number advances), not just answering RPC. A sub-quorum
                     # QBFT chain (<3 of 4 validators) answers RPC but never advances → catching
                     # it here beats a later submit() that hangs forever.
  local url="$1" max="$2" waited=0 b0="" b1
  while [ -z "$b0" ]; do                       # phase 1: RPC reachable (tunnel established)
    b0="$(rpc_block "$url" || true)"
    [ -n "$b0" ] && break
    [ "$waited" -ge "$max" ] && die "chain RPC $url unreachable after ${max}s — is the tunnel open / 'make infra-up' done?"
    sleep 2; waited=$((waited + 2))
  done
  while :; do                                  # phase 2: block number advances
    sleep 3; waited=$((waited + 3))
    b1="$(rpc_block "$url" || true)"
    if [ -n "$b1" ] && [ "$b1" -gt "$b0" ]; then
      log "ready: chain producing blocks ($b0 -> $b1)"; return 0
    fi
    [ "$waited" -ge "$max" ] && die "chain reachable but NOT producing blocks after ${max}s — need >=3 of 4 validators online (see docs/TROUBLESHOOTING.md)"
  done
}
