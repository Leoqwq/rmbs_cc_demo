# Troubleshooting & operational gotchas

The day-to-day flow is the `make` targets (`make help`); this file is the "why did
that break" reference. It distills the hard-won gotchas from running the demo end to
end. Most failures are environment/ordering issues, not code bugs.

**First moves when something is wrong:**
- `make status` — what local processes + tunnels are alive, and chain/TEE reachability.
- `make doctor` — preflight: gcloud auth, `.env` completeness, RPC/TEE/decryption-node reachability.
- `.run/*.log` — live logs of the tunnels, decryption nodes, and oracle agents (`make up`
  launches them with `PYTHONUNBUFFERED=1`, so progress shows immediately).

---

## Chain / consensus

- **Chain not producing blocks / txs stay pending** → fewer than 3 of the 4 validators are
  online. QBFT needs **≥3 of 4** to produce blocks. Check status:
  `gcloud compute instances describe <validator> --zone=<zone> --format="value(status)"`.
- **`ZONE_RESOURCE_POOL_EXHAUSTED` when starting `validator-3` (us-central1-c)** → GCP is
  out of capacity for that VM type in that zone (transient). **Not fatal**: `make infra-up`
  warns and continues, and the chain still runs on the other 3 validators (that's QBFT's
  1-of-4 fault tolerance). Re-run `make infra-up` later to pull `validator-3` back in.
- **`-32009 Gas price below minimum` / tx rejected** → the chain is **not** gas-free
  (validators didn't set `--min-gas-price=0`). Never pass `--gas-price 0`; `forge` uses
  `--legacy`, the Python scripts use `w3.eth.gas_price`.

## Oracle DON / attestations

- **`make result`/`read_result` stays empty, agent loops `-32000 Known transaction`** →
  the oracle account has **no gas**. Its `attest` tx sits in the mempool and the agent
  re-sends it. Fix: `make bootstrap` (tops up under-funded oracles) or `python fund_oracles.py`;
  confirm with `cast balance <oracle> --rpc-url $RPC_URL`. The agent self-heals once funded.
- **One oracle never prints `attested ok`** → normal m-of-n behaviour: once the first `m`
  oracles reach quorum, the extras find the request already finalized and skip.
- **`finalized=False` right after submit** → expected; finalization is asynchronous (agents
  poll, collect cfrags, call the TEE, then attest ×m). `make demo` already waits for this.
- **Agent log shows `only k/m valid cfrags`** → fewer than `threshold` decryption nodes are
  online (or one was started with `CORRUPTED=1`). Confirm the decryption nodes are up
  (`make status`), then retry.

## TEE / enclave

- **TEE unreachable at `127.0.0.1:8000`** → the TEE tunnel isn't open. `make up` opens it;
  `make sync`/`make doctor` do **not**. The TEE itself runs on `tee-node` as the `rmbs-tee`
  systemd service (auto-starts on boot — see the service bullets below).
- **`[4003] Failed to connect to port 22`** running an owner `make tee-install`/`tee-deploy`/
  `tee-restart`/`tee-logs` → a freshly-started `tee-node` isn't SSH-ready for ~30–60s after
  boot. Wait ~30s and re-run. (`make infra-up` no longer SSHes to `tee-node`, so it isn't
  affected.)
- **`bad TEE sig` / TEE signature verification fails** → most often the enclave receiving
  key was regenerated but `keygen` wasn't re-run, so the kfrags no longer match the enclave
  pubkey. Re-run `make bootstrap` (it re-keygens when the umbral state no longer matches the
  live enclave key), then `make publish-config` to push the new state to the TEE.
- **`No such file …/kd/umbral_state.json`** → either (local) `keygen` hasn't run, or (on the
  TEE node, path under `/home/<user>/…`) the state wasn't pushed. `make bootstrap` +
  `make publish-config` handle both.
- **TEE unreachable but tunnel is open** → the `rmbs-tee` service may be down on `tee-node`.
  Check `make tee-logs` (or `sudo systemctl status rmbs-tee` on the node); `make tee-restart`
  to bounce it. The service auto-starts on boot; `make infra-up` no longer starts it over SSH,
  so a member who can start the VM no longer needs SSH access to bring the TEE up.
- **Updating TEE code** → edit `tee/*.py` (or `abi_digest.py`/`umbral_io.py`) locally, then
  `make tee-deploy` (pushes `.py` only + restarts). **Never** `scp --recurse tee/` or copy
  `tee/kd/` to the node — that overwrites the remote signing/enclave keys, changing
  `TEE_ADDRESS` and forcing a contract redeploy. If `requirements.txt` changed, `pip install`
  on `tee-node` first, then `make tee-restart`.

## Networking / tunnels

- **`403 Host not authorized` (RPC) or TEE `Connection refused`** → use `127.0.0.1`, never
  `localhost`: the Besu RPC host-allowlist only permits `127.0.0.1`, and `localhost` can
  resolve to IPv6 `::1` while uvicorn listens on IPv4. The `ops/` scripts already bind
  `127.0.0.1` everywhere.
- **RPC failover** — set `RPC_URLS` to several validator tunnels (one IAP tunnel per
  validator on distinct local ports); each agent/CLI uses the first reachable and fails over.

## Local environment

- **`ModuleNotFoundError: No module named 'web3'`** running a bare script → the shell is on
  the wrong Python (e.g. conda `(base)`), not the project `.venv`. Either `source
  .venv/bin/activate` first, run `.venv/bin/python <script>.py`, or use the `make` targets
  (they activate the venv themselves — e.g. `make result ID=N` instead of `python read_result.py N`).
- **decryption node `address already in use` on port 5000** → macOS AirPlay occupies 5000.
  The default base port is **5005** (`DEC_BASE_PORT` in `.env`); `DECRYPTION_NODE_URLS`
  must match.

## Node lifecycle edge cases (rare)

- **`tee-node` "looks wiped" (venv/code/keys gone) after only stop/start** → almost always
  **OS Login** got enabled and the SSH username/home changed, so `~` points at a new empty
  home (old files are still on disk, just under the old home). Disk-persistent
  validator/Besu (boot-autostarted) are unaffected. Mitigations: always log in as the same
  user; back up `tee/kd/*.json`; only stop/start `tee-node`, never delete/recreate it.
- **`tee-node` deleted/recreated, or home changed** → the enclave signing key under
  `tee/kd/` is gone, so the TEE starts with a **new** `TEE_ADDRESS`. Update `.env` and
  re-deploy the contract with the new address (`make bootstrap` deploys when the on-chain
  `teeAddress` no longer matches). Backing up `tee/kd/tee_signing_key.json` +
  `enclave_enc_key.json` and restoring them avoids this entirely.

## The two independent thresholds (don't conflate)

- **Umbral re-encryption quorum `m`** — minimum cfrags to decrypt inside the enclave; set by
  `keygen --shares N --threshold m` (`UMBRAL_SHARES`/`UMBRAL_THRESHOLD` in `.env`), written
  into `kd/umbral_state.json`, checked by the TEE/oracles.
- **Oracle attestation quorum** — the contract's `threshold` (`.env THRESHOLD`), the number
  of distinct oracle signatures needed to finalize on-chain.

These are separate mechanisms with separate values (this demo uses umbral m=2 of 3 nodes,
oracle quorum 3 of 4).
