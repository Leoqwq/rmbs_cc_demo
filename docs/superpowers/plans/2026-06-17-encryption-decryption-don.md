# Encryption + Decryption DON Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make user inputs confidential end-to-end (encrypted under a threshold key, re-encrypted to the enclave by a decryption DON, decrypted only inside the TEE) while leaving the TEE-attested, m-of-n oracle-quorum *result* path unchanged.

**Architecture:** pyUmbral threshold proxy re-encryption. A trusted-dealer `keygen.py` produces a master keypair + N key fragments (kfrags) bound to the enclave's static receiving key. The user Umbral-encrypts inputs; each oracle independently collects ≥ m re-encryption fragments (cfrags) from the decryption nodes, forwards them to the TEE, which decrypts and computes. The request→result binding digest moves from plaintext fields to `keccak(capsule‖ciphertext)`. Each node operator runs two processes: `decryption_node.py` (pure re-encryption) and `oracle_agent.py` (watch + attest).

**Tech Stack:** Python 3 (FastAPI, web3, eth_abi, pyUmbral `umbral==0.3.0`), Solidity (Foundry), Besu chain.

**Spec:** `docs/superpowers/specs/2026-06-16-encryption-decryption-don-design.md`

---

## File Structure

**New files**
- `umbral_io.py` (root) — shared Umbral glue: base64 helpers, load public state, load kfrags, `verify_cfrags`.
- `keygen.py` (root) — trusted-dealer setup; writes `kd/umbral_state.json`.
- `decryption_node.py` (root) — FastAPI `/reencrypt`; holds one kfrag.
- `run_decryption_nodes.py` (root) — launches N decryption nodes.
- `tee/enclave_keys.py` — enclave Umbral receiving keypair (load-or-create).
- `tests/_umbral_helpers.py` — test helper: build an encrypted request (state + capsule + ciphertext + cfrags + enclave secret).
- `tests/test_umbral_io.py`, `tests/test_keygen.py`, `tests/test_encryption_seam.py`, `tests/test_decryption_node.py` — new tests.

**Modified files**
- `abi_digest.py` — new `tee_digest(id, ciphertextHash, resultHash)` + `ciphertext_hash(capsule, ciphertext)`.
- `tee/signing.py` — `sign_request_bound(id, ciphertextHash, resultHash, pk)`.
- `tee/encryption_seam.py` — real `decrypt_inputs(...)`.
- `tee/tee_service.py` — `GET /enclave_pubkey`, new `/compute` schema + digest.
- `oracle_agent.py` — cfrag collection + new digest verification.
- `submit_request.py` — encrypt inputs, `submitRequest(capsule, ciphertext)`.
- `contracts/ConfidentialCompute.sol` — store `bytes capsule/ciphertext`; new digest.
- `test/ConfidentialCompute.t.sol` — updated sign helpers + cross-language hash check.
- `tests/test_oracle_don.py`, `tests/test_tee_compute.py` — updated to new digest/schema.
- `requirements.txt` — add `umbral==0.3.0`.
- `RUNBOOK.md`, `CLAUDE.md`, `docs/FUTURE_WORK.md` — docs.

**Conventions:** all Umbral byte blobs are base64 strings at process boundaries (HTTP/JSON/state file). Tests and CLIs run from repo root with the `.venv` active. Commit straight to `main`.

---

## Task 1: Add pyUmbral dependency + shared Umbral I/O module

**Files:**
- Modify: `requirements.txt`
- Create: `umbral_io.py`
- Test: `tests/test_umbral_io.py`

- [ ] **Step 1: Install the dependency and pin it**

Run: `source .venv/bin/activate && pip install 'umbral==0.3.0'`
Then add this line to `requirements.txt` (anywhere, keep file sorted if it is):

```
umbral==0.3.0
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_umbral_io.py`:

```python
import json
import os
import tempfile

from umbral import SecretKey

import umbral_io as uio


def test_b64_roundtrip():
    raw = b"\x00\x01\x02\xaa\xff"
    assert uio.b64d(uio.b64e(raw)) == raw


def test_load_public_state_parses_keys_and_threshold():
    master = SecretKey.random().public_key()
    authority = SecretKey.random().public_key()
    enclave = SecretKey.random().public_key()
    fd, path = tempfile.mkstemp(suffix="_state.json")
    os.close(fd)
    with open(path, "w") as f:
        json.dump({
            "master_public_key": uio.b64e(bytes(master)),
            "authority_public_key": uio.b64e(bytes(authority)),
            "enclave_public_key": uio.b64e(bytes(enclave)),
            "threshold": 2,
            "kfrags": [uio.b64e(b"k1"), uio.b64e(b"k2")],
        }, f)

    state = uio.load_public_state(path)
    assert bytes(state["master_pk"]) == bytes(master)
    assert bytes(state["authority_pk"]) == bytes(authority)
    assert bytes(state["enclave_pk"]) == bytes(enclave)
    assert state["threshold"] == 2
    assert uio.load_kfrags(path) == [b"k1", b"k2"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_umbral_io.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'umbral_io'`.

- [ ] **Step 4: Write the implementation**

Create `umbral_io.py`:

```python
"""Shared pyUmbral glue used by the user CLI, oracle agent, decryption node,
keygen, and the TEE seam. All Umbral blobs cross process boundaries as base64.

Public state (kd/umbral_state.json) holds only PUBLIC material plus the per-node
key fragments; no master/enclave secret ever lives here.
"""
import base64
import json
import os

from umbral import Capsule, CapsuleFrag, PublicKey

DEFAULT_STATE = os.path.join(os.path.dirname(__file__), "kd", "umbral_state.json")


def b64e(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("utf-8")


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))


def _state_path(path: str | None) -> str:
    return path or os.getenv("UMBRAL_STATE") or DEFAULT_STATE


def load_public_state(path: str | None = None) -> dict:
    """Load master/authority/enclave public keys + threshold from the state file."""
    with open(_state_path(path)) as f:
        d = json.load(f)
    return {
        "master_pk": PublicKey.from_bytes(b64d(d["master_public_key"])),
        "authority_pk": PublicKey.from_bytes(b64d(d["authority_public_key"])),
        "enclave_pk": PublicKey.from_bytes(b64d(d["enclave_public_key"])),
        "threshold": int(d["threshold"]),
    }


def load_kfrags(path: str | None = None) -> list[bytes]:
    """Raw (base64-decoded) kfrag bytes, one per node."""
    with open(_state_path(path)) as f:
        d = json.load(f)
    return [b64d(k) for k in d.get("kfrags", [])]


def verify_cfrags(capsule_b64: str, cfrag_b64_list: list[str], state: dict) -> list[str]:
    """Return the base64 cfrags that verify against the state's keys; drop the rest.

    A corrupt/lying decryption node yields a cfrag that fails verification — this is
    how the quorum tolerates it (mirrors ccc-demo's faulty-node demo)."""
    capsule = Capsule.from_bytes(b64d(capsule_b64))
    verified: list[str] = []
    for cb in cfrag_b64_list:
        try:
            cfrag = CapsuleFrag.from_bytes(b64d(cb))
            cfrag.verify(
                capsule,
                verifying_pk=state["authority_pk"],
                delegating_pk=state["master_pk"],
                receiving_pk=state["enclave_pk"],
            )
            verified.append(cb)
        except Exception:
            continue
    return verified
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_umbral_io.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt umbral_io.py tests/test_umbral_io.py
git commit -m "feat: add pyUmbral dep + shared umbral_io helpers"
```

