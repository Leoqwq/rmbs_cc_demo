# RMBS Confidential Compute Waterfall Demo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a plaintext (encryption-omitted) confidential-compute pipeline where a user submits RMBS waterfall inputs to a smart contract on the existing 6-node Besu/QBFT private chain, an orchestrator forwards the request to a TEE service that runs the `rmbs_platform` waterfall engine, and the TEE-signed result is written back on-chain.

**Architecture:** Four cooperating pieces. (1) `ConfidentialCompute.sol` — a minimal application contract that stores compute requests, emits events, and verifies a TEE ECDSA signature before storing results. (2) `tee/` — a FastAPI "compute enclave" that vendors the 5 waterfall-engine files, runs `WaterfallRunner.run_period`, and signs the result. (3) `orchestrator.py` — watches `ComputeRequested` events, calls the TEE, posts the signed result back. (4) `submit_request.py` — the user CLI that submits one period of cashflows. The Besu chain itself is the decentralized-consensus layer; the TEE only computes; the contract never runs the waterfall.

**Tech Stack:** Solidity 0.8.20 + Foundry (forge) for the contract; Python 3.11 + FastAPI/uvicorn + web3.py + eth-account for the TEE and orchestrator; the `rmbs_platform` waterfall engine (vendored, only external dep is `jsonschema`); pytest + FastAPI TestClient for Python tests; forge test for the contract.

---

## File Structure

```
rmbs_cc_demo/
  foundry.toml                         # Foundry config (solc 0.8.20)
  lib/forge-std/                       # installed via forge install
  contracts/ConfidentialCompute.sol    # application contract (self-contained ecrecover)
  script/Deploy.s.sol                  # forge deploy script
  test/ConfidentialCompute.t.sol       # forge contract tests
  tee/
    __init__.py
    engine/                            # VENDORED from rmbs_platform/engine
      __init__.py
      loader.py  state.py  compute.py  audit_trail.py  waterfall.py
    sample_deal.py                     # built-in basic_sequential_deal dict
    compute.py                         # compute_waterfall() — pure function
    signing.py                         # TEE key load/generate + sign helper
    tee_service.py                     # FastAPI app: POST /compute, GET /tee_address
  orchestrator.py                      # event listener -> TEE -> postResult
  submit_request.py                    # user CLI
  tests/
    test_sample_deal.py                # engine sanity / ground-truth numbers
    test_tee_compute.py                # compute_waterfall + /compute endpoint + signature
  requirements.txt
  .env.example
  .gitignore
  README.md
```

**Responsibility boundaries:** `tee/compute.py` is pure (inputs → result dict, no I/O, no crypto) so it is trivially testable and reusable as the on-host "ground truth". `tee/signing.py` owns all key/signature logic. `tee/tee_service.py` is a thin HTTP shell over those two. `orchestrator.py` and `submit_request.py` own all on-chain I/O. The contract is self-contained (inline `ecrecover`, no OpenZeppelin) to avoid library-version pitfalls.

