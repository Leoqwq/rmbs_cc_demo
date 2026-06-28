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

> **Two audiences.** Everything from here through **Robustness** is the **teammate guide** —
> what you need to run the demo against the already-deployed shared infrastructure. Standing
> up that infrastructure, onboarding teammates, and operating the TEE are collected in the
> **[Owner](#owner--managing-the-shared-deployment)** section at the end; teammates can ignore it.

---

# Teammate guide

## Prerequisites
- Python 3.10+ (Setup creates a `.venv`).
- macOS or Linux — the tooling is Bash + `make` + `gcloud` (on Windows, use WSL).
- The Google Cloud CLI, authenticated (Setup), with IAM the **owner** has granted you:
  start/stop the shared instances, open IAP tunnels, and SSH to `tee-node`.
- You do **not** need Foundry, and you do **not** hand-edit `.env` — `make sync` pulls the
  contract ABI and the full config from the shared deployment.

## Setup

`cd` into the cloned repo (every `make` / `python` command runs from the repo root), then
create the Python environment:
```bash
cd rmbs_cc_demo                      # the demo repo root
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Install + authenticate the Google Cloud CLI (first time using gcloud):
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
The owner must have granted your Google account the roles listed under
[Owner → Onboard a teammate](#onboard-a-teammate-iam). Your first `gcloud compute ssh`/`scp`
(run by `make sync` / `make up`) auto-provisions your OS Login user and generates an SSH key —
no manual key setup needed. `make sync` then populates `.env` + ABI + umbral state from the
shared deployment, so you never create or hand-fill `.env`.

## Quick start (make)

The shared VMs are stopped by default to save cost, so a session bookends with `infra-up` /
`infra-down` (the TEE auto-starts on boot, so no SSH is needed to bring it up):
```bash
make infra-up   # start the shared cloud VMs (incl. tee-node); wait ~1 min for them to boot
make sync       # FIRST run on this machine only: pull config + ABI + umbral state from tee-node
make up         # open tunnels, start decryption nodes + oracle agents (health-gated)
make demo       # submit a request and read the finalized result
make down       # stop local processes (tunnels/nodes/agents)
make infra-down # stop the shared cloud VMs when done (cost control)

# --- auxiliary commands (run any time, not part of the fixed flow) ---
make help          # list every target with a one-line description
make doctor        # preflight checks (after `make up`): gcloud / .env / RPC / TEE / nodes
make status        # show tracked local processes + chain/TEE reachability
make result ID=N   # read a finalized result back from the chain by request id (N)
```
`make sync` is one-time per machine and **must run after `infra-up`** (it pulls from
`tee-node`, which has to be running); later sessions are just `infra-up → up → demo → down →
infra-down`. Run every `make` command from the repo root, and run the demo one person at a
time — the VMs and oracle keys are shared.

### What each step does
- **`make up`** opens the two IAP tunnels (chain RPC + TEE, bound to `127.0.0.1`), gates on
  chain and TEE health, then starts the decryption-DON nodes and the oracle-DON agents
  locally (one per `ORACLE_KEYS` entry), tracking PIDs/logs in `.run/`.
- **`make demo`** encrypts the inputs client-side, submits the ciphertext (plaintext never
  goes on-chain), waits for the m-of-n oracle quorum, prints the result, and archives it to
  `demo-results/`.
- **`make result ID=N`** reads any finalized result back from the chain.

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
needs multiple TEEs + quorum (the white-paper's compute-enclave pool) — a separate,
cloud-cost-incurring step.

---

# Owner — managing the shared deployment

**Teammates can skip this section.** It covers standing up the shared infrastructure,
onboarding teammates, and operating the TEE. Everything here needs Foundry locally and sudo
on `tee-node`; the owner's `.env` is the source of truth (its secrets are distributed to
teammates by `make publish-config` → `make sync`).

## Stand up a fresh / standalone deployment
First-time `tee-node` provisioning is a one-time manual step on the node: `sudo apt-get
install -y python3-venv python3-pip tmux`, create the venv in `~/rmbs_cc_demo`, and copy the
repo (`tee/`, `abi_digest.py`, `umbral_io.py`, `requirements.txt`) onto it. Then, locally:
```bash
cp .env.example .env                       # fill DEPLOYER_PRIVATE_KEY etc. (see the comments)
forge install foundry-rs/forge-std && forge build
make infra-up        # start the shared VMs
make tee-install     # install + enable the rmbs-tee systemd service on tee-node (auto-start on boot)
make bootstrap       # deploy contract + keygen + fund oracles (idempotent — no-op once done)
make publish-config  # publish the member bundle to /opt/rmbs-share on tee-node
```
After `publish-config`, teammates can `make sync`. `make bootstrap` is safe to re-run; it
only acts on what's missing/changed and is a no-op otherwise.

## Onboard a teammate (IAM)
Grant the teammate's Google account these **project** roles:
- *Compute OS Login* — SSH into `tee-node` (the project enforces OS Login).
- *IAP-secured Tunnel User* — open IAP tunnels.
- *Service Account User* — `actAs` when starting instances.
- a **start/stop instances** role. Simplest is *Compute Instance Admin (v1)*, but it also
  grants `compute.instances.delete` — i.e. the teammate could delete `tee-node` and lose the
  signing key (`TEE_ADDRESS` would change → contract redeploy). For least privilege, create a
  start/stop-only custom role and grant that instead:
  ```bash
  gcloud iam roles create rmbsInstanceLifecycle --project=rmbs-495107 \
    --title="RMBS Instance Start/Stop" \
    --permissions=compute.instances.start,compute.instances.stop,compute.instances.get,compute.instances.list,compute.zones.get,compute.zones.list
  ```

## Operate / update the TEE
- `make tee-deploy` — push updated `tee/*.py` (+ `abi_digest.py` / `umbral_io.py`) to
  `tee-node` and restart. **Safe by construction:** copies only `.py` files, **never**
  `tee/kd/` (copying the key dir would change `TEE_ADDRESS` and force a contract redeploy).
- `make tee-restart` / `make tee-logs` — restart the service / tail its logs.
- If you re-`bootstrap` in a way that changes shared state (redeploy the contract, or
  regenerate the enclave key), re-run `make publish-config`, then **every teammate must
  `make sync` again** to pick up the new contract address / umbral state.

## Cost
Stop the chain and `tee-node` when done: `make infra-down` (and `make down` to stop the
local tunnels/nodes/agents first).