---

## Task 2: Move the signing seam to the ciphertext-bound digest (Python side)

**Files:**
- Modify: `abi_digest.py:18-24` (replace `tee_digest`, add `ciphertext_hash`)
- Modify: `tee/signing.py:30-35` (replace `sign_request_bound`)
- Modify: `tests/test_oracle_don.py:7-14` (update digest test)
- Test: `tests/test_oracle_don.py`

- [ ] **Step 1: Write the failing tests**

Replace `test_tee_digest_matches_manual_abi_encode` in `tests/test_oracle_don.py` (lines 7-14) with:

```python
def test_tee_digest_matches_manual_abi_encode():
    from eth_abi import encode
    rh = Web3.keccak(text='{"x":1}')
    ch = Web3.keccak(b"\xaa\xbb\xcc\xdd")
    expected = Web3.keccak(encode(["uint256", "bytes32", "bytes32"], [1, ch, rh]))
    assert ad.tee_digest(1, ch, rh) == expected


def test_ciphertext_hash_is_keccak_of_raw_concat():
    # Must match Solidity keccak256(abi.encodePacked(capsule, ciphertext)).
    assert ad.ciphertext_hash(b"\xaa\xbb", b"\xcc\xdd") == Web3.keccak(b"\xaa\xbb\xcc\xdd")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_oracle_don.py::test_tee_digest_matches_manual_abi_encode tests/test_oracle_don.py::test_ciphertext_hash_is_keccak_of_raw_concat -q`
Expected: FAIL — `tee_digest()` takes the old 6 args / `ciphertext_hash` missing.

- [ ] **Step 3: Implement the digest change**

In `abi_digest.py`, replace the `tee_digest` function (lines 18-24) with:

```python
def ciphertext_hash(capsule: bytes, ciphertext: bytes) -> bytes:
    """keccak256(capsule || ciphertext) — matches Solidity
    keccak256(abi.encodePacked(capsule, ciphertext)) (raw byte concat)."""
    return Web3.keccak(bytes(capsule) + bytes(ciphertext))


def tee_digest(id: int, ciphertext_hash_bytes: bytes, result_hash: bytes) -> bytes:
    return Web3.keccak(
        encode(
            ["uint256", "bytes32", "bytes32"],
            [int(id), bytes(ciphertext_hash_bytes), bytes(result_hash)],
        )
    )
```

Also update the module docstring (lines 4-6) to read:

```python
- tee_digest:    keccak256(abi.encode(id, ciphertextHash, resultHash))
                 where ciphertextHash = keccak256(capsule || ciphertext)
                 -> binds the enclave result to the exact submitted ciphertext.
```

In `tee/signing.py`, replace `sign_request_bound` (lines 30-35) with:

```python
def sign_request_bound(id: int, ciphertext_hash_bytes: bytes,
                       result_hash_bytes: bytes, private_key: str) -> bytes:
    """Sign keccak256(abi.encode(id, ciphertextHash, resultHash)) — binds the
    enclave result to the exact submitted ciphertext (inputs stay encrypted)."""
    digest = ad.tee_digest(id, ciphertext_hash_bytes, result_hash_bytes)
    return ad.sign_digest(digest, private_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_oracle_don.py::test_tee_digest_matches_manual_abi_encode tests/test_oracle_don.py::test_ciphertext_hash_is_keccak_of_raw_concat tests/test_oracle_don.py::test_oracle_digest_matches_manual_abi_encode tests/test_oracle_don.py::test_sign_and_recover_roundtrip -q`
Expected: PASS (4 tests). (The two `/compute` endpoint tests in this file will be fixed in Task 7 — they are expected to fail until then; do not run the whole file yet.)

- [ ] **Step 5: Commit**

```bash
git add abi_digest.py tee/signing.py tests/test_oracle_don.py
git commit -m "feat: bind TEE digest to ciphertext hash instead of plaintext fields"
```

---

## Task 3: Update the contract + Forge tests to the new digest and encrypted inputs

**Files:**
- Modify: `contracts/ConfidentialCompute.sol`
- Modify: `test/ConfidentialCompute.t.sol`

- [ ] **Step 1: Update the failing Forge tests first**

In `test/ConfidentialCompute.t.sol`:

Replace the `DEAL`/`RJSON` constants (lines 16-17) with:

```solidity
    bytes constant CAP = hex"aabb";
    bytes constant CT = hex"ccdd";
    string constant RJSON = '{"period":1}';
```

Replace `_newRequest` (lines 27-29) with:

```solidity
    function _newRequest() internal returns (uint256 id) {
        id = cc.submitRequest(CAP, CT);
    }
```

Replace `_teeSig` (lines 35-39) with:

```solidity
    function _teeSig(uint256 id, bytes32 rh) internal view returns (bytes memory) {
        bytes32 ch = keccak256(abi.encodePacked(CAP, CT));
        bytes32 d = keccak256(abi.encode(id, ch, rh));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(teePk, _eth(d));
        return abi.encodePacked(r, s, v);
    }
```

In `test_RejectsBadTeeSig` replace the digest line (line 88) with:

```solidity
        bytes32 ch = keccak256(abi.encodePacked(CAP, CT));
        bytes32 d = keccak256(abi.encode(id, ch, rh));
```

Add a cross-language anchor test at the end of the contract (before the closing brace):

```solidity
    function test_CiphertextHashMatchesPythonVector() public pure {
        // Python: Web3.keccak(b"\xaa\xbb\xcc\xdd"); both sides hash the raw concat.
        assertEq(keccak256(abi.encodePacked(CAP, CT)), keccak256(hex"aabbccdd"));
    }
```

- [ ] **Step 2: Run Forge tests to verify they fail to compile/pass**

Run: `~/.foundry/bin/forge test -vv`
Expected: FAIL — `submitRequest(bytes,bytes)` does not exist yet (compile error).