**Note on the canonical demo inputs** used throughout the plan: `IAF = 500000`, `PAF = 1000000`, `period = 1` against the built-in sample deal. These produce deterministic results used in test assertions:
- `ClassA.current_balance = 79000000.00` (paid 1,000,000 principal from PAF)
- `ClassB.current_balance = 15000000.00`, `ClassC.current_balance = 5000000.00` (PAF exhausted by ClassA's `ALL` step)
- all `interest_shortfall = 0` (IAF 500,000 fully covers servicing fee 20,833.33 + interest 300,000 + 75,000 + 33,333.33)
- `cash_remaining = {IAF: 70833.33, PAF: 0.0}`

---

## Task 1: Repo scaffolding, vendored engine, dependencies

**Files:**
- Create: `requirements.txt`, `.gitignore`, `tee/__init__.py`, `tee/engine/__init__.py`
- Create (by copy): `tee/engine/{loader,state,compute,audit_trail,waterfall}.py`
- Test: `tests/test_sample_deal.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.env
.venv/
venv/
out/
cache/
broadcast/
tee/kd/
contract-address.json
node_modules/
```

- [ ] **Step 2: Create `requirements.txt`**

```text
fastapi
uvicorn
pydantic
web3
eth-account
requests
python-dotenv
jsonschema
pytest
httpx
```

- [ ] **Step 3: Vendor the 5 engine files and create package markers**

Run:
```bash
cd /Users/leo/Desktop/rmbs_cc_demo
mkdir -p tee/engine tests
cp /Users/leo/Desktop/rmbs_platform/engine/loader.py    tee/engine/loader.py
cp /Users/leo/Desktop/rmbs_platform/engine/state.py     tee/engine/state.py
cp /Users/leo/Desktop/rmbs_platform/engine/compute.py   tee/engine/compute.py
cp /Users/leo/Desktop/rmbs_platform/engine/audit_trail.py tee/engine/audit_trail.py
cp /Users/leo/Desktop/rmbs_platform/engine/waterfall.py tee/engine/waterfall.py
touch tee/__init__.py tee/engine/__init__.py tests/__init__.py
```

These files use only relative imports (`from .loader import ...`) plus stdlib and `jsonschema`, so they work unmodified inside the `tee.engine` package.

- [ ] **Step 4: Install dependencies**

Run:
```bash
cd /Users/leo/Desktop/rmbs_cc_demo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
Expected: all packages install without error.

- [ ] **Step 5: Write the engine sanity test (ground-truth numbers)**

Create `tests/test_sample_deal.py`. This test pins the deterministic waterfall output for the canonical inputs and proves the vendored engine works end-to-end. It imports the sample deal from Task 2, so it will fail to import until Task 2 — that is expected and handled by ordering; for now write it against the engine directly using an inline minimal copy is NOT allowed (DRY). Instead, this test depends on `tee/sample_deal.py` which is created in Task 2. To keep Task 1 self-contained, load the fixture inline here is avoided; we instead defer the assertions to the imported constant.

```python
from tee.engine.loader import DealLoader
from tee.engine.state import DealState
from tee.engine.compute import ExpressionEngine
from tee.engine.waterfall import WaterfallRunner
from tee.sample_deal import SAMPLE_DEAL


def test_sample_deal_runs_and_matches_known_numbers():
    deal_def = DealLoader().load_from_json(SAMPLE_DEAL)
    state = DealState(deal_def)
    state.deposit_funds("IAF", 500000.0)
    state.deposit_funds("PAF", 1000000.0)
    WaterfallRunner(ExpressionEngine()).run_period(state)

    assert round(state.bonds["ClassA"].current_balance, 2) == 79000000.00
    assert round(state.bonds["ClassB"].current_balance, 2) == 15000000.00
    assert round(state.bonds["ClassC"].current_balance, 2) == 5000000.00
    assert state.bonds["ClassA"].interest_shortfall == 0
    assert state.bonds["ClassB"].interest_shortfall == 0
    assert state.bonds["ClassC"].interest_shortfall == 0
    assert round(state.cash_balances["IAF"], 2) == 70833.33
    assert round(state.cash_balances["PAF"], 2) == 0.0
```

- [ ] **Step 6: Commit (test will be exercised after Task 2 provides SAMPLE_DEAL)**

```bash
cd /Users/leo/Desktop/rmbs_cc_demo
git add .gitignore requirements.txt tee/ tests/
git commit -m "chore: scaffold repo, vendor rmbs waterfall engine, add deps"
```

---

## Task 2: Built-in sample deal + pure compute function

**Files:**
- Create: `tee/sample_deal.py`, `tee/compute.py`
- Test: `tests/test_sample_deal.py` (from Task 1 now runs), `tests/test_tee_compute.py` (compute portion)

- [ ] **Step 1: Create `tee/sample_deal.py`**

This is the `basic_sequential_deal` fixture from `rmbs_platform/unit_tests/test_waterfall.py`, copied verbatim as a module constant.

```python
"""Built-in sample RMBS deal for the confidential-compute demo.

Copied verbatim from rmbs_platform/unit_tests/test_waterfall.py
(basic_sequential_deal): 3 sequential tranches A/B/C, $100M collateral,
a servicing fee, fixed-rate interest, sequential principal. No triggers,
no Net WAC, loss allocation defined but not exercised.
"""

SAMPLE_DEAL = {
    "meta": {
        "deal_id": "TEST_SEQ_2024",
        "deal_name": "Sequential Test Deal",
        "asset_type": "RMBS",
        "version": "1.0",
    },
    "dates": {
        "cutoff_date": "2024-01-01",
        "closing_date": "2024-01-30",
        "first_payment_date": "2024-02-25",
        "maturity_date": "2054-01-01",
        "payment_frequency": "MONTHLY",
        "day_count": "30_360",
    },
    "collateral": {
        "original_balance": 100_000_000.0,
        "current_balance": 100_000_000.0,
        "wac": 0.065,
        "wam": 348,
    },
    "funds": [
        {"id": "IAF", "description": "Interest Available Funds"},
        {"id": "PAF", "description": "Principal Available Funds"},
    ],
    "accounts": [
        {"id": "RESERVE", "type": "RESERVE", "target_rule": "500000.0"},
    ],
    "variables": {
        "ServicingFee": "collateral.current_balance * 0.0025 / 12",
        "ClassA_Int": "bonds.ClassA.balance * 0.045 / 12",
        "ClassB_Int": "bonds.ClassB.balance * 0.060 / 12",
        "ClassC_Int": "bonds.ClassC.balance * 0.080 / 12",
    },
    "tests": [],
    "bonds": [
        {
            "id": "ClassA",
            "type": "NOTE",
            "original_balance": 80_000_000.0,
            "priority": {"interest": 1, "principal": 1},
            "coupon": {"kind": "FIXED", "fixed_rate": 0.045},
        },
        {
            "id": "ClassB",
            "type": "NOTE",
            "original_balance": 15_000_000.0,
            "priority": {"interest": 2, "principal": 2},
            "coupon": {"kind": "FIXED", "fixed_rate": 0.060},
        },
        {
            "id": "ClassC",
            "type": "NOTE",
            "original_balance": 5_000_000.0,
            "priority": {"interest": 3, "principal": 3},
            "coupon": {"kind": "FIXED", "fixed_rate": 0.080},
        },
    ],
    "waterfalls": {
        "interest": {
            "steps": [
                {"id": "1", "action": "PAY_FEE", "from_fund": "IAF", "amount_rule": "ServicingFee"},
                {"id": "2", "action": "PAY_BOND_INTEREST", "from_fund": "IAF", "group": "ClassA", "amount_rule": "ClassA_Int"},
                {"id": "3", "action": "PAY_BOND_INTEREST", "from_fund": "IAF", "group": "ClassB", "amount_rule": "ClassB_Int"},
                {"id": "4", "action": "PAY_BOND_INTEREST", "from_fund": "IAF", "group": "ClassC", "amount_rule": "ClassC_Int"},
            ],
        },
        "principal": {
            "steps": [
                {"id": "1", "action": "PAY_BOND_PRINCIPAL", "from_fund": "PAF", "group": "ClassA", "amount_rule": "ALL"},
                {"id": "2", "action": "PAY_BOND_PRINCIPAL", "from_fund": "PAF", "group": "ClassB", "amount_rule": "ALL"},
                {"id": "3", "action": "PAY_BOND_PRINCIPAL", "from_fund": "PAF", "group": "ClassC", "amount_rule": "ALL"},
            ],
        },
        "loss_allocation": {
            "loss_source_rule": "variables.RealizedLoss",
            "write_down_order": ["ClassC", "ClassB", "ClassA"],
        },
    },
}
```

- [ ] **Step 2: Run the Task 1 sanity test — it should now pass**

Run: `cd /Users/leo/Desktop/rmbs_cc_demo && source .venv/bin/activate && python -m pytest tests/test_sample_deal.py -v`
Expected: PASS (1 passed). If numbers differ, STOP and reconcile against the engine before continuing — the assertions encode the contract.

- [ ] **Step 3: Write the failing test for `compute_waterfall`**

Create `tests/test_tee_compute.py` (compute section only for now):

```python
import json
from tee.compute import compute_waterfall


def test_compute_waterfall_canonical_inputs():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)

    assert result["period"] == 1
    assert result["bonds"]["ClassA"]["current_balance"] == 79000000.00
    assert result["bonds"]["ClassB"]["current_balance"] == 15000000.00
    assert result["bonds"]["ClassC"]["current_balance"] == 5000000.00
    assert result["bonds"]["ClassA"]["interest_shortfall"] == 0.0
    assert result["cash_remaining"]["IAF"] == 70833.33
    assert result["cash_remaining"]["PAF"] == 0.0


