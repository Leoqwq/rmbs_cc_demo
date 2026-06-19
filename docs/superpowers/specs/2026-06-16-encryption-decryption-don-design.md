# Encryption + Decryption DON — Design Spec

**Date:** 2026-06-16
**Status:** Design approved, ready for implementation plan
**Implements:** `docs/FUTURE_WORK.md` #2 ("Encryption + decryption DON")
**Reference:** Chainlink Confidential Compute white paper, Figure 1 (steps 0–7);
prior art `~/Desktop/ccc-demo` (pyUmbral threshold proxy re-encryption).

---

## 1. Goal

Restore the white paper's privacy path so that **user inputs are confidential from
everyone — including the node operators and the cloud host** — while keeping the existing
TEE-attested, m-of-n oracle-quorum result path exactly as it is today.

Concretely: today inputs travel plaintext end-to-end (`submit_request.py` puts
`iaf/paf/period/dealId` on-chain in the clear; oracles read them from the event and POST
them to the TEE). After this change, inputs are **encrypted under a threshold public key**,
re-encrypted to the enclave by a decryption DON, and decrypted **only inside the TEE**.

This slots into the seams already left for it: `tee/encryption_seam.py`
(`decrypt_inputs`, identity today) and the oracle agent's input-handling boundary.

## 2. Scope and non-goals

**In scope**
- Threshold proxy re-encryption of user inputs (pyUmbral), end-to-end.
- A decryption DON (re-encryption capability) reusing the existing node operator set.
- Key setup (trusted-dealer), enclave receiving key, and the cross-language digest change
  needed because plaintext inputs no longer exist on-chain.

**Out of scope (deliberate, documented as simplifications / future work)**
- **Result confidentiality.** The waterfall *result* stays plaintext + attested on-chain,
  exactly as today. This matches Figure 1: step 6 "produce an attestation over the result",
  step 7 "return the signed result to the application" — the result is attested, never
  encrypted. The §3.1 confidentiality guarantees are about *private inputs* only.
- **Two physically separate DONs.** The white paper keeps the oracle DON and decryption
  DON separate (so no single node can both decrypt inputs *and* attest results). We
  **reuse the oracle operator set as the decryption operators** (same N, same threshold
  value, shared keygen) to keep moving parts down. Documented as a divergence; "split into
  two independent DONs" is future work.
- **Threshold DKG.** Key shares (Umbral `kfrags`) are produced by a **trusted dealer**
  (`keygen.py`), not by a distributed key-generation ceremony among the nodes. Same
  simplification ccc-demo makes. White-paper step 0 ("decryption nodes jointly generate")
  is future work.
- **Per-request forward-secure enclave keys.** The enclave uses a **static** long-lived
  receiving keypair (kfrags pre-generated for it). The white paper's per-request fresh
  enclave key (§3.1 "forward-secure encryption") is future work.
- **Real hardware attestation (SEV-SNP).** Unchanged from today; tracked separately as
  FUTURE_WORK #1. The enclave's encryption key, like its signing key, is trusted by
  configuration, not by an attestation report.

## 3. Cryptographic design (pyUmbral)

We use **pyUmbral** threshold proxy re-encryption (PRE), the same scheme as ccc-demo. It
maps directly onto the white paper's threshold PKE:

- **Master keypair** (Umbral "delegating" key) ↔ the threshold master decryption key.
  The master public key is what users encrypt under.
- **kfrags** (key fragments) ↔ the secret key in threshold form. `generate_kfrags(...,
  threshold=m, shares=n)` splits delegation into `n` fragments; any `m` `cfrags` recover
  the plaintext. One kfrag per decryption node.
- **Authority/verifying key** ↔ the signer whose signature lets anyone *verify* a cfrag
  came from a genuine kfrag (catches a corrupt/lying node, as ccc-demo demonstrates).
- **Enclave receiving keypair** ↔ the compute enclave's own keypair (Figure 1 step 0,
  "each compute enclave generates its own public-private key pair"). kfrags are generated
  delegating *to this enclave's* public key.

We serialize the inputs to canonical JSON bytes and **Umbral-encrypt them directly** — no
separate AES hybrid layer. This is **not** because the inputs happen to be small; it is
because **Umbral's `encrypt()` is itself a KEM/DEM hybrid scheme**: it returns
`(capsule, ciphertext)` where the `capsule` encapsulates a symmetric key (KEM, fixed small
size) and the `ciphertext` is authenticated **symmetric** encryption under a key derived
from the capsule (DEM, **arbitrary payload size**). So Umbral already provides the
"AES layer" internally.

