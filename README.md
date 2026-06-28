# rmbs_cc_demo — RMBS Confidential Compute (Waterfall) Demo

Confidential-compute pipeline following the Chainlink Confidential Compute
white-paper response path: a user **encrypts** one period of RMBS cashflows and
submits the ciphertext to a contract on the 6-node Besu/QBFT private chain; a
**decryption DON** re-encrypts the inputs to the TEE enclave; an oracle DON (N
independent agents) gathers the re-encryption fragments, calls the TEE which
decrypts in-enclave, runs the waterfall, and signs a request-bound attestation;
each oracle verifies that attestation and posts its own; the contract finalizes
once m-of-n oracle attestations are recorded. Inputs stay encrypted on-chain
throughout.

The Besu chain orders transactions (the ledger layer). The oracle DON provides
m-of-n attested relay — removing the single-orchestrator trust and liveness SPOF.
**Encryption is implemented** (pyUmbral threshold proxy re-encryption): inputs are
encrypted under the decryption DON's master key, re-encrypted to the enclave's
receiving key by the decryption nodes, and decrypted only inside the TEE — the
boundary lives in `tee/encryption_seam.py`. Remaining deliberate simplifications
are tracked in `docs/FUTURE_WORK.md`.

Running it is automated via `make` (see **Quick start** below; `make help` lists every
target). See `docs/superpowers/specs/2026-06-16-encryption-decryption-don-design.md` for the
encryption / decryption-DON design (and `…/2026-06-03-rmbs-cc-waterfall-demo-design.md`
for the base waterfall demo), `private_chain/TEE.md` (in the RMBS vault) for the TEE VM,
and **`docs/TROUBLESHOOTING.md`** for operational gotchas + troubleshooting.

## Prerequisites
- Local: Python 3.10+ and a working `.venv` (see Setup); an authenticated `gcloud` with
  IAP access. Foundry (`forge`) is needed only by the infra **owner** (deploy/build);
  teammates who `make sync` an existing deployment do not need it.
- `tee-node` (Ubuntu, owner one-time): `sudo apt-get install -y python3-venv python3-pip tmux`.
- The Besu chain and the `tee-node` confidential VM started (both are stopped by
  default to control cost) — `make infra-up` does this.

## Setup

Everyone — clone the repo, then create the Python environment:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Everyone also needs the Google Cloud CLI, authenticated as the account the owner granted
access to (first time using gcloud):
```bash
# 1. Install the CLI — macOS: `brew install --cask gcloud-cli`
#    others: https://cloud.google.com/sdk/docs/install
gcloud --version                 # confirm it's installed

# 2. Log in (opens a browser)
gcloud auth login

# 3. (optional) make it your default project — the ops scripts pin it regardless
gcloud config set project rmbs-495107

# 4. Smoke-test access — should list the project's VMs (they may be STOPPED)
gcloud compute instances list --project=rmbs-495107
```
The owner must have granted your Google account: a start/stop-instances role (or *Compute
Instance Admin*), *Compute OS Login*, *IAP-secured Tunnel User*, and *Service Account User*.
Your first `gcloud compute ssh`/`scp` (run by `make sync` / `make up`) auto-provisions your
OS Login user and generates an SSH key — no manual key setup needed.

**Teammates stop here.** `make sync` populates `.env` (plus the contract ABI and umbral
state) from the shared deployment — do **not** create or hand-fill `.env`, and you don't
need Foundry.

**Owner only**, for a fresh/standalone deployment (teammates skip this): install Foundry,
then
```bash
cp .env.example .env   # fill in DEPLOYER_PRIVATE_KEY etc. (see the comments in the file)
forge install foundry-rs/forge-std
forge build
```
followed by `make tee-install` → `make bootstrap` → `make publish-config` (see Quick start).

## Quick start (make)