def test_compute_waterfall_is_deterministic():
    a = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    b = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
```

- [ ] **Step 4: Run it to verify it fails**

Run: `python -m pytest tests/test_tee_compute.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tee.compute'`.

- [ ] **Step 5: Implement `tee/compute.py`**

```python
"""Pure waterfall computation for the confidential-compute demo.

No I/O, no crypto — given a period's cashflows it loads the built-in deal,
runs one waterfall period, and returns a deterministic result dict. This is
the exact computation the TEE performs and the on-host ground truth for tests.
"""
from typing import Any, Dict

from tee.engine.loader import DealLoader
from tee.engine.state import DealState
from tee.engine.compute import ExpressionEngine
from tee.engine.waterfall import WaterfallRunner
from tee.sample_deal import SAMPLE_DEAL


def compute_waterfall(iaf: float, paf: float, period: int) -> Dict[str, Any]:
    """Run one waterfall period on the built-in deal and return the result.

    Amounts are rounded to 2 decimals so the result serializes to a stable,
    hashable JSON string (the contract signs/stores its keccak hash).
    """
    deal_def = DealLoader().load_from_json(SAMPLE_DEAL)
    state = DealState(deal_def)
    state.deposit_funds("IAF", float(iaf))
    state.deposit_funds("PAF", float(paf))

    WaterfallRunner(ExpressionEngine()).run_period(state)

    bonds = {
        bond_id: {
            "current_balance": round(bond.current_balance, 2),
            "interest_shortfall": round(bond.interest_shortfall, 2),
        }
        for bond_id, bond in sorted(state.bonds.items())
    }
    cash_remaining = {
        fund_id: round(balance, 2)
        for fund_id, balance in sorted(state.cash_balances.items())
    }
    return {"period": int(period), "bonds": bonds, "cash_remaining": cash_remaining}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_tee_compute.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add tee/sample_deal.py tee/compute.py tests/test_tee_compute.py
git commit -m "feat: built-in sample deal and pure compute_waterfall function"
```

---

## Task 3: TEE signing helper

**Files:**
- Create: `tee/signing.py`
- Test: `tests/test_tee_compute.py` (append signing tests)

- [ ] **Step 1: Write failing tests for canonical JSON, hashing, and signing**

