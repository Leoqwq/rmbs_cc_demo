# Oracle DON Attestation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the demo follow the Chainlink Confidential Compute white-paper workflow for the response path — replace the single off-chain orchestrator with a decentralized **oracle DON**: N independent oracle agents that each fetch the enclave's attested result, verify it, and sign; the contract accepts the result only after an **m-of-n DON quorum** (the "DON-attested response"), on top of the TEE attestation.

**Architecture:** `Application(contract) → ComputeRequested → N oracle agents → TEE → (TEE-attested result) → each oracle verifies + signs → contract.attest() accumulates m-of-n → finalized (DON-attested)`. The Besu chain remains the application's host ledger (orders `submitRequest`/`attest` txs); the oracle DON is a separate software layer co-located on the validator hosts (reuses the existing 6-node infra, no new VMs). Encryption is still omitted (no decryption DON), but every boundary that encryption will later occupy is left as an explicit identity-function seam.

**Tech Stack:** Solidity 0.8.20 + Foundry; Python 3.10+ FastAPI/web3.py/eth-account/eth-abi; the result is bound to its request via `keccak256(abi.encode(id, dealId, period, iaf, paf, resultHash))`.

---

## Design decisions (review these first)

- **D1 — Quorum assembly = on-chain accumulation (chosen).** Each oracle agent independently sends its own `attest(...)` tx from its own funded account; the contract counts unique oracle signatures and finalizes at `threshold`. Rationale: every oracle is fully independent (no leader/aggregator single point — consistent with the robustness work already done), simplest to implement and audit, and the m-of-n quorum is verifiable on-chain. *Alternative (not chosen now): OCR-style off-chain aggregation into one tx — fewer txs and closer to "DON returns one response," but needs agent-to-agent gossip + a rotating leader. Can swap later; it only changes `attest` into a single multi-sig call.*
- **D2 — What an oracle attests.** Per the white paper, oracle nodes *verify the enclave's attestation*; they do **not** recompute the waterfall (they may not even have plaintext once encryption is added). Each agent verifies the TEE signature over `(id, inputs, resultHash)` and then signs `(id, resultHash)`. The DON thus decentralizes the **relay + attestation check**, removing the single-orchestrator trust/liveness SPOF. It does **not** add compute redundancy (still one enclave) — that is the separate multi-TEE item.
- **D3 — "TEE-attested" = the enclave's ECDSA signature** by its persistent key (today's stand-in for hardware attestation). Real SEV-SNP attestation-report verification is a documented future seam, not in this plan.
- **D4 — Result binding (security fix).** The TEE currently signs only `resultHash`, so a result isn't bound to its request/inputs. This plan binds it: the TEE signs `keccak256(abi.encode(id, dealId, period, iaf, paf, resultHash))`; the contract recomputes the same digest from the stored request fields, so a result can't be replayed against a different request.
- **D5 — Encryption seam (future decryption DON).** The oracle agent has an explicit `obtain_tee_inputs(request)` step (today: identity / plaintext passthrough) and the TEE has an explicit `decrypt_inputs()` step (today: identity). These are where the decryption DON / re-encryption slots in later. We build neither now; we only name the boundary.
- **D6 — Oracle identity & gas.** N fresh oracle keys (one per agent), registered in the contract at deploy. Each agent sends its own `attest` tx, so each oracle account needs a little native gas — a one-time `fund_oracles.py` tops them up from the deployer. Default **n=4, m=3** (one per validator host; tolerates 1 oracle down, mirroring QBFT's fault tolerance).

---

## File Structure

```
contracts/ConfidentialCompute.sol     # MODIFY: oracle registry + m-of-n attest() flow + request-bound TEE digest
test/ConfidentialCompute.t.sol        # REWRITE: quorum/attestation tests
script/Deploy.s.sol                   # MODIFY: constructor takes (teeAddress, oracles[], threshold)
tee/signing.py                        # MODIFY: add request-bound TEE digest + sign/verify helpers
tee/tee_service.py                    # MODIFY: /compute takes id; signs the bound digest; returns teeSig
tee/encryption_seam.py                # CREATE: identity decrypt_inputs() — the future decryption-DON boundary
oracle_agent.py                       # CREATE: one DON oracle node (was orchestrator.py); verify TEE + sign + attest
orchestrator.py                       # DELETE: superseded by oracle_agent.py
chain.py                              # REUSE as-is (RPC failover)
fund_oracles.py                       # CREATE: top up oracle accounts with gas from the deployer
read_result.py                        # MODIFY: show finalized + attestationCount
submit_request.py                     # REUSE (unchanged)
abi_digest.py                         # CREATE: shared helpers for the abi.encode digests (DRY across agent/tests)
tests/test_oracle_don.py             # CREATE: offline tests (digests, signing/recovery, abi match, agent helpers)
.env.example                          # MODIFY: ORACLE_ADDRESSES, THRESHOLD, ORACLE_KEY, per-agent notes
README.md / RUNBOOK.md                # MODIFY: DON topology + run N agents
```

**Canonical demo values** (unchanged inputs): `id=1, dealId=TEST_SEQ_2024, period=1, IAF=500000, PAF=1000000` → result `{period:1, bonds:{ClassA 79000000.0,...}, cash_remaining:{IAF 70833.33, PAF 0.0, RESERVE 0.0}}`. With `n=4, m=3`, 3 oracle attestations finalize the result.

---

## Task 1: Shared digest helpers (`abi_digest.py`) + offline tests

**Files:** Create `abi_digest.py`, `tests/test_oracle_don.py`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_oracle_don.py`:

```python
from eth_account import Account
from web3 import Web3

import abi_digest as ad


def test_tee_digest_matches_manual_abi_encode():
    from eth_abi import encode
    rh = Web3.keccak(text='{"x":1}')
    expected = Web3.keccak(
        encode(["uint256", "string", "uint256", "uint256", "uint256", "bytes32"],
               [1, "TEST_SEQ_2024", 1, 500000, 1000000, rh])
    )
    assert ad.tee_digest(1, "TEST_SEQ_2024", 1, 500000, 1000000, rh) == expected


def test_oracle_digest_matches_manual_abi_encode():
    from eth_abi import encode
    rh = Web3.keccak(text='{"x":1}')
    expected = Web3.keccak(encode(["uint256", "bytes32"], [1, rh]))
    assert ad.oracle_digest(1, rh) == expected


def test_sign_and_recover_roundtrip():
    pk = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    digest = Web3.keccak(text="hello")
    sig = ad.sign_digest(digest, pk)
    assert ad.recover_digest(digest, sig) == Account.from_key(pk).address
```

- [ ] **Step 2: Run, verify failure**

Run: `cd /Users/leo/Desktop/rmbs_cc_demo && source .venv/bin/activate && python -m pytest tests/test_oracle_don.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'abi_digest'`.

- [ ] **Step 3: Implement `abi_digest.py`**

```python
"""Shared digest + signature helpers for the oracle DON.

Digests are computed with eth_abi so they match the contract's abi.encode:
- tee_digest:    keccak256(abi.encode(id, dealId, period, iaf, paf, resultHash))
                 -> binds the enclave result to the exact request + inputs.
- oracle_digest: keccak256(abi.encode(id, resultHash))
                 -> what each oracle signs to attest the (verified) result.

Both are signed/recovered with the Ethereum personal-sign prefix (EIP-191),
matching the contract's "\\x19Ethereum Signed Message:\\n32" handling.
"""
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3


def tee_digest(id: int, deal_id: str, period: int, iaf: int, paf: int, result_hash: bytes) -> bytes:
    return Web3.keccak(
        encode(
            ["uint256", "string", "uint256", "uint256", "uint256", "bytes32"],
            [int(id), str(deal_id), int(period), int(iaf), int(paf), bytes(result_hash)],
        )
    )


def oracle_digest(id: int, result_hash: bytes) -> bytes:
    return Web3.keccak(encode(["uint256", "bytes32"], [int(id), bytes(result_hash)]))


def sign_digest(digest: bytes, private_key: str) -> bytes:
    signed = Account.sign_message(encode_defunct(primitive=digest), private_key)
    return bytes(signed.signature)


def recover_digest(digest: bytes, signature: bytes) -> str:
    return Account.recover_message(encode_defunct(primitive=digest), signature=signature)
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_oracle_don.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add abi_digest.py tests/test_oracle_don.py
git commit -m "feat: shared abi.encode digest + signature helpers for oracle DON"
```

---

## Task 2: TEE binds its signature to the request (`signing.py`, `tee_service.py`)

**Files:** Modify `tee/signing.py`, `tee/tee_service.py`; create `tee/encryption_seam.py`; extend `tests/test_oracle_don.py`.

- [ ] **Step 1: Write the failing test (TEE signs the request-bound digest)**

Append to `tests/test_oracle_don.py`:

```python
import os


def test_tee_endpoint_signs_request_bound_digest():
    os.environ["TEE_PRIVATE_KEY"] = (
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    )
    from fastapi.testclient import TestClient
    from tee.tee_service import app
    from tee.signing import result_hash
    import abi_digest as ad

    client = TestClient(app)
    resp = client.post("/compute", json={
        "id": 1, "dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000,
    })
    body = resp.json()
    assert body["success"] is True
    rh = result_hash(body["result"])
    assert body["resultHash"] == rh.hex()

    digest = ad.tee_digest(1, "TEST_SEQ_2024", 1, 500000, 1000000, rh)
    sig = bytes.fromhex(body["teeSig"][2:])
    assert ad.recover_digest(digest, sig) == body["teeAddress"]
```

- [ ] **Step 2: Run, verify failure**

Run: `python -m pytest tests/test_oracle_don.py -k request_bound -v`
Expected: FAIL (KeyError `id` or `teeSig`, since `/compute` doesn't take `id` or return `teeSig` yet).

- [ ] **Step 3: Create `tee/encryption_seam.py` (the future decryption-DON boundary)**

```python
"""Encryption seam — where the decryption DON / re-encryption will plug in later.

Today the pipeline is plaintext, so this is the identity function. When encryption
is added, this becomes: receive re-encrypted key shares from the decryption DON and
recover the plaintext inputs inside the enclave. Keeping the boundary explicit means
that upgrade does not require re-plumbing the TEE service.
"""
from typing import Any, Dict


def decrypt_inputs(raw_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Plaintext passthrough for now (no decryption DON yet)."""
    return raw_inputs
```

- [ ] **Step 4: Add request-bound signing helper to `tee/signing.py`**

Add (keep the existing `canonical_json`, `result_hash`, `load_or_create_key`; the old `sign_result`/`get_signer` may stay or be removed if unused — remove `sign_result` since it is replaced):

```python
# add at top with the other imports
import abi_digest as ad


def sign_request_bound(id: int, deal_id: str, period: int, iaf: int, paf: int,
                       result_hash_bytes: bytes, private_key: str) -> bytes:
    """Sign keccak256(abi.encode(id, dealId, period, iaf, paf, resultHash)) —
    binds the enclave result to the exact request and inputs."""
    digest = ad.tee_digest(id, deal_id, period, iaf, paf, result_hash_bytes)
    return ad.sign_digest(digest, private_key)
```

Then delete the now-unused `sign_result` function and its import of `encode_defunct`/`Account` if they become unused (keep `result_hash`, `canonical_json`, `load_or_create_key`, `get_signer`).

- [ ] **Step 5: Update `tee/tee_service.py`**

```python
"""TEE compute enclave (FastAPI).

POST /compute  -> decrypt inputs (identity for now), run the waterfall, return the
                  result + keccak hash + a TEE signature BOUND to the request
                  (id + inputs + resultHash). Oracle nodes verify this signature.
GET  /tee_address -> the enclave's signing address.
"""
from fastapi import FastAPI
from pydantic import BaseModel

from tee.compute import compute_waterfall
from tee.encryption_seam import decrypt_inputs
from tee.signing import load_or_create_key, result_hash, sign_request_bound

app = FastAPI(title="RMBS Confidential Compute TEE")

PRIVATE_KEY, TEE_ADDRESS = load_or_create_key()
print(f"TEE signing address: {TEE_ADDRESS}")
print("Set this as TEE_ADDRESS in .env (and pass it to deploy) before deploying.")


class ComputeRequest(BaseModel):
    id: int
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
        inp = decrypt_inputs({"iaf": req.iaf, "paf": req.paf, "period": req.period})
        result = compute_waterfall(iaf=float(inp["iaf"]), paf=float(inp["paf"]), period=inp["period"])
        h = result_hash(result)
        tee_sig = sign_request_bound(req.id, req.dealId, req.period, req.iaf, req.paf, h, PRIVATE_KEY)
        return {
            "success": True,
            "result": result,
            "resultHash": h.hex(),
            "teeSig": "0x" + tee_sig.hex(),
            "teeAddress": TEE_ADDRESS,
        }
    except Exception as e:  # noqa: BLE001 - surface to caller for the demo
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

- [ ] **Step 6: Update the old TEE endpoint test in `tests/test_tee_compute.py`**

The existing `test_compute_endpoint_matches_pure_function_and_verifies_signature` posts without `id` and expects `signature`. Replace its request body with the new shape and assert on `teeSig` + the request-bound digest:

```python
def test_compute_endpoint_matches_pure_function_and_verifies_signature():
    os.environ["TEE_PRIVATE_KEY"] = (
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    )
    from tee.tee_service import app
    import abi_digest as ad

    client = TestClient(app)
    resp = client.post("/compute", json={
        "id": 1, "dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True

    expected = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    assert body["result"] == expected
    rh = result_hash(expected)
    assert body["resultHash"] == rh.hex()
    digest = ad.tee_digest(1, "TEST_SEQ_2024", 1, 500000, 1000000, rh)
    assert ad.recover_digest(digest, bytes.fromhex(body["teeSig"][2:])) == body["teeAddress"]
```

Also delete the older signing tests in `test_tee_compute.py` that referenced the removed `sign_result` (the `test_signature_recovers_to_signer_address` test) — its behavior is now covered by `abi_digest` roundtrip and the endpoint test.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: PASS (all tests, including the updated endpoint + the Task 1 digest tests).

- [ ] **Step 8: Commit**

```bash
git add tee/signing.py tee/tee_service.py tee/encryption_seam.py tests/test_tee_compute.py tests/test_oracle_don.py
git commit -m "feat: TEE signs request-bound digest; add encryption seam"
```

---

## Task 3: Contract — oracle registry + m-of-n attestation

**Files:** Rewrite `contracts/ConfidentialCompute.sol`, `test/ConfidentialCompute.t.sol`.

- [ ] **Step 1: Rewrite `contracts/ConfidentialCompute.sol`**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ConfidentialCompute
/// @notice Application contract for the RMBS confidential-compute demo.
///         Stores plaintext compute requests; accepts a result only after the
///         enclave attestation (TEE signature bound to the request) AND an
///         m-of-n oracle DON quorum have been verified on-chain (DON-attested).
contract ConfidentialCompute {
    struct Request {
        string dealId;
        uint256 period;
        uint256 iaf;
        uint256 paf;
        address requester;
        bool resultStored;        // TEE-attested result recorded
        bool finalized;           // DON quorum reached
        bytes32 resultHash;
        string resultJson;
        uint256 attestationCount;
    }

    address public admin;
    address public teeAddress;
    uint256 public threshold;                 // m
    address[] public oracles;                 // n
    mapping(address => bool) public isOracle;
    uint256 public requestCount;
    mapping(uint256 => Request) public requests;
    mapping(uint256 => mapping(address => bool)) public hasAttested;

    event ComputeRequested(
        uint256 indexed id, string dealId, uint256 period, uint256 iaf, uint256 paf, address requester
    );
    event Attested(uint256 indexed id, address indexed oracle, uint256 count);
    event ResultPosted(uint256 indexed id, bytes32 resultHash, string resultJson); // DON-attested

    modifier onlyAdmin() {
        require(msg.sender == admin, "Only admin");
        _;
    }

    constructor(address _teeAddress, address[] memory _oracles, uint256 _threshold) {
        admin = msg.sender;
        teeAddress = _teeAddress;
        require(_threshold > 0 && _threshold <= _oracles.length, "bad threshold");
        threshold = _threshold;
        for (uint256 i = 0; i < _oracles.length; i++) {
            require(!isOracle[_oracles[i]], "dup oracle");
            isOracle[_oracles[i]] = true;
            oracles.push(_oracles[i]);
        }
    }

    function setTEEAddress(address a) external onlyAdmin {
        teeAddress = a;
    }

    function oracleCount() external view returns (uint256) {
        return oracles.length;
    }

    function submitRequest(string calldata dealId, uint256 period, uint256 iaf, uint256 paf)
        external
        returns (uint256 id)
    {
        id = ++requestCount;
        Request storage r = requests[id];
        r.dealId = dealId;
        r.period = period;
        r.iaf = iaf;
        r.paf = paf;
        r.requester = msg.sender;
        emit ComputeRequested(id, dealId, period, iaf, paf, msg.sender);
    }

    /// @notice One oracle's attestation of a TEE result. The first valid call for an
    ///         id records the result and verifies the enclave attestation; every call
    ///         adds the caller's oracle signature. Finalizes at `threshold` oracles.
    /// @param resultJson  required on the first call; ignored afterwards
    /// @param teeSig      required on the first call; enclave signature over the
    ///                    request-bound digest; ignored afterwards
    /// @param oracleSig   this oracle's signature over keccak256(abi.encode(id, resultHash))
    function attest(
        uint256 id,
        bytes32 resultHash,
        string calldata resultJson,
        bytes calldata teeSig,
        bytes calldata oracleSig
    ) external {
        Request storage r = requests[id];
        require(r.requester != address(0), "unknown request");
        require(!r.finalized, "finalized");

        if (!r.resultStored) {
            require(keccak256(bytes(resultJson)) == resultHash, "hash mismatch");
            bytes32 teeDigest = keccak256(
                abi.encode(id, r.dealId, r.period, r.iaf, r.paf, resultHash)
            );
            require(_recover(_ethSigned(teeDigest), teeSig) == teeAddress, "bad TEE sig");
            r.resultHash = resultHash;
            r.resultJson = resultJson;
            r.resultStored = true;
        } else {
            require(resultHash == r.resultHash, "result mismatch");
        }

        bytes32 oracleDigest = keccak256(abi.encode(id, resultHash));
        address signer = _recover(_ethSigned(oracleDigest), oracleSig);
        require(isOracle[signer], "not an oracle");
        require(!hasAttested[id][signer], "dup attestation");
        hasAttested[id][signer] = true;
        r.attestationCount += 1;
        emit Attested(id, signer, r.attestationCount);

        if (r.attestationCount >= threshold) {
            r.finalized = true;
            emit ResultPosted(id, r.resultHash, r.resultJson);
        }
    }

    function getResult(uint256 id)
        external
        view
        returns (bool finalized, uint256 attestationCount, bytes32 resultHash, string memory resultJson)
    {
        Request storage r = requests[id];
        return (r.finalized, r.attestationCount, r.resultHash, r.resultJson);
    }

    function _ethSigned(bytes32 h) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", h));
    }

    function _recover(bytes32 hash, bytes memory sig) internal pure returns (address) {
        require(sig.length == 65, "bad sig length");
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
        require(v == 27 || v == 28, "bad v");
        return ecrecover(hash, v, r, s);
    }
}
```

- [ ] **Step 2: Rewrite `test/ConfidentialCompute.t.sol`**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {ConfidentialCompute} from "../contracts/ConfidentialCompute.sol";

contract ConfidentialComputeTest is Test {
    ConfidentialCompute cc;

    uint256 teePk = 0xA11CE;
    address tee;
    // three oracle keys (n=4 registered, threshold m=3; 4th used for negative test)
    uint256[4] oraclePks = [uint256(0x0racle1), 0x0racle2, 0x0racle3, 0x0racle4];
    address[] oracles;

    string constant DEAL = "TEST_SEQ_2024";
    string constant RJSON = '{"period":1}';

    function setUp() public {
        tee = vm.addr(teePk);
        for (uint256 i = 0; i < 4; i++) {
            oracles.push(vm.addr(oraclePks[i]));
        }
        cc = new ConfidentialCompute(tee, oracles, 3);
    }

    function _newRequest() internal returns (uint256 id) {
        id = cc.submitRequest(DEAL, 1, 500000, 1000000);
    }

    function _eth(bytes32 h) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", h));
    }

    function _teeSig(uint256 id, bytes32 rh) internal view returns (bytes memory) {
        bytes32 d = keccak256(abi.encode(id, DEAL, uint256(1), uint256(500000), uint256(1000000), rh));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(teePk, _eth(d));
        return abi.encodePacked(r, s, v);
    }

    function _oracleSig(uint256 pk, uint256 id, bytes32 rh) internal pure returns (bytes memory) {
        bytes32 d = keccak256(abi.encode(id, rh));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, _eth(d));
        return abi.encodePacked(r, s, v);
    }

    function test_QuorumFinalizesAtThreshold() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        bytes memory teeSig = _teeSig(id, rh);

        cc.attest(id, rh, RJSON, teeSig, _oracleSig(oraclePks[0], id, rh));
        (bool fin1,,,) = cc.getResult(id);
        assertFalse(fin1); // 1 of 3

        cc.attest(id, rh, "", "", _oracleSig(oraclePks[1], id, rh));
        (bool fin2, uint256 c2,,) = cc.getResult(id);
        assertFalse(fin2);
        assertEq(c2, 2);

        cc.attest(id, rh, "", "", _oracleSig(oraclePks[2], id, rh));
        (bool fin3, uint256 c3, bytes32 sh, string memory sj) = cc.getResult(id);
        assertTrue(fin3); // 3 of 3
        assertEq(c3, 3);
        assertEq(sh, rh);
        assertEq(sj, RJSON);
    }

    function test_RejectsNonOracleSignature() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        vm.expectRevert("not an oracle");
        cc.attest(id, rh, RJSON, _teeSig(id, rh), _oracleSig(0xBEEF, id, rh));
    }

    function test_RejectsDuplicateOracle() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        cc.attest(id, rh, RJSON, _teeSig(id, rh), _oracleSig(oraclePks[0], id, rh));
        vm.expectRevert("dup attestation");
        cc.attest(id, rh, "", "", _oracleSig(oraclePks[0], id, rh));
    }

    function test_RejectsBadTeeSig() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        // teeSig from a non-TEE key
        bytes32 d = keccak256(abi.encode(id, DEAL, uint256(1), uint256(500000), uint256(1000000), rh));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xBAD, _eth(d));
        vm.expectRevert("bad TEE sig");
        cc.attest(id, rh, RJSON, abi.encodePacked(r, s, v), _oracleSig(oraclePks[0], id, rh));
    }

    function test_RejectsResultHashMismatchOnSecond() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        cc.attest(id, rh, RJSON, _teeSig(id, rh), _oracleSig(oraclePks[0], id, rh));
        bytes32 other = keccak256(bytes('{"period":2}'));
        vm.expectRevert("result mismatch");
        cc.attest(id, other, "", "", _oracleSig(oraclePks[1], id, other));
    }
}
```

- [ ] **Step 3: Run forge tests + build**

Run: `forge test -vv && forge build`
Expected: 5 tests pass; compile clean.

- [ ] **Step 4: Commit**

```bash
git add contracts/ConfidentialCompute.sol test/ConfidentialCompute.t.sol
git commit -m "feat: contract oracle registry + m-of-n DON attestation with request-bound TEE sig"
```

---

## Task 4: Deploy script + oracle funding

**Files:** Modify `script/Deploy.s.sol`; create `fund_oracles.py`.

- [ ] **Step 1: Update `script/Deploy.s.sol`**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script, console2} from "forge-std/Script.sol";
import {ConfidentialCompute} from "../contracts/ConfidentialCompute.sol";

/// @notice Deploy with the TEE address, the oracle set, and the m-of-n threshold.
/// Env vars:
///   DEPLOYER_PRIVATE_KEY  (0x-prefixed)
///   TEE_ADDRESS           (from the TEE service)
///   ORACLE_ADDRESSES      (comma-separated 0x addresses)
///   THRESHOLD             (m)
contract Deploy is Script {
    function run() external {
        uint256 pk = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address tee = vm.envAddress("TEE_ADDRESS");
        address[] memory oracles = vm.envAddress("ORACLE_ADDRESSES", ",");
        uint256 threshold = vm.envUint("THRESHOLD");

        vm.startBroadcast(pk);
        ConfidentialCompute cc = new ConfidentialCompute(tee, oracles, threshold);
        vm.stopBroadcast();

        console2.log("ConfidentialCompute deployed at:", address(cc));
        console2.log("TEE:", tee);
        console2.log("threshold:", threshold);
        console2.log("oracles:", oracles.length);
    }
}
```

- [ ] **Step 2: Verify it compiles**

Run: `forge build`
Expected: `Compiler run successful`.

- [ ] **Step 3: Create `fund_oracles.py`** (one-time gas top-up for oracle accounts)

```python
"""Top up each oracle account with native gas from the deployer.

Each oracle agent sends its own attest() tx, so each oracle account needs a little
native balance. Usage: python fund_oracles.py 0xOracle1 0xOracle2 ...
(or it reads ORACLE_ADDRESSES from .env if no args).
"""
import os
import sys

from dotenv import load_dotenv
from web3 import Web3

from chain import connect_web3, get_rpc_urls

load_dotenv()
CHAIN_ID = int(os.environ["CHAIN_ID"])
PK = os.environ["DEPLOYER_PRIVATE_KEY"]
AMOUNT_ETHER = float(os.getenv("ORACLE_FUND_ETHER", "1"))


def main():
    addrs = sys.argv[1:] or [a.strip() for a in os.environ["ORACLE_ADDRESSES"].split(",") if a.strip()]
    w3 = connect_web3(get_rpc_urls())
    acct = w3.eth.account.from_key(PK)
    nonce = w3.eth.get_transaction_count(acct.address)
    value = w3.to_wei(AMOUNT_ETHER, "ether")
    for addr in addrs:
        to = Web3.to_checksum_address(addr)
        tx = {
            "from": acct.address, "to": to, "value": value,
            "nonce": nonce, "gas": 21000, "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID,
        }
        signed = w3.eth.account.sign_transaction(tx, PK)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(h)
        print(f"funded {to} with {AMOUNT_ETHER} (tx {h.hex()})")
        nonce += 1


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Commit**

```bash
git add script/Deploy.s.sol fund_oracles.py
git commit -m "feat: deploy with oracle set + threshold; oracle gas funding script"
```

---

## Task 5: Oracle agent (replaces orchestrator) + read_result update

**Files:** Create `oracle_agent.py`; delete `orchestrator.py`; modify `read_result.py`.

- [ ] **Step 1: Create `oracle_agent.py`**

```python
"""One oracle node of the DON.

Per the white-paper response path, each oracle independently: watches the contract
for ComputeRequested, fetches the enclave's attested result from the TEE, VERIFIES
the TEE signature is bound to this exact request, signs its own attestation over
(id, resultHash), and submits attest() on-chain. The contract finalizes once an
m-of-n quorum of distinct oracles has attested (the DON-attested response).

Reuses chain.ResilientChain (RPC failover) and persists progress per oracle so a
restart resumes idempotently (the contract is the source of truth via getResult /
hasAttested). Run one instance per oracle, each with its own ORACLE_KEY.
"""
import json
import os
import time

import requests
from dotenv import load_dotenv

import abi_digest as ad
from chain import ResilientChain, TRANSPORT_ERRORS, get_rpc_urls

POLL_INTERVAL = 3
ABI_PATH = os.path.join(
    os.path.dirname(__file__), "out", "ConfidentialCompute.sol", "ConfidentialCompute.json"
)


def load_abi():
    with open(ABI_PATH) as f:
        return json.load(f)["abi"]


def state_path(oracle_id: str) -> str:
    return os.path.join(os.path.dirname(__file__), f"oracle_state_{oracle_id}.json")


def load_state(path: str):
    if os.path.exists(path):
        with open(path) as f:
            d = json.load(f)
        return int(d.get("last_scanned_block", 0)), set(d.get("attested_ids", []))
    return 0, set()


def save_state(last_block: int, attested_ids, path: str):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"last_scanned_block": last_block, "attested_ids": sorted(attested_ids)}, f, indent=2)
    os.replace(tmp, path)


def handle_request(chain, tee_url, tee_address, oracle_pk, oracle_address,
                   chain_id, request_id, args, attested_ids) -> bool:
    if request_id in attested_ids:
        return True
    finalized, _, _, _ = chain.run(lambda w3, c: c.functions.getResult(request_id).call())
    if finalized:
        attested_ids.add(request_id)
        return True
    already = chain.run(lambda w3, c: c.functions.hasAttested(request_id, oracle_address).call())
    if already:
        attested_ids.add(request_id)
        return True

    print(f"\n>>> [oracle {oracle_address}] ComputeRequested id={request_id} "
          f"IAF={args['iaf']} PAF={args['paf']}")
    try:
        resp = requests.post(f"{tee_url}/compute", json={
            "id": request_id, "dealId": args["dealId"], "period": args["period"],
            "iaf": args["iaf"], "paf": args["paf"],
        }, timeout=30)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        print(f"  TEE unreachable ({e}); retry next loop")
        return False
    if not body.get("success"):
        print(f"  TEE error: {body.get('error')}; retry next loop")
        return False

    result_hash = bytes.fromhex(body["resultHash"])
    tee_sig = bytes.fromhex(body["teeSig"][2:])

    # Verify the enclave attestation is bound to THIS request before signing.
    digest = ad.tee_digest(request_id, args["dealId"], args["period"], args["iaf"], args["paf"], result_hash)
    recovered = ad.recover_digest(digest, tee_sig)
    if recovered.lower() != tee_address.lower():
        print(f"  BAD TEE signature (got {recovered}, want {tee_address}); refusing to attest")
        return False

    result_json = json.dumps(body["result"], sort_keys=True, separators=(",", ":"))
    oracle_sig = ad.sign_digest(ad.oracle_digest(request_id, result_hash), oracle_pk)

    def _send(w3, contract):
        tx = contract.functions.attest(
            request_id, result_hash, result_json, tee_sig, oracle_sig
        ).build_transaction({
            "from": oracle_address,
            "nonce": w3.eth.get_transaction_count(oracle_address),
            "gas": 900000, "gasPrice": w3.eth.gas_price, "chainId": chain_id,
        })
        signed = w3.eth.account.sign_transaction(tx, oracle_pk)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        return h, w3.eth.wait_for_transaction_receipt(h)

    tx_hash, receipt = chain.run(_send)
    if receipt["status"] == 1:
        print(f"  attested ok tx={tx_hash.hex()}")
        attested_ids.add(request_id)
        return True
    # Someone may have finalized between our checks; treat as done if so.
    finalized, _, _, _ = chain.run(lambda w3, c: c.functions.getResult(request_id).call())
    if finalized or chain.run(lambda w3, c: c.functions.hasAttested(request_id, oracle_address).call()):
        attested_ids.add(request_id)
        return True
    print(f"  attest reverted (status 0); retry next loop")
    return False


def main():
    load_dotenv()
    oracle_id = os.environ["ORACLE_ID"]
    oracle_pk = os.environ["ORACLE_KEY"]
    chain_id = int(os.environ["CHAIN_ID"])
    contract_address = os.environ["CONTRACT_ADDRESS"]
    tee_url = os.environ["TEE_URL"].rstrip("/")
    tee_address = os.environ["TEE_ADDRESS"]

    chain = ResilientChain(get_rpc_urls(), contract_address, load_abi())
    oracle_address = chain.w3.eth.account.from_key(oracle_pk).address

    path = state_path(oracle_id)
    last_block, attested_ids = load_state(path)
    print(f"Oracle agent '{oracle_id}' up. addr={oracle_address} contract={chain.contract_address}")
    print(f"TEE={tee_url} (expect {tee_address})  RPCs={chain.rpc_urls}")
    print(f"Resuming from block {last_block}, {len(attested_ids)} requests already attested.")

    while True:
        try:
            current = chain.run(lambda w3, c: w3.eth.block_number)
            if current > last_block:
                events = chain.run(lambda w3, c: c.events.ComputeRequested.create_filter(
                    from_block=last_block + 1, to_block=current).get_all_entries())
                all_done = True
                for ev in events:
                    if not handle_request(chain, tee_url, tee_address, oracle_pk, oracle_address,
                                          chain_id, ev["args"]["id"], ev["args"], attested_ids):
                        all_done = False
                        break
                if all_done:
                    last_block = current
                save_state(last_block, attested_ids, path)
        except TRANSPORT_ERRORS as e:
            print(f"  RPC unavailable ({e}); retry in {POLL_INTERVAL}s")
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            print(f"  unexpected error: {e}; retry in {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Delete `orchestrator.py`**

Run: `git rm orchestrator.py`
(Its role is now played by N `oracle_agent.py` instances. `chain.py` is unchanged and reused.)

- [ ] **Step 3: Update `read_result.py` to show DON status**

```python
"""Read a stored compute result: python read_result.py <id>."""
import json
import os
import sys

from dotenv import load_dotenv
from web3 import Web3

from chain import connect_web3, get_rpc_urls

load_dotenv()

w3 = connect_web3(get_rpc_urls())
with open(os.path.join(os.path.dirname(__file__), "out",
                       "ConfidentialCompute.sol", "ConfidentialCompute.json")) as f:
    abi = json.load(f)["abi"]
contract = w3.eth.contract(
    address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]),
    abi=abi,
)

request_id = int(sys.argv[1])
finalized, attestation_count, result_hash, result_json = contract.functions.getResult(request_id).call()
threshold = contract.functions.threshold().call()
print(f"finalized={finalized}  attestations={attestation_count}/{threshold} (DON quorum)")
print(f"resultHash=0x{result_hash.hex()}")
print(f"resultJson={result_json}")
if finalized:
    print("parsed:", json.dumps(json.loads(result_json), indent=2))
```

- [ ] **Step 4: Syntax check**

Run: `python -c "import ast; [ast.parse(open(f).read()) for f in ['oracle_agent.py','read_result.py','fund_oracles.py']]; print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 5: Run full offline suite**

Run: `python -m pytest tests/ -q && forge test -q`
Expected: all Python tests pass; 5 forge tests pass.

- [ ] **Step 6: Commit**

```bash
git add oracle_agent.py read_result.py
git rm orchestrator.py
git commit -m "feat: oracle DON agent (replaces single orchestrator); read_result shows quorum"
```

---

## Task 6: Config + docs

**Files:** Modify `.env.example`, `.gitignore`, `README.md`, `RUNBOOK.md`.

- [ ] **Step 1: Update `.env.example`**

Append after the existing TEE/contract section:

```bash
# Oracle DON (m-of-n). Register these addresses at deploy; run one agent per key.
ORACLE_ADDRESSES=0xOracle1,0xOracle2,0xOracle3,0xOracle4
THRESHOLD=3
ORACLE_FUND_ETHER=1
# Per-agent (set differently in each agent's shell/instance):
ORACLE_ID=1
ORACLE_KEY=0x<this oracle's private key>
```

- [ ] **Step 2: Ignore per-oracle state files** — add to `.gitignore`:

```gitignore
oracle_state_*.json
oracle_state_*.json.tmp
```

- [ ] **Step 3: Update `README.md` "Run the demo"** — replace the single-orchestrator step with: start the TEE; generate N oracle keys; put addresses in `ORACLE_ADDRESSES` + `THRESHOLD`; deploy; `python fund_oracles.py`; start N agents (each with its own `ORACLE_ID`/`ORACLE_KEY`, ideally one per validator host); `submit_request`; `read_result` shows `attestations=m/n, finalized=True`. State that the chain orders txs while the oracle DON provides the m-of-n attested response, and note the encryption seam (`tee/encryption_seam.py`) for the future decryption DON.

- [ ] **Step 4: Update `RUNBOOK.md`** — replace stage 5 (single orchestrator) with the DON sequence: deploy with oracle set, `fund_oracles.py`, launch N `oracle_agent.py` (one per validator host via the in-VPC pattern, each `ORACLE_ID`/`ORACLE_KEY`), and the new `read_result` output (`attestations=3/3 finalized=True`). Add a robustness note: with m=3,n=4 the DON tolerates 1 oracle offline.

- [ ] **Step 5: Commit**

```bash
git add .env.example .gitignore README.md RUNBOOK.md
git commit -m "docs: oracle DON config + run instructions"
```

---

## Task 7: End-to-end on-chain verification (manual — run when chain + TEE are up)

- [ ] **Step 1** Start infra + tunnels (RUNBOOK stages 0–3); start the TEE (tmux on tee-node).
- [ ] **Step 2** Generate 4 oracle keys; set `ORACLE_ADDRESSES`/`THRESHOLD=3`; deploy; `python fund_oracles.py`; confirm `cast call $CONTRACT "oracleCount()(uint256)"` == 4 and `threshold()(uint256)` == 3.
- [ ] **Step 3** Launch 4 agents (each its own `ORACLE_ID`/`ORACLE_KEY`), ideally one per validator host (in-VPC pattern from RUNBOOK path B).
- [ ] **Step 4** `python submit_request.py --iaf 500000 --paf 1000000` → id=1. Watch ≥3 agents print `attested ok`.
- [ ] **Step 5** `python read_result.py 1` → `finalized=True  attestations=3/3` with ClassA 79,000,000 etc.
- [ ] **Step 6** Robustness drill: stop one agent, submit another request → remaining 3 still finalize (m-of-n tolerates 1 down).
- [ ] **Step 7** Stop all instances (cost).

---

## Self-Review

- **White-paper fidelity (response path):** request → DON (N oracle agents, Task 5) → TEE (Task 2) → each oracle verifies the enclave attestation + signs → contract m-of-n quorum (Task 3) = DON-attested response. Covered. Forward path (app→DON→TEE) is the agents reading `ComputeRequested` and calling the TEE. Decryption DON omitted by requirement; seam left in `tee/encryption_seam.py` + agent inputs (D5). Covered.
- **Oracle DON verification mandatory:** contract `attest` will not finalize without `threshold` distinct registered-oracle signatures over the result; each agent independently verifies the request-bound TEE signature before attesting. Covered (Task 3 tests + agent).
- **Reuses existing Besu infra:** agents are software co-located on validator hosts; no new chain role; chain still just orders txs. Covered (docs Task 6, e2e Task 7).
- **Result binding fix (D4):** TEE signs `abi.encode(id, dealId, period, iaf, paf, resultHash)`; contract recomputes from stored request fields. Covered (Tasks 2–3; `test_RejectsBadTeeSig`, digest tests).
- **Placeholder scan:** none — all code blocks complete; commands have expected output.
- **Type/name consistency:** `tee_digest`/`oracle_digest`/`sign_digest`/`recover_digest` used identically in `abi_digest.py`, `signing.py`, `tee_service.py`, `oracle_agent.py`, and tests; contract `attest(id, resultHash, resultJson, teeSig, oracleSig)` matches the agent's call and the forge tests; `getResult` returns `(finalized, attestationCount, resultHash, resultJson)` consistently in contract, `read_result.py`, and forge tests; ABI types in `abi_digest.tee_digest` (`uint256,string,uint256,uint256,uint256,bytes32`) match the contract's `abi.encode(id, r.dealId, r.period, r.iaf, r.paf, resultHash)`.
- **Out-of-scope avoided:** no multi-TEE/compute redundancy, no real SEV-SNP attestation, no encryption — all explicitly deferred per D2/D3/D5.
```