ccc-demo's extra step (random AES key → AES-encrypt payload → Umbral-encrypt the AES key)
is therefore a **redundant double hybrid** — it re-implements by hand what Umbral's DEM
already does. Encrypting the payload directly is cleaner and **scales to larger future
inputs natively**: the DEM handles bulk data, and proxy re-encryption operates only on the
`capsule` (fixed size), so cfrag generation/collection cost is **independent of input
size**. The thing that *does* scale with input size is on-chain storage cost, not the
crypto layering — see §10.

**The entire input payload is encrypted** (`dealId, period, iaf, paf` together as one
JSON blob). Nothing about the inputs — not even which deal/period — leaks on-chain. The
oracle and decryption nodes never see plaintext; only the TEE decrypts.

### 3.1 pyUmbral primitives used

```
encrypt(master_pk, plaintext)                       -> (capsule, ciphertext)        # user
generate_kfrags(delegating_sk=master_sk,            -> [kfrag_1 .. kfrag_n]         # keygen
                receiving_pk=enclave_pk,
                signer=authority_signer,
                threshold=m, shares=n)
reencrypt(capsule, kfrag)                            -> cfrag                        # decryption node
cfrag.verify(capsule, verifying_pk=authority_pk,    -> verified_cfrag               # oracle
             delegating_pk=master_pk,
             receiving_pk=enclave_pk)
decrypt_reencrypted(receiving_sk=enclave_sk,         -> plaintext                    # TEE
                    delegating_pk=master_pk,
                    capsule, verified_cfrags, ciphertext)
```

## 4. Architecture — mapping to Figure 1 (steps 0–7)

```
 Figure 1 step                                    this demo
─────────────────────────────────────────────────────────────────────────────────────────
0  decryption DON gen threshold key;          keygen.py (trusted dealer): master keypair +
   enclaves gen keypair                       authority signer + N kfrags for enclave_pk.
                                              TEE mints its static encryption keypair at
                                              startup and serves GET /enclave_pubkey.
1  user encrypts inputs under master pk       submit_request.py: encrypt(master_pk, json(inputs))
2  app assembles enc_inputs + code → request  ConfidentialCompute.submitRequest(capsule, ciphertext)
                                              -> stores bytes, emits ComputeRequested(id, capsule,
                                                 ciphertext, requester)
3  oracle verifies req, assigns enclave,      oracle_agent (each, independently): read (capsule,
   sends enc_inputs + pk_enclave to           ciphertext) from the event, POST {capsule} to each
   decryption nodes                           decryption node's /reencrypt
4  decryption nodes re-encrypt to enclave pk  decryption_node.py: reencrypt(capsule, its kfrag) -> cfrag
5  oracle forwards reenc_inputs to enclave    oracle_agent: collect >= m verified cfrags, POST
                                              {id, capsule, ciphertext, cfrags} to TEE /compute
6  enclave decrypts + computes + attests      tee_service /compute: encryption_seam.decrypt_inputs
                                              (REAL umbral decrypt) -> compute_waterfall ->
                                              sign(id, ciphertextHash, resultHash)
7  oracle verifies attestation, quorum-signs  oracle_agent: verify TEE sig over new digest, then
   result, return to application              attest() on-chain  (UNCHANGED quorum/finalize path)
```

### 4.1 Node topology (two processes per operator)

Each of the **N node operators** runs **two processes**, sharing operator identity/config
but isolated by runtime role (each kfrag stays in its own process, so the m-of-n threshold
is real):

- **`decryption_node.py`** *(new)* — pure re-encryption capability, modeled on ccc-demo's
  `nodes/node.py`. Holds exactly one `KFRAG` (base64, from env). Exposes `POST /reencrypt`
  `{capsule}` → `{cfrag}`. Stateless, no chain access, no funded account. The set of N of
  these *is* the decryption DON.
- **`oracle_agent.py`** *(existing, extended)* — the oracle node: the watch→compute→attest
  loop. Now also drives re-encryption: per request it collects cfrags from all N decryption
  nodes, verifies each, and forwards `>= m` of them to the TEE.