Append to `tests/test_tee_compute.py`:

```python
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from tee.compute import compute_waterfall
from tee.signing import canonical_json, result_hash, sign_result, get_signer


def test_canonical_json_is_sorted_and_compact():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    s = canonical_json(result)
    assert s == json.dumps(result, sort_keys=True, separators=(",", ":"))


def test_result_hash_matches_web3_keccak_of_canonical_json():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    s = canonical_json(result)
    assert result_hash(result) == Web3.keccak(text=s)


def test_signature_recovers_to_signer_address():
    # deterministic test key (NOT used in production)
    pk = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    h = result_hash(result)
    sig = sign_result(h, pk)
    recovered = Account.recover_message(encode_defunct(primitive=h), signature=sig)
    assert recovered == get_signer(pk)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_tee_compute.py -k "canonical or hash or signature" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tee.signing'`.

- [ ] **Step 3: Implement `tee/signing.py`**

```python
"""TEE signing: canonical serialization, keccak hashing, ECDSA over the hash.

The TEE signs keccak256(canonical_json(result)) with the Ethereum personal-sign
prefix (encode_defunct). The contract recovers the same way and checks it equals
the configured TEE address. Key handling mirrors ccc-demo: load from a file or
generate-and-persist on first run.
"""
import json
import os
from typing import Any, Dict

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

TEE_KEY_FILE = os.path.join(os.path.dirname(__file__), "kd", "tee_signing_key.json")


def canonical_json(result: Dict[str, Any]) -> str:
    """Deterministic JSON string the hash is computed over."""
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def result_hash(result: Dict[str, Any]) -> bytes:
    """keccak256 of the canonical JSON bytes (matches Solidity keccak256(bytes))."""
    return Web3.keccak(text=canonical_json(result))


def sign_result(hash_bytes: bytes, private_key: str) -> bytes:
    """Personal-sign the 32-byte hash; returns 65-byte signature."""
    signed = Account.sign_message(encode_defunct(primitive=hash_bytes), private_key)
    return bytes(signed.signature)


def get_signer(private_key: str) -> str:
    """Ethereum address for a private key."""
    return Account.from_key(private_key).address


def load_or_create_key() -> tuple[str, str]:
    """Return (private_key, address). Use TEE_PRIVATE_KEY env if set, else a
    persisted file under tee/kd/, generating a fresh key on first run."""
    env_pk = os.getenv("TEE_PRIVATE_KEY")
    if env_pk:
        if not env_pk.startswith("0x"):
            env_pk = "0x" + env_pk
        return env_pk, get_signer(env_pk)

    if os.path.exists(TEE_KEY_FILE):
        with open(TEE_KEY_FILE) as f:
            data = json.load(f)
        return data["private_key"], data["address"]

    acct = Account.create()
    pk = acct.key.hex()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    os.makedirs(os.path.dirname(TEE_KEY_FILE), exist_ok=True)
    with open(TEE_KEY_FILE, "w") as f:
        json.dump({"private_key": pk, "address": acct.address}, f, indent=2)
    return pk, acct.address
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tee_compute.py -k "canonical or hash or signature" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tee/signing.py tests/test_tee_compute.py
git commit -m "feat: TEE canonical-json hashing and ECDSA signing helpers"
```

---

## Task 4: TEE FastAPI service

**Files:**
- Create: `tee/tee_service.py`
- Test: `tests/test_tee_compute.py` (append endpoint test)

- [ ] **Step 1: Write the failing endpoint test (FastAPI TestClient)**

Append to `tests/test_tee_compute.py`:

```python
from fastapi.testclient import TestClient


def test_compute_endpoint_matches_pure_function_and_verifies_signature():
    os.environ["TEE_PRIVATE_KEY"] = (
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    )
    from tee.tee_service import app  # import after env set

    client = TestClient(app)
    resp = client.post(
        "/compute",
        json={"dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True

    expected = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    assert body["result"] == expected
    assert body["resultHash"] == result_hash(expected).hex()

    # signature recovers to the advertised TEE address
    sig = bytes.fromhex(body["signature"][2:])
    recovered = Account.recover_message(
        encode_defunct(primitive=result_hash(expected)), signature=sig
    )
    assert recovered == body["teeAddress"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_tee_compute.py -k endpoint -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tee.tee_service'`.

- [ ] **Step 3: Implement `tee/tee_service.py`**

