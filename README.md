# rmbs_cc_demo — RMBS Confidential Compute (Waterfall) Demo

Plaintext confidential-compute pipeline: a user submits one period of RMBS
cashflows to a contract on the 6-node Besu/QBFT private chain; an orchestrator
forwards the request to a TEE that runs the `rmbs_platform` waterfall engine;
the TEE-signed result is written back on-chain. Encryption is intentionally
omitted — the goal is to prove confidential compute can run the waterfall.

See `docs/superpowers/specs/2026-06-03-rmbs-cc-waterfall-demo-design.md` for the
design and `private_chain/TEE.md` (in the RMBS vault) for the TEE VM.

## Prerequisites
- Foundry (`forge`), Python 3.11+
- The Besu chain and the `tee-node` confidential VM started (both are stopped by
  default to control cost).

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in DEPLOYER_PRIVATE_KEY, CONTRACT_ADDRESS, TEE_ADDRESS
forge install foundry-rs/forge-std --no-commit
forge build
```

## Run the demo
Open separate terminals.

1. **Tunnels** (chain RPC + TEE service):
   ```bash
   gcloud compute start-iap-tunnel validator-1 8545 \
     --local-host-port=localhost:8545 --zone=us-central1-a
   gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap \
     -- -L 8000:localhost:8000
   ```
2. **TEE service** (on tee-node, or locally during dev). Note the printed TEE
   address and put it in `.env` as `TEE_ADDRESS`:
   ```bash
   python -m tee.tee_service
   ```
3. **Deploy the contract** (uses `TEE_ADDRESS`, `DEPLOYER_PRIVATE_KEY`). Put the
   printed address in `.env` as `CONTRACT_ADDRESS`:
   ```bash
   source .env
   forge script script/Deploy.s.sol:Deploy --rpc-url "$RPC_URL" \
     --broadcast --legacy --gas-price 0
   ```
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

## Cost
Stop the chain and `tee-node` when done (`gcloud compute instances stop ...`).