- [ ] **Step 3: Update the contract**

In `contracts/ConfidentialCompute.sol`:

Replace the `Request` struct input fields (lines 11-14) — change

```solidity
        string dealId;
        uint256 period;
        uint256 iaf;
        uint256 paf;
```

to

```solidity
        bytes capsule;
        bytes ciphertext;
```

Replace the `ComputeRequested` event (lines 32-34) with:

```solidity
    event ComputeRequested(
        uint256 indexed id, bytes capsule, bytes ciphertext, address requester
    );
```

Replace `submitRequest` (lines 63-75) with:

```solidity
    function submitRequest(bytes calldata capsule, bytes calldata ciphertext)
        external
        returns (uint256 id)
    {
        id = ++requestCount;
        Request storage r = requests[id];
        r.capsule = capsule;
        r.ciphertext = ciphertext;
        r.requester = msg.sender;
        emit ComputeRequested(id, capsule, ciphertext, msg.sender);
    }
```

Replace the `teeDigest` computation inside `attest` (lines 97-99) with:

```solidity
            bytes32 ciphertextHash = keccak256(abi.encodePacked(r.capsule, r.ciphertext));
            bytes32 teeDigest = keccak256(abi.encode(id, ciphertextHash, resultHash));
```

- [ ] **Step 4: Run Forge tests to verify they pass**

Run: `~/.foundry/bin/forge build && ~/.foundry/bin/forge test -vv`
Expected: PASS (6 tests: the original 5 + `test_CiphertextHashMatchesPythonVector`).

- [ ] **Step 5: Commit**

```bash
git add contracts/ConfidentialCompute.sol test/ConfidentialCompute.t.sol out/
git commit -m "feat: store encrypted inputs on-chain; bind result to ciphertext hash"
```

---

## Task 4: Trusted-dealer key generation (`keygen.py`)

**Files:**
- Create: `keygen.py`
- Test: `tests/test_keygen.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_keygen.py`:

```python
import os
import tempfile

import pytest
from umbral import (SecretKey, VerifiedKeyFrag, encrypt, reencrypt,
                    decrypt_reencrypted)

import umbral_io as uio
from keygen import run_keygen


def _state_file():
    fd, path = tempfile.mkstemp(suffix="_state.json")
    os.close(fd)
    return path


def test_run_keygen_produces_threshold_recoverable_state():
    enclave_sk = SecretKey.random()
    path = _state_file()
    run_keygen(enclave_sk.public_key(), shares=3, threshold=2, out_path=path)

    state = uio.load_public_state(path)
    assert state["threshold"] == 2
    kfrags = uio.load_kfrags(path)
    assert len(kfrags) == 3

    capsule, ciphertext = encrypt(state["master_pk"], b'{"iaf":7}')
    vkfrags = [VerifiedKeyFrag.from_verified_bytes(k) for k in kfrags[:2]]
    cfrags = [reencrypt(capsule=capsule, kfrag=k) for k in vkfrags]
    plain = decrypt_reencrypted(
        receiving_sk=enclave_sk, delegating_pk=state["master_pk"],
        capsule=capsule, verified_cfrags=cfrags, ciphertext=ciphertext)
    assert plain == b'{"iaf":7}'


def test_below_threshold_cannot_decrypt():
    enclave_sk = SecretKey.random()
    path = _state_file()
    run_keygen(enclave_sk.public_key(), shares=3, threshold=2, out_path=path)
    state = uio.load_public_state(path)
    capsule, ciphertext = encrypt(state["master_pk"], b'{"iaf":7}')
    one = [VerifiedKeyFrag.from_verified_bytes(uio.load_kfrags(path)[0])]
    cfrags = [reencrypt(capsule=capsule, kfrag=k) for k in one]
    with pytest.raises(Exception):
        decrypt_reencrypted(
            receiving_sk=enclave_sk, delegating_pk=state["master_pk"],
            capsule=capsule, verified_cfrags=cfrags, ciphertext=ciphertext)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_keygen.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'keygen'`.

- [ ] **Step 3: Write the implementation**

Create `keygen.py`:

```python
"""Trusted-dealer setup for the decryption DON (white-paper step 0).

Generates the master keypair (delegating), an authority signer (for cfrag
verification), and N key fragments bound to the enclave's receiving public key,
then writes kd/umbral_state.json (public material + kfrags). The master/authority
secrets are NOT persisted — a crude stand-in for "no single party holds the key"
(real threshold DKG is future work).

Usage (after the TEE is running):
  python keygen.py --shares 3 --threshold 2
"""
import argparse
import json
import os

import requests
from umbral import PublicKey, SecretKey, Signer, generate_kfrags

from umbral_io import DEFAULT_STATE, b64d, b64e


def run_keygen(enclave_pk: PublicKey, shares: int, threshold: int, out_path: str) -> dict:
    master_sk = SecretKey.random()
    authority_sk = SecretKey.random()
    kfrags = generate_kfrags(
        delegating_sk=master_sk,
        receiving_pk=enclave_pk,
        signer=Signer(authority_sk),
        threshold=threshold,
        shares=shares,
    )
    state = {
        "master_public_key": b64e(bytes(master_sk.public_key())),
        "authority_public_key": b64e(bytes(authority_sk.public_key())),
        "enclave_public_key": b64e(bytes(enclave_pk)),
        "threshold": threshold,
        "kfrags": [b64e(bytes(k)) for k in kfrags],
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(state, f, indent=2)
    return state


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tee-url", default=os.getenv("TEE_URL", "http://127.0.0.1:8000"))
    p.add_argument("--shares", type=int, required=True, help="N decryption nodes")
    p.add_argument("--threshold", type=int, required=True, help="m needed to decrypt")
    p.add_argument("--out", default=DEFAULT_STATE)
    a = p.parse_args()

    resp = requests.get(f"{a.tee_url.rstrip('/')}/enclave_pubkey", timeout=10)
    resp.raise_for_status()
    enclave_pk = PublicKey.from_bytes(b64d(resp.json()["pubkey"]))

    run_keygen(enclave_pk, a.shares, a.threshold, a.out)
    print(f"Wrote {a.out}: {a.shares} kfrags, threshold {a.threshold}, "
          f"enclave_pk pinned. Distribute kfrags[i] as KFRAG to node i.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_keygen.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add keygen.py tests/test_keygen.py
git commit -m "feat: trusted-dealer keygen for the decryption DON"
```

---

## Task 5: Enclave receiving keypair (`tee/enclave_keys.py`)

**Files:**
- Create: `tee/enclave_keys.py`
- Test: `tests/test_encryption_seam.py` (one test here; the rest of the file is Task 6)