```python
"""TEE compute enclave (FastAPI).

POST /compute  -> runs the waterfall, returns the result + keccak hash + TEE
                  signature over that hash.
GET  /tee_address -> the TEE's Ethereum signing address (for contract wiring).

Encryption is intentionally omitted in this demo: inputs/outputs are plaintext.
The signature is what lets the contract trust the result came from this TEE.
"""
from fastapi import FastAPI
from pydantic import BaseModel

from tee.compute import compute_waterfall
from tee.signing import load_or_create_key, result_hash, sign_result

app = FastAPI(title="RMBS Confidential Compute TEE")

PRIVATE_KEY, TEE_ADDRESS = load_or_create_key()
print(f"TEE signing address: {TEE_ADDRESS}")
print("Set this as TEE_ADDRESS in .env before deploying the contract.")


class ComputeRequest(BaseModel):
    dealId: str
    period: int
    iaf: int
    paf: int


@app.get("/tee_address")
def tee_address():
    return {"success": True, "address": TEE_ADDRESS}


@app.post("/compute")
def compute(req: ComputeRequest):
    try:
        result = compute_waterfall(iaf=float(req.iaf), paf=float(req.paf), period=req.period)
        h = result_hash(result)
        sig = sign_result(h, PRIVATE_KEY)
        return {
            "success": True,
            "result": result,
            "resultHash": h.hex(),
            "signature": "0x" + sig.hex(),
            "teeAddress": TEE_ADDRESS,
        }
    except Exception as e:  # noqa: BLE001 - surface error to caller for the demo
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `python -m pytest tests/ -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add tee/tee_service.py tests/test_tee_compute.py
git commit -m "feat: TEE FastAPI /compute and /tee_address endpoints"
```

---

## Task 5: Foundry project + ConfidentialCompute contract + tests

**Files:**
- Create: `foundry.toml`, `contracts/ConfidentialCompute.sol`, `test/ConfidentialCompute.t.sol`
- Install: `lib/forge-std`

- [ ] **Step 1: Create `foundry.toml`**

```toml
[profile.default]
src = "contracts"
out = "out"
libs = ["lib"]
test = "test"
script = "script"
solc_version = "0.8.20"
evm_version = "paris"
optimizer = true
optimizer_runs = 200
```

- [ ] **Step 2: Install forge-std**

Run:
```bash
cd /Users/leo/Desktop/rmbs_cc_demo
forge install foundry-rs/forge-std --no-commit
```
Expected: `lib/forge-std` created. (If `--no-commit` is unsupported on the installed forge version, run `forge install foundry-rs/forge-std` and then `git restore --staged .` as needed.)

- [ ] **Step 3: Create `contracts/ConfidentialCompute.sol`**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ConfidentialCompute
/// @notice Minimal application contract for the RMBS confidential-compute demo.
///         Stores plaintext compute requests, emits events for the orchestrator,
///         and stores results only after verifying a TEE ECDSA signature over
///         keccak256(resultJson). The contract never computes the waterfall.
contract ConfidentialCompute {
    struct Request {
        string dealId;
        uint256 period;
        uint256 iaf;
        uint256 paf;
        address requester;
        bool resultPosted;
        bytes32 resultHash;
        string resultJson;
    }

    address public admin;
    address public teeAddress;
    uint256 public requestCount;
    mapping(uint256 => Request) public requests;

    event ComputeRequested(
        uint256 indexed id,
        string dealId,
        uint256 period,
        uint256 iaf,
        uint256 paf,
        address requester
    );
    event ResultPosted(uint256 indexed id, bytes32 resultHash, string resultJson);

    modifier onlyAdmin() {
        require(msg.sender == admin, "Only admin");
        _;
    }

    constructor(address _teeAddress) {
        admin = msg.sender;
        teeAddress = _teeAddress;
    }

    function setTEEAddress(address _teeAddress) external onlyAdmin {
        teeAddress = _teeAddress;
    }

    function submitRequest(
        string calldata dealId,
        uint256 period,
        uint256 iaf,
        uint256 paf
    ) external returns (uint256 id) {
        id = ++requestCount;
        Request storage r = requests[id];
        r.dealId = dealId;
        r.period = period;
        r.iaf = iaf;
        r.paf = paf;
        r.requester = msg.sender;
        emit ComputeRequested(id, dealId, period, iaf, paf, msg.sender);
    }

    function postResult(
        uint256 id,
        bytes32 resultHash,
        string calldata resultJson,
        bytes calldata sig
    ) external {
        Request storage r = requests[id];
        require(r.requester != address(0), "Unknown request");
        require(!r.resultPosted, "Already posted");
        require(keccak256(bytes(resultJson)) == resultHash, "Hash mismatch");

        bytes32 ethHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", resultHash)
        );
        require(_recover(ethHash, sig) == teeAddress, "Invalid TEE signature");

        r.resultPosted = true;
        r.resultHash = resultHash;
        r.resultJson = resultJson;
        emit ResultPosted(id, resultHash, resultJson);
    }

    function getResult(uint256 id)
        external
        view
        returns (bool posted, bytes32 resultHash, string memory resultJson)
    {
        Request storage r = requests[id];
        return (r.resultPosted, r.resultHash, r.resultJson);
    }

    function _recover(bytes32 hash, bytes memory sig) internal pure returns (address) {
        require(sig.length == 65, "Bad sig length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := mload(add(sig, 32))
            s := mload(add(sig, 64))
            v := byte(0, mload(add(sig, 96)))
        }
        if (v < 27) {
            v += 27;
        }
        require(v == 27 || v == 28, "Bad v");
        return ecrecover(hash, v, r, s);
    }
}
```

