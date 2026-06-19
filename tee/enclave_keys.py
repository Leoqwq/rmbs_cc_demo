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