- [ ] **Step 1: Write the failing test**

Create `tests/test_encryption_seam.py` with this first test:

```python
import os

from umbral import SecretKey

from tee.enclave_keys import load_or_create_enclave_key
from umbral_io import b64e, b64d


def test_load_enclave_key_from_env_is_deterministic():
    sk = SecretKey.random()
    os.environ["ENCLAVE_ENC_SECRET"] = b64e(sk.to_secret_bytes())
    got_sk, got_pk = load_or_create_enclave_key()
    assert got_sk.to_secret_bytes() == sk.to_secret_bytes()
    assert bytes(got_pk) == bytes(sk.public_key())
    del os.environ["ENCLAVE_ENC_SECRET"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_encryption_seam.py::test_load_enclave_key_from_env_is_deterministic -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tee.enclave_keys'`.

- [ ] **Step 3: Write the implementation**

Create `tee/enclave_keys.py`:

```python
"""Enclave receiving (encryption) keypair — distinct from the ECDSA signing key.

The decryption DON's kfrags are generated FOR this public key; re-encrypted
inputs can only be opened with this secret. Mirrors tee/signing.py: env override,
else a persisted file, else generate-and-persist on first run.
"""
import json
import os

from umbral import PublicKey, SecretKey

from umbral_io import b64d, b64e

ENCLAVE_KEY_FILE = os.path.join(os.path.dirname(__file__), "kd", "enclave_enc_key.json")


def load_or_create_enclave_key() -> tuple[SecretKey, PublicKey]:
    env = os.getenv("ENCLAVE_ENC_SECRET")
    if env:
        sk = SecretKey.from_bytes(b64d(env))
        return sk, sk.public_key()

    if os.path.exists(ENCLAVE_KEY_FILE):
        with open(ENCLAVE_KEY_FILE) as f:
            d = json.load(f)
        sk = SecretKey.from_bytes(b64d(d["secret_key"]))
        return sk, sk.public_key()

    sk = SecretKey.random()
    os.makedirs(os.path.dirname(ENCLAVE_KEY_FILE), exist_ok=True)
    with open(ENCLAVE_KEY_FILE, "w") as f:
        json.dump({
            "secret_key": b64e(sk.to_secret_bytes()),
            "public_key": b64e(bytes(sk.public_key())),
        }, f, indent=2)
    return sk, sk.public_key()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_encryption_seam.py::test_load_enclave_key_from_env_is_deterministic -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tee/enclave_keys.py tests/test_encryption_seam.py
git commit -m "feat: enclave receiving keypair (load-or-create)"
```

---

## Task 6: Real `decrypt_inputs` in the encryption seam

**Files:**
- Create: `tests/_umbral_helpers.py`
- Modify: `tee/encryption_seam.py`
- Modify: `tests/test_encryption_seam.py` (add tests)

- [ ] **Step 1: Write the shared test helper**

Create `tests/_umbral_helpers.py`:

```python
"""Build a fully-encrypted request for tests: write a fresh umbral_state.json,
encrypt a payload under its master key, and produce >= threshold cfrags. Returns
everything a /compute call or the seam needs."""
import json
import os
import tempfile

from umbral import (PublicKey, SecretKey, VerifiedKeyFrag, encrypt, reencrypt)

from keygen import run_keygen
from umbral_io import b64d, b64e


def setup_encrypted_request(payload: dict, shares: int = 3, threshold: int = 2) -> dict:
    enclave_sk = SecretKey.random()
    fd, path = tempfile.mkstemp(suffix="_state.json")
    os.close(fd)
    run_keygen(enclave_sk.public_key(), shares, threshold, path)

    with open(path) as f:
        d = json.load(f)
    master_pk = PublicKey.from_bytes(b64d(d["master_public_key"]))
    plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    capsule, ciphertext = encrypt(master_pk, plaintext)
    kfrags = [VerifiedKeyFrag.from_verified_bytes(b64d(k)) for k in d["kfrags"][:threshold]]
    cfrags_b64 = [b64e(bytes(reencrypt(capsule=capsule, kfrag=k))) for k in kfrags]

    return {
        "state_path": path,
        "enclave_secret_b64": b64e(enclave_sk.to_secret_bytes()),
        "capsule_b64": b64e(bytes(capsule)),
        "ciphertext_b64": b64e(ciphertext),
        "cfrags_b64": cfrags_b64,
    }
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_encryption_seam.py`:

```python
import pytest

from tee.encryption_seam import decrypt_inputs
from umbral_io import load_public_state
from tests._umbral_helpers import setup_encrypted_request

PAYLOAD = {"dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000}


def test_decrypt_inputs_roundtrip():
    s = setup_encrypted_request(PAYLOAD, shares=3, threshold=2)
    state = load_public_state(s["state_path"])
    enclave_sk = SecretKey.from_bytes(b64d(s["enclave_secret_b64"]))
    out = decrypt_inputs(s["capsule_b64"], s["ciphertext_b64"], s["cfrags_b64"],
                         enclave_sk, state)
    assert out == PAYLOAD


def test_decrypt_inputs_drops_corrupt_cfrag_but_succeeds_at_threshold():
    s = setup_encrypted_request(PAYLOAD, shares=3, threshold=2)
    state = load_public_state(s["state_path"])
    enclave_sk = SecretKey.from_bytes(b64d(s["enclave_secret_b64"]))
    bad = b64e(b"\x00" + b64d(s["cfrags_b64"][0])[1:])  # flip first byte
    cfrags = [bad, s["cfrags_b64"][0], s["cfrags_b64"][1]]  # 1 bad + 2 good
    out = decrypt_inputs(s["capsule_b64"], s["ciphertext_b64"], cfrags, enclave_sk, state)
    assert out == PAYLOAD


def test_decrypt_inputs_raises_below_threshold():
    s = setup_encrypted_request(PAYLOAD, shares=3, threshold=2)
    state = load_public_state(s["state_path"])
    enclave_sk = SecretKey.from_bytes(b64d(s["enclave_secret_b64"]))
    with pytest.raises(Exception):
        decrypt_inputs(s["capsule_b64"], s["ciphertext_b64"], s["cfrags_b64"][:1],
                       enclave_sk, state)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_encryption_seam.py -q`
Expected: FAIL — `decrypt_inputs` currently returns its argument unchanged (wrong signature / wrong result).

- [ ] **Step 4: Replace the seam implementation**

Replace the entire contents of `tee/encryption_seam.py` with:

```python
"""Encryption seam — the decryption DON / re-encryption boundary (white-paper step 6).

decrypt_inputs takes the on-chain ciphertext (capsule + ciphertext) and the
re-encryption fragments (cfrags) gathered by the oracle, verifies the cfrags,
recovers the plaintext inputs INSIDE the enclave using the enclave's receiving
secret, and returns the input dict. Keeping this boundary explicit means the rest
of the TEE service does not change."""
import json
from typing import Any, Dict

from umbral import Capsule, CapsuleFrag, SecretKey, decrypt_reencrypted

from umbral_io import b64d


def decrypt_inputs(capsule_b64: str, ciphertext_b64: str, cfrags_b64: list[str],
                   enclave_sk: SecretKey, state: Dict[str, Any]) -> Dict[str, Any]:
    capsule = Capsule.from_bytes(b64d(capsule_b64))
    ciphertext = b64d(ciphertext_b64)

    verified = []
    for cb in cfrags_b64:
        try:
            cfrag = CapsuleFrag.from_bytes(b64d(cb))
            verified.append(cfrag.verify(
                capsule,
                verifying_pk=state["authority_pk"],
                delegating_pk=state["master_pk"],
                receiving_pk=state["enclave_pk"],
            ))
        except Exception:
            continue  # drop a corrupt/lying node's fragment

    plaintext = decrypt_reencrypted(
        receiving_sk=enclave_sk,
        delegating_pk=state["master_pk"],
        capsule=capsule,
        verified_cfrags=verified,
        ciphertext=ciphertext,
    )
    return json.loads(plaintext.decode("utf-8"))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_encryption_seam.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add tee/encryption_seam.py tests/_umbral_helpers.py tests/test_encryption_seam.py
git commit -m "feat: real threshold decryption in the encryption seam"
```

---

## Task 7: TEE service — `/enclave_pubkey` + encrypted `/compute`

**Files:**
- Modify: `tee/tee_service.py`
- Modify: `tests/test_tee_compute.py:41-65` (replace endpoint test)
- Modify: `tests/test_oracle_don.py:34-54` (replace endpoint test)

- [ ] **Step 1: Write the failing test**

In `tests/test_tee_compute.py`, replace `test_compute_endpoint_matches_pure_function_and_verifies_signature` (lines 41-65) with:

```python
import os
from fastapi.testclient import TestClient


def test_compute_endpoint_encrypted_roundtrip_and_signature():
    from tests._umbral_helpers import setup_encrypted_request
    payload = {"dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000}
    s = setup_encrypted_request(payload, shares=3, threshold=2)

    os.environ["TEE_PRIVATE_KEY"] = (
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    )
    os.environ["ENCLAVE_ENC_SECRET"] = s["enclave_secret_b64"]
    os.environ["UMBRAL_STATE"] = s["state_path"]

    from tee.tee_service import app
    from tee.signing import result_hash
    import abi_digest as ad
    from umbral_io import b64d

    client = TestClient(app)
    resp = client.post("/compute", json={
        "id": 1,
        "capsule": s["capsule_b64"],
        "ciphertext": s["ciphertext_b64"],
        "cfrags": s["cfrags_b64"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True

    expected = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    assert body["result"] == expected
    rh = result_hash(expected)
    assert body["resultHash"] == rh.hex()
    ch = ad.ciphertext_hash(b64d(s["capsule_b64"]), b64d(s["ciphertext_b64"]))
    digest = ad.tee_digest(1, ch, rh)
    assert ad.recover_digest(digest, bytes.fromhex(body["teeSig"][2:])) == body["teeAddress"]


def test_enclave_pubkey_endpoint():
    from umbral import SecretKey
    from umbral_io import b64e
    sk = SecretKey.random()
    os.environ["ENCLAVE_ENC_SECRET"] = b64e(sk.to_secret_bytes())
    from tee.tee_service import app
    client = TestClient(app)
    resp = client.get("/enclave_pubkey")
    assert resp.json()["pubkey"] == b64e(bytes(sk.public_key()))
    del os.environ["ENCLAVE_ENC_SECRET"]
```

In `tests/test_oracle_don.py`, replace `test_tee_endpoint_signs_request_bound_digest` (lines 34-54) with:

```python
def test_tee_endpoint_signs_request_bound_digest():
    import os
    from fastapi.testclient import TestClient
    from tests._umbral_helpers import setup_encrypted_request
    from tee.signing import result_hash
    import abi_digest as ad
    from umbral_io import b64d

    payload = {"dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000}
    s = setup_encrypted_request(payload, shares=3, threshold=2)
    os.environ["TEE_PRIVATE_KEY"] = (
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    )
    os.environ["ENCLAVE_ENC_SECRET"] = s["enclave_secret_b64"]
    os.environ["UMBRAL_STATE"] = s["state_path"]

    from tee.tee_service import app
    client = TestClient(app)
    resp = client.post("/compute", json={
        "id": 1, "capsule": s["capsule_b64"],
        "ciphertext": s["ciphertext_b64"], "cfrags": s["cfrags_b64"],
    })
    body = resp.json()
    assert body["success"] is True
    rh = result_hash(body["result"])
    ch = ad.ciphertext_hash(b64d(s["capsule_b64"]), b64d(s["ciphertext_b64"]))
    digest = ad.tee_digest(1, ch, rh)
    sig = bytes.fromhex(body["teeSig"][2:])
    assert ad.recover_digest(digest, sig) == body["teeAddress"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tee_compute.py tests/test_oracle_don.py -q`
Expected: FAIL — `/compute` rejects the new body / `/enclave_pubkey` missing.

- [ ] **Step 3: Update the TEE service**

Replace `tee/tee_service.py` with:

```python
"""TEE compute enclave (FastAPI).

GET  /enclave_pubkey -> the enclave's Umbral receiving public key (base64).
POST /compute        -> decrypt the re-encrypted inputs inside the enclave, run the
                        waterfall, return result + keccak hash + a TEE signature BOUND
                        to the request (id + ciphertextHash + resultHash).
GET  /tee_address    -> the enclave's signing address.
"""
from fastapi import FastAPI
from pydantic import BaseModel

import abi_digest as ad
from tee.compute import compute_waterfall
from tee.enclave_keys import load_or_create_enclave_key
from tee.encryption_seam import decrypt_inputs
from tee.signing import load_or_create_key, result_hash, sign_request_bound
from umbral_io import b64d, b64e, load_public_state

app = FastAPI(title="RMBS Confidential Compute TEE")

PRIVATE_KEY, TEE_ADDRESS = load_or_create_key()
print(f"TEE signing address: {TEE_ADDRESS}")
print("Set this as TEE_ADDRESS in .env (and pass it to deploy) before deploying.")


class ComputeRequest(BaseModel):
    id: int
    capsule: str
    ciphertext: str
    cfrags: list[str]


@app.get("/tee_address")
def tee_address():
    return {"success": True, "address": TEE_ADDRESS}


@app.get("/enclave_pubkey")
def enclave_pubkey():
    _, pk = load_or_create_enclave_key()
    return {"success": True, "pubkey": b64e(bytes(pk))}


@app.post("/compute")
def compute(req: ComputeRequest):
    try:
        enclave_sk, _ = load_or_create_enclave_key()
        state = load_public_state()
        inp = decrypt_inputs(req.capsule, req.ciphertext, req.cfrags, enclave_sk, state)
        result = compute_waterfall(
            iaf=float(inp["iaf"]), paf=float(inp["paf"]), period=int(inp["period"]))
        h = result_hash(result)
        ch = ad.ciphertext_hash(b64d(req.capsule), b64d(req.ciphertext))
        tee_sig = sign_request_bound(req.id, ch, h, PRIVATE_KEY)
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tee_compute.py tests/test_oracle_don.py -q`
Expected: PASS (all tests in both files).