- [ ] **Step 4: Create `test/ConfidentialCompute.t.sol`**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {ConfidentialCompute} from "../contracts/ConfidentialCompute.sol";

contract ConfidentialComputeTest is Test {
    ConfidentialCompute cc;
    uint256 teePk = 0xA11CE;
    address tee;

    function setUp() public {
        tee = vm.addr(teePk);
        cc = new ConfidentialCompute(tee);
    }

    function _sign(uint256 pk, bytes32 resultHash) internal pure returns (bytes memory) {
        bytes32 ethHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", resultHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, ethHash);
        return abi.encodePacked(r, s, v);
    }

    function test_SubmitRequestIncrementsAndEmits() public {
        vm.expectEmit(true, false, false, true);
        emit ConfidentialCompute.ComputeRequested(1, "TEST_SEQ_2024", 1, 500000, 1000000, address(this));
        uint256 id = cc.submitRequest("TEST_SEQ_2024", 1, 500000, 1000000);
        assertEq(id, 1);
        assertEq(cc.requestCount(), 1);
    }

    function test_PostResultWithValidSignatureStores() public {
        uint256 id = cc.submitRequest("TEST_SEQ_2024", 1, 500000, 1000000);
        string memory resultJson = '{"period":1}';
        bytes32 resultHash = keccak256(bytes(resultJson));
        bytes memory sig = _sign(teePk, resultHash);

        cc.postResult(id, resultHash, resultJson, sig);

        (bool posted, bytes32 h, string memory j) = cc.getResult(id);
        assertTrue(posted);
        assertEq(h, resultHash);
        assertEq(j, resultJson);
    }

    function test_PostResultRevertsOnWrongSigner() public {
        uint256 id = cc.submitRequest("TEST_SEQ_2024", 1, 500000, 1000000);
        string memory resultJson = '{"period":1}';
        bytes32 resultHash = keccak256(bytes(resultJson));
        bytes memory sig = _sign(0xB0B, resultHash); // not the TEE key

        vm.expectRevert("Invalid TEE signature");
        cc.postResult(id, resultHash, resultJson, sig);
    }

    function test_PostResultRevertsOnHashMismatch() public {
        uint256 id = cc.submitRequest("TEST_SEQ_2024", 1, 500000, 1000000);
        string memory resultJson = '{"period":1}';
        bytes32 wrongHash = keccak256(bytes('{"period":2}'));
        bytes memory sig = _sign(teePk, wrongHash);

        vm.expectRevert("Hash mismatch");
        cc.postResult(id, wrongHash, resultJson, sig);
    }
}
```

- [ ] **Step 5: Run forge tests**

Run: `cd /Users/leo/Desktop/rmbs_cc_demo && forge test -vv`
Expected: PASS — 4 tests passing in `ConfidentialComputeTest`.

- [ ] **Step 6: Commit**

```bash
git add foundry.toml contracts/ test/ lib/ .gitmodules
git commit -m "feat: ConfidentialCompute contract with TEE signature verification + forge tests"
```

---

## Task 6: Foundry deploy script

**Files:**
- Create: `script/Deploy.s.sol`

- [ ] **Step 1: Create `script/Deploy.s.sol`**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script, console2} from "forge-std/Script.sol";
import {ConfidentialCompute} from "../contracts/ConfidentialCompute.sol";

/// @notice Deploys ConfidentialCompute with the TEE address baked in.
/// Env vars:
///   DEPLOYER_PRIVATE_KEY  (0x-prefixed) — funded genesis account on the Besu chain
///   TEE_ADDRESS           — printed by the TEE service on startup
contract Deploy is Script {
    function run() external {
        uint256 pk = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address tee = vm.envAddress("TEE_ADDRESS");

        vm.startBroadcast(pk);
        ConfidentialCompute cc = new ConfidentialCompute(tee);
        vm.stopBroadcast();

        console2.log("ConfidentialCompute deployed at:", address(cc));
        console2.log("TEE address:", tee);
    }
}
```

- [ ] **Step 2: Verify it compiles**

Run: `forge build`
Expected: `Compiler run successful` with no errors.

- [ ] **Step 3: Commit**

```bash
git add script/Deploy.s.sol
git commit -m "feat: forge deploy script for ConfidentialCompute"
```

---

## Task 7: Orchestrator, user CLI, config, README

**Files:**
- Create: `orchestrator.py`, `submit_request.py`, `.env.example`, `README.md`

- [ ] **Step 1: Create `.env.example`**

```bash
# Besu private chain (reach via IAP tunnel to a validator's 8545)
RPC_URL=http://localhost:8545
CHAIN_ID=20260416

# Funded genesis account private key (0x-prefixed). FILL THIS IN — leave blank in git.
DEPLOYER_PRIVATE_KEY=

# Deployed contract address (from Deploy.s.sol output)
CONTRACT_ADDRESS=

# TEE service (reach via IAP SSH port-forward to tee-node:8000)
TEE_URL=http://localhost:8000
# TEE signing address (printed by tee_service.py on startup; also used by Deploy.s.sol)
TEE_ADDRESS=
```

