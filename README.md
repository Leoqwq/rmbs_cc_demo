# rmbs_cc_demo — RMBS Confidential Compute (Waterfall) Demo

Confidential-compute pipeline following the Chainlink Confidential Compute
white-paper response path: a user submits one period of RMBS cashflows to a
contract on the 6-node Besu/QBFT private chain; an oracle DON (N independent
oracle agents, one per validator host) watches for the request, each fetches the
result from the TEE enclave, verifies the enclave's request-bound attestation,
and posts its own on-chain attestation; the contract finalizes the result once
m-of-n oracle attestations are recorded (the DON-attested response).

The Besu chain orders transactions (the ledger layer). The oracle DON is a
software layer co-located on the validator hosts that provides m-of-n attested
relay — removing the single-orchestrator trust and liveness SPOF. Encryption is
intentionally omitted (see `tee/encryption_seam.py` for the future decryption
DON seam) — the goal is to prove the DON attestation flow works end-to-end with
the waterfall engine.

See `docs/superpowers/specs/2026-06-03-rmbs-cc-waterfall-demo-design.md` for the
design, `private_chain/TEE.md` (in the RMBS vault) for the TEE VM, and
**`RUNBOOK.md` for the full, tested step-by-step end-to-end procedure** (start
here if you're actually running the demo).

## Prerequisites
- Local: Foundry (`forge`), Python 3.10+.
- `tee-node` (Ubuntu): `sudo apt-get install -y python3-venv python3-pip` before
  creating the venv there.
- The Besu chain and the `tee-node` confidential VM started (both are stopped by
  default to control cost).

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in DEPLOYER_PRIVATE_KEY, CONTRACT_ADDRESS, TEE_ADDRESS
forge install foundry-rs/forge-std
forge build
```

## Run the demo
Open separate terminals.

1. **Tunnels** (chain RPC + TEE service). Use `127.0.0.1` (not `localhost`): the
   Besu RPC host-allowlist only permits `127.0.0.1`, and forcing IPv4 avoids the
   `::1` connect-refused on the TEE forward.
   ```bash
   gcloud compute start-iap-tunnel validator-1 8545 \
     --local-host-port=127.0.0.1:8545 --zone=us-central1-a
   gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap \
     -- -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 8000:127.0.0.1:8000
   ```
2. **TEE service** — SSH into `tee-node`, then run it under `tmux` (so it survives
   SSH/tunnel drops). Note the printed TEE address and put it in `.env` as
   `TEE_ADDRESS`:
   ```bash
   # from your local machine — open a shell on the confidential VM
   gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap
   # --- now inside tee-node ---
   tmux new -s tee
   cd ~/rmbs_cc_demo && source .venv/bin/activate && python -m tee.tee_service
   # Ctrl-b then d to detach; service keeps running. (First-time node setup:
   # sudo apt-get install -y python3-venv python3-pip tmux; see RUNBOOK.md stage 1.)
   ```
3. **Generate oracle keys** (one per validator host, default n=4 m=3):
   ```bash
   # generate four keys with cast wallet new (or any keygen tool)
   # put the four 0x addresses in .env as ORACLE_ADDRESSES (comma-separated)
   # put THRESHOLD=3 in .env
   ```
4. **Deploy the contract** (uses `TEE_ADDRESS`, `ORACLE_ADDRESSES`, `THRESHOLD`,
   `DEPLOYER_PRIVATE_KEY`). Put the printed address in `.env` as `CONTRACT_ADDRESS`:
   ```bash
   set -a; source .env; set +a     # export so forge sees the vars
   forge script script/Deploy.s.sol:Deploy --rpc-url "$RPC_URL" --broadcast --legacy
   ```
   > The chain is not actually gas-free (validators didn't set `--min-gas-price=0`),
   > so do NOT pass `--gas-price 0`; forge legacy uses the node's price. The Python
   > scripts likewise use `w3.eth.gas_price`.
5. **Fund oracle accounts** (one-time gas top-up from the deployer):
   ```bash
   python fund_oracles.py
   ```
6. **Oracle DON agents** — start one instance per oracle key (ideally one per
   validator host, in the VPC). Each agent watches `ComputeRequested`, calls the
   TEE, verifies the request-bound TEE attestation, and posts `attest()` from its
   own account. Set `ORACLE_ID` and `ORACLE_KEY` differently for each instance:
   ```bash
   # agent 1
   ORACLE_ID=1 ORACLE_KEY=0x<key1> python oracle_agent.py
   # agent 2 (separate shell/host)
   ORACLE_ID=2 ORACLE_KEY=0x<key2> python oracle_agent.py
   # ... (repeat for all N agents)
   ```
   The contract finalizes once `THRESHOLD` distinct oracle attestations are
   recorded. With n=4 m=3, the DON tolerates 1 oracle offline (mirrors QBFT's
   fault tolerance). Per-oracle progress is persisted to `oracle_state_<id>.json`
   so each agent resumes idempotently after a restart.
7. **Submit a request** and read the result:
   ```bash
   python submit_request.py --iaf 500000 --paf 1000000
   python read_result.py 1
   # expected: finalized=True  attestations=3/3 (DON quorum)
   ```

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
- **Encryption seam** — `tee/encryption_seam.py` is the explicit boundary where
  the future decryption DON / re-encryption will plug in. Today it is an identity
  function (plaintext passthrough); upgrading it does not require re-plumbing the
  TEE service or oracle agents.

Not yet redundant: the **TEE itself is a single node**. Surviving a TEE outage
needs multiple TEEs + quorum (whitepaper's compute-enclave pool) — a separate,
cloud-cost-incurring step.

## Cost
Stop the chain and `tee-node` when done (`gcloud compute instances stop ...`).
