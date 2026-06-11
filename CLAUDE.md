# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo of **decentralized confidential compute** modeled on the Chainlink Confidential
Compute white paper (Figure 1), specialized to run an **RMBS waterfall** calculation
inside a TEE. A user submits plaintext cashflows to an on-chain contract; a decentralized
**oracle DON** fetches the result from the TEE, verifies the enclave's signature, and
finalizes it on-chain only after an **m-of-n quorum** of oracle attestations.

**Deliberate simplifications (important context):**
- **Encryption is omitted** on purpose. Data is plaintext end-to-end; there is no
  decryption DON yet. The boundary where it will plug in is `tee/encryption_seam.py`
  (`decrypt_inputs`, currently identity). Do not "add encryption" unless asked.
- **TEE attestation is an ECDSA signature**, not real SEV-SNP hardware attestation. The
  TEE holds a plain signing key; the contract trusts its address. Real attestation is a
  recorded future task — see `docs/FUTURE_WORK.md` #1.
- Everything except encryption is meant to follow the white-paper workflow faithfully.

## Commands

Python side (run from repo root, venv at `.venv`):
```bash
source .venv/bin/activate
python -m pytest tests/ -q                      # all Python tests (offline)
python -m pytest tests/test_oracle_don.py -v    # one file
python -m pytest tests/test_tee_compute.py::test_compute_endpoint_matches_pure_function_and_verifies_signature -v  # one test
python -m tee.tee_service                        # run the TEE service (binds 0.0.0.0:8000)
```

Solidity side (Foundry; `forge` at `~/.foundry/bin/forge`):
```bash
forge build
forge test -vv                                   # contract tests
forge test --match-test test_QuorumFinalizesAtThreshold -vv   # one test
```

There is no separate linter/build for Python; the offline test suites (`pytest tests/`
= 17 tests, `forge test` = 5 tests) are the gate. Most components require the live Besu
chain + TEE VM and are exercised manually — **`RUNBOOK.md` is the authoritative
operational guide** (it tags every step as first-time-only vs every-run and has a
troubleshooting section).

## Architecture (big picture)

Data/trust flow (request id is the unit of work):
```
submit_request.py → ConfidentialCompute.submitRequest() → ComputeRequested event
  → N oracle_agent.py (each independently): GET TEE /compute(id,...) → verify TEE sig
    → sign (id,resultHash) → attest()
  → contract accumulates m-of-n distinct oracle sigs (+ one valid TEE sig) → finalized
  → read_result.py / getResult()  (third party can verify)
```

Component map:
- `contracts/ConfidentialCompute.sol` — the "application". Stores requests, holds the
  oracle registry + `threshold` (m-of-n), and `attest(id, resultHash, resultJson, teeSig,
  oracleSig)`. First valid attest records the result and verifies the **request-bound**
  TEE signature; each call adds one unique oracle signature; finalizes at `threshold`.
  Self-contained `ecrecover` (no OpenZeppelin).
- `tee/` — the compute enclave (FastAPI). `tee_service.py` `/compute` runs the waterfall
  and signs the result. `compute.py` is a **pure** function (`compute_waterfall`) — the
  on-host ground truth used by tests. `sample_deal.py` is the built-in deal. `engine/` is
  **vendored verbatim** from `rmbs_platform/engine/` (5 files: loader, state, compute,
  audit_trail, waterfall) — do not edit it to "fix" waterfall behavior; it mirrors upstream.
- `oracle_agent.py` — one DON node. Run N instances, each with its own `ORACLE_ID` +
  `ORACLE_KEY`. Watches events, calls the TEE, **verifies the TEE signature before
  attesting**, persists progress to `oracle_state_<id>.json` for idempotent resume. (It
  replaced the earlier single `orchestrator.py`, which is gone.)
- `chain.py` — `ResilientChain` (multi-RPC failover; `RPC_URLS` then `RPC_URL`) reused by
  the agent and CLIs.
- `submit_request.py` / `read_result.py` / `fund_oracles.py` — user CLIs.

### The single most fragile thing: the cross-language signing seam

`abi_digest.py` (Python, `eth_abi.encode`) **must** byte-match the contract's `abi.encode`:
- `tee_digest(id, dealId, period, iaf, paf, resultHash)` ↔ `keccak256(abi.encode(id,
  r.dealId, r.period, r.iaf, r.paf, resultHash))` — types `uint256,string,uint256,uint256,
  uint256,bytes32`, in that order. This **binds a result to its exact request/inputs**.
- `oracle_digest(id, resultHash)` ↔ `keccak256(abi.encode(id, resultHash))`.
- Both sign/verify with the EIP-191 personal-sign prefix (`encode_defunct` ↔
  `"\x19Ethereum Signed Message:\n32"`), signature layout `r||s||v`.
- The on-chain `resultJson` is the **canonical JSON** (`json.dumps(sort_keys=True,
  separators=(",",":"))`); the contract checks `keccak256(bytes(resultJson)) == resultHash`,
  so the agent must re-serialize identically (it does).

If you change a digest's field order/types on one side, change the other and the Forge
sign helpers, or signatures silently fail to verify on-chain. No single automated test
crosses both languages — verify by reasoning + the Forge tests + a local hash compare.

## Environment facts that bite (see RUNBOOK troubleshooting)

- The Besu chain is **not gas-free** (validators don't set `--min-gas-price=0`). Never use
  `--gas-price 0`; forge uses `--legacy`, Python uses `w3.eth.gas_price`.
- Use `127.0.0.1`, never `localhost`, in RPC/TEE URLs and IAP tunnels (Besu host-allowlist
  403; IPv6 `::1` vs IPv4 on the TEE forward).
- Each oracle account sends its own `attest` tx, so **new oracle accounts must be funded**
  (`fund_oracles.py`) or attest txs sit pending and agents loop on `-32000 Known transaction`.
- After editing `.env`, re-run `set -a; source .env; set +a` (and Python `load_dotenv`
  does not override already-exported vars).
- QBFT needs **≥3 of 4 validators online** to produce blocks. An oracle agent is a local
  process unrelated to any validator node — killing a validator tests chain fault
  tolerance; Ctrl-C'ing an agent tests DON fault tolerance.

## Conventions

- Work is committed directly on `main` (no feature branches for this repo). Commit/push
  only when asked.
- Design specs and implementation plans live under `docs/superpowers/{specs,plans}/`;
  deferred work is tracked in `docs/FUTURE_WORK.md`.