- [ ] **Step 2: Create `orchestrator.py`**

```python
"""Orchestrator (Oracle role): watch ComputeRequested, call the TEE, post results.

Plaintext pipeline — no decryption. For each new on-chain request it forwards
{dealId, period, iaf, paf} to the TEE, receives the result + TEE signature, and
sends postResult() back to the contract (signed by the funded deployer account).
"""
import json
import os
import time

import requests
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

RPC_URL = os.environ["RPC_URL"]
CHAIN_ID = int(os.environ["CHAIN_ID"])
CONTRACT_ADDRESS = Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"])
TEE_URL = os.environ["TEE_URL"].rstrip("/")
ADMIN_PK = os.environ["DEPLOYER_PRIVATE_KEY"]
POLL_INTERVAL = 3

ABI_PATH = os.path.join(
    os.path.dirname(__file__), "out", "ConfidentialCompute.sol", "ConfidentialCompute.json"
)


def load_abi():
    with open(ABI_PATH) as f:
        return json.load(f)["abi"]


def post_result(w3, contract, admin, request_id, body):
    result_hash = bytes.fromhex(body["resultHash"])
    result_json = json.dumps(body["result"], sort_keys=True, separators=(",", ":"))
    sig = bytes.fromhex(body["signature"][2:])

    tx = contract.functions.postResult(request_id, result_hash, result_json, sig).build_transaction(
        {
            "from": admin.address,
            "nonce": w3.eth.get_transaction_count(admin.address),
            "gas": 800000,
            "gasPrice": 0,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, ADMIN_PK)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"  postResult tx {tx_hash.hex()} status={receipt['status']}")


def handle_event(w3, contract, admin, event):
    request_id = event["args"]["id"]
    args = event["args"]
    print(f"\n>>> ComputeRequested id={request_id} deal={args['dealId']} "
          f"period={args['period']} IAF={args['iaf']} PAF={args['paf']}")

    print("  forwarding to TEE...")
    resp = requests.post(
        f"{TEE_URL}/compute",
        json={
            "dealId": args["dealId"],
            "period": args["period"],
            "iaf": args["iaf"],
            "paf": args["paf"],
        },
        timeout=30,
    )
    body = resp.json()
    if not body.get("success"):
        print(f"  TEE error: {body.get('error')}")
        return
    print(f"  TEE result: {body['result']}")
    print("  posting signed result on-chain...")
    post_result(w3, contract, admin, request_id, body)


def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    assert w3.is_connected(), f"Cannot connect to {RPC_URL}"
    admin = w3.eth.account.from_key(ADMIN_PK)
    contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=load_abi())

    print(f"Orchestrator up. chain_id={w3.eth.chain_id} contract={CONTRACT_ADDRESS}")
    print(f"Admin={admin.address}  TEE={TEE_URL}")

    last_block = w3.eth.block_number
    processed = set()
    print(f"Listening for ComputeRequested from block {last_block}...")
    while True:
        current = w3.eth.block_number
        if current > last_block:
            flt = contract.events.ComputeRequested.create_filter(
                from_block=last_block + 1, to_block=current
            )
            for event in flt.get_all_entries():
                key = (event["transactionHash"].hex(), event["logIndex"])
                if key not in processed:
                    handle_event(w3, contract, admin, event)
                    processed.add(key)
            last_block = current
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create `submit_request.py`**

```python
"""User CLI: submit one period of cashflows to the ConfidentialCompute contract.

Usage:
  python submit_request.py --iaf 500000 --paf 1000000 [--deal TEST_SEQ_2024] [--period 1]
"""
import argparse
import json
import os

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

RPC_URL = os.environ["RPC_URL"]
CHAIN_ID = int(os.environ["CHAIN_ID"])
CONTRACT_ADDRESS = Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"])
PK = os.environ["DEPLOYER_PRIVATE_KEY"]

