# rmbs_cc_demo — RMBS Confidential Compute (Waterfall) Demo

Plaintext confidential-compute pipeline: a user submits one period of RMBS
cashflows to a contract on the 6-node Besu/QBFT private chain; an orchestrator
forwards the request to a TEE that runs the `rmbs_platform` waterfall engine;
the TEE-signed result is written back on-chain. Encryption is intentionally
omitted — the goal is to prove confidential compute can run the waterfall.

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
3. **Deploy the contract** (uses `TEE_ADDRESS`, `DEPLOYER_PRIVATE_KEY`). Put the
   printed address in `.env` as `CONTRACT_ADDRESS`:
   ```bash
   set -a; source .env; set +a     # export so forge sees the vars
   forge script script/Deploy.s.sol:Deploy --rpc-url "$RPC_URL" --broadcast --legacy
   ```
   > The chain is not actually gas-free (validators didn't set `--min-gas-price=0`),
   > so do NOT pass `--gas-price 0`; forge legacy uses the node's price. The Python
   > scripts likewise use `w3.eth.gas_price`.
4. **Orchestrator**:
   ```bash
   python orchestrator.py
   ```
5. **Submit a request** and read the result:
   ```bash
   python submit_request.py --iaf 500000 --paf 1000000
   python read_result.py 1
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
tolerates 1 being offline (needs 3 of 4 to keep producing blocks). The
orchestrator adds two local hardening features so a single node/tunnel outage
doesn't stall the compute path either:

- **RPC failover** — set `RPC_URLS` to several validator tunnels (one IAP tunnel
  per validator on distinct local ports, e.g. `validator-1→8545`,
  `validator-2→8546`). The orchestrator and CLIs connect to the first reachable
  one and fail over on transport errors.
  ```bash
  gcloud compute start-iap-tunnel validator-1 8545 --local-host-port=127.0.0.1:8545 --zone=us-central1-a
  gcloud compute start-iap-tunnel validator-2 8545 --local-host-port=127.0.0.1:8546 --zone=us-central1-b
  gcloud compute start-iap-tunnel validator-3 8545 --local-host-port=127.0.0.1:8547 --zone=us-central1-c
  ```
- **Idempotent + resumable orchestrator** — progress (last scanned block +
  completed request ids) is persisted to `orchestrator_state.json`. On restart it
  resumes from the last block and re-checks each request's on-chain `getResult()`
  before acting, so requests are never computed/posted twice and a request that
  fails (TEE down, reverted tx) is retried on the next poll rather than lost.

Not yet redundant: the **TEE itself is a single node**. Surviving a TEE outage
needs multiple TEEs + quorum (whitepaper's compute-enclave pool) — a separate,
cloud-cost-incurring step.

## Cost
Stop the chain and `tee-node` when done (`gcloud compute instances stop ...`).