- [ ] **Step 5: Commit**

```bash
git add tee/tee_service.py tests/test_tee_compute.py tests/test_oracle_don.py
git commit -m "feat: TEE serves enclave pubkey and decrypts re-encrypted inputs"
```

---

## Task 8: Decryption node (`decryption_node.py`) + launcher

**Files:**
- Create: `decryption_node.py`
- Create: `run_decryption_nodes.py`
- Test: `tests/test_decryption_node.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_decryption_node.py`:

```python
import json
import os

from fastapi.testclient import TestClient
from umbral import Capsule, CapsuleFrag

from umbral_io import b64d, load_public_state
from tests._umbral_helpers import setup_encrypted_request


def test_reencrypt_returns_verifiable_cfrag():
    s = setup_encrypted_request({"iaf": 1, "paf": 2, "period": 1, "dealId": "D"},
                                shares=3, threshold=2)
    with open(s["state_path"]) as f:
        kfrag_b64 = json.load(f)["kfrags"][0]
    os.environ["KFRAG"] = kfrag_b64

    from decryption_node import app
    client = TestClient(app)
    resp = client.post("/reencrypt", json={"capsule": s["capsule_b64"]})
    assert resp.status_code == 200
    cfrag_b64 = resp.json()["cfrag"]

    state = load_public_state(s["state_path"])
    capsule = Capsule.from_bytes(b64d(s["capsule_b64"]))
    cfrag = CapsuleFrag.from_bytes(b64d(cfrag_b64))
    # Raises if invalid; passing means the node produced a genuine fragment.
    cfrag.verify(capsule, verifying_pk=state["authority_pk"],
                 delegating_pk=state["master_pk"], receiving_pk=state["enclave_pk"])
    del os.environ["KFRAG"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_decryption_node.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'decryption_node'`.

- [ ] **Step 3: Write the implementation**

Create `decryption_node.py`:

```python
"""One decryption-DON node (white-paper steps 3-4): a pure re-encryption capability.

Holds exactly one key fragment (KFRAG, base64) and, given a capsule, returns a
re-encryption fragment (cfrag). Stateless; no chain access; no funded account.
Set CORRUPTED=1 to flip a byte and demonstrate that the oracle's cfrag
verification rejects a faulty node while the quorum still succeeds.

Run one per node:
  KFRAG=<base64> uvicorn decryption_node:app --host 0.0.0.0 --port 5000
"""
import os

from fastapi import FastAPI
from pydantic import BaseModel
from umbral import Capsule, VerifiedKeyFrag, reencrypt

from umbral_io import b64d, b64e

app = FastAPI(title="RMBS Decryption Node")


class ReencryptRequest(BaseModel):
    capsule: str


def _load_kfrag() -> VerifiedKeyFrag:
    kfrag_b64 = os.environ["KFRAG"]
    return VerifiedKeyFrag.from_verified_bytes(b64d(kfrag_b64))


@app.post("/reencrypt")
def reencrypt_capsule(data: ReencryptRequest):
    kfrag = _load_kfrag()
    capsule = Capsule.from_bytes(b64d(data.capsule))
    cfrag = reencrypt(capsule=capsule, kfrag=kfrag)
    cfrag_bytes = bytes(cfrag)
    if os.getenv("CORRUPTED", "0") == "1":
        corrupted = bytearray(cfrag_bytes)
        corrupted[0] ^= 0xFF
        cfrag_bytes = bytes(corrupted)
    return {"cfrag": b64e(cfrag_bytes)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_decryption_node.py -q`
Expected: PASS.

- [ ] **Step 5: Write the launcher (no test — operational script)**

Create `run_decryption_nodes.py`:

```python
"""Launch N decryption nodes, one per kfrag in kd/umbral_state.json.

  python run_decryption_nodes.py            # all kfrags, ports 5000..
  BASE_PORT=5000 NUM_NODES=3 python run_decryption_nodes.py
"""
import json
import os
import subprocess

from umbral_io import DEFAULT_STATE

BASE_PORT = int(os.getenv("BASE_PORT", "5000"))


def main():
    with open(DEFAULT_STATE) as f:
        kfrags = json.load(f)["kfrags"]
    n = int(os.getenv("NUM_NODES", len(kfrags)))
    n = min(n, len(kfrags))

    procs = []
    for i in range(n):
        port = BASE_PORT + i
        env = os.environ.copy()
        env["KFRAG"] = kfrags[i]
        cmd = ["uvicorn", "decryption_node:app", "--host", "0.0.0.0", "--port", str(port)]
        print(f"Starting decryption node {i} on port {port}")
        procs.append(subprocess.Popen(cmd, env=env))

    print("PIDs:", [p.pid for p in procs], "— Ctrl+C to stop")
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Commit**

```bash
git add decryption_node.py run_decryption_nodes.py tests/test_decryption_node.py
git commit -m "feat: decryption node re-encryption capability + launcher"
```

---

## Task 9: Oracle agent — collect cfrags and forward to the TEE

**Files:**
- Modify: `oracle_agent.py`
- Test: covered by `tests/test_umbral_io.py` (add a `verify_cfrags` test); the HTTP loop is exercised manually per RUNBOOK.

- [ ] **Step 1: Write the failing test for cfrag filtering**

Append to `tests/test_umbral_io.py`:

```python
def test_verify_cfrags_keeps_good_drops_corrupt():
    from tests._umbral_helpers import setup_encrypted_request
    s = setup_encrypted_request({"iaf": 1, "paf": 2, "period": 1, "dealId": "D"},
                                shares=3, threshold=2)
    state = uio.load_public_state(s["state_path"])
    good = s["cfrags_b64"]
    corrupt = uio.b64e(b"\x00" + uio.b64d(good[0])[1:])
    kept = uio.verify_cfrags(s["capsule_b64"], [corrupt] + good, state)
    assert kept == good  # corrupt dropped, both good kept, order preserved
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `python -m pytest tests/test_umbral_io.py::test_verify_cfrags_keeps_good_drops_corrupt -q`
Expected: PASS (`verify_cfrags` was implemented in Task 1). If it fails, fix `verify_cfrags` in `umbral_io.py` before continuing.