ABI_PATH = os.path.join(
    os.path.dirname(__file__), "out", "ConfidentialCompute.sol", "ConfidentialCompute.json"
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--iaf", type=int, required=True, help="Interest Available Funds")
    p.add_argument("--paf", type=int, required=True, help="Principal Available Funds")
    p.add_argument("--deal", default="TEST_SEQ_2024")
    p.add_argument("--period", type=int, default=1)
    args = p.parse_args()

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    assert w3.is_connected(), f"Cannot connect to {RPC_URL}"
    acct = w3.eth.account.from_key(PK)
    with open(ABI_PATH) as f:
        abi = json.load(f)["abi"]
    contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=abi)

    tx = contract.functions.submitRequest(args.deal, args.period, args.iaf, args.paf).build_transaction(
        {
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "gas": 400000,
            "gasPrice": 0,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, PK)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"submitRequest tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    logs = contract.events.ComputeRequested().process_receipt(receipt)
    request_id = logs[0]["args"]["id"]
    print(f"Request submitted: id={request_id} (deal={args.deal}, IAF={args.iaf}, PAF={args.paf})")
    print(f"Watch the orchestrator; then read the result with:")
    print(f"  python read_result.py {request_id}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `read_result.py`** (small helper used by the e2e step)

```python
"""Read a stored compute result from the contract: python read_result.py <id>."""
import json
import os
import sys

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"]))
contract = w3.eth.contract(
    address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]),
    abi=json.load(open(os.path.join(os.path.dirname(__file__), "out",
                  "ConfidentialCompute.sol", "ConfidentialCompute.json")))["abi"],
)

request_id = int(sys.argv[1])
posted, result_hash, result_json = contract.functions.getResult(request_id).call()
print(f"posted={posted}")
print(f"resultHash=0x{result_hash.hex()}")
print(f"resultJson={result_json}")
if posted:
    print("parsed:", json.dumps(json.loads(result_json), indent=2))
```

- [ ] **Step 5: Verify the Python files import cleanly (syntax/deps)**

Run:
```bash
cd /Users/leo/Desktop/rmbs_cc_demo && source .venv/bin/activate
python -c "import ast; [ast.parse(open(f).read()) for f in ['orchestrator.py','submit_request.py','read_result.py']]; print('syntax ok')"
```
Expected: `syntax ok`. (Full runtime needs the live chain — see Task 8.)

- [ ] **Step 6: Create `README.md`**

````markdown
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
````

- [ ] **Step 7: Commit**

```bash
git add orchestrator.py submit_request.py read_result.py .env.example README.md
git commit -m "feat: orchestrator, user CLI, result reader, config and README"
```

---

## Task 8: End-to-end on-chain verification (manual — run when chain + TEE are up)

This is the spec's acceptance test. It is a documented manual procedure because
the chain and TEE VM are user-controlled and stopped by default. Record the
outcome (paste terminal output) into the PR / notes.

- [ ] **Step 1: Start infra** — `gcloud compute instances start` the 6 chain nodes and `tee-node`; open both tunnels (README step 1).

- [ ] **Step 2: Start TEE + deploy + orchestrator** (README steps 2–4). Confirm `TEE_ADDRESS` in `.env` matches the address the TEE printed and the address baked into the deployed contract (`cast call $CONTRACT_ADDRESS "teeAddress()(address)" --rpc-url $RPC_URL`).

- [ ] **Step 3: Submit the canonical request**

Run: `python submit_request.py --iaf 500000 --paf 1000000`
Expected: prints `Request submitted: id=1`. Orchestrator terminal shows the TEE result and a `postResult tx ... status=1`.

- [ ] **Step 4: Read and verify the on-chain result**

Run: `python read_result.py 1`
Expected: `posted=True` and `resultJson` parses to ClassA `current_balance` 79000000.0, ClassB 15000000.0, ClassC 5000000.0, `cash_remaining` IAF 70833.33 / PAF 0.0.

- [ ] **Step 5: Confirm equality with the local engine**

Run: `python -m pytest tests/ -v`
Expected: all pass — the same numbers stored on-chain. This closes the spec's
verification loop ("confidential compute correctly computes the waterfall and
writes a signature-verified result back to the chain").

- [ ] **Step 6: Stop infra** — `gcloud compute instances stop` all chain nodes and `tee-node`.

---

## Self-Review Notes (verification of this plan against the spec)

- **Spec §3 data flow (①–⑥):** submit (`submit_request.py`, Task 7) → contract event (Task 5) → orchestrator listen+forward (Task 7) → TEE compute+sign (Tasks 2–4) → postResult+verify (Tasks 5,7) → getResult (`read_result.py`, Task 7 / Task 8). Covered.
- **Spec §2.2 engine reuse (5 files, jsonschema only external dep):** Task 1 vendors exactly those files; `requirements.txt` includes `jsonschema`. Covered.
- **Spec §2.2 built-in `basic_sequential_deal`:** Task 2 `sample_deal.py`. Covered.
- **Spec §5 contract interface (events, submitRequest, postResult w/ ecrecover==teeAddress, getResult):** Task 5 contract. Self-contained `_recover` replaces OZ to avoid version drift; eth-prefix matches `encode_defunct`. Covered.
- **Spec §5 integer amounts:** contract uses `uint256`; TEE casts to float internally. Covered.
- **Spec §6 run order incl. two IAP tunnels + TEE on tee-node:** README (Task 7) + Task 8. Covered.
- **Spec §8 verification (on-chain == local engine):** Task 8 step 5 + pytest assertions. Covered.
- **Spec §7 YAGNI (no encryption, no extra nodes, no UI, no tokens):** none added. Covered.
- **Type consistency:** `compute_waterfall(iaf, paf, period)` signature identical across compute.py/tests/tee_service; result keys `period`/`bonds`/`cash_remaining` consistent; `result_hash` returns bytes, `.hex()` used at the HTTP/JSON boundary; signature is `0x`-prefixed in HTTP and stripped before `bytes.fromhex` in orchestrator. Consistent.
- **Placeholder scan:** Task 1 Step 5 prose explains the deferred-import ordering; no TBD/TODO; all code blocks complete.
```
