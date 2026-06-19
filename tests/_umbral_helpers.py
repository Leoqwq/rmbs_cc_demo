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