- [ ] **Step 3: Rewrite `handle_request` and `main` env in `oracle_agent.py`**

Replace `handle_request` (lines 53-118) with:

```python
def handle_request(chain, tee_url, tee_address, oracle_pk, oracle_address,
                   chain_id, request_id, args, attested_ids, node_urls, state) -> bool:
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

    capsule_b64 = ad_b64(args["capsule"])
    ciphertext_b64 = ad_b64(args["ciphertext"])
    print(f"\n>>> [oracle {oracle_address}] ComputeRequested id={request_id} (encrypted)")

    # Steps 3-4: collect re-encryption fragments from the decryption DON.
    raw_cfrags = []
    for url in node_urls:
        try:
            r = requests.post(f"{url}/reencrypt", json={"capsule": capsule_b64}, timeout=10)
            r.raise_for_status()
            raw_cfrags.append(r.json()["cfrag"])
        except Exception as e:  # noqa: BLE001 - a down/bad node must not stop the quorum
            print(f"  decryption node {url} failed ({e}); skipping")
    cfrags = uio.verify_cfrags(capsule_b64, raw_cfrags, state)
    if len(cfrags) < state["threshold"]:
        print(f"  only {len(cfrags)}/{state['threshold']} valid cfrags; retry next loop")
        return False

    # Step 5-6: forward to the enclave, which decrypts + computes + signs.
    try:
        resp = requests.post(f"{tee_url}/compute", json={
            "id": request_id, "capsule": capsule_b64,
            "ciphertext": ciphertext_b64, "cfrags": cfrags,
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

    # Step 7: verify the enclave attestation is bound to THIS ciphertext before signing.
    ciphertext_hash = ad.ciphertext_hash(uio.b64d(capsule_b64), uio.b64d(ciphertext_b64))
    digest = ad.tee_digest(request_id, ciphertext_hash, result_hash)
    recovered = ad.recover_digest(digest, tee_sig)
    if recovered.lower() != tee_address.lower():
        print(f"  BAD TEE signature (got {recovered}, want {tee_address}); refusing")
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
    finalized, _, _, _ = chain.run(lambda w3, c: c.functions.getResult(request_id).call())
    if finalized or chain.run(lambda w3, c: c.functions.hasAttested(request_id, oracle_address).call()):
        attested_ids.add(request_id)
        return True
    print(f"  attest reverted (status 0); retry next loop")
    return False
```

Add a small helper near the top of `oracle_agent.py` (after the imports, before `load_abi`):

```python
def ad_b64(value) -> str:
    """ComputeRequested event bytes (HexBytes/bytes) -> base64 string for HTTP/JSON."""
    return uio.b64e(bytes(value))
```

Add these imports to the top of `oracle_agent.py` (alongside `import abi_digest as ad`):

```python
import umbral_io as uio
```

In `main`, after `tee_address = os.environ["TEE_ADDRESS"]` (line 128), add:

```python
    node_urls = [u.strip().rstrip("/") for u in os.environ["DECRYPTION_NODE_URLS"].split(",") if u.strip()]
    state = uio.load_public_state()
```

Update the two `handle_request(...)` call sites. The one in the event loop (line 147-148) becomes:

```python
                    if not handle_request(chain, tee_url, tee_address, oracle_pk, oracle_address,
                                          chain_id, ev["args"]["id"], ev["args"], attested_ids,
                                          node_urls, state):
```

Also update the startup print (after line 137) to show the nodes:

```python
    print(f"Decryption nodes: {node_urls} (threshold {state['threshold']})")
```

- [ ] **Step 4: Verify it imports and the unit test passes**

Run: `python -c "import oracle_agent" && python -m pytest tests/test_umbral_io.py -q`
Expected: import succeeds (no syntax/name errors); tests PASS.

- [ ] **Step 5: Commit**

```bash
git add oracle_agent.py tests/test_umbral_io.py
git commit -m "feat: oracle collects re-encryption fragments and forwards to the TEE"
```

---

## Task 10: User CLI — encrypt inputs and submit ciphertext

**Files:**
- Modify: `submit_request.py`

- [ ] **Step 1: Rewrite `submit_request.py`**

Replace `submit_request.py` with:

```python
"""User CLI: encrypt one period of cashflows under the decryption DON's master
public key and submit the ciphertext to the ConfidentialCompute contract.

Usage:
  python submit_request.py --iaf 500000 --paf 1000000 [--deal TEST_SEQ_2024] [--period 1]
"""
import argparse
import json
import os

from dotenv import load_dotenv
from umbral import encrypt
from web3 import Web3

from chain import connect_web3, get_rpc_urls
from umbral_io import b64e, load_public_state

load_dotenv()

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

    state = load_public_state()
    payload = json.dumps(
        {"dealId": args.deal, "period": args.period, "iaf": args.iaf, "paf": args.paf},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    capsule, ciphertext = encrypt(state["master_pk"], payload)

    w3 = connect_web3(get_rpc_urls())
    acct = w3.eth.account.from_key(PK)
    with open(ABI_PATH) as f:
        abi = json.load(f)["abi"]
    contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=abi)

    tx = contract.functions.submitRequest(bytes(capsule), bytes(ciphertext)).build_transaction(
        {
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "gas": 600000,
            "gasPrice": w3.eth.gas_price,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, PK)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"submitRequest tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    logs = contract.events.ComputeRequested().process_receipt(receipt)
    request_id = logs[0]["args"]["id"]
    print(f"Request submitted (encrypted): id={request_id} "
          f"(capsule {len(bytes(capsule))}B, ciphertext {len(bytes(ciphertext))}B)")
    print(f"Inputs are NOT on-chain in plaintext. Read the result later with:")
    print(f"  python read_result.py {request_id}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python -c "import ast; ast.parse(open('submit_request.py').read()); print('ok')"`
Expected: `ok` (full run requires the live chain + `umbral_state.json`; exercised manually per RUNBOOK).

- [ ] **Step 3: Run the full offline suite**

Run: `python -m pytest tests/ -q && ~/.foundry/bin/forge test -q`
Expected: all Python tests PASS and all Forge tests PASS.

- [ ] **Step 4: Commit**

```bash
git add submit_request.py
git commit -m "feat: user CLI encrypts inputs before submitting"
```

---

