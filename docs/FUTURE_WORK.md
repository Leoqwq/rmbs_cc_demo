# Future Work — roadmap toward full Confidential Compute

This demo deliberately ships a simplified slice and adds fidelity in stages. Items
below are **explicitly deferred**, in rough priority order. Each is independent
enough to be its own spec → plan → implementation cycle.

---

## 1. Real TEE attestation (SEV-SNP), replacing the bare ECDSA-key trust  ← next after Oracle DON

**Status:** deferred (recorded 2026-06-07). Oracle DON is being built first; see
`docs/superpowers/plans/2026-06-07-oracle-don-attestation.md` decision **D3**.

**Problem today.** The TEE service generates a plain Ethereum key
(`tee/kd/tee_signing_key.json`) and we hardcode its address as `teeAddress` in the
contract. An ECDSA signature only proves "the holder of that key signed this." It
does **not** prove the signer ran inside a genuine enclave, that memory was
encrypted (operator couldn't peek), or that the **unmodified** waterfall code ran.
Anyone who exfiltrates the key (malicious cloud operator, rooted VM) can forge
valid results. This is the "TEEs alone are too brittle" gap the white paper closes.

**Goal.** Anchor trust in AMD hardware instead of "a key we typed into the
contract". `tee-node` is already an AMD **SEV-SNP** Confidential VM, so the
capability exists — we just don't fetch/verify the attestation report yet.

**Approach (attestation-bootstrapped signing key).**
1. On TEE startup, generate the ECDSA signing keypair **inside** the enclave.
2. Request a SEV-SNP **attestation report** with `report_data = keccak(pubkey)`.
   The report is signed by the chip's **VCEK** (Versioned Chip Endorsement Key),
   certified by AMD's cert chain (ARK → ASK → VCEK), and contains the guest
   **launch measurement** (hash of the booted image/code) and the security
   config (SNP enabled, debug off, TCB version).
3. TEE exposes `(pubkey, attestation_report)` (e.g. a `GET /attestation` endpoint).
4. The **oracle DON** verifies the report *before trusting the key* — exactly the
   white-paper step "oracle nodes verify the enclaves' attestations": check the
   AMD cert chain, the report signature, that the measurement == the expected
   code hash, and that `report_data == keccak(pubkey)`. Only then pin that pubkey
   as the trusted enclave key and accept its result signatures.
5. Runtime signing stays ECDSA (fast); it's now *bootstrapped* from a one-time
   hardware attestation rather than from a human-entered address.

**Where it plugs in.** Verification lives **off-chain in the oracle agents**
(AMD cert-chain verification is too heavy for the EVM). The contract keeps
trusting `teeAddress`, but that address becomes the attested-and-pinned key the
DON agreed on (e.g. via an admin/DON update once attestation passes), instead of
a value set by hand at deploy.

**Rough scope.** TEE: fetch SNP report (e.g. via `/dev/sev-guest` or a helper like
`snpguest`), expose it. Oracle agent: add an attestation-verification step (AMD
cert chain + measurement allowlist) gating which TEE key it will attest under.
Decide how the pinned key reaches the contract (admin set vs DON-quorum set).

---

## 2. Encryption + decryption DON (the "confidential" in confidential compute)

**Status:** DONE (2026-06-18). Implemented per
`docs/superpowers/specs/2026-06-16-encryption-decryption-don-design.md` and
`docs/superpowers/plans/2026-06-17-encryption-decryption-don.md`.

---

## 3. Multi-TEE compute redundancy (enclave pool)

**Status:** deferred. The current single `tee-node` is the last single point in the
compute path (the Oracle DON removes the relay/attestation single point, but not
this). The white paper assigns a request to one or more enclaves from a **pool**.
Run ≥2 TEEs; have the DON require agreement (or attest each independently) so a
TEE outage or compromise is tolerated. Pairs naturally with item 1 (each enclave
attests itself).

---

## 4. OCR-style off-chain attestation aggregation

**Status:** deferred (Oracle DON plan decision **D1** chose on-chain accumulation).
Optimization: have oracles gossip off-chain and submit a single multi-signature
`attest` transaction (one DON-attested response, fewer txs), à la Chainlink OCR,
instead of one tx per oracle. Only changes the aggregation/submission layer.

---

## 5. Richer waterfall coverage

**Status:** deferred. The demo uses the simple `basic_sequential_deal`. Exercise
`rmbs_platform` deals with triggers / Net WAC cap / multi-period / loss allocation
to validate confidential compute over the engine's full feature set.

---

## 6. Split the oracle DON and decryption DON into two networks

**Status:** deferred. We reuse the oracle operators as decryption nodes (one operator
runs both `oracle_agent.py` and `decryption_node.py`). The white paper keeps them
separate so no single node can both decrypt inputs and attest results. Because they are
already two processes, splitting is mostly a deployment change (run the decryption nodes
on a distinct operator set).

---

## 7. Threshold DKG instead of trusted-dealer keygen

**Status:** deferred. `keygen.py` is a trusted dealer that transiently holds the full
master secret. Replace with a distributed key-generation ceremony among the nodes
(white-paper step 0, "decryption nodes jointly generate").

---

## 8. Per-request forward-secure enclave keys

**Status:** deferred. The enclave uses a static receiving key (kfrags pre-generated for
it). The white paper's per-request fresh enclave key (3.1 forward-secure encryption)
limits a compromised enclave to only the requests assigned while compromised.

---

## 9. Off-chain ciphertext storage for large inputs

**Status:** deferred. `capsule + ciphertext` are stored on-chain as `bytes`; fine for the
small RMBS inputs but gas-costly for large future inputs. Store the ciphertext off-chain
and keep only `ciphertextHash` on-chain (the binding digest already carries it).

---

## 10. Shared genesis/deployer key in the member config bundle

**Status:** deferred. `make sync` distributes `members.env` containing the shared
`DEPLOYER_PRIVATE_KEY` (needed to pay for `submitRequest`) and the 4 `ORACLE_KEYS`;
a real deployment would issue per-member funded accounts and use a secret manager
instead of sharing the genesis key. Reference `docs/superpowers/specs/2026-06-26-startup-automation-design.md` §6.