Teammates sharing the existing cloud deployment. Prerequisites: a working `.venv` (see Setup
above if missing); an authenticated `gcloud` (`gcloud auth login` — the ops scripts pin the
GCP project, so you don't need to set it); and IAM to **start/stop the instances, open IAP
tunnels, and SSH to `tee-node`** (`make sync`/`make up` scp from / port-forward to it — if the
project enforces OS Login you also need `roles/compute.osLogin`). The shared VMs are stopped by
default to save cost, so a session bookends with `infra-up` / `infra-down` (the TEE auto-starts
on boot, so no SSH is needed to bring it up):
```bash
make sync       # one-time per machine: pull shared config + ABI + umbral state, run doctor
make infra-up   # start the shared cloud VMs (wait ~1 min; the TEE auto-starts on boot)
make up         # open tunnels, start decryption nodes + oracle agents (health-gated)
make demo       # submit a request and read the finalized result
make down       # stop local processes (tunnels/nodes/agents)
make infra-down # stop the shared cloud VMs when done (cost control)

# --- auxiliary commands (run any time, not part of the fixed flow) ---
make help          # list every target with a one-line description
make doctor        # preflight checks (after `make up`): gcloud / .env / RPC / TEE / nodes
make status        # show tracked local processes + chain/TEE reachability
make result ID=10  # read a finalized result back from the chain by request id
```
Run the demo one person at a time — the VMs and oracle keys are shared.

One-time **owner** setup, before teammates can `sync`/run: `make tee-install` (install the
`rmbs-tee` systemd service on `tee-node`), `make bootstrap` (deploy contract, keygen, fund —
idempotent), `make publish-config` (push the member config bundle). Updating TEE code later:
`make tee-deploy` (then `make tee-restart` / `make tee-logs`). `make help` lists every target;
operational gotchas + troubleshooting live in `docs/TROUBLESHOOTING.md`.

### What each step does
`make up` opens the two IAP tunnels (chain RPC + TEE, bound to `127.0.0.1`), gates on chain
+ TEE health, then starts the decryption-DON nodes and the oracle-DON agents locally (one
per `ORACLE_KEYS` entry), tracking PIDs/logs in `.run/`. `make demo` encrypts the inputs
client-side, submits the ciphertext (plaintext never goes on-chain), waits for the m-of-n
oracle quorum, prints the result, and archives it to `demo-results/`. `make result ID=N`
reads any finalized result back from the chain. The owner's `make bootstrap` performs the
one-time provisioning (deploy, keygen, oracle funding) and is a no-op once done; the
mechanics of each are in the design spec referenced above.

## Verify
The on-chain `resultJson` must equal a local run of the engine on the same
inputs:
```bash
python -m pytest tests/ -v   # encodes the expected numbers
```
For IAF=500000 / PAF=1000000 the result is ClassA=79,000,000, ClassB=15,000,000,
ClassC=5,000,000, IAF remaining 70,833.33.

## Robustness
The **consensus/ledger layer** is the Besu QBFT network: with 4 validators it
tolerates 1 being offline (needs 3 of 4 to keep producing blocks). The oracle DON
and CLIs add hardening features so a single node/tunnel outage doesn't stall the
compute path:

- **Oracle DON m-of-n** — with n=4 oracles and threshold m=3, the DON tolerates 1
  oracle offline while still finalizing results. Each oracle is fully independent;
  there is no leader/aggregator single point of failure.
- **RPC failover** — set `RPC_URLS` to several validator tunnels (one IAP tunnel
  per validator on distinct local ports, e.g. `validator-1→8545`,
  `validator-2→8546`). Each oracle agent and the CLIs connect to the first
  reachable endpoint and fail over on transport errors.
  ```bash
  gcloud compute start-iap-tunnel validator-1 8545 --local-host-port=127.0.0.1:8545 --zone=us-central1-a
  gcloud compute start-iap-tunnel validator-2 8545 --local-host-port=127.0.0.1:8546 --zone=us-central1-b
  gcloud compute start-iap-tunnel validator-3 8545 --local-host-port=127.0.0.1:8547 --zone=us-central1-c
  ```
- **Idempotent + resumable agents** — each oracle agent persists its progress
  (last scanned block + attested request ids) to `oracle_state_<id>.json`. On
  restart it resumes from the last block and checks `hasAttested` and `getResult`
  on-chain before acting, so attestations are never duplicated and a request that
  fails (TEE down, reverted tx) is retried on the next poll rather than lost.
- **Decryption DON m-of-n** — inputs are threshold-re-encrypted (pyUmbral); each
  oracle gathers cfrags from the decryption nodes and `verify_cfrags` drops any
  corrupt/forged fragment. With `--shares 3 --threshold 2` the re-encryption layer
  tolerates 1 bad-or-offline decryption node; below threshold the agent stalls
  safely and self-heals once a node returns. Decryption happens only inside the
  TEE (boundary in `tee/encryption_seam.py`).

Not yet redundant: the **TEE itself is a single node**. Surviving a TEE outage
needs multiple TEEs + quorum (whitepaper's compute-enclave pool) — a separate,
cloud-cost-incurring step.

## Cost
Stop the chain and `tee-node` when done: `make infra-down` (and `make down` to stop the
local tunnels/nodes/agents first).