## Task 11: Documentation

**Files:**
- Modify: `RUNBOOK.md`
- Modify: `CLAUDE.md`
- Modify: `docs/FUTURE_WORK.md`

- [ ] **Step 1: Update `docs/FUTURE_WORK.md`**

Change the `## 2. Encryption + decryption DON` section's **Status** line to:

```markdown
**Status:** DONE (2026-06-17). Implemented per
`docs/superpowers/specs/2026-06-16-encryption-decryption-don-design.md` and
`docs/superpowers/plans/2026-06-17-encryption-decryption-don.md`.
```

Then add these new deferred items (renumber the existing #3/#4/#5 down by 3, or append):

```markdown
## 6. Split the oracle DON and decryption DON into two networks

**Status:** deferred. We reuse the oracle operators as decryption nodes (one operator
runs both `oracle_agent.py` and `decryption_node.py`). The white paper keeps them
separate so no single node can both decrypt inputs and attest results. Because they are
already two processes, splitting is mostly a deployment change (run the decryption nodes
on a distinct operator set).

## 7. Threshold DKG instead of trusted-dealer keygen

**Status:** deferred. `keygen.py` is a trusted dealer that transiently holds the full
master secret. Replace with a distributed key-generation ceremony among the nodes
(white-paper step 0, "decryption nodes jointly generate").

## 8. Per-request forward-secure enclave keys

**Status:** deferred. The enclave uses a static receiving key (kfrags pre-generated for
it). The white paper's per-request fresh enclave key (§3.1 forward-secure encryption)
limits a compromised enclave to only the requests assigned while compromised.

## 9. Off-chain ciphertext storage for large inputs

**Status:** deferred. `capsule + ciphertext` are stored on-chain as `bytes`; fine for the
small RMBS inputs but gas-costly for large future inputs. Store the ciphertext off-chain
and keep only `ciphertextHash` on-chain (the binding digest already carries it).
```

- [ ] **Step 2: Update `CLAUDE.md`**

- In "Deliberate simplifications", change the **Encryption is omitted** bullet to describe
  the new state:

```markdown
- **Encryption is implemented** (pyUmbral threshold proxy re-encryption). User inputs are
  encrypted under the decryption DON's master key, re-encrypted to the enclave, and
  decrypted only inside the TEE. Deliberate remaining simplifications: the oracle and
  decryption DONs share operators (FUTURE_WORK #6); keys come from a trusted-dealer
  `keygen.py`, not threshold DKG (#7); the enclave receiving key is static (#8).
```

- In the data/trust flow diagram, insert the decryption step:

```
submit_request.py (encrypt under master pk) → submitRequest(capsule, ciphertext) → ComputeRequested
  → N oracle_agent.py (each): collect >= m cfrags from decryption_node.py → POST TEE /compute
    → TEE decrypts inside enclave → run waterfall → sign(id, ciphertextHash, resultHash)
  → verify TEE sig → attest() → m-of-n quorum → finalized
  → read_result.py / getResult()
```

- In the component map, add: `keygen.py`, `decryption_node.py` / `run_decryption_nodes.py`,
  `tee/enclave_keys.py`, `umbral_io.py`; note `tee/encryption_seam.py` now does real
  threshold decryption and `/compute` takes `{id, capsule, ciphertext, cfrags}` plus
  `GET /enclave_pubkey`.

- In "the single most fragile thing: the cross-language signing seam", replace the
  `tee_digest` bullet with:

```markdown
- `tee_digest(id, ciphertextHash, resultHash)` ↔ `keccak256(abi.encode(id, ciphertextHash,
  resultHash))` — types `uint256,bytes32,bytes32`, where `ciphertextHash =
  keccak256(abi.encodePacked(capsule, ciphertext))` ↔ Python `keccak(capsule + ciphertext)`.
  This binds a result to the exact submitted ciphertext (inputs stay encrypted).
```

- Update the test counts note (pytest count rises; `forge test` = 6 tests now).

- [ ] **Step 3: Update `RUNBOOK.md`**

Add a first-time-only setup block (after the TEE is started, before deploy):

```markdown
### Key setup (first-time only, after the TEE is running)

The decryption DON's keys are bound to the enclave's receiving key, so the TEE must be up.

1. Start the TEE (`python -m tee.tee_service`) and confirm `GET /enclave_pubkey` responds.
2. Generate keys (choose shares = your node count, threshold = your quorum m):
   `python keygen.py --shares 3 --threshold 2`
   This writes `kd/umbral_state.json` (master/authority/enclave public keys + N kfrags).
3. If the enclave key file (`tee/kd/enclave_enc_key.json`) is ever deleted/regenerated,
   re-run keygen — kfrags are bound to the enclave's public key.
```

Add an every-run block for the decryption nodes:

```markdown
### Start the decryption nodes (every run)

`python run_decryption_nodes.py`  (one process per kfrag, ports 5000+).
Decryption nodes hold a key fragment and serve `/reencrypt`. They send NO chain
transactions, so — unlike oracle accounts — they do NOT need funding.
Set `DECRYPTION_NODE_URLS` in `.env`, e.g.
`DECRYPTION_NODE_URLS=http://127.0.0.1:5000,http://127.0.0.1:5001,http://127.0.0.1:5002`.
```

Update the `submit_request.py` usage note: inputs are now encrypted client-side; nothing
about iaf/paf appears on-chain.

Add troubleshooting entries:
- `FileNotFoundError: kd/umbral_state.json` → run `keygen.py` first.
- Oracle logs "only k/m valid cfrags" → fewer than `threshold` decryption nodes are up, or
  a node is `CORRUPTED=1`; start more nodes.
- "bad TEE sig" after the change → the enclave key was regenerated without re-running
  keygen, or `UMBRAL_STATE`/`.env` points at a stale state file.

- [ ] **Step 4: Commit**

```bash
git add RUNBOOK.md CLAUDE.md docs/FUTURE_WORK.md
git commit -m "docs: encryption + decryption DON runbook, CLAUDE, future work"
```

---

## Final verification

- [ ] **Run the full offline gate**

Run: `python -m pytest tests/ -q && ~/.foundry/bin/forge test -q`
Expected: all Python tests PASS, all 6 Forge tests PASS.

- [ ] **Sanity-check the cross-language seam by hand**

Run:
```bash
python -c "import abi_digest as ad; print(ad.ciphertext_hash(b'\xaa\xbb', b'\xcc\xdd').hex())"
~/.foundry/bin/forge test --match-test test_CiphertextHashMatchesPythonVector -vv
```
Expected: the Python hash equals `keccak256(0xaabbccdd)` and the Forge test passes — both
sides agree on `ciphertextHash`.
```
```