Each oracle drives the flow **independently** (no central orchestrator — more decentralized
than ccc-demo's single `contract_listener`). Consequence: the TEE performs one decryption
per oracle. Acceptable for a demo; deterministic, so every oracle gets the same result and
TEE signature.

The "reuse the oracle DON as the decryption DON" decision lives at the topology level: same
operators, same N, same threshold value `m`, one shared `keygen.py`. Operator *i* runs both
`oracle_agent` (oracle identity `ORACLE_KEY_i`) and `decryption_node` (fragment `KFRAG_i`).

## 5. Component changes

### 5.1 `keygen.py` (new, first-time-only setup)

Trusted-dealer setup. Steps:
1. Require the TEE to be running; `GET {TEE_URL}/enclave_pubkey` to fetch `enclave_pk`.
2. Generate master keypair (`master_sk`, `master_pk`) and an authority signer
   (`authority_sk`, `authority_pk`).
3. `kfrags = generate_kfrags(master_sk, enclave_pk, authority_signer, threshold=m, shares=n)`.
4. Write `kd/umbral_state.json`:
   `{ master_public_key, authority_public_key, enclave_public_key, threshold }`
   (all base64). This is the **public** material consumed by user/oracle/TEE.
5. Emit one base64 `KFRAG_i` per node — written into `kd/umbral_state.json` as a `kfrags`
   array (matching ccc-demo's `run_nodes.py` convention) and/or echoed for `.env`.

`master_sk` / `authority_sk` are the dealer's secrets; they are **not** needed at runtime by
any node (only `master_pk`, `authority_pk`, `enclave_pk` are). They may be discarded after
keygen (a crude stand-in for "no single party holds the master key"; real threshold DKG is
future work).

### 5.2 TEE — enclave receiving key + new `/compute`

- **Enclave encryption keypair** (new module, e.g. `tee/enclave_keys.py`, mirroring
  `tee/signing.py`): `load_or_create_enclave_key()` persists an Umbral keypair to
  `tee/kd/enclave_enc_key.json`, returns `(enclave_sk, enclave_pk)`. (Distinct from the
  ECDSA *signing* key, which is unchanged.)
- **`GET /enclave_pubkey`** → `{ "address"/"pubkey": base64(enclave_pk) }`. Consumed by
  `keygen.py`.
- **`POST /compute`** request schema changes:
  - **Old:** `{ id, dealId, period, iaf, paf }`
  - **New:** `{ id, capsule, ciphertext, cfrags }` (capsule/ciphertext base64; cfrags a
    base64 list)
  Handler: call `encryption_seam.decrypt_inputs(...)` to recover the plaintext input dict,
  then `compute_waterfall(...)` as today, then sign the **new** request-bound digest
  (§6) over `(id, ciphertextHash, resultHash)`.

### 5.3 `tee/encryption_seam.py` — real decryption

`decrypt_inputs` stops being identity. New signature (concrete shape to be finalized in the
plan):

```python
def decrypt_inputs(capsule_b64, ciphertext_b64, cfrags_b64,
                   enclave_sk, master_pk, authority_pk, enclave_pk) -> dict:
    # 1. parse capsule, ciphertext, each cfrag
    # 2. verify each cfrag (verifying_pk=authority_pk, delegating_pk=master_pk,
    #    receiving_pk=enclave_pk); keep verified ones
    # 3. decrypt_reencrypted(enclave_sk, master_pk, capsule, verified_cfrags, ciphertext)
    # 4. json.loads -> {"dealId":..., "period":..., "iaf":..., "paf":...}
```

The public keys (`master_pk`, `authority_pk`, `enclave_pk`) are loaded from
`kd/umbral_state.json` (enclave_sk from `tee/kd/enclave_enc_key.json`). This keeps the
boundary explicit: the TEE service hands the seam the encrypted material + keys, the seam
returns plaintext inputs.

### 5.4 `decryption_node.py` (new)

Modeled on ccc-demo `nodes/node.py`:
- Reads its single fragment from env (`KFRAG`, base64).
- `POST /reencrypt {capsule}` → `reencrypt(capsule, kfrag)` → `{cfrag: base64}`.
- Optional `CORRUPTED=1` switch (flip a byte) to demo a faulty node, as ccc-demo does — the
  oracle's cfrag `verify` rejects it, and the quorum still succeeds. (Nice-to-have for the
  demo; include if cheap.)
- A `run_decryption_nodes.py` launcher (ccc-demo `run_nodes.py` shape) starting N uvicorn
  instances on consecutive ports, each with its `KFRAG_i`.

### 5.5 `oracle_agent.py` — re-encryption collection

`handle_request` changes between reading the event and calling the TEE:
1. Read `capsule`, `ciphertext` from the `ComputeRequested` event (replacing
   `dealId/period/iaf/paf`).
2. For each decryption node URL (`DECRYPTION_NODE_URLS`), `POST /reencrypt {capsule}`,
   collect `cfrag`s.
3. `cfrag.verify(...)` each against `authority_pk/master_pk/enclave_pk` (from
   `umbral_state.json`); keep verified. If `< m` verified cfrags → retry next loop.
4. `POST {id, capsule, ciphertext, cfrags(>=m)}` to TEE `/compute`.
5. Verify the TEE signature over the **new** digest `(id, ciphertextHash, resultHash)`
   where `ciphertextHash = keccak(capsule || ciphertext)` (§6), then `attest()` — the
   on-chain quorum/finalize path is **unchanged**.

The agent loads `master_pk/authority_pk/enclave_pk` from `umbral_state.json` at startup.

### 5.6 `submit_request.py` — user-side encryption

1. Load `master_pk` from `kd/umbral_state.json`.
2. `plaintext = json.dumps({"dealId","period","iaf","paf"}, sort_keys, separators).encode()`
3. `capsule, ciphertext = encrypt(master_pk, plaintext)`
4. `submitRequest(capsule_bytes, ciphertext_bytes)` (was `submitRequest(dealId, period,
   iaf, paf)`). Print the returned id as today.

### 5.7 `contracts/ConfidentialCompute.sol`

- `Request` struct: replace `string dealId; uint256 period; uint256 iaf; uint256 paf;`
  with `bytes capsule; bytes ciphertext;` (keep `requester`, `resultStored`, `finalized`,
  `resultHash`, `resultJson`, `attestationCount`).
- `submitRequest(bytes calldata capsule, bytes calldata ciphertext) returns (uint256 id)`.
- `event ComputeRequested(uint256 indexed id, bytes capsule, bytes ciphertext, address requester)`.
- `attest(...)` first-call branch: replace the old teeDigest with
  ```solidity
  bytes32 ciphertextHash = keccak256(abi.encodePacked(r.capsule, r.ciphertext));
  bytes32 teeDigest = keccak256(abi.encode(id, ciphertextHash, resultHash));
  ```
  Everything else (`resultJson` hash check, oracleSig recovery, `isOracle`, dedupe,
  threshold finalize) is unchanged.

### 5.8 `abi_digest.py` + Forge sign helpers

- New `tee_digest(id, ciphertext_hash, result_hash)` →
  `keccak(eth_abi.encode(["uint256","bytes32","bytes32"], [id, ciphertext_hash, result_hash]))`.
- New helper `ciphertext_hash(capsule_bytes, ciphertext_bytes)` →
  `keccak(capsule_bytes + ciphertext_bytes)` (raw concat ↔ Solidity
  `abi.encodePacked(bytes,bytes)`).
- `oracle_digest(id, result_hash)` unchanged.
- Update the Forge test sign helpers to the new teeDigest types/order.

### 5.9 `requirements.txt`

Add the NuCypher pyUmbral package — PyPI name `umbral` (module `umbral`, e.g.
`umbral==0.3.0`), the same one ccc-demo imports (`from umbral import ...`). Pin the version.

## 6. The cross-language signing seam (the most fragile thing — handle with care)

CLAUDE.md flags this as the single most fragile seam. This change moves it; all three sides
must move **together** and byte-match:

| | Old | New |
|---|---|---|
| **TEE digest** | `keccak(abi.encode(id, dealId, period, iaf, paf, resultHash))` types `uint256,string,uint256,uint256,uint256,bytes32` | `keccak(abi.encode(id, ciphertextHash, resultHash))` types `uint256,bytes32,bytes32` |
| **ciphertextHash** | — | `keccak(capsule ‖ ciphertext)` ↔ `keccak256(abi.encodePacked(capsule, ciphertext))` (raw byte concat, **no** length prefix) |
| **oracle digest** | `keccak(abi.encode(id, resultHash))` | unchanged |

Properties preserved: the TEE signature still **binds the result to the exact submitted
inputs** — now via the hash of the exact ciphertext, instead of via the plaintext fields.
EIP-191 personal-sign prefix, `r‖s‖v` layout, and the `resultJson` canonical-JSON hash
check are all unchanged.

Verification (per CLAUDE.md, no single automated test crosses both languages): reason it
through + Forge tests (with updated sign helpers) + a local hash compare of
`ciphertext_hash` (Python) against a Solidity `abi.encodePacked` in a Forge test.

## 7. Data flow (encrypted), end to end

```
submit_request.py
  inputs={dealId,period,iaf,paf} → encrypt(master_pk) → (capsule, ciphertext)
  → submitRequest(capsule, ciphertext) → ComputeRequested(id, capsule, ciphertext, requester)

each oracle_agent (independently):
  read (capsule, ciphertext) from event
  → for each decryption_node: POST /reencrypt {capsule} → cfrag
  → verify cfrags (authority/master/enclave pk); keep >= m
  → POST TEE /compute {id, capsule, ciphertext, cfrags}
       TEE: decrypt_inputs → {dealId,period,iaf,paf} → compute_waterfall
            → resultHash → sign(id, keccak(capsule‖ciphertext), resultHash)
  → verify TEE sig over (id, ciphertextHash, resultHash)
  → attest(id, resultHash, resultJson, teeSig, oracleSig)

contract: first attest stores result + verifies TEE sig; each adds one oracle sig;
          finalizes at threshold  (UNCHANGED)

read_result.py / getResult(): plaintext result, as today  (UNCHANGED)
```

## 8. Testing strategy

The offline suites (`pytest tests/`, `forge test`) remain the gate.

**New / changed Python tests**
- **Umbral round-trip** (pure): `encrypt(master_pk, payload)` → `reencrypt` with `m` of `n`
  kfrags → `decrypt_inputs` recovers the exact payload. Assert `< m` cfrags fails to decrypt.
- **Corrupt-node tolerance**: a corrupted cfrag fails `verify`; with `n-1` honest nodes and
  threshold `m`, the quorum still recovers the payload (mirrors ccc-demo's demo).
- **`/compute` with encrypted inputs**: feed `{id, capsule, ciphertext, cfrags}`, assert the
  result equals the pure `compute_waterfall` on the original inputs, and the TEE signature
  verifies under the **new** digest.
- **ciphertextHash compare**: `abi_digest.ciphertext_hash(capsule, ciphertext)` equals an
  independent `keccak(capsule+ciphertext)` (and is exercised against Solidity in Forge).
- **oracle cfrag collection**: given mocked decryption nodes, the agent keeps only verified
  cfrags and forwards `>= m`.

**Changed Solidity tests**
- `submitRequest(capsule, ciphertext)` + `attest` happy path with the new teeDigest (update
  the Forge sign helper).
- `test_QuorumFinalizesAtThreshold` and the bad-TEE-sig test, updated to the new digest.
- A Forge assertion that `abi.encodePacked(capsule, ciphertext)` hashed on-chain matches a
  vector produced by Python (the cross-language check for `ciphertextHash`).

**Manual (live chain + TEE + nodes)**: per RUNBOOK — run keygen, start decryption nodes,
start oracle agents, `submit_request.py`, watch finalize, `read_result.py`. Confirm the
chain shows only ciphertext (no plaintext iaf/paf anywhere on-chain or in events).

## 9. Documentation updates

- **`RUNBOOK.md`**: first-time-only `keygen.py` step (after the TEE is up, before deploy);
  starting the N decryption nodes (`run_decryption_nodes.py`, `KFRAG_i` env); note that
  decryption nodes need **no funding** (no chain txs); updated `submit_request.py` usage;
  troubleshooting (missing `umbral_state.json`, `< m` cfrags, enclave key mismatch).
- **`CLAUDE.md`**: update the encryption note (no longer "omitted"); the seam section to the
  new digest types; the component map (`keygen.py`, `decryption_node.py`, real
  `encryption_seam`, new `/compute` schema, `/enclave_pubkey`); the data-flow diagram.
- **`docs/FUTURE_WORK.md`**: mark #2 done; spawn follow-ups — split into two independent
  DONs; threshold DKG (replace trusted dealer); per-request forward-secure enclave keys;
  (output confidentiality if ever wanted).

## 10. Risks / things to watch

- **Cross-language digest** (§6) — the highest-risk change; verify by reasoning + Forge +
  hash compare before declaring done.
- **pyUmbral serialization** — capsule/ciphertext/cfrag/key byte formats must round-trip
  through base64 (Python) and `bytes` (Solidity storage only stores the raw blobs; it does
  not parse Umbral). Pin the `umbral-pre` version.
- **kfrag ↔ enclave key binding** — kfrags are generated for a *specific* `enclave_pk`. If
  the enclave's encryption key is regenerated (file deleted), keygen must be re-run.
  Document in RUNBOOK.
- **N decryption calls** — each oracle decrypts independently; fine for the demo but note
  it. OCR-style aggregation (FUTURE_WORK #4) would also collapse this.
- **Trusted-dealer master key** — `keygen.py` transiently holds the full master secret;
  this is the documented DKG simplification, not a production posture.
- **On-chain ciphertext storage scales with input size** — `capsule + ciphertext` are
  stored on-chain as `bytes`. Fine for the small RMBS inputs, but for larger future inputs
  (multi-KB/MB) the per-byte storage gas becomes the real cost driver (not the crypto). The
  fix is **store the ciphertext off-chain and keep only a hash pointer on-chain** — and our
  binding digest already carries `ciphertextHash`, so the seam is ready for it. This is
  orthogonal to the encryption scheme; recorded as future work, not addressed here.
