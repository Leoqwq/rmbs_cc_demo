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
