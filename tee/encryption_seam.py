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

    # Defense-in-depth: the oracle already gathered >= threshold, but the TEE does
    # not trust it. A short count here turns a wrong-key state (e.g. a stale
    # umbral_state.json) into a clear error instead of an opaque umbral failure.
    if len(verified) < state["threshold"]:
        raise ValueError(
            f"only {len(verified)}/{state['threshold']} cfrags verified — "
            "possible wrong-key state or too few honest decryption nodes"
        )

    plaintext = decrypt_reencrypted(
        receiving_sk=enclave_sk,
        delegating_pk=state["master_pk"],
        capsule=capsule,
        verified_cfrags=verified,
        ciphertext=ciphertext,
    )
    return json.loads(plaintext.decode("utf-8"))
